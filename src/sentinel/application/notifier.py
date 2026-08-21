from loguru import logger
from datetime import UTC, datetime, timedelta
from typing import Optional, Dict, Any
import httpx
from sentinel.config import settings
from sentinel.domain.schemas import AlertTrendData, ServiceStatusSnapshot, MetricsSnapshot, AlertHistorySnapshot


class AlertNotifier:
    def __init__(self) -> None:
        self.chat_id = settings.clean_chat_id
        # Simple in-memory cache to avoid repeated queries.
        self._cache: Dict[str, tuple[Any, datetime]] = {}

    def _get_from_cache(self, key: str, max_age_seconds: int = 30) -> Optional[Any]:
        """Get value from cache if not expired."""
        if key not in self._cache:
            return None
        
        value, timestamp = self._cache[key]
        age = (datetime.now(UTC).replace(tzinfo=None) - timestamp).total_seconds()
        
        if age > max_age_seconds:
            del self._cache[key]
            return None
        
        return value

    def _set_cache(self, key: str, value: Any) -> None:
        """Store value in cache with timestamp."""
        self._cache[key] = (value, datetime.now(UTC).replace(tzinfo=None))

    async def _send_telegram_msg(self, message: str, parse_mode: str = "Markdown") -> bool:
        """Send a Telegram message using the configured bot credentials."""
        token = settings.clean_bot_token
        if not token:
            logger.error("BOT_TOKEN is not configured.")
            return False

        api_url = f"https://api.telegram.org/bot{token}/sendMessage"
        
        async with httpx.AsyncClient() as client:
            try:
                payload = {
                    "chat_id": self.chat_id, 
                    "text": message, 
                    "parse_mode": parse_mode
                }
                response = await client.post(api_url, json=payload, timeout=10.0)
                
                if response.status_code == 200:
                    return True
                else:
                    logger.error(f"❌ Telegram error ({response.status_code}): {response.text}")
                    return False
            except Exception as e:
                logger.error(f"Telegram connection failure: {e}")
                return False

    def _format_duration(self, duration_seconds: Optional[float]) -> str:
        """Format a duration in seconds for operator-friendly messages."""
        if duration_seconds is None or duration_seconds < 0:
            return "n/a"

        total_seconds = int(duration_seconds)
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)

        if hours > 0:
            return f"{hours}h {minutes}m {seconds}s"
        if minutes > 0:
            return f"{minutes}m {seconds}s"
        return f"{seconds}s"

    async def notify_failure(
        self,
        service_name: str,
        url: str,
        status: str,
        severity: str = "HIGH",
        response_time_ms: Optional[float] = None,
        failure_streak: Optional[int] = None,
        incident_started_at: Optional[datetime] = None,
    ) -> bool:
        """Send a confirmed service failure alert."""
        response_time_text = "n/a" if response_time_ms is None else f"{response_time_ms:.2f} ms"
        incident_started_text = (
            incident_started_at.strftime('%Y-%m-%d %H:%M:%S')
            if incident_started_at is not None
            else "n/a"
        )
        msg = (
            f"🚨 *SENTINEL-Q ALERT*\n\n"
            f"🔴 *Severity:* {severity}\n"
            f"❌ *Service:* `{service_name}`\n"
            f"🌐 *URL:* {url}\n"
            f"⚠️ *Status:* {status}\n"
            f"📉 *Failure Streak:* {failure_streak or 1}\n"
            f"⏱️ *Response Time:* {response_time_text}\n"
            f"🕒 *Incident Started:* {incident_started_text}\n"
            f"⏰ *Time:* {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        return await self._send_telegram_msg(msg)

    async def notify_success(
        self,
        service_name: str,
        url: str,
        response_time_ms: Optional[float] = None,
        recovery_streak: Optional[int] = None,
        downtime_seconds: Optional[float] = None,
    ) -> bool:
        """Send a confirmed service recovery alert."""
        response_time_text = "n/a" if response_time_ms is None else f"{response_time_ms:.2f} ms"
        msg = (
            f"✅ *SENTINEL-Q RECOVERED*\n\n"
            f"🟢 *Service:* `{service_name}`\n"
            f"🌐 *URL:* {url}\n"
            f"👌 *Status:* Online / Healthy\n"
            f"📈 *Recovery Streak:* {recovery_streak or 1}\n"
            f"⏱️ *Response Time:* {response_time_text}\n"
            f"🛠️ *Downtime:* {self._format_duration(downtime_seconds)}\n"
            f"⏰ *Time:* {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        return await self._send_telegram_msg(msg)

    # ========================================================================
    # ENHANCED TELEGRAM - Methods for contextual alerts and commands
    # ========================================================================

    async def notify_failure_with_trend(
        self,
        trend: AlertTrendData,
        url: str,
        error_status: str,
    ) -> bool:
        """Send failure alert with trend context for better operator awareness."""
        msg = (
            f"🚨 *SENTINEL-Q ALERT*\n\n"
            f"❌ *Service:* `{trend.service_name}`\n"
            f"🔴 *Status:* {trend.current_status}\n"
            f"🌐 *URL:* {url}\n"
            f"⚠️ *Error:* {error_status}\n\n"
            f"📊 *Context (24h):*\n"
            f"  • Uptime: {trend.uptime_percentage:.1f}%\n"
            f"  • Avg Latency: {trend.avg_response_time_ms:.0f}ms\n"
            f"  • Failures Today: {trend.failure_count_today}\n"
            f"  • Consecutive Down: {trend.consecutive_failures}\n\n"
            f"⏰ Last Check: {trend.last_check_at.strftime('%H:%M:%S')}"
        )
        return await self._send_telegram_msg(msg)

    async def notify_status_snapshot(self, snapshot: ServiceStatusSnapshot) -> bool:
        """Send /status command response with all services overview."""
        # Build service list
        services_text = ""
        if snapshot.services:
            for svc in snapshot.services[:10]:  # Limit to 10 for message size
                name = svc.get("name", "unknown")
                status_emoji = "🟢" if svc.get("status") == "OK" else "🟡" if svc.get("status") == "DEGRADED" else "🔴"
                uptime = svc.get("uptime", 0)
                services_text += f"{status_emoji} {name} ({uptime:.0f}%)\n"

        msg = (
            f"📊 *SENTINEL-Q STATUS REPORT*\n\n"
            f"🟢 Healthy: {snapshot.healthy}\n"
            f"🟡 Degraded: {snapshot.degraded}\n"
            f"🔴 Critical: {snapshot.critical}\n\n"
            f"📋 Services:\n{services_text or 'No services monitored'}\n\n"
            f"⏰ Report: {snapshot.timestamp.strftime('%Y-%m-%d %H:%M:%S')}"
        )
        return await self._send_telegram_msg(msg)

    async def notify_metrics_snapshot(self, snapshot: MetricsSnapshot) -> bool:
        """Send /metrics command response with global performance metrics."""
        msg = (
            f"📈 *SENTINEL-Q METRICS (24h)*\n\n"
            f"✅ Success Rate: {snapshot.success_rate:.1f}%\n"
            f"📊 Total Checks: {snapshot.total_checks}\n\n"
            f"⏱️ Response Times:\n"
            f"  • Average: {snapshot.avg_response_time_ms:.0f}ms\n"
            f"  • P95: {snapshot.p95_response_time_ms:.0f}ms\n"
            f"  • Max: {snapshot.max_response_time_ms:.0f}ms\n\n"
            f"⏰ Report: {snapshot.timestamp.strftime('%Y-%m-%d %H:%M:%S')}"
        )
        return await self._send_telegram_msg(msg)

    async def notify_alerts_history(self, snapshot: AlertHistorySnapshot) -> bool:
        """Send /alerts command response with recent alert history."""
        alerts_text = ""
        if snapshot.last_10_alerts:
            for alert in snapshot.last_10_alerts[:5]:  # Show last 5 for brevity
                emoji = "🔴" if alert.severity == "CRITICAL" else "🟡"
                time_str = alert.timestamp.strftime('%H:%M')
                alerts_text += f"{emoji} [{time_str}] {alert.service_name}: {alert.event_type}\n"

        msg = (
            f"🚨 *SENTINEL-Q ALERT HISTORY (24h)*\n\n"
            f"🔴 Critical: {snapshot.critical_count}\n"
            f"🟡 Warnings: {snapshot.warning_count}\n"
            f"📊 Total Alerts: {snapshot.total_alerts_today}\n\n"
            f"📋 Recent Events:\n{alerts_text or 'No alerts in last 24h'}\n\n"
            f"⏰ Report: {snapshot.timestamp.strftime('%Y-%m-%d %H:%M:%S')}"
        )
        return await self._send_telegram_msg(msg)