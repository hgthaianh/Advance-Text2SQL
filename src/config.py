from functools import lru_cache

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Vietnamese Text2SQL"
    query_max_limit: int = Field(default=1000, ge=1, le=1000)
    query_timeout_ms: int = Field(default=5000, ge=100, le=60000)
    database_connect_timeout_s: int = Field(default=3, ge=1, le=30)
    postgres_host: str = "localhost"
    postgres_port: int = Field(default=5432, ge=1, le=65535)
    postgres_db: str = "text2sql"
    postgres_user: str = "text2sql"
    postgres_password: SecretStr = SecretStr("text2sql")
    auth_secret_key: SecretStr
    auth_algorithm: str = "HS256"
    auth_access_token_expire_minutes: int = Field(default=30, ge=1)
    auth_refresh_token_expire_minutes: int = Field(default=60 * 24 * 7, ge=1)
    cors_allowed_origins: str = ""

    @field_validator("auth_secret_key")
    @classmethod
    def validate_auth_secret(cls, value: SecretStr) -> SecretStr:
        secret = value.get_secret_value()
        if len(secret) < 32 or secret.startswith("replace-with-"):
            raise ValueError("AUTH_SECRET_KEY must be a unique secret of at least 32 characters")
        return value

    @property
    def database_url(self) -> URL:
        return URL.create(
            "postgresql+psycopg",
            username=self.postgres_user,
            password=self.postgres_password.get_secret_value(),
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
            query={"connect_timeout": str(self.database_connect_timeout_s)},
        )

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_allowed_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
