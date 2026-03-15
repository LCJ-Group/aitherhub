"""Utilities for Azure Blob uploads and SAS generation."""

import logging
import os

logger = logging.getLogger(__name__)
import uuid
from datetime import datetime, timedelta, timezone
from typing import Tuple

from azure.storage.blob import (
    BlobServiceClient,
    BlobSasPermissions,
    generate_blob_sas,
)

CONNECTION_STRING = os.getenv("AZURE_STORAGE_CONNECTION_STRING")


def _parse_account_name(conn_str: str | None) -> str:
    """Extract AccountName from a connection string (fallback)."""
    if not conn_str:
        return ""
    for part in conn_str.split(";"):
        if part.startswith("AccountName="):
            return part.split("=", 1)[1]
    return ""


ACCOUNT_NAME = (
    os.getenv("AZURE_STORAGE_ACCOUNT_NAME")
    or _parse_account_name(CONNECTION_STRING)
)
CONTAINER_NAME = os.getenv("AZURE_BLOB_CONTAINER", "videos")
SAS_EXP_MINUTES = int(os.getenv("AZURE_BLOB_SAS_EXP_MINUTES", "1440"))  # default 1 day
SAS_DOWNLOAD_EXP_MINUTES = int(os.getenv("AZURE_BLOB_SAS_DOWNLOAD_MINUTES", "1440"))  # Default 24 hours


def _parse_account_key(conn_str: str) -> str:
    """Extract AccountKey from connection string."""
    if not conn_str:
        raise ValueError("Missing AZURE_STORAGE_CONNECTION_STRING")
    parts = conn_str.split(";")
    for p in parts:
        if p.startswith("AccountKey="):
            return p.split("=", 1)[1]
    raise ValueError("AccountKey not found in connection string")


def _ensure_container(service_client: BlobServiceClient, container: str) -> None:
    container_client = service_client.get_container_client(container)
    try:
        container_client.create_container()
    except Exception:
        # ignore if already exists or cannot create (Azurite already present)
        pass


def generate_blob_name(email: str, video_id: str, filename: str | None = None) -> str:
    """Create a blob name using folder structure: email/video_id/filename"""
    # If filename contains a path (e.g. 'reportvideo/0.0_104.0.mp4'), treat it
    # as a relative blob path under email/video_id/ and keep it intact.
    if filename:
        if "/" in filename:
            return f"{email}/{video_id}/{filename}"
        if "." in filename:
            ext = filename.rsplit(".", 1)[1]
            blob_filename = f"{video_id}.{ext}"
        else:
            blob_filename = f"{video_id}.mp4"
    else:
        blob_filename = f"{video_id}.mp4"

    return f"{email}/{video_id}/{blob_filename}"


async def generate_upload_sas(email: str, video_id: str | None = None, filename: str | None = None) -> Tuple[str, str, str, datetime]:
    """
    Generate a write-only SAS URL for a single blob with folder structure: email/video_id/filename

    Returns:
        upload_url: full SAS URL for direct upload
        blob_url: public blob URL (without SAS)
        expiry: datetime in UTC
    """
    vid = video_id or str(uuid.uuid4())

    if not CONNECTION_STRING:
        raise RuntimeError("AZURE_STORAGE_CONNECTION_STRING is required to generate SAS")

    blob_name = generate_blob_name(email, vid, filename)
    account_key = _parse_account_key(CONNECTION_STRING)
    expiry = datetime.now(timezone.utc) + timedelta(minutes=SAS_EXP_MINUTES)

    # Detect Azurite vs Azure
    is_azurite = "devstoreaccount1" in ACCOUNT_NAME.lower()
    
    # Generate SAS token (works for both Azurite and Azure)
    sas_token = generate_blob_sas(
        account_name=ACCOUNT_NAME,
        container_name=CONTAINER_NAME,
        blob_name=blob_name,
        account_key=account_key,
        permission=BlobSasPermissions(write=True, create=True),
        expiry=expiry,
    )

    if is_azurite:
        # Azurite: extract BlobEndpoint from connection string
        # For local dev: http://localhost:10000/devstoreaccount1
        # For Docker: http://azurite:10000/devstoreaccount1
        blob_endpoint = "http://localhost:10000/devstoreaccount1"  # Default for local
        for part in CONNECTION_STRING.split(";"):
            if part.startswith("BlobEndpoint="):
                blob_endpoint = part.split("=", 1)[1]
                break
        blob_url = f"{blob_endpoint}/{CONTAINER_NAME}/{blob_name}"
    else:
        # Production Azure: use HTTPS
        blob_url = f"https://{ACCOUNT_NAME}.blob.core.windows.net/{CONTAINER_NAME}/{blob_name}"
    
    upload_url = f"{blob_url}?{sas_token}"
    print(f"[storage_service] upload_url: {upload_url}")
    return vid, upload_url, blob_url, expiry


def generate_read_sas_from_url(
    blob_url: str,
    container: str = CONTAINER_NAME,
    expires_hours: int = 24,
) -> str | None:
    """Generate a read-only SAS URL from an existing blob URL.

    This is the **single place** to create SAS tokens for arbitrary blob URLs
    (e.g. clip URLs, report URLs).  All endpoints should call this instead of
    inlining the connection-string parsing + generate_blob_sas logic.

    Returns the full SAS URL, or *None* if credentials are unavailable.
    """
    if not CONNECTION_STRING:
        return None
    try:
        parts = blob_url.split("/")
        # Find container in URL path and extract blob_name after it
        container_idx = parts.index(container) if container in parts else -1
        if container_idx < 0 or container_idx + 1 >= len(parts):
            return None
        blob_name = "/".join(parts[container_idx + 1:])
        # Strip any existing query string from blob_name
        if "?" in blob_name:
            blob_name = blob_name.split("?", 1)[0]
        # URL-decode blob_name (e.g. %40 → @) for correct SAS signature
        from urllib.parse import unquote
        blob_name = unquote(blob_name)
        account_key = _parse_account_key(CONNECTION_STRING)
        expiry = datetime.now(timezone.utc) + timedelta(hours=expires_hours)
        sas = generate_blob_sas(
            account_name=ACCOUNT_NAME,
            container_name=container,
            blob_name=blob_name,
            account_key=account_key,
            permission=BlobSasPermissions(read=True),
            expiry=expiry,
        )
        base_url = blob_url.split("?", 1)[0]  # strip old query
        base_url = unquote(base_url)  # decode %40 etc. to match SAS signature
        return f"{base_url}?{sas}"
    except Exception as exc:
        logger.warning("generate_read_sas_from_url failed for %s: %s", blob_url[:80] if blob_url else None, exc)
        return None


def check_blob_exists(email: str, video_id: str, filename: str) -> bool:
    """
    BUILD 33: Check if a specific blob exists in Azure Blob Storage.
    Used to verify chunks were actually uploaded before starting analysis.
    """
    if not CONNECTION_STRING:
        logger.warning("[check_blob_exists] No connection string — cannot verify blob")
        return True  # Fail open if we can't check

    try:
        blob_name = generate_blob_name(email, video_id, filename)
        service_client = BlobServiceClient.from_connection_string(CONNECTION_STRING)
        blob_client = service_client.get_blob_client(
            container=CONTAINER_NAME, blob=blob_name
        )
        return blob_client.exists()
    except Exception as exc:
        logger.warning(f"[check_blob_exists] Error checking blob: {exc}")
        return True  # Fail open on error


async def generate_download_sas(email: str, video_id: str, filename: str | None = None, expires_in_minutes: int | None = None) -> Tuple[str, datetime]:
    """
    Generate a read-only SAS URL for downloading a blob with folder structure: email/video_id/filename

    Args:
        email: User email for folder path
        video_id: Video ID for folder path
        filename: Optional filename (to determine extension)
        expires_in_minutes: Optional custom expiry in minutes (defaults to SAS_DOWNLOAD_EXP_MINUTES)

    Returns:
        download_url: full SAS URL for direct download
        expiry: datetime in UTC
    """
    if not CONNECTION_STRING:
        raise RuntimeError("AZURE_STORAGE_CONNECTION_STRING is required to generate SAS")

    blob_name = generate_blob_name(email, video_id, filename)
    account_key = _parse_account_key(CONNECTION_STRING)
    
    ttl_minutes = expires_in_minutes if expires_in_minutes is not None else SAS_DOWNLOAD_EXP_MINUTES
    expiry = datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)

    # Detect Azurite vs Azure
    is_azurite = "devstoreaccount1" in ACCOUNT_NAME.lower()
    
    # Generate SAS token with read permission
    sas_token = generate_blob_sas(
        account_name=ACCOUNT_NAME,
        container_name=CONTAINER_NAME,
        blob_name=blob_name,
        account_key=account_key,
        permission=BlobSasPermissions(read=True),
        expiry=expiry,
    )

    if is_azurite:
        blob_endpoint = "http://localhost:10000/devstoreaccount1"
        for part in CONNECTION_STRING.split(";"):
            if part.startswith("BlobEndpoint="):
                blob_endpoint = part.split("=", 1)[1]
                break
        blob_url = f"{blob_endpoint}/{CONTAINER_NAME}/{blob_name}"
    else:
        blob_url = f"https://{ACCOUNT_NAME}.blob.core.windows.net/{CONTAINER_NAME}/{blob_name}"
    
    download_url = f"{blob_url}?{sas_token}"
    print(f"[storage_service] download_url: {download_url} (expires in {ttl_minutes} min)")
    return download_url, expiry

