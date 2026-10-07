# Geo Measurement API (`geo-measurement-api`)

A production-grade, high-performance geospatial backend built with **FastAPI**, **SQLAlchemy 2.x**, **GeoPandas**, **PyProj**, and **Shapely**. 

The service ingests aerial survey datasets and GIS vector files (`.kml` and `.zip` Shapefiles), validates spatial integrity and archive security, projects coordinates into local metric planar coordinate systems (UTM / Equal-Area), computes geometric properties (areas in $\text{m}^2$ and hectares, lengths in meters and kilometers), and persists features as query-optimized JSON records.

---

## Table of Contents
1. [Overview](#1-overview)
2. [Setup & Installation](#2-setup--installation)
3. [API Documentation & Examples](#3-api-documentation--examples)
4. [Standardized Error Format](#4-standardized-error-format)
5. [System Architecture & Flows](#5-system-architecture--flows)
6. [Geospatial & CRS Strategy](#6-geospatial--crs-strategy)
7. [Design Decisions & Trade-Off Alternatives](#7-design-decisions--trade-off-alternatives)
8. [Security & Defensive Hardening](#8-security--defensive-hardening)
9. [Limitations & Assumptions](#9-limitations--assumptions)
10. [Engineering Learnings Draft](#10-engineering-learnings-draft)
11. [Future Scope](#11-future-scope)

---

## 1. Overview

### Problem Being Solved
Geographic coordinates (such as WGS 84 / `EPSG:4326`) express positions on an ellipsoidal Earth in **angular degrees**. Measuring distances or areas directly in degrees yields mathematically invalid results because the linear ground distance of a degree of longitude varies from $\approx 111.32\text{ km}$ at the equator to $0\text{ km}$ at the poles.

Furthermore, naive GIS web backends frequently suffer from two critical flaws:
1. **Security Vulnerabilities:** Archives are susceptible to zip-slip path traversal and decompression bombs.
2. **I/O Bottlenecks:** APIs re-open, decompress, and re-parse heavy binary Shapefiles or XML KML trees on every measurement query.

### What the Service Accepts
- **KML Datasets (`.kml`):** Single- or multi-layer Keyhole Markup Language XML files containing points, line strings, polygons, or collections.
- **ESRI Shapefile Archives (`.zip`):** Compressed archives containing the required shapefile family: `.shp` (geometry), `.shx` (spatial index), `.dbf` (attributes), and `.prj` (Coordinate Reference System definition).

### What the Service Returns
- Synchronously validated file upload confirmation (`HTTP 201 Created`).
- Detailed file lifecycle and processing state inspections (`HTTP 200 OK`).
- Paginated, filterable per-feature measurements including GeoJSON geometry representations, source and projected CRS identifiers, calculated planar metrics ($\text{m}^2$, hectares, meters, kilometers), and warnings (e.g., topology repairs, zone boundaries, or polar fallbacks).

---

## 2. Setup & Installation

### Prerequisites
- **Python:** 3.11+ (tested on Python 3.11.9 on Windows x64)
- **OS:** Windows 10/11, macOS, or Linux

### 1. Clone & Navigate to Project Root
```powershell
cd "d:\Geospatial Aereo"
```

### 2. Create Virtual Environment
```powershell
python -m venv venv
```

### 3. Activate Virtual Environment
#### PowerShell:
```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned -Force
.\venv\Scripts\Activate.ps1
```
#### Command Prompt (cmd.exe):
```cmd
venv\Scripts\activate.bat
```

### 4. Upgrade pip & Install Dependencies
```powershell
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 5. Environment Configuration
The application uses `pydantic-settings` with default values. To customize settings, create a `.env` file in the project root:
```env
# Database connection (defaults to local SQLite)
DATABASE_URL=sqlite:///./geo_measurement.db

# Storage configuration
UPLOAD_DIR=uploads
MAX_UPLOAD_SIZE_BYTES=20971520

# Debug and Logging
DEBUG=false
```

### 6. Database Initialization
Database tables (`uploaded_files` and `feature_records`) initialize automatically on FastAPI application startup via lifespan events. To initialize tables manually:
```powershell
python -c "from app.db import init_db; init_db(); print('Database tables initialized successfully')"
```

### 7. Run Local Development Server
```powershell
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```
Interactive Swagger API documentation is available at: `http://127.0.0.1:8000/docs`.

### 8. Run Complete Test Suite
Execute the comprehensive 98-test test suite covering all units, integration pipelines, accuracy thresholds, and security hardening:
```powershell
python -m pytest -v
```

---

## 3. API Documentation & Examples

### Endpoint Summary
| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/health` | Service operational health check |
| `POST` | `/api/files/` | Ingest, validate, extract, and measure a geospatial file |
| `GET` | `/api/files/{id}/` | Inspect file metadata, processing state, and error logs |
| `GET` | `/api/files/{id}/measurements/` | Retrieve precomputed feature measurements (with pagination and filtering) |

---

### `POST /api/files/`
Ingests a `.kml` or `.zip` Shapefile upload, validates archive security, extracts features, calculates planar measurements, and persists records synchronously.

- **Content-Type:** `multipart/form-data`
- **Request Field:** `file` (Binary file)

#### cURL Example:
```bash
curl -X POST "http://127.0.0.1:8000/api/files/" \
  -H "accept: application/json" \
  -F "file=@sample_data/sample.kml"
```

#### Successful Response (`HTTP 201 Created`):
```json
{
  "id": 1,
  "filename": "sample.kml",
  "feature_count": 3,
  "crs": "EPSG:4326",
  "status": "COMPLETED"
}
```

#### Error Response Example (`HTTP 400 Bad Request`):
```json
{
  "error": {
    "code": "MISSING_CRS",
    "message": "Shapefile 'plots.shp' is missing required .prj coordinate reference system file."
  }
}
```

---

### `GET /api/files/{id}/`
Retrieves ingestion metadata, feature count, detected CRS, and error messages for a specific file.

- **Path Parameter:** `id` (integer)

#### cURL Example:
```bash
curl -X GET "http://127.0.0.1:8000/api/files/1/" -H "accept: application/json"
```

#### Successful Response (`HTTP 200 OK`):
```json
{
  "id": 1,
  "filename": "sample.kml",
  "feature_count": 3,
  "crs": "EPSG:4326",
  "status": "COMPLETED",
  "error_message": null,
  "created_at": "2026-10-07T05:05:38.822123Z"
}
```

#### Error Response (`HTTP 404 Not Found`):
```json
{
  "error": {
    "code": "FILE_NOT_FOUND",
    "message": "File with ID 999999 not found."
  }
}
```

---

### `GET /api/files/{id}/measurements/`
Retrieves precomputed spatial measurements directly from database JSON without touching disk files.

- **Path Parameter:** `id` (integer)
- **Query Parameters:**
  - `limit` (int, default: `50`, min: `1`, max: `500`): Maximum features per page.
  - `offset` (int, default: `0`, min: `0`): Number of features to skip.
  - `geometry_type` (string, optional): Filter by geometry primitive (e.g., `Polygon`, `LineString`, `Point`).

#### cURL Example (Paginated & Filtered):
```bash
curl -X GET "http://127.0.0.1:8000/api/files/1/measurements/?geometry_type=Polygon&limit=10&offset=0" \
  -H "accept: application/json"
```

#### Successful Response (`HTTP 200 OK`):
```json
{
  "file_id": 1,
  "filename": "sample.kml",
  "total_features": 1,
  "page": 1,
  "page_size": 10,
  "total_pages": 1,
  "limit": 10,
  "offset": 0,
  "features": [
    {
      "feature_id": 1,
      "geometry_type": "Polygon",
      "geometry": {
        "type": "Polygon",
        "coordinates": [
          [
            [78.0, 11.0],
            [78.001, 11.0],
            [78.001, 11.001],
            [78.0, 11.001],
            [78.0, 11.0]
          ]
        ]
      },
      "crs": "EPSG:4326",
      "properties": {
        "Name": "Plot A",
        "description": null
      },
      "measurement": {
        "type": "area",
        "value": 12111.2851,
        "unit": "m²",
        "projected_crs": "EPSG:32644",
        "supported": true,
        "reason": null,
        "hectares": 1.211129,
        "kilometers": null
      },
      "warnings": []
    }
  ]
}
```

#### Error Response (`HTTP 409 Conflict`):
Returned when measurements are requested for a file that is still `PROCESSING` or has `FAILED`:
```json
{
  "error": {
    "code": "FILE_NOT_READY",
    "message": "File is currently in status 'FAILED'. Measurements are only available for COMPLETED files."
  }
}
```

---

### `GET /health`
Operational readiness probe for load balancers and orchestrators.

#### cURL Example:
```bash
curl -X GET "http://127.0.0.1:8000/health" -H "accept: application/json"
```

#### Successful Response (`HTTP 200 OK`):
```json
{
  "status": "ok",
  "app": "Geo Measurement API",
  "version": "0.1.0"
}
```

---

## 4. Standardized Error Format

Every client and server failure returns a consistent JSON envelope:

```json
{
  "error": {
    "code": "MACHINE_READABLE_CODE",
    "message": "Human-readable explanation of what failed."
  }
}
```

### Error Code Reference
| Code | Status Code | Trigger Reason |
| :--- | :--- | :--- |
| `INVALID_FILE_EXTENSION` | `400 Bad Request` | Upload extension is not `.zip` or `.kml`. |
| `INVALID_FILE_CONTENT` | `400 Bad Request` | File signature/magic bytes do not match declared extension. |
| `EMPTY_FILE` | `400 Bad Request` | Uploaded file contains 0 bytes. |
| `FILE_TOO_LARGE` | `413 Content Too Large` | Upload size exceeds configured ceiling (`20 MB`). |
| `CORRUPT_ARCHIVE` | `400 Bad Request` | Zip archive is truncated, corrupted, or unreadable. |
| `MISSING_SHAPEFILE_COMPONENTS`| `400 Bad Request` | Archive is missing required `.shp`, `.shx`, or `.dbf` companion files. |
| `MISSING_CRS` | `400 Bad Request` | Shapefile lacks a `.prj` coordinate reference file or has no CRS. |
| `UNREADABLE_KML` | `400 Bad Request` | Malformed XML or unparseable KML structure. |
| `ZERO_FEATURES` | `422 Unprocessable` | Dataset contains zero valid features. |
| `SECURITY_VIOLATION` | `400 Bad Request` | Directory traversal (zip-slip) detected inside archive. |
| `ARCHIVE_TOO_LARGE` | `413 Content Too Large` | Total uncompressed archive size exceeds safety limit (`50 MB`). |
| `FILE_NOT_FOUND` | `404 Not Found` | Requested file ID does not exist in the database. |
| `FILE_NOT_READY` | `409 Conflict` | Measurements requested before processing completes (`PROCESSING` or `FAILED`). |
| `INTERNAL_SERVER_ERROR` | `500 Server Error` | Unexpected server exception; logs traceback internally and returns safe message. |

---

## 5. System Architecture & Flows

### Project Directory Layout
```text
geo-measurement-api/
  app/
    main.py           # FastAPI app entrypoint, lifespan events, global error handlers
    config.py         # Pydantic Settings (env vars, upload limits, database URL)
    db.py             # SQLAlchemy 2.x engine, SQLite PRAGMA hooks, get_db dependency
    api/
      files.py        # Thin REST route handlers (/api/files)
    schemas/
      common.py       # Standard ErrorResponse schema
      files.py        # FileUploadResponse & FileDetailResponse
      measurements.py # MeasurementDetail, FeatureMeasurementResponse, PaginatedResponse
    models/
      uploaded_file.py  # UploadedFile ORM table and FileStatus Enum
      feature_record.py # FeatureRecord ORM table with JSON feature_data column
    services/
      storage.py           # Secure file streaming, path traversal validation, UUID filenames
      file_reader.py       # Archive security checks, Shapefile verification, KML merger
      feature_extractor.py # JSON sanitization (NaN/NumPy/Pandas), 3D coordinate handling
      crs.py               # UTM zone selection, polar fallback EPSG:6933, transformer caching
      measurement.py       # Planar area/length calculations, topology repair via make_valid
      processing.py        # End-to-end orchestration pipeline and failure rollback
  tests/
    conftest.py               # Shared pytest fixtures (in-memory SQLite StaticPool)
    test_crs.py               # UTM math, zone boundaries, hemisphere, polar fallback
    test_measurement.py       # Area & length calculations, accuracy within 1%, 3D geometries
    test_storage.py           # Safe filenames, size enforcement, traversal validation
    test_file_reader.py       # Shapefile components, corrupt archives, bomb & slip attacks
    test_feature_extractor.py # JSON serialization, NaN handling, 3D GeoJSON extraction
    test_processing.py        # Pipeline execution, database persistence, failure rollback
    test_api.py               # FastAPI TestClient endpoints, 404/409, error envelopes
    test_full_suite.py        # Comprehensive 40-test end-to-end integration & accuracy suite
  sample_data/
    sample.kml                # Tiruchengode sample dataset (Polygon, LineString, Point)
    sample_shapefile.zip      # Tiruchengode sample Shapefile (Polygon)
  requirements.txt
  README.md
  .gitignore
```

### Architecture Diagram
```text
                       HTTP POST /api/files/
                                 │
                                 ▼
                     ┌───────────────────────┐
                     │   FastAPI Transport   │ (Thin Route Handler)
                     └───────────┬───────────┘
                                 │
                                 ▼
                     ┌───────────────────────┐
                     │  app/services/storage │ -> Stream upload, enforce size limit,
                     └───────────┬───────────┘    generate UUID filename to disk
                                 │
                                 ▼
                     ┌───────────────────────┐
                     │  UploadedFile Record  │ -> Insert status=PROCESSING into DB
                     └───────────┬───────────┘
                                 │
                                 ▼
                     ┌───────────────────────┐
                     │ app/services/reader   │ -> Check zip-slip, decompression bomb,
                     └───────────┬───────────┘    read layers (GeoPandas / Pyogrio)
                                 │
                                 ▼
                     ┌───────────────────────┐
                     │ app/services/extractor│ -> Sanitize NaN, NumPy, Pandas types;
                     └───────────┬───────────┘    preserve 3D coords as valid GeoJSON
                                 │
                                 ▼
                     ┌───────────────────────┐
                     │ app/services/crs &    │ -> Select local UTM zone (or EPSG:6933),
                     │      measurement      │    reproject, compute m² / ha / m / km
                     └───────────┬───────────┘
                                 │
                                 ▼
                     ┌───────────────────────┐
                     │  Database Persistence │ -> Save FeatureRecords with JSON data,
                     └───────────┬───────────┘    mark UploadedFile as COMPLETED
                                 │
                                 ▼
                      HTTP 201 Created Response

========================================================================================

                 HTTP GET /api/files/{id}/measurements/
                                 │
                                 ▼
                     ┌───────────────────────┐
                     │   FastAPI Transport   │ (Thin Route Handler)
                     └───────────┬───────────┘
                                 │
                                 ▼
                     ┌───────────────────────┐
                     │  SQLAlchemy Database  │ -> SELECT from feature_records WHERE
                     └───────────┬───────────┘    uploaded_file_id = :id (O(log N))
                                 │
                      (NO DISK FILE RE-READING!)
                                 │
                                 ▼
                      HTTP 200 OK Paginated JSON
```

---

## 6. Geospatial & CRS Strategy

### 1. Why Geographic Coordinates (`EPSG:4326`) Cannot Be Measured Directly
Geographic coordinates express positions in spherical angles $(\text{degrees})$. Because meridians converge at the poles, a degree of longitude represents different distances depending on latitude:
$$\Delta x \approx 111.32\text{ km} \times \cos(\text{latitude})$$
At the equator ($0^\circ$), $1^\circ \approx 111.32\text{ km}$; at $60^\circ\text{N}$, $1^\circ \approx 55.66\text{ km}$; at the poles, $1^\circ = 0\text{ m}$. Calculating Euclidean planar area $(\Delta x \times \Delta y)$ on degree coordinates produces arbitrary numbers with no direct real-world meaning. Therefore, geographic data must be transformed into a metric planar coordinate reference system prior to measurement.

### 2. Universal Transverse Mercator (UTM) Selection
For non-polar geographic features, we compute the optimal local UTM zone from the feature's representative longitude:
$$\text{zone} = \left\lfloor \frac{\text{longitude} + 180^\circ}{6^\circ} \right\rfloor + 1 \quad (\text{clamped to } [1, 60])$$
$$\text{EPSG Code} = (32600 \text{ if latitude} \ge 0 \text{ else } 32700) + \text{zone}$$

#### Tiruchengode Case Study:
For coordinates $(78.0^\circ\text{E}, 11.0^\circ\text{N})$:
$$\text{zone} = \left\lfloor \frac{78.0 + 180}{6} \right\rfloor + 1 = \lfloor 43.0 \rfloor + 1 = 44$$
$$\text{latitude} = 11.0 \ge 0 \implies 32600 + 44 = \mathbf{32644} \quad (\text{WGS 84 / UTM Zone 44N})$$
Projecting the Tiruchengode sample polygon into `EPSG:32644` yields **$12,111.29\text{ m}^2$** ($1.2111\text{ ha}$) with less than $0.001\%$ error against standard reference baselines.

### 3. UTM Per Feature Strategy
Rather than computing a single UTM zone for an entire file (which causes severe distortion if features span across regional boundaries), our engine determines the optimal projection per feature based on its centroid.

### 4. `pyproj.Transformer` & Memoization
Instantiating PROJ coordinate operation pipelines involves disk I/O and C-level thread locking. We memoize transformers using Python's `@functools.lru_cache(maxsize=256)` keyed by canonical `(source_crs, target_crs)` pairs, ensuring zero repetitive instantiation overhead.

### 5. Enforcing `always_xy=True`
Under PROJ 6+ / EPSG standards, `EPSG:4326` defines axis order as `lat, lon` (Northing, Easting). Setting `always_xy=True` on every transformer guarantees consistent GIS `(x, y)` / `(lon, lat)` ordering regardless of underlying authority specifications.

### 6. Projected Source CRS & Non-Meter Linear Units
If an uploaded dataset is already in a projected planar CRS:
- **In Meters (e.g., Web Mercator `EPSG:3857`):** Measured directly without unnecessary reprojection.
- **In Non-Meter Units (e.g., California State Plane `EPSG:2227` in US Survey Feet):** We inspect `crs.axis_info[0].unit_conversion_factor` and scale measurements to standard SI metric units ($\text{meters} = \text{feet} \times 0.3048006$, $\text{m}^2 = \text{feet}^2 \times 0.3048006^2$) while issuing an explanatory warning.

### 7. Polar Fallback (`EPSG:6933`)
Transverse Mercator projections are mathematically undefined at the poles (distortions approach infinity beyond $84^\circ\text{N}$ and $80^\circ\text{S}$). When $|\text{latitude}| > 84^\circ$, the engine falls back to **EPSG:6933** (*WGS 84 / NSIDC EASE-Grid 2.0 Global*), an equal-area cylindrical projection scaled in meters, and appends a warning.

### 8. Zone-Boundary Warnings
UTM projections maintain conformal accuracy within narrow $6^\circ$ longitudinal corridors. When a feature's bounding box spans across zone boundaries (`get_utm_zone(minx) != get_utm_zone(maxx)`), projecting all vertices onto a single zone causes slight scale distortion near edges. We detect this condition and append a warning to the feature payload.

---

## 7. Design Decisions & Trade-Off Alternatives

### 1. UTM Per Feature vs. One UTM Per File
- **Decision:** UTM determined per feature based on geometry centroid.
- **Why:** In multi-feature datasets covering broad regions, a single file-level UTM zone causes severe scale degradation for distant features. Centroid-based selection ensures local accuracy.
- **Alternative:** Single file UTM simplifies bulk processing but causes geometric distortion ($> 5\%$) for regional survey datasets.

### 2. UTM / Equal-Area Projections vs. Geodesic `pyproj.Geod`
- **Decision:** Transform geometries to conformal/equal-area planar projections and measure via Shapely.
- **Why:** Allows native GeoJSON coordinate export, standard planar polygon geometry operations, and compatibility with standard GIS toolchains.
- **Alternative:** Pure ellipsoidal geodesic calculations (`Geod.geometry_area_perimeter`) compute surface metrics accurately without reprojection, but do not provide a visual planar geometry for client-side rendering.

### 3. Synchronous Ingestion vs. Background Worker Queues
- **Decision:** Synchronous execution inside `app/services/processing.py` with an isolated worker boundary.
- **Why:** Keeps deployment lightweight for the assignment (zero external Redis/RabbitMQ infrastructure needed).
- **Alternative:** For multi-gigabyte production workloads, passing `file_id` to an async queue worker (Celery/ARQ) prevents HTTP request timeouts. Our architecture isolates `process_uploaded_file(file_id, db)` so migrating to a queue requires zero code changes.

### 4. SQLite vs. PostgreSQL / PostGIS
- **Decision:** SQLite with JSON column storage and enforced foreign keys (`PRAGMA foreign_keys=ON`).
- **Why:** Self-contained, zero-configuration local execution with zero Docker dependencies.
- **Alternative:** PostGIS provides native spatial indexes (`GIST`) and SQL-level spatial queries (`ST_Area`). For high-throughput concurrent writes and complex spatial joins, PostgreSQL is preferred.

### 5. Persisting Feature JSON vs. Recomputing on Demand
- **Decision:** Persist precomputed metric measurements and GeoJSON in `feature_records.feature_data`.
- **Why:** Eliminates all disk reads, zip decompressions, and reprojections during measurement queries ($O(1)$ database lookup).
- **Alternative:** Storing only raw geometries and recomputing on demand reduces database storage by $\approx 40\%$, but increases query latency by $100\times$ to $1000\times$.

---

## 8. Security & Defensive Hardening

1. **Defending Against Zip-Slip (Arbitrary File Overwrites):**
   Malicious archives can embed relative traversal paths (e.g., `../../../../Windows/System32/evil.dll`). Before extracting any zip member, we verify:
   ```python
   (extraction_root / member.filename).resolve().relative_to(extraction_root.resolve())
   ```
   If a member attempts to escape the root, extraction aborts with `ZipSlipError` (`SECURITY_VIOLATION`).

2. **Decompression Bomb Protection:**
   Tiny archives can decompress into hundreds of gigabytes, exhausting server disk space (Denial of Service). Before extraction, we calculate:
   ```python
   total_uncompressed = sum(info.file_size for info in zip_ref.infolist())
   ```
   If total uncompressed size exceeds $50\text{ MB}$, the archive is rejected with `DecompressionBombError` (`ARCHIVE_TOO_LARGE`).

3. **Content Magic Bytes vs. Extension Spoofing:**
   We never trust file extensions alone:
   - For `.zip`: Validates initial 4 bytes (`b"PK\x03\x04"`) and tests structure via `zipfile.is_zipfile`.
   - For `.kml`: Inspects the initial 2 KB for XML/KML markers (`<?xml` or `<kml`).

4. **Never Trusting Client Filenames:**
   User-supplied filenames can contain null bytes or illegal characters. Disk storage names are generated as collision-proof UUID4 hashes with clean sanitized extensions (e.g., `4a3b8...c1.kml`).

5. **Streaming Quota Enforcement:**
   Uploads are written to disk in 1 MB chunks while tracking cumulative bytes. If the 20 MB ceiling is exceeded, writing halts, the partial file is unlinked, and `HTTP 413` is returned.

6. **Server-Side Traceback Protection:**
   Internal exceptions log full tracebacks to server logs, but the client receives a sanitized error payload without revealing source code paths or database schemas.

---

## 9. Limitations & Assumptions

1. **Synchronous Execution Timeout:** Because processing runs synchronously within the HTTP upload request, files with hundreds of thousands of features may hit HTTP proxy timeouts (e.g., Nginx 60s timeout).
2. **SQLite Write Concurrency:** SQLite uses database-level write locking. High concurrent write throughput will require migrating to PostgreSQL.
3. **Planar 2D Measurements:** Geometries with 3D elevation coordinates ($Z$) have their elevation dropped to compute planar XY ground area and length. Surface topography slope area (drape area) is not computed.
4. **Memory Footprint for Massive XML:** Extremely large KML files ($> 100\text{ MB}$) parsed via GDAL may consume significant RAM during DOM tree parsing.

---

## 10. Engineering Learnings Draft

> ### Draft — rewrite these in my own words
> - **Binary Wheel Ecosystem on Windows:** Modern geospatial Python on Windows 11 no longer requires manual OSGeo4W or Conda environments. In Python 3.11+, PyPI packages for `shapely` 2.x, `pyproj`, and `pyogrio` distribute pre-compiled binary wheels with bundled C-extensions (GEOS, PROJ, and GDAL), making standard `pip install` reproducible.
> - **Strict Coordinate Ordering:** PROJ 6+ officially adopted EPSG authority axis ordering (`lat, lon`). Forgetting `always_xy=True` results in inverted coordinates and completely broken UTM projections.
> - **Defensive Archive Unpacking:** Validating zip contents *prior* to extraction is essential. Checking magic bytes, validating path containment (`is_relative_to`), and summing uncompressed file sizes prevents major security exploits.
> - **Thin Transport Adapters:** Keeping FastAPI route handlers completely free of GIS logic makes testing straightforward and guarantees future worker migration requires zero changes to route signatures.
> - **Database JSON Caching:** Precomputing and storing GeoJSON geometries and measurement metrics during upload turns heavy GIS calculations into simple database selects, resulting in fast query response times.

---

## 11. Future Scope

1. **Asynchronous Task Queue:** Integrate **Celery** or **ARQ** with Redis to handle long-running, multi-gigabyte uploads asynchronously with progress webhooks.
2. **PostgreSQL / PostGIS Support:** Provide native PostGIS spatial indexing (`GIST`) for bounding-box and spatial intersection queries.
3. **Format Expansion:** Add drivers for standalone **GeoJSON** (`.geojson`) and **OGC GeoPackage** (`.gpkg`).
4. **Containerization:** Provide a production multi-stage `Dockerfile` and `docker-compose.yml`.
5. **Authentication & Multi-Tenancy:** Secure endpoints using OAuth2 JWT bearer tokens and tenant isolation.
6. **Streaming Ingestion:** Implement chunked generator parsing for massive datasets using `pyogrio.read_dataframe(..., max_features=1000)`.
7. **Continuous Integration:** Configure automated GitHub Actions workflows running the 98-test pytest suite across Ubuntu, macOS, and Windows runners.
