"""Application settings, overridable through ``GEO_*`` environment variables."""

from pydantic_settings import BaseSettings, SettingsConfigDict

_MB = 1024 * 1024


class Settings(BaseSettings):
    """Runtime configuration.

    Every field can be set with an environment variable of the same name,
    upper-cased and prefixed with ``GEO_`` (e.g. ``GEO_DATABASE_URL``).
    """

    model_config = SettingsConfigDict(env_prefix="GEO_", env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./geo.db"

    max_upload_bytes: int = 50 * _MB
    max_uncompressed_bytes: int = 500 * _MB
    max_zip_members: int = 200
    max_features: int = 100_000

    insert_chunk_size: int = 1_000
