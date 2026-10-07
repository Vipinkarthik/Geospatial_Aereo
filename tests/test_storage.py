"""Unit tests for secure file storage and upload validation."""

import io
from pathlib import Path
import pytest

from app.services.storage import (
    EmptyFileError,
    FileTooLargeError,
    PathTraversalError,
    generate_safe_filename,
    save_upload_stream,
    validate_destination_path,
)


def test_generate_safe_filename():
    """Verify generated filename is safe, unique, and preserves sanitized extension."""
    safe_name1 = generate_safe_filename("survey_data.KML")
    safe_name2 = generate_safe_filename("../../../etc/passwd.zip")

    assert safe_name1.endswith(".kml")
    assert safe_name2.endswith(".zip")
    assert "/" not in safe_name2
    assert "\\" not in safe_name2
    assert safe_name1 != safe_name2


def test_save_empty_file_rejected(tmp_path):
    """Verify 0-byte upload is rejected."""
    empty_stream = io.BytesIO(b"")
    with pytest.raises(EmptyFileError):
        save_upload_stream(empty_stream, "test.kml", target_dir=tmp_path)


def test_save_file_exceeds_size_limit(tmp_path):
    """Verify upload exceeding maximum allowed size is aborted and cleaned up."""
    oversized_data = b"X" * (1024 * 1024 + 10)  # > 1 MB
    with pytest.raises(FileTooLargeError):
        save_upload_stream(
            oversized_data,
            "large.zip",
            target_dir=tmp_path,
            max_size_bytes=1024 * 1024,  # 1 MB limit
        )

    # Ensure no remnant files remain
    assert len(list(tmp_path.iterdir())) == 0


def test_path_traversal_validation(tmp_path):
    """Verify destination path escaping base directory is blocked."""
    outside_path = tmp_path.parent / "escape.txt"
    with pytest.raises(PathTraversalError):
        validate_destination_path(outside_path, tmp_path)


def test_save_valid_file(tmp_path):
    """Verify valid upload stream writes successfully to disk."""
    data = b"Sample valid geospatial file content"
    saved_path = save_upload_stream(data, "valid.kml", target_dir=tmp_path)

    assert saved_path.exists()
    assert saved_path.read_bytes() == data
