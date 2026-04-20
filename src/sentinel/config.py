import os
from pydantic_settings import BaseSettings
from dotenv import load_dotenv
from typing import Optional

# Carga las variables del archivo .env a las variables de entorno del Sistema Operativo (Debian)
load_dotenv()

class Settings(BaseSettings):
    """
    Configuración Centralizada de Sentinel-Q.
    Utiliza Pydantic para validación de tipos y carga automática desde el entorno.
    """
    
    # --- APP CORE ---
    APP_NAME: str = "Sentinel-Q"
    APP_VERSION: str = "0.1.0"
    
    # --- SECURITY ---
    # Llave para validar peticiones desde PowerShell/Clientes externos
    API_KEY: str = os.getenv("API_KEY", "SENTINEL_PRO_SECRET_2026_V1")
    
    # --- INFRASTRUCTURE (PostgreSQL) ---
    # Formato: postgresql://user:password@hostname:port/dbname
    DATABASE_URL: str = os.getenv("DATABASE_URL")

    # --- TELEGRAM ---
    # Variables crudas del .env
    BOT_TOKEN: Optional[str] = os.getenv("BOT_TOKEN")
    CHAT_ID: Optional[str] = os.getenv("CHAT_ID")

    # --- LOGGING ---
    LOG_LEVEL: str = "INFO"

    # --- PROPIEDADES CALCULADAS (BLINDADAS) ---
    
    @property
    def clean_bot_token(self) -> str:
        """
        Limpia el token de espacios, saltos de línea (\n) o retornos de carro (\r).
        Esto previene el Error 404 si el .env fue editado en Windows.
        """
        if self.BOT_TOKEN:
            # .strip() elimina caracteres invisibles en ambos extremos
            return self.BOT_TOKEN.strip()
        return ""

    @property
    def clean_chat_id(self) -> str:
        """Asegura que el Chat ID no tenga espacios accidentales."""
        if self.CHAT_ID:
            return self.CHAT_ID.strip()
        return ""

    class Config:
        # Permite que 'bot_token' en el .env mapee a 'BOT_TOKEN' en la clase
        case_sensitive = False

# Instancia global para ser importada en el resto del sistema
settings = Settings()