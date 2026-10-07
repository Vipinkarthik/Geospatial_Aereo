"""Geospatial file reading and archive security validation service.

Strictly validates archive contents, guards against zip-slip and decompression bombs,
requires critical shapefile components (.shp, .shx, .dbf, .prj), and combines
multi-layer KML datasets into standardized GeoDataFrames.
"""

import os
from pathlib import Path
from typing import Any
import zipfile

import geopandas as gpd
import pandas as pd
import pyogrio

from app.services.storage import secure_temporary_directory

# Default ceiling for uncompressed archive extraction (50 MB)
MAX_UNCOMPRESSED_ARCHIVE_BYTES = 50 * 1024 * 1024


class GeospatialReaderError(Exception):
    """Base exception for geospatial file reading errors."""
    pass


class InvalidFileExtensionError(GeospatialReaderError):
    """Raised when an unsupported file extension is provided."""
    pass


class InvalidFileContentError(GeospatialReaderError):
    """Raised when file signature/content does not match the file extension."""
    pass


class CorruptZipFileError(GeospatialReaderError):
    """Raised when a zip archive is corrupted or damaged."""
    pass


class ZipSlipError(GeospatialReaderError):
    """Raised when a zip entry attempts path traversal outside the extraction root."""
    pass


class DecompressionBombError(GeospatialReaderError):
    """Raised when uncompressed archive size exceeds maximum safety limit."""
    pass


class MissingShapefileComponentError(GeospatialReaderError):
    """Raised when an essential Shapefile component (.shp, .shx, .dbf) is missing."""
    pass


class MissingCRSFileError(GeospatialReaderError):
    """Raised when a Shapefile archive lacks a .prj file or CRS specification."""
    pass


class UnreadableKMLError(GeospatialReaderError):
    """Raised when KML content cannot be parsed or read."""
    pass


class ZeroFeaturesError(GeospatialReaderError):
    """Raised when an ingested geospatial dataset contains zero features."""
    pass


def _validate_zip_archive_security(
    zip_path: Path,
    max_uncompressed_bytes: int | None = None,
) -> tuple[str, list[str]]:
    """
    Perform deep security and structural validation on a ZIP archive before extraction.

    Checks:
    - Valid ZIP header signature (magic bytes)
    - Structural integrity via zipfile.testzip()
    - Decompression bomb threshold
    - Zip-slip directory traversal paths
    - Presence of required Shapefile components (.shp, .shx, .dbf) and .prj

    Returns:
    - (primary_shp_entry_name, list_of_all_valid_entries)
    """
    limit_uncompressed = (
        max_uncompressed_bytes
        if max_uncompressed_bytes is not None
        else MAX_UNCOMPRESSED_ARCHIVE_BYTES
    )
    # 1. Content magic byte validation
    with open(zip_path, "rb") as f:
        header = f.read(4)
    if header != b"PK\x03\x04" and not zipfile.is_zipfile(zip_path):
        raise InvalidFileContentError("File has .zip extension but is not a valid ZIP archive.")

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            # 2. Integrity check
            corrupt_member = zf.testzip()
            if corrupt_member:
                raise CorruptZipFileError(f"Corrupted file detected inside archive: '{corrupt_member}'")

            infolist = zf.infolist()
            if not infolist:
                raise CorruptZipFileError("Zip archive is empty.")

            # 3. Decompression bomb threshold check
            total_uncompressed = sum(info.file_size for info in infolist)
            if total_uncompressed > limit_uncompressed:
                raise DecompressionBombError(
                    f"Total uncompressed size ({total_uncompressed} bytes) exceeds limit of {limit_uncompressed} bytes."
                )

            # Filter out macOS metadata files and folders
            valid_entries = [
                info.filename for info in infolist
                if not info.is_dir()
                and not info.filename.startswith("__MACOSX")
                and not Path(info.filename).name.startswith("._")
            ]

            # 4. Zip-slip path traversal check
            dummy_root = Path(".").resolve()
            for filename in valid_entries:
                resolved_dest = (dummy_root / filename).resolve()
                try:
                    resolved_dest.relative_to(dummy_root)
                except ValueError:
                    raise ZipSlipError(f"Potential zip-slip path traversal attack in entry: '{filename}'")

            # 5. Component check for Shapefiles
            shp_entries = [name for name in valid_entries if name.lower().endswith(".shp")]
            if not shp_entries:
                raise MissingShapefileComponentError("Zip archive does not contain any .shp Shapefile component.")

            # Pick primary .shp
            primary_shp = shp_entries[0]
            shp_stem = str(Path(primary_shp).with_suffix("")).lower()

            # Normalized lookup for companion files
            entry_set = {name.lower(): name for name in valid_entries}
            req_shx = f"{shp_stem}.shx"
            req_dbf = f"{shp_stem}.dbf"
            req_prj = f"{shp_stem}.prj"

            missing: list[str] = []
            if req_shx not in entry_set:
                missing.append(".shx")
            if req_dbf not in entry_set:
                missing.append(".dbf")

            if missing:
                raise MissingShapefileComponentError(
                    f"Shapefile '{primary_shp}' is missing required companion file(s): {', '.join(missing)}"
                )

            if req_prj not in entry_set:
                raise MissingCRSFileError(
                    f"Shapefile '{primary_shp}' is missing required .prj coordinate reference system file."
                )

            return primary_shp, valid_entries

    except zipfile.BadZipFile as exc:
        raise CorruptZipFileError(f"Corrupt or invalid zip archive: {exc}") from exc


def _read_shapefile_zip(
    zip_path: Path,
    max_uncompressed_bytes: int | None = None,
) -> tuple[gpd.GeoDataFrame, str]:
    """Safely extract validated shapefile archive into a temporary folder and read it."""
    primary_shp, _ = _validate_zip_archive_security(zip_path, max_uncompressed_bytes)

    with secure_temporary_directory() as temp_extract_dir:
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(temp_extract_dir)

        shp_full_path = temp_extract_dir / primary_shp
        try:
            gdf = gpd.read_file(shp_full_path, engine="pyogrio")
        except Exception as exc:
            raise GeospatialReaderError(f"Failed to read extracted shapefile: {exc}") from exc

        if gdf.crs is None:
            raise MissingCRSFileError("Shapefile has no coordinate reference system defined in .prj.")

        if len(gdf) == 0:
            raise ZeroFeaturesError("Shapefile contains zero geospatial features.")

        crs_str = f"EPSG:{gdf.crs.to_epsg()}" if gdf.crs.to_epsg() else gdf.crs.to_string()
        return gdf, crs_str


def _read_kml(kml_path: Path) -> tuple[gpd.GeoDataFrame, str]:
    """
    Validate, inspect, and read all layers from a KML file, combining them into a unified GeoDataFrame.
    According to OGC standards, KML coordinates are geographic WGS 84 (EPSG:4326).
    """
    # 1. Content validation
    with open(kml_path, "rb") as f:
        header_sample = f.read(2048)
    if not header_sample:
        raise InvalidFileContentError("KML file is empty.")

    header_text = header_sample.decode("utf-8", errors="ignore").lower()
    if "<kml" not in header_text and "<?xml" not in header_text:
        raise InvalidFileContentError("File has .kml extension but does not contain valid KML/XML markup.")

    # 2. Discover all available layers
    try:
        layers_info = pyogrio.list_layers(kml_path)
    except Exception as exc:
        raise UnreadableKMLError(f"Failed to inspect KML layers: {exc}") from exc

    if len(layers_info) == 0:
        raise ZeroFeaturesError("KML file contains zero layers or features.")

    # 3. Read every layer and merge
    layer_dfs: list[gpd.GeoDataFrame] = []
    for layer_entry in layers_info:
        layer_name = layer_entry[0] if hasattr(layer_entry, "__getitem__") else str(layer_entry)
        try:
            gdf_layer = gpd.read_file(kml_path, layer=layer_name, engine="pyogrio")
            if len(gdf_layer) > 0:
                layer_dfs.append(gdf_layer)
        except Exception:
            # Continue to read remaining layers if an individual layer fails
            continue

    if not layer_dfs:
        raise ZeroFeaturesError("KML file contains zero geospatial features across all layers.")

    combined_df = pd.concat(layer_dfs, ignore_index=True)
    combined_gdf = gpd.GeoDataFrame(combined_df, geometry="geometry", crs="EPSG:4326")

    return combined_gdf, "EPSG:4326"


def read_geospatial_file(
    file_path: Path | str,
    max_uncompressed_bytes: int | None = None,
) -> tuple[gpd.GeoDataFrame, str]:
    """
    Validate and ingest a geospatial file (.zip Shapefile or .kml).

    Returns:
        tuple[gpd.GeoDataFrame, str]: The loaded GeoDataFrame and canonical source CRS string.

    Raises:
        InvalidFileExtensionError: If extension is not .zip or .kml.
        InvalidFileContentError: If content does not match extension.
        CorruptZipFileError: If zip file cannot be unpacked or tested.
        ZipSlipError: If zip entry attempts path traversal.
        DecompressionBombError: If uncompressed zip size exceeds threshold.
        MissingShapefileComponentError: If .shp, .shx, or .dbf is missing.
        MissingCRSFileError: If .prj is missing or CRS is absent.
        UnreadableKMLError: If KML layers cannot be parsed.
        ZeroFeaturesError: If dataset contains 0 features.
    """
    path = Path(file_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"File not found: '{path}'")

    ext = path.suffix.lower()
    if ext not in (".zip", ".kml"):
        raise InvalidFileExtensionError(
            f"Unsupported file extension '{ext}'. Only .zip (Shapefile) and .kml are supported."
        )

    if ext == ".zip":
        return _read_shapefile_zip(path, max_uncompressed_bytes)

    return _read_kml(path)
