from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration loaded from environment variables or .env file."""

    app_name: str = "Geo Measurement API"
    app_version: str = "0.1.0"
    debug: bool = False

    # Database configuration (defaults to local SQLite file)
    database_url: str = "sqlite:///./geo_measurement.db"

    # File storage configuration
    upload_dir: str = "uploads"
    max_upload_size_bytes: int = 20 * 1024 * 1024  # 20 MB default

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def max_upload_size_mb(self) -> float:
        """Helper to return maximum upload size in megabytes."""
        return self.max_upload_size_bytes / (1024 * 1024)


settings = Settings()
