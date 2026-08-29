import os
from functools import lru_cache
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

class Settings(BaseSettings):
    alpaca_api_key: Optional[str] = Field(default=None, alias="ALPACA_API_KEY")
    alpaca_api_secret: Optional[str] = Field(default=None, alias="ALPACA_API_SECRET")
    alpaca_api_url: str = Field(default="https://paper-api.alpaca.markets", alias="ALPACA_API_URL")

    model_config = SettingsConfigDict(
        env_file=(".env", "backend/.env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    def get_secret(self) -> Optional[str]:
        return self.alpaca_api_secret or os.getenv("ALPACA_API_SECRET")

    def get_key(self) -> Optional[str]:
        return self.alpaca_api_key or os.getenv("ALPACA_API_KEY")

@lru_cache
def get_settings() -> Settings:
    try:
        from dotenv import load_dotenv
        for p in ["backend/.env", ".env"]:
            if os.path.exists(p):
                load_dotenv(p, override=False)
    except Exception:
        pass
    return Settings()
