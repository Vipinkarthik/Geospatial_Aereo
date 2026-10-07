"""Secure file storage service for incoming uploads.

Enforces size limitations, prevents directory traversal, rejects zero-byte payloads,
and generates unpredictable collision-proof filenames.
"""

from collections.abc import Generator
from contextlib import contextmanager
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import BinaryIO, Union
import uuid

from app.config import settings


class StorageError(ValueError):
    """Base exception for file storage failures."""
    pass


class EmptyFileError(StorageError):
    """Raised when an uploaded file contains zero bytes."""
    pass


class FileTooLargeError(StorageError):
    """Raised when an uploaded file exceeds the configured maximum size threshold."""
    pass


class PathTraversalError(StorageError):
    """Raised when a filename or path attempts to escape the designated storage directory."""
    pass


def sanitize_extension(filename: str) -> str:
    """Extract and validate the lowercased file extension (e.g., '.kml', '.zip')."""
    ext = Path(filename).suffix.lower()
    # Strip any non-alphanumeric characters except dot
    clean_ext = re.sub(r"[^a-z0-9.]", "", ext)
    return clean_ext if clean_ext.startswith(".") else f".{clean_ext}"


def generate_safe_filename(original_filename: str) -> str:
    """
    Generate an unpredictable filename combining a UUID4 and sanitized extension.
    Never trusts or retains client-supplied path components or unsafe characters.
    """
    ext = sanitize_extension(original_filename)
    unique_id = uuid.uuid4().hex
    return f"{unique_id}{ext}"


def validate_destination_path(destination_path: Path, base_dir: Path) -> Path:
    """Ensure that destination_path strictly resides within base_dir."""
    resolved_base = base_dir.resolve()
    resolved_dest = destination_path.resolve()
    try:
        resolved_dest.relative_to(resolved_base)
    except ValueError as exc:
        raise PathTraversalError(
            f"Path traversal detected: '{resolved_dest}' escapes base directory '{resolved_base}'"
        ) from exc
    return resolved_dest


def save_upload_stream(
    content_stream: Union[BinaryIO, bytes],
    original_filename: str,
    target_dir: Union[Path, str, None] = None,
    max_size_bytes: int | None = None,
    chunk_size: int = 1024 * 1024,  # 1 MB chunk
) -> Path:
    """
    Safely stream and persist upload content to disk.

    Enforces:
    - 0-byte check (rejects empty uploads)
    - Size ceiling (aborts immediately and deletes partial file if threshold exceeded)
    - Path confinement to target_dir
    """
    base_dir = Path(target_dir if target_dir is not None else settings.upload_dir)
    base_dir.mkdir(parents=True, exist_ok=True)

    max_limit = max_size_bytes if max_size_bytes is not None else settings.max_upload_size_bytes
    safe_name = generate_safe_filename(original_filename)
    dest_path = validate_destination_path(base_dir / safe_name, base_dir)

    total_bytes = 0

    try:
        with open(dest_path, "wb") as out_file:
            if isinstance(content_stream, (bytes, bytearray)):
                total_bytes = len(content_stream)
                if total_bytes == 0:
                    raise EmptyFileError("Uploaded file is empty (0 bytes).")
                if total_bytes > max_limit:
                    raise FileTooLargeError(
                        f"File size ({total_bytes} bytes) exceeds maximum limit of {max_limit} bytes."
                    )
                out_file.write(content_stream)
            else:
                while True:
                    chunk = content_stream.read(chunk_size)
                    if not chunk:
                        break
                    total_bytes += len(chunk)
                    if total_bytes > max_limit:
                        raise FileTooLargeError(
                            f"File size exceeded maximum limit of {max_limit} bytes while streaming."
                        )
                    out_file.write(chunk)

                if total_bytes == 0:
                    raise EmptyFileError("Uploaded file is empty (0 bytes).")

    except Exception:
        # Guarantee cleanup of partial file on any failure
        if dest_path.exists():
            dest_path.unlink(missing_ok=True)
        raise

    return dest_path


@contextmanager
def secure_temporary_directory(prefix: str = "geo_extract_") -> Generator[Path, None, None]:
    """Context manager creating a safe, auto-deleting temporary directory for archive extraction."""
    temp_dir = tempfile.mkdtemp(prefix=prefix)
    try:
        yield Path(temp_dir)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
