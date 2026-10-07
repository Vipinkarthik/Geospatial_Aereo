# Geo Measurement API

A production-ready FastAPI backend for parsing geospatial data formats (KML, Shapefile archives, GeoJSON), persisting file metadata and spatial features, and computing geometric properties and measurements.

## Project Structure

```text
geo-measurement-api/
  app/
    __init__.py
    main.py           # FastAPI application entrypoint and lifespan management
    config.py         # Pydantic Settings (env vars, upload limits, db url)
    db.py             # SQLAlchemy 2.x engine, session dependency, and table init
    api/              # API route definitions
      __init__.py
      files.py        # File upload and measurement endpoints
    schemas/          # Pydantic request/response validation schemas
      __init__.py
    models/           # SQLAlchemy ORM declarative models
      __init__.py
      uploaded_file.py
      feature_record.py
    services/         # Geospatial parsing and measurement calculation logic
      __init__.py
  tests/              # Pytest test suite
  sample_data/        # Sample geospatial test datasets
    sample.kml
    sample_shapefile.zip
  requirements.txt    # Pinned production and test dependencies
  README.md
  .gitignore
```

## Setup & Running

### 1. Activate Virtual Environment (PowerShell)
```powershell
.\venv\Scripts\Activate.ps1
```

### 2. Verify Database & Application
```powershell
python -c "from app.main import app; from app.db import init_db; init_db(); print('App and DB initialized successfully')"
```

### 3. Run Development Server
```powershell
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```
Interactive API docs are available at `http://127.0.0.1:8000/docs`.
