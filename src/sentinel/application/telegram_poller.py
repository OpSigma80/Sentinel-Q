"""
Telegram bot command handler using CPU-friendly long polling.

Handles operator commands sent to the bot:
  /status  — services health overview
  /metrics — global performance metrics (24h)
  /alerts  — recent alert history (24h)
  /help    — available commands list

Design decisions:
- Uses httpx directly (already a dependency). No python-telegram-bot.
- Long polling with Telegram timeout=30 ensures the loop is idle ~30s between cycles.
- Only processes messages from the authorized chat_id configured in settings.
- DB sessions opened per-command and closed immediately (no leaks).
"""

import asyncio
from typing import Optional

import httpx
from loguru import logger

from sentinel.application.notifier import AlertNotifier
from sentinel.config import settings
from sentinel.infrastructure.database import SessionLocal
from sentinel.services.telegram_query_service import TelegramQueryService

_HELP_TEXT = (
    "*SENTINEL-Q Bot - Available commands*\n\n"
    "/status - Current status for all services\n"
    "/metrics - Global performance metrics (24h)\n"
    "/alerts - Recent alert history (24h)\n"
    "/help - Show this message"
)


class TelegramBotPoller:
    """
    CPU-friendly Telegram bot command handler using long polling.

    Runs as a background asyncio task. A single persistent httpx connection
    handles the getUpdates loop, keeping CPU and network overhead minimal.
    Unauthorized chat IDs are silently ignored and logged as warnings.
    """

    def __init__(self) -> None:
        self._offset: int = 0
        self._notifier: AlertNotifier = AlertNotifier()
        self._running: bool = False
        self._retry_delay: float = 1.0
        self._retry_delay_max: float = 60.0
        self._last_fetch_outcome: str = "idle"
        self._last_fetch_successful: bool = False
        self._last_logged_error: Optional[tuple[str, float]] = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_api_url(self, method: str) -> str:
        token = settings.clean_bot_token
        return f"https://api.telegram.org/bot{token}/{method}"

    def _authorized_chat_id(self) -> Optional[str]:
        cid = settings.clean_chat_id
        return cid if cid else None

    def _is_authorized(self, chat_id: str) -> bool:
        authorized = self._authorized_chat_id()
        if not authorized:
            return False
        return str(chat_id) == str(authorized)

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------

    async def _fetch_updates(self, client: httpx.AsyncClient) -> list[dict]:
        """
        Fetch pending updates with long polling.
        Returns an empty list on any error so the loop continues safely.
        """
        self._last_fetch_successful = False
        self._last_fetch_outcome = "error"
        try:
            response = await client.get(
                self._build_api_url("getUpdates"),
                params={
                    "offset": self._offset,
                    "timeout": 30,
                    "allowed_updates": ["message"],
                },
                timeout=35.0,  # Slightly above Telegram timeout to avoid racing
            )
            if response.status_code == 200:
                data = response.json()
                if data.get("ok"):
                    self._last_fetch_successful = True
                    self._last_fetch_outcome = "success"
                    return data.get("result", [])
                self._last_fetch_outcome = "invalid_response"
            else:
                self._last_fetch_outcome = f"http_{response.status_code}"
        except httpx.TimeoutException:
            self._last_fetch_outcome = "timeout"
        except Exception as exc:
            self._last_fetch_outcome = f"exception:{type(exc).__name__}"
        return []

    def _handle_poll_result(self) -> Optional[float]:
        """Return the retry delay after an unsuccessful poll, or None on success/timeout."""
        if self._last_fetch_successful:
            self._retry_delay = 1.0
            self._last_logged_error = None
            return None

        if self._last_fetch_outcome == "timeout":
            return None

        delay = self._retry_delay
        log_key = (self._last_fetch_outcome, delay)
        if log_key != self._last_logged_error:
            logger.warning(
                "Telegram polling failed ({outcome}); retrying in {delay:.0f}s",
                outcome=self._last_fetch_outcome,
                delay=delay,
            )
            self._last_logged_error = log_key

        self._retry_delay = min(self._retry_delay * 2.0, self._retry_delay_max)
        return delay

    async def _process_update(self, update: dict) -> None:
        """Extract message and dispatch if it is a valid command from the authorized chat."""
        message = update.get("message")
        if not message:
            return

        text: str = message.get("text", "")
        if not text.startswith("/"):
            return

        chat_id = str(message.get("chat", {}).get("id", ""))

        if not self._is_authorized(chat_id):
            logger.warning(f"🔐 Bot command from unauthorized chat_id={chat_id} — ignored.")
            return

        logger.info(f"🤖 Bot command: '{text}' from chat_id={chat_id}")
        try:
            await self._dispatch_command(text)
        except Exception as exc:
            logger.error(f"Error handling bot command '{text}': {exc}")

    # ------------------------------------------------------------------
    # Command dispatch
    # ------------------------------------------------------------------

    async def _dispatch_command(self, text: str) -> None:
        """Route a /command to the appropriate handler. Handles /cmd@botname form."""
        raw = text.strip().split()[0].lower()
        command = raw.split("@")[0]  # Strip @botname suffix if present

        handlers = {
            "/status": self._cmd_status,
            "/metrics": self._cmd_metrics,
            "/alerts": self._cmd_alerts,
            "/help": self._cmd_help,
            "/start": self._cmd_help,
        }

        handler = handlers.get(command)
        if handler:
            await handler()
        else:
            await self._notifier._send_telegram_msg(
                f"❓ Comando `{command}` no reconocido\\. Usa /help\\."
            )

    async def _cmd_status(self) -> None:
        with SessionLocal() as db:
            snapshot = TelegramQueryService(db).get_status_snapshot()
        await self._notifier.notify_status_snapshot(snapshot)

    async def _cmd_metrics(self) -> None:
        with SessionLocal() as db:
            snapshot = TelegramQueryService(db).get_metrics_snapshot()
        await self._notifier.notify_metrics_snapshot(snapshot)

    async def _cmd_alerts(self) -> None:
        with SessionLocal() as db:
            snapshot = TelegramQueryService(db).get_alerts_history()
        await self._notifier.notify_alerts_history(snapshot)

    async def _cmd_help(self) -> None:
        await self._notifier._send_telegram_msg(_HELP_TEXT)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """
        Main polling loop. Designed to run as an asyncio background task.
        Exits cleanly when stop() is called or BOT_TOKEN is not configured.
        """
        token = settings.clean_bot_token
        if not token:
            logger.warning("BOT_TOKEN not configured — Telegram bot polling disabled.")
            return

        self._running = True
        logger.info("🤖 Telegram bot polling started (long polling, timeout=30s).")

        async with httpx.AsyncClient() as client:
            while self._running:
                updates = await self._fetch_updates(client)
                for update in updates:
                    update_id: int = update.get("update_id", 0)
                    self._offset = update_id + 1
                    await self._process_update(update)

                retry_delay = self._handle_poll_result()
                if retry_delay is not None and self._running:
                    await asyncio.sleep(retry_delay)

        logger.info("🤖 Telegram bot polling stopped.")

    def stop(self) -> None:
        """Signal the polling loop to exit after the current cycle completes."""
        self._running = False
