from loguru import logger
from datetime import datetime
import httpx
from sentinel.config import settings

class AlertNotifier:
    def __init__(self):
        # El Chat ID también lo limpiamos por seguridad
        self.chat_id = settings.clean_chat_id

    async def _send_telegram_msg(self, message: str):
        """Método privado para centralizar el envío de peticiones a Telegram."""
        token = settings.clean_bot_token
        if not token:
            logger.error("BOT_TOKEN no configurado en el sistema.")
            return

        api_url = f"https://api.telegram.org/bot{token}/sendMessage"
        
        async with httpx.AsyncClient() as client:
            try:
                payload = {
                    "chat_id": self.chat_id, 
                    "text": message, 
                    "parse_mode": "Markdown"
                }
                response = await client.post(api_url, json=payload, timeout=10.0)
                
                if response.status_code == 200:
                    return True
                else:
                    logger.error(f"❌ Error Telegram ({response.status_code}): {response.text}")
                    return False
            except Exception as e:
                logger.error(f"Fallo de conexión con Telegram: {e}")
                return False

    async def notify_failure(self, service_name: str, url: str, status: str):
        """Envía alerta roja cuando un servicio cae."""
        msg = (
            f"🚨 *SENTINEL-Q ALERT*\n\n"
            f"❌ *Service:* `{service_name}`\n"
            f"🌐 *URL:* {url}\n"
            f"⚠️ *Status:* {status}\n"
            f"⏰ *Time:* {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        await self._send_telegram_msg(msg)

    async def notify_success(self, service_name: str, url: str):
        """Envía alerta verde cuando un servicio se recupera."""
        msg = (
            f"✅ *SENTINEL-Q RECOVERED*\n\n"
            f"🟢 *Service:* `{service_name}`\n"
            f"🌐 *URL:* {url}\n"
            f"👌 *Status:* Online / Healthy\n"
            f"⏰ *Time:* {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        await self._send_telegram_msg(msg)