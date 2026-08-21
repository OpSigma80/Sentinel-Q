import os
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from dotenv import load_dotenv
from typing import Optional

# Load variables from the .env file into OS environment variables.
load_dotenv()

class Settings(BaseSettings):
    """
    Centralized Sentinel-Q configuration.
    Uses Pydantic settings for typed environment loading and validation.
    """
    
    # --- APP CORE ---
    APP_NAME: str = "Sentinel-Q"
    APP_VERSION: str = "0.1.0"
    
    # --- SECURITY ---
    # Key used to validate requests from external clients.
    API_KEY: str = os.getenv("API_KEY", "SENTINEL_PRO_SECRET_2026_V1")

    # --- JWT AUTH (Week 2) ---
    # Admin credentials — set via env vars in production, never commit real values
    ADMIN_USERNAME: str = os.getenv("ADMIN_USERNAME", "admin")
    ADMIN_PASSWORD: str = os.getenv("ADMIN_PASSWORD", "sentinel_admin_2026")
    JWT_SECRET_KEY: str = os.getenv("JWT_SECRET_KEY", "CHANGE_ME_IN_PRODUCTION_USE_STRONG_SECRET")
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = int(os.getenv("JWT_EXPIRE_MINUTES", "60"))
    
    # --- INFRASTRUCTURE (PostgreSQL) ---
    # Format: postgresql://user:password@hostname:port/dbname
    DATABASE_URL: str = os.getenv("DATABASE_URL")

    # --- TELEGRAM ---
    BOT_TOKEN: Optional[str] = os.getenv("BOT_TOKEN")
    CHAT_ID: Optional[str] = os.getenv("CHAT_ID")
    ALERT_FAILURE_THRESHOLD: int = Field(default=2, ge=1, le=10)
    ALERT_RECOVERY_THRESHOLD: int = Field(default=2, ge=1, le=10)
    ALERT_COOLDOWN_SECONDS: int = Field(default=300, ge=0, le=86400)
    ALERT_STABILITY_WINDOW_SECONDS: int = Field(default=300, ge=0, le=3600)
    ALERT_CRITICAL_THROTTLE_SECONDS: int = Field(default=60, ge=5, le=3600)
    ALERT_WARNING_THROTTLE_SECONDS: int = Field(default=300, ge=5, le=7200)
    ALERT_MEDIUM_THROTTLE_SECONDS: int = Field(default=600, ge=5, le=7200)
    TELEGRAM_THROTTLE_SECONDS: int = Field(default=60, ge=5, le=3600, description="Minimum seconds between alerts of the same type")
    TELEGRAM_CACHE_SECONDS: int = Field(default=30, ge=5, le=300, description="Metrics/history cache TTL")

    # --- LOGGING ---
    LOG_LEVEL: str = "INFO"

    # --- SAFE DERIVED PROPERTIES ---
    
    @property
    def clean_bot_token(self) -> str:
        """
        Strip surrounding whitespace and hidden line endings.
        This prevents malformed Telegram API URLs.
        """
        if self.BOT_TOKEN:
            return self.BOT_TOKEN.strip()
        return ""

    @property
    def clean_chat_id(self) -> str:
        """Strip accidental whitespace from the Telegram chat id."""
        if self.CHAT_ID:
            return self.CHAT_ID.strip()
        return ""

    model_config = SettingsConfigDict(case_sensitive=False)

settings = Settings()