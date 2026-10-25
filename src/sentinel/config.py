from typing import Optional

from dotenv import load_dotenv
from pydantic import Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Load variables from the .env file into OS environment variables.
# Variables already present in the environment take precedence over .env.
load_dotenv()

# Placeholder values that must never be accepted as real credentials.
_INSECURE_VALUES = {
    "admin",
    "sentinel_admin_2026",
    "change_me_in_production_use_strong_secret",
}


class Settings(BaseSettings):
    """
    Centralized Sentinel-Q configuration.
    Uses Pydantic settings for typed environment loading and validation.
    Each field is read from the environment variable with the same name.
    """

    # --- APP CORE ---
    APP_NAME: str = "Sentinel-Q"
    APP_VERSION: str = "0.1.0"

    # --- SECURITY ---
    # Legacy key from the pre-JWT API. No default: set it only if it is still used.
    API_KEY: Optional[str] = None

    # --- JWT AUTH ---
    # Required, with no default: the app refuses to start without real values.
    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD: str = Field(..., min_length=12)
    JWT_SECRET_KEY: str = Field(..., min_length=32)
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = Field(default=60, ge=1)

    # --- INFRASTRUCTURE (PostgreSQL) ---
    # Format: postgresql://user:password@hostname:port/dbname
    DATABASE_URL: str

    # --- TELEGRAM ---
    BOT_TOKEN: Optional[str] = None
    CHAT_ID: Optional[str] = None
    ALERT_FAILURE_THRESHOLD: int = Field(default=2, ge=1, le=10)
    ALERT_RECOVERY_THRESHOLD: int = Field(default=2, ge=1, le=10)
    ALERT_COOLDOWN_SECONDS: int = Field(default=300, ge=0, le=86400)
    ALERT_STABILITY_WINDOW_SECONDS: int = Field(default=300, ge=0, le=3600)
    ALERT_CRITICAL_THROTTLE_SECONDS: int = Field(default=60, ge=5, le=3600)
    ALERT_WARNING_THROTTLE_SECONDS: int = Field(default=300, ge=5, le=7200)
    ALERT_MEDIUM_THROTTLE_SECONDS: int = Field(default=600, ge=5, le=7200)
    TELEGRAM_THROTTLE_SECONDS: int = Field(
        default=60,
        ge=5,
        le=3600,
        description="Minimum seconds between alerts of the same type",
    )
    TELEGRAM_CACHE_SECONDS: int = Field(
        default=30, ge=5, le=300, description="Metrics/history cache TTL"
    )

    # --- LOGGING ---
    LOG_LEVEL: str = "INFO"

    # --- VALIDATION ---

    @field_validator("ADMIN_PASSWORD", "JWT_SECRET_KEY")
    @classmethod
    def _reject_placeholder_values(cls, v: str) -> str:
        """Refuse well-known placeholder values (the message never echoes the value)."""
        if v.strip().lower() in _INSECURE_VALUES:
            raise ValueError("placeholder value not allowed; set a strong random value")
        return v

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


def _load_settings() -> Settings:
    """Build Settings, turning validation errors into a clear message without secrets."""
    try:
        return Settings()
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or 'settings'}: {err['msg']}"
            for err in exc.errors()
        )
        raise RuntimeError(
            f"Invalid or missing configuration -> {problems}. "
            "Set the variables in the environment or in .env (see .env.example)."
        ) from None


settings = _load_settings()
