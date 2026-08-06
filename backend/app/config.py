from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    app_name: str = "Social Intelligence API"
    database_url: str = f"sqlite:///{BASE_DIR / 'social_intelligence.db'}"
    cors_origins: str = "http://localhost:3000"
    fixture_dir: Path = BASE_DIR / "data" / "fixtures"
    lana_ssh_host: Optional[str] = None
    lana_postgres_container: str = "lana-postgres"
    # Prefer TCP to Lana Postgres when co-located (shared docker network).
    # Example: postgresql://lana:lana_dev_password@lana-postgres:5432/lana
    lana_database_url: Optional[str] = None

    model_config = SettingsConfigDict(env_file=".env", env_prefix="SI_", extra="ignore")


settings = Settings()
