from pydantic_settings import BaseSettings
from pydantic import field_validator


class Settings(BaseSettings):
    # Required
    dataverse_url: str  # e.g. https://yourorg.crm.dynamics.com

    # Required: Azure AD Application (client) ID from your Entra ID app registration
    client_id: str

    # Optional with defaults
    tenant_id: str = "common"
    auth_redirect_port: int = 5577  # Fixed port for interactive auth redirect server

    # Internal constant — not configurable via env
    token_cache_path: str = "/data/token_cache.json"

    @field_validator("dataverse_url")
    @classmethod
    def strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/")

    @property
    def authority(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant_id}"

    @property
    def scopes(self) -> list[str]:
        return [f"{self.dataverse_url}/.default"]

    @property
    def api_base(self) -> str:
        return f"{self.dataverse_url}/api/data/v9.2"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
