from pydantic_settings import BaseSettings
from pydantic import field_validator
from typing import Optional


class Settings(BaseSettings):
    # Required
    dataverse_url: str  # e.g. https://yourorg.crm.dynamics.com

    # Required: Azure AD Application (client) ID from your Entra ID app registration
    client_id: str

    # Required: Azure AD Application (client) secret from your Entra ID app registration
    client_secret: str

    # Optional with defaults
    tenant_id: str = "common"

    mcp_base_url: str = "http://localhost:8000"
    jwt_signing_key: Optional[str] = None  # Stable key for signing JWTs
    storage_encryption_key: Optional[str] = None  # Fernet key for encrypting OAuth tokens at rest

    # Destructive-action confirmation settings
    confirm_token_ttl_seconds: int = 120  # 2 minutes
    bulk_delete_cap: int = 50  # max record IDs per future bulk proposal

    # Redis — required for shared cache and proposal storage across instances
    redis_url: str  # e.g. redis://redis:6379/0 or rediss://:<key>@<host>:6380/0

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
    def mcp_required_scopes(self) -> list[str]:
        return ["mcp-access"]

    @property
    def api_base(self) -> str:
        return f"{self.dataverse_url}/api/data/v9.2"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
