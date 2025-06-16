"""
Loads ALL secrets from env-vars.
Import once anywhere:  from config.credentials import settings
"""
from functools import lru_cache
from pydantic_settings import BaseSettings          # ✔️ Pydantic-2 helper
from pydantic import SecretStr


class _Settings(BaseSettings):
    # -------- OpenAI --------
    OPENAI_API_KEY: SecretStr

    # -------- Microsoft Graph / Teams --------
    MS_CLIENT_ID:      SecretStr
    MS_CLIENT_SECRET:  SecretStr
    MS_TENANT_ID:      str

    # ---------- (optional) add more secrets later ----------

    class Config:
        env_file = ".env"        # used only for local dev
        case_sensitive = True


@lru_cache
def get_settings() -> _Settings:
    """Singleton accessor for settings ↔ avoids re-parsing envs."""
    return _Settings()


# Shortcut so you can:  from config.credentials import settings
settings = get_settings()
