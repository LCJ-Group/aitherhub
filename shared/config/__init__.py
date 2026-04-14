"""
Shared Configuration
====================
Centralised environment variable loading for all services.
Both API and Worker read config from here.
"""
import os
import ssl
from pathlib import Path
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
from dotenv import load_dotenv

# Load .env from project root (works for both API and Worker)
_project_root = Path(__file__).resolve().parent.parent.parent
load_dotenv(_project_root / ".env")
load_dotenv()  # also load from cwd if present


# =============================================================================
# Database
# =============================================================================

DATABASE_URL: str = os.getenv("DATABASE_URL", "")

def prepare_database_url(url: str) -> tuple:
    """
    Prepare database URL for asyncpg compatibility.
    asyncpg doesn't support 'sslmode' parameter — convert to ssl context.

    Returns:
        (cleaned_url, connect_args) tuple
    """
    if not url:
        return url, {}

    parsed = urlparse(url)
    query_params = parse_qs(parsed.query)
    connect_args = {}

    if "sslmode" in query_params:
        sslmode = query_params["sslmode"][0]
        del query_params["sslmode"]

        if sslmode == "require":
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            connect_args["ssl"] = ssl_context
        elif sslmode in ("verify-ca", "verify-full"):
            connect_args["ssl"] = ssl.create_default_context()
        elif sslmode == "disable":
            connect_args["ssl"] = False

    new_query = urlencode(query_params, doseq=True)
    cleaned_url = urlunparse((
        parsed.scheme, parsed.netloc, parsed.path,
        parsed.params, new_query, parsed.fragment,
    ))
    return cleaned_url, connect_args


# =============================================================================
# Azure Storage
# =============================================================================

AZURE_STORAGE_CONNECTION_STRING: str = os.getenv("AZURE_STORAGE_CONNECTION_STRING", "")
AZURE_BLOB_CONTAINER: str = os.getenv("AZURE_BLOB_CONTAINER", "videos")
AZURE_BLOB_SAS_EXP_MINUTES: int = int(os.getenv("AZURE_BLOB_SAS_EXP_MINUTES", "1440"))


# =============================================================================
# Azure Queue
# =============================================================================

AZURE_QUEUE_NAME: str = os.getenv("AZURE_QUEUE_NAME", "video-jobs")
AZURE_DEAD_LETTER_QUEUE_NAME: str = os.getenv("AZURE_DEAD_LETTER_QUEUE_NAME", "video-jobs-dead")


# =============================================================================
# Worker
# =============================================================================

WORKER_MAX_CONCURRENT: int = int(os.getenv("WORKER_MAX_CONCURRENT", "2"))
WORKER_MAX_RETRIES: int = int(os.getenv("WORKER_MAX_RETRIES", "6"))  # Increased from 3: 2 workers share queue, each dequeue increments count
WORKER_VIDEO_TIMEOUT: int = int(os.getenv("WORKER_VIDEO_TIMEOUT", str(720 * 60)))  # 12h default (reduced from 24h to free worker capacity sooner)
WORKER_CLIP_TIMEOUT: int = int(os.getenv("WORKER_CLIP_TIMEOUT", str(30 * 60)))


# =============================================================================
# OpenAI
# =============================================================================

OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")


# =============================================================================
# Environment
# =============================================================================

ENVIRONMENT: str = os.getenv("ENVIRONMENT", os.getenv("ENV", "dev"))
