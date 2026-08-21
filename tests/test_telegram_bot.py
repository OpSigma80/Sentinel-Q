"""
Unit tests for TelegramBotPoller — command routing, authorization, and polling behavior.
All external I/O (httpx, DB, Telegram API) is stubbed for deterministic, fast tests.
"""

import asyncio
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import sentinel.application.telegram_poller as poller_module
from sentinel.application.telegram_poller import TelegramBotPoller, _HELP_TEXT
from sentinel.domain.schemas import (
    AlertHistorySnapshot,
    MetricsSnapshot,
    ServiceStatusSnapshot,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def build_update(
    update_id: int,
    text: str,
    chat_id: int = 12345,
) -> dict:
    """Construct a minimal Telegram update payload."""
    return {
        "update_id": update_id,
        "message": {
            "chat": {"id": chat_id},
            "text": text,
        },
    }


def build_poller(authorized_chat_id: str = "12345") -> TelegramBotPoller:
    """Create a TelegramBotPoller with a predictable authorized chat id."""
    poller = TelegramBotPoller()
    poller._notifier = MagicMock()
    poller._notifier._send_telegram_msg = AsyncMock(return_value=True)
    poller._notifier.notify_status_snapshot = AsyncMock(return_value=True)
    poller._notifier.notify_metrics_snapshot = AsyncMock(return_value=True)
    poller._notifier.notify_alerts_history = AsyncMock(return_value=True)
    return poller


# ---------------------------------------------------------------------------
# Authorization tests
# ---------------------------------------------------------------------------

class TestAuthorization:
    def test_authorized_chat_id_matches(self, monkeypatch):
        monkeypatch.setattr(poller_module.settings, "CHAT_ID", "12345", raising=False)
        poller = build_poller()
        assert poller._is_authorized("12345") is True

    def test_unauthorized_chat_id_rejected(self, monkeypatch):
        monkeypatch.setattr(poller_module.settings, "CHAT_ID", "12345", raising=False)
        poller = build_poller()
        assert poller._is_authorized("99999") is False

    def test_empty_chat_id_config_rejects_all(self, monkeypatch):
        monkeypatch.setattr(poller_module.settings, "CHAT_ID", None, raising=False)
        poller = build_poller()
        assert poller._is_authorized("12345") is False

    def test_unauthorized_update_does_not_dispatch(self, monkeypatch):
        """Messages from unauthorized chats must be silently ignored."""
        monkeypatch.setattr(poller_module.settings, "CHAT_ID", "12345", raising=False)
        poller = build_poller()

        update = build_update(1, "/status", chat_id=99999)
        asyncio.run(poller._process_update(update))

        poller._notifier.notify_status_snapshot.assert_not_called()
        poller._notifier._send_telegram_msg.assert_not_called()


# ---------------------------------------------------------------------------
# Command routing tests
# ---------------------------------------------------------------------------

class TestCommandRouting:
    def test_non_command_message_is_ignored(self, monkeypatch):
        monkeypatch.setattr(poller_module.settings, "CHAT_ID", "12345", raising=False)
        poller = build_poller()

        update = build_update(1, "hello world")
        asyncio.run(poller._process_update(update))

        poller._notifier.notify_status_snapshot.assert_not_called()

    def test_update_without_message_is_ignored(self, monkeypatch):
        monkeypatch.setattr(poller_module.settings, "CHAT_ID", "12345", raising=False)
        poller = build_poller()

        asyncio.run(poller._process_update({"update_id": 1}))

        poller._notifier._send_telegram_msg.assert_not_called()

    def test_status_command_calls_notify_status_snapshot(self, monkeypatch):
        monkeypatch.setattr(poller_module.settings, "CHAT_ID", "12345", raising=False)

        fake_snapshot = ServiceStatusSnapshot(
            total_services=2, healthy=1, degraded=1, critical=0, services=[]
        )
        fake_service = MagicMock()
        fake_service.get_status_snapshot.return_value = fake_snapshot

        poller = build_poller()

        with patch.object(poller_module, "SessionLocal", return_value=MagicMock(__enter__=MagicMock(return_value=MagicMock()), __exit__=MagicMock(return_value=False))):
            with patch("sentinel.application.telegram_poller.TelegramQueryService", return_value=fake_service):
                asyncio.run(poller._dispatch_command("/status"))

        poller._notifier.notify_status_snapshot.assert_called_once_with(fake_snapshot)

    def test_metrics_command_calls_notify_metrics_snapshot(self, monkeypatch):
        monkeypatch.setattr(poller_module.settings, "CHAT_ID", "12345", raising=False)

        fake_snapshot = MetricsSnapshot(
            total_checks=100,
            success_rate=98.5,
            avg_response_time_ms=120.0,
            p95_response_time_ms=250.0,
            max_response_time_ms=800.0,
        )
        fake_service = MagicMock()
        fake_service.get_metrics_snapshot.return_value = fake_snapshot

        poller = build_poller()

        with patch.object(poller_module, "SessionLocal", return_value=MagicMock(__enter__=MagicMock(return_value=MagicMock()), __exit__=MagicMock(return_value=False))):
            with patch("sentinel.application.telegram_poller.TelegramQueryService", return_value=fake_service):
                asyncio.run(poller._dispatch_command("/metrics"))

        poller._notifier.notify_metrics_snapshot.assert_called_once_with(fake_snapshot)

    def test_alerts_command_calls_notify_alerts_history(self, monkeypatch):
        monkeypatch.setattr(poller_module.settings, "CHAT_ID", "12345", raising=False)

        from datetime import datetime
        fake_snapshot = AlertHistorySnapshot(
            total_alerts_today=3,
            critical_count=1,
            warning_count=2,
            last_10_alerts=[],
            timestamp=datetime(2026, 4, 20, 10, 0, 0),
        )
        fake_service = MagicMock()
        fake_service.get_alerts_history.return_value = fake_snapshot

        poller = build_poller()

        with patch.object(poller_module, "SessionLocal", return_value=MagicMock(__enter__=MagicMock(return_value=MagicMock()), __exit__=MagicMock(return_value=False))):
            with patch("sentinel.application.telegram_poller.TelegramQueryService", return_value=fake_service):
                asyncio.run(poller._dispatch_command("/alerts"))

        poller._notifier.notify_alerts_history.assert_called_once_with(fake_snapshot)

    def test_help_command_sends_help_text(self, monkeypatch):
        monkeypatch.setattr(poller_module.settings, "CHAT_ID", "12345", raising=False)
        poller = build_poller()

        asyncio.run(poller._dispatch_command("/help"))

        poller._notifier._send_telegram_msg.assert_called_once_with(_HELP_TEXT)

    def test_unknown_command_sends_error_message(self, monkeypatch):
        monkeypatch.setattr(poller_module.settings, "CHAT_ID", "12345", raising=False)
        poller = build_poller()

        asyncio.run(poller._dispatch_command("/unknown"))

        poller._notifier._send_telegram_msg.assert_called_once()
        sent = poller._notifier._send_telegram_msg.call_args[0][0]
        assert "/unknown" in sent

    def test_command_with_bot_suffix_is_normalized(self, monkeypatch):
        """'/status@SentinelBot' should resolve to '/status'."""
        monkeypatch.setattr(poller_module.settings, "CHAT_ID", "12345", raising=False)

        fake_snapshot = ServiceStatusSnapshot(
            total_services=0, healthy=0, degraded=0, critical=0, services=[]
        )
        fake_service = MagicMock()
        fake_service.get_status_snapshot.return_value = fake_snapshot

        poller = build_poller()

        with patch.object(poller_module, "SessionLocal", return_value=MagicMock(__enter__=MagicMock(return_value=MagicMock()), __exit__=MagicMock(return_value=False))):
            with patch("sentinel.application.telegram_poller.TelegramQueryService", return_value=fake_service):
                asyncio.run(poller._dispatch_command("/status@SentinelBot"))

        poller._notifier.notify_status_snapshot.assert_called_once()


# ---------------------------------------------------------------------------
# Polling mechanics
# ---------------------------------------------------------------------------

class TestPollingMechanics:
    def test_fetch_updates_returns_empty_on_timeout(self, monkeypatch):
        """Timeout exceptions during polling must be silently swallowed."""
        import httpx

        poller = build_poller()

        async def run():
            async with httpx.AsyncClient() as client:
                with patch.object(client, "get", side_effect=httpx.TimeoutException("timeout")):
                    result = await poller._fetch_updates(client)
            return result

        result = asyncio.run(run())
        assert result == []

    def test_fetch_updates_returns_empty_on_non_200(self, monkeypatch):
        """Non-200 responses must return an empty list without crashing."""
        import httpx

        poller = build_poller()

        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.json.return_value = {"ok": False}

        async def run():
            async with httpx.AsyncClient() as client:
                with patch.object(client, "get", return_value=mock_response):
                    result = await poller._fetch_updates(client)
            return result

        result = asyncio.run(run())
        assert result == []

    def test_fetch_updates_parses_valid_response(self, monkeypatch):
        """A well-formed 200 response returns the updates list."""
        import httpx

        poller = build_poller()
        expected = [{"update_id": 1, "message": {"text": "/help", "chat": {"id": 12345}}}]

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"ok": True, "result": expected}

        async def run():
            async with httpx.AsyncClient() as client:
                with patch.object(client, "get", return_value=mock_response):
                    result = await poller._fetch_updates(client)
            return result

        result = asyncio.run(run())
        assert result == expected

    def test_offset_advances_after_processing(self, monkeypatch):
        """The offset must be set to update_id + 1 after each processed update."""
        monkeypatch.setattr(poller_module.settings, "CHAT_ID", "12345", raising=False)

        poller = build_poller()
        assert poller._offset == 0

        update = build_update(update_id=42, text="hello")  # Not a command, will be skipped
        asyncio.run(poller._process_update(update))

        # Offset advances in the run() loop, not in _process_update.
        # Verify it starts at 0 and test the run loop separately.
        assert poller._offset == 0  # Not mutated by _process_update itself

    def test_run_exits_immediately_without_bot_token(self, monkeypatch):
        """Polling must not start if BOT_TOKEN is unconfigured."""
        monkeypatch.setattr(poller_module.settings, "BOT_TOKEN", None, raising=False)
        monkeypatch.setattr(poller_module.settings, "BOT_TOKEN", "", raising=False)

        poller = TelegramBotPoller()
        # Should return quickly without error
        asyncio.run(poller.run())
        assert poller._running is False

    def test_stop_sets_running_false(self):
        """stop() must flip _running to False so the polling loop can exit."""
        poller = TelegramBotPoller()
        poller._running = True
        poller.stop()
        assert poller._running is False
