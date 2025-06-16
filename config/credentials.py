"""
Loads ALL secrets from env-vars.
Import once anywhere:  from config.credentials import settings
"""
from functools import lru_cache
from pydantic import BaseSettings, SecretStr


class _Settings(BaseSettings):
    # OpenAI
    OPENAI_API_KEY: SecretStr

    # Microsoft Graph (Teams)
    MS_CLIENT_ID:      SecretStr
    MS_CLIENT_SECRET:  SecretStr
    MS_TENANT_ID:      str

    class Config:
        env_file = ".env"      # only used locally
        case_sensitive = True


@lru_cache
def get_settings() -> _Settings:
    return _Settings()


# handy shortcut so you can   from config.credentials import settings
settings = get_settings()
