"""
OCPP Charger Registration API
Simple API to register chargers in the Nginx htpasswd file
"""

import os
import re
import logging
import tempfile
import subprocess
from pathlib import Path

from fastapi import FastAPI, HTTPException, status, Depends, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field, field_validator
from passlib.apache import HtpasswdFile

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Environment variables
HTPASSWD_PATH = os.getenv("HTPASSWD_PATH", "/etc/nginx/auth/ocpp_passwd")
SHARED_PASSWORD = os.getenv("CHARGER_PASSWORD", "")  # Predefined shared password
API_KEY = os.getenv("API_KEY", "")  # API key for authentication
CA_CERT_PATH = os.getenv("CA_CERT_PATH", "/etc/nginx/mtls/ca.crt")
CA_KEY_PATH = os.getenv("CA_KEY_PATH", "/etc/nginx/mtls/ca.key")
CA_SERIAL_PATH = os.getenv(
    "CA_SERIAL_PATH", "/var/lib/ca/ca.srl"
)  # Persistent serial file

# API Key Security
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def verify_api_key(api_key: str = Security(api_key_header)):
    """Verify the API key from header."""
    if not API_KEY:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="API key not configured. Set API_KEY environment variable.",
        )
    if api_key is None or api_key != API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
            headers={"WWW-Authenticate": "X-API-Key"},
        )
    return api_key


# FastAPI app
app = FastAPI(
    title="OCPP Charger Registration API",
    description="API for registering EV chargers in the OCPP authentication system",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =============================================================================
# Pydantic Models
# =============================================================================


class HealthResponse(BaseModel):
    """Health check response."""

    status: str
    htpasswd_exists: bool
    charger_count: int


class ChargerRegisterRequest(BaseModel):
    """Request model for charger registration."""

    charger_id: str = Field(
        ...,
        min_length=1,
        max_length=50,
        description="Unique charger identifier (e.g., CP001, VCP001)",
    )

    @field_validator("charger_id")
    @classmethod
    def validate_charger_id(cls, v: str) -> str:
        """Validate charger ID format - alphanumeric and underscores only."""
        if not re.match(r"^[A-Za-z0-9_-]+$", v):
            raise ValueError(
                "Charger ID must contain only letters, numbers, underscores, and hyphens"
            )
        return v


class ChargerRegisterResponse(BaseModel):
    """Response model for charger registration."""

    status: str
    charger_id: str
    message: str


class ChargerListResponse(BaseModel):
    """Response model for listing chargers."""

    count: int
    chargers: list[str]


class ChargerDeleteRequest(BaseModel):
    """Request model for charger deletion."""

    charger_id: str = Field(..., description="Charger ID to delete")


class CertificateRequest(BaseModel):
    """Request model for client certificate generation."""

    charger_serial_number: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Charger serial number for certificate CN",
    )
    country: str = Field(
        default="HK", max_length=2, description="Country code (2 letters)"
    )
    state: str = Field(default="HK", max_length=50, description="State/Province")
    organization: str = Field(
        default="Computime", max_length=100, description="Organization name"
    )
    organizational_unit: str = Field(
        default="EV Chargers", max_length=100, description="Organizational unit"
    )
    validity_days: int = Field(
        default=825, ge=1, le=3650, description="Certificate validity in days"
    )

    @field_validator("charger_serial_number")
    @classmethod
    def validate_serial_number(cls, v: str) -> str:
        """Validate charger serial number format - alphanumeric, underscores, and hyphens only."""
        if not re.match(r"^[A-Za-z0-9_-]+$", v):
            raise ValueError(
                "Serial number must contain only letters, numbers, underscores, and hyphens"
            )
        return v


class CertificateResponse(BaseModel):
    """Response model for certificate generation."""

    status: str
    charger_serial_number: str
    private_key: str
    certificate: str
    csr: str
    ca: str
    message: str


# =============================================================================
# Helper Functions
# =============================================================================


def get_htpasswd() -> HtpasswdFile:
    """Get or create htpasswd file."""
    path = Path(HTPASSWD_PATH)
    if not path.exists():
        # Create empty htpasswd file if it doesn't exist
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    return HtpasswdFile(str(path))


def get_charger_list() -> list[str]:
    """Get list of registered chargers."""
    try:
        htpasswd = get_htpasswd()
        return list(htpasswd.users())
    except Exception as e:
        logger.error(f"Error reading htpasswd: {e}")
        return []


def generate_client_certificate(
    serial_number: str,
    country: str,
    state: str,
    organization: str,
    org_unit: str,
    validity_days: int,
) -> tuple[str, str, str]:
    """
    Generate a client certificate signed by the CA.

    Returns:
        Tuple of (private_key, certificate, csr)
    """
    # Verify CA files exist
    ca_cert = Path(CA_CERT_PATH)
    ca_key = Path(CA_KEY_PATH)

    if not ca_cert.exists():
        raise FileNotFoundError(f"CA certificate not found at {CA_CERT_PATH}")
    if not ca_key.exists():
        raise FileNotFoundError(f"CA key not found at {CA_KEY_PATH}")

    # Ensure serial file directory exists
    srl_path = Path(CA_SERIAL_PATH)
    srl_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmpdir:
        key_path = Path(tmpdir) / f"{serial_number}.key"
        csr_path = Path(tmpdir) / f"{serial_number}.csr"
        crt_path = Path(tmpdir) / f"{serial_number}.crt"
        ext_path = Path(tmpdir) / "ext.cnf"

        # Write extension file for clientAuth
        ext_path.write_text("extendedKeyUsage=clientAuth")

        # Generate private key (4096 bits)
        subprocess.run(
            ["openssl", "genrsa", "-out", str(key_path), "4096"],
            check=True,
            capture_output=True,
        )

        # Generate CSR
        subject = (
            f"/C={country}/ST={state}/O={organization}/OU={org_unit}/CN={serial_number}"
        )
        subprocess.run(
            [
                "openssl",
                "req",
                "-new",
                "-key",
                str(key_path),
                "-out",
                str(csr_path),
                "-subj",
                subject,
            ],
            check=True,
            capture_output=True,
        )

        # Sign the certificate with CA
        # Use -CAserial with persistent path for serial tracking
        # -CAcreateserial creates the file if it doesn't exist
        subprocess.run(
            [
                "openssl",
                "x509",
                "-req",
                "-in",
                str(csr_path),
                "-CA",
                str(ca_cert),
                "-CAkey",
                str(ca_key),
                "-CAserial",
                str(srl_path),
                "-CAcreateserial",
                "-out",
                str(crt_path),
                "-days",
                str(validity_days),
                "-sha256",
                "-extfile",
                str(ext_path),
            ],
            check=True,
            capture_output=True,
        )

        # Read generated files
        private_key = key_path.read_text()
        certificate = crt_path.read_text()
        csr = csr_path.read_text()
        ca_certificate = ca_cert.read_text()

        return private_key, certificate, csr, ca_certificate


# =============================================================================
# API Endpoints
# =============================================================================


@app.get("/", tags=["Root"])
async def root():
    """Root endpoint with API information (no auth required)."""
    return {
        "name": "OCPP Charger Registration API",
        "version": "1.0.0",
        "endpoints": {
            "health": "/health",
            "docs": "/docs",
            "register": "POST /charger/register",
            "list": "GET /chargers",
            "delete": "DELETE /charger/{charger_id}",
            "generate_cert": "POST /certificate/generate",
        },
    }


@app.get("/health", response_model=HealthResponse, tags=["Health"])
async def health_check(api_key: str = Depends(verify_api_key)):
    """Health check endpoint."""
    path = Path(HTPASSWD_PATH)
    chargers = get_charger_list()
    return HealthResponse(
        status="healthy", htpasswd_exists=path.exists(), charger_count=len(chargers)
    )


@app.get("/chargers", response_model=ChargerListResponse, tags=["Chargers"])
async def list_chargers(api_key: str = Depends(verify_api_key)):
    """List all registered chargers."""
    chargers = get_charger_list()
    return ChargerListResponse(count=len(chargers), chargers=chargers)


@app.get("/charger/{charger_id}", tags=["Chargers"])
async def check_charger(charger_id: str, api_key: str = Depends(verify_api_key)):
    """Check if a charger is registered."""
    chargers = get_charger_list()
    exists = charger_id in chargers
    return {"charger_id": charger_id, "registered": exists}


@app.post(
    "/charger/register", response_model=ChargerRegisterResponse, tags=["Chargers"]
)
async def register_charger(
    request: ChargerRegisterRequest, api_key: str = Depends(verify_api_key)
):
    """
    Register a new charger in the authentication system.

    The charger will be added to the htpasswd file with the predefined shared password.
    """
    if not SHARED_PASSWORD:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Shared password not configured. Set CHARGER_PASSWORD environment variable.",
        )

    try:
        htpasswd = get_htpasswd()

        # Check if charger already exists
        if request.charger_id in htpasswd.users():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Charger '{request.charger_id}' is already registered",
            )

        # Add charger with shared password
        htpasswd.set_password(request.charger_id, SHARED_PASSWORD)
        htpasswd.save()

        logger.info(f"Registered charger: {request.charger_id}")

        return ChargerRegisterResponse(
            status="success",
            charger_id=request.charger_id,
            message=f"Charger '{request.charger_id}' registered successfully",
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error registering charger: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to register charger: {str(e)}",
        )


@app.delete("/charger/{charger_id}", tags=["Chargers"])
async def delete_charger(charger_id: str, api_key: str = Depends(verify_api_key)):
    """Remove a charger from the authentication system."""
    try:
        htpasswd = get_htpasswd()

        # Check if charger exists
        if charger_id not in htpasswd.users():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Charger '{charger_id}' not found",
            )

        # Delete charger
        htpasswd.delete(charger_id)
        htpasswd.save()

        logger.info(f"Deleted charger: {charger_id}")

        return {
            "status": "success",
            "charger_id": charger_id,
            "message": f"Charger '{charger_id}' deleted successfully",
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting charger: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete charger: {str(e)}",
        )


@app.post(
    "/certificate/generate", response_model=CertificateResponse, tags=["Certificates"]
)
async def generate_certificate(
    request: CertificateRequest, api_key: str = Depends(verify_api_key)
):
    """
    Generate a client certificate for mTLS authentication.

    This endpoint generates a private key, CSR, and certificate signed by the CA.
    The certificate is intended for EV charger mTLS client authentication.
    """
    try:
        logger.info(f"Generating certificate for: {request.charger_serial_number}")

        private_key, certificate, csr, ca_certificate = generate_client_certificate(
            serial_number=request.charger_serial_number,
            country=request.country,
            state=request.state,
            organization=request.organization,
            org_unit=request.organizational_unit,
            validity_days=request.validity_days,
        )

        logger.info(
            f"Certificate generated successfully for: {request.charger_serial_number}"
        )

        return CertificateResponse(
            status="success",
            charger_serial_number=request.charger_serial_number,
            private_key=private_key,
            certificate=certificate,
            csr=csr,
            ca=ca_certificate,
            message=f"Certificate for '{request.charger_serial_number}' generated successfully (valid for {request.validity_days} days)",
        )

    except FileNotFoundError as e:
        logger.error(f"CA files not found: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)
        )
    except subprocess.CalledProcessError as e:
        logger.error(f"OpenSSL error: {e.stderr.decode() if e.stderr else str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate certificate: OpenSSL error",
        )
    except Exception as e:
        logger.error(f"Error generating certificate: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate certificate: {str(e)}",
        )


@app.get("/internal/validate/{charger_id}", tags=["Internal"])
async def validate_charger_internal(charger_id: str):
    """
    Internal endpoint for Nginx auth_request.
    Returns 200 if charger is registered, 403 if not.
    No API key required (should only be accessible internally).
    """
    chargers = get_charger_list()
    if charger_id in chargers:
        return {"status": "ok"}
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN, detail="Charger not registered"
    )


# =============================================================================
# Startup/Shutdown Events
# =============================================================================


@app.on_event("startup")
async def startup_event():
    """Application startup tasks."""
    logger.info("OCPP Charger Registration API starting up...")
    logger.info(f"Htpasswd path: {HTPASSWD_PATH}")
    logger.info(f"Shared password configured: {'Yes' if SHARED_PASSWORD else 'No'}")
    logger.info(f"API key configured: {'Yes' if API_KEY else 'No'}")
    logger.info(f"CA cert path: {CA_CERT_PATH}")
    logger.info(f"CA key path: {CA_KEY_PATH}")
    logger.info(f"CA serial path: {CA_SERIAL_PATH}")

    # Check htpasswd file
    path = Path(HTPASSWD_PATH)
    if path.exists():
        chargers = get_charger_list()
        logger.info(f"Found {len(chargers)} registered chargers")
    else:
        logger.warning(f"Htpasswd file not found at {HTPASSWD_PATH}")

    # Check CA files
    ca_cert = Path(CA_CERT_PATH)
    ca_key = Path(CA_KEY_PATH)
    if ca_cert.exists() and ca_key.exists():
        logger.info("CA certificate and key found - certificate generation enabled")
    else:
        logger.warning("CA files not found - certificate generation will fail")


@app.on_event("shutdown")
async def shutdown_event():
    """Application shutdown tasks."""
    logger.info("OCPP Charger Registration API shutting down...")
