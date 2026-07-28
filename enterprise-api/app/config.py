from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    service_name: str = "enterprise-api"
    api_token: str = Field(default="lab-read-token", validation_alias="ENTERPRISE_API_TOKEN")
    inject_timeout_seconds: float = Field(default=2.0, validation_alias="INJECT_TIMEOUT_SECONDS")


settings = Settings()
