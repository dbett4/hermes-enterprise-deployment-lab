from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    service_name: str = "enterprise-api"
    # Both tokens are required, with no default: a missing variable stops the
    # service at startup instead of silently accepting the public fixture values.
    # Read scope: incident/runbook GETs.
    api_token: str = Field(min_length=1, validation_alias="ENTERPRISE_API_TOKEN")
    # Write scope: incident action mutations. Deliberately a DIFFERENT value so a
    # read-only token provably cannot mutate.
    api_write_token: str = Field(min_length=1, validation_alias="ENTERPRISE_API_WRITE_TOKEN")
    inject_timeout_seconds: float = Field(default=2.0, validation_alias="INJECT_TIMEOUT_SECONDS")
    # Optional JSON file path. When unset the action store stays in memory.
    action_store_path: str | None = Field(default=None, validation_alias="ACTION_STORE_PATH")


settings = Settings()
