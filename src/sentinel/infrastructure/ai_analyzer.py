"""
AI-Sentinel Analyzer V1.1: Industrial Grade Diagnostics.
Rationale: Decoupled, throttled, and sanitized AI analysis for SRE operations.
"""

from __future__ import annotations
import asyncio
import json
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional
import httpx
from loguru import logger

from sentinel.config import settings
from sentinel.infrastructure.security.sanitizer import LogSanitizer

@dataclass(slots=True)
class AIInsight:
    hypothesis: str
    confidence: float
    recommendation: str
    trigger: str
    generated_at: datetime

class AIIncidentAnalyzer:
    _SYSTEM_PROMPT = (
        "You are a Senior SRE. Analyze the provided sanitized logs and telemetry. "
        "Provide a diagnostic hypothesis, confidence score (0-1), and one actionable recommendation. "
        "Output MUST be strict JSON."
    )

    def __init__(self) -> None:
        self._last_analysis_at: dict[int, datetime] = {}
        self._latest_insight_by_target: dict[int, AIInsight] = {}
        self._quota_used: int = 0
        self._quota_day: date = datetime.now(UTC).date()
        # CONCURRENCY CONTROL: Max 2 simultaneous AI calls to protect 1GB RAM.
        self._concurrency_limit = asyncio.Semaphore(2) 
        
        self._metrics = {
            "ai_analysis_total": 0,
            "ai_analysis_errors": 0,
            "ai_latency_ms": 0,
        }

    async def maybe_schedule_analysis(self, **kwargs) -> None:
        """Evaluates policy and schedules analysis without blocking."""
        if not settings.AI_ANALYZER_ENABLED:
            return

        target_id = kwargs.get("target_id")
        trigger = self._evaluate_trigger(kwargs)
        
        if trigger and self._can_analyze(target_id):
            self._last_analysis_at[target_id] = datetime.now(UTC)
            self._quota_used += 1
            # Fire-and-forget with managed concurrency
            asyncio.create_task(self._run_safe_analysis(kwargs, trigger))

    def _evaluate_trigger(self, data: dict) -> Optional[str]:
        """Simple trigger logic: Status >= 400 and streak met."""
        if data.get("status_code", 0) >= 400 and data.get("failure_streak", 0) >= settings.AI_FAILURE_STREAK_TRIGGER:
            return "STREAK_FAILURE"
        return None

    def _can_analyze(self, target_id: int) -> bool:
        """Quota and Cooldown guardrails."""
        self._reset_quota_if_new_day()
        if self._quota_used >= settings.AI_DAILY_QUOTA:
            return False
        
        last = self._last_analysis_at.get(target_id)
        if not last: return True
        return (datetime.now(UTC) - last) >= timedelta(seconds=settings.AI_COOLDOWN_SECONDS)

    def _reset_quota_if_new_day(self) -> None:
        if datetime.now(UTC).date() != self._quota_day:
            self._quota_day = datetime.now(UTC).date()
            self._quota_used = 0

    async def _run_safe_analysis(self, kwargs: dict, trigger: str):
        """Wrapper to manage concurrency and exceptions."""
        async with self._concurrency_limit:
            start_time = datetime.now(UTC)
            try:
                # 1. Sanitize input
                sanitizer = LogSanitizer()
                logs = self._read_logs(sanitizer)
                payload = self._build_payload(kwargs, trigger, logs, sanitizer)
                
                # 2. Call AI
                insight = await self._call_gemini(payload, trigger)
                if insight:
                    self._latest_insight_by_target[kwargs["target_id"]] = insight
                    self._metrics["ai_analysis_total"] += 1
                    
                    # 3. AUTO-NOTIFICATION: The analyzer now pushes to the notifier
                    from sentinel.application.notifier import AlertNotifier
                    notifier = AlertNotifier()
                    await notifier.notify_ai_insight(kwargs["service_name"], insight.hypothesis)

            except Exception as e:
                self._metrics["ai_analysis_errors"] += 1
                logger.error(f"AI Analyzer Error: {e}")
            finally:
                self._metrics["ai_latency_ms"] = (datetime.now(UTC) - start_time).total_seconds() * 1000

    async def _call_gemini(self, payload: str, trigger: str) -> Optional[AIInsight]:
        """Calls Google Gemini API with hard timeout."""
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{settings.AI_MODEL_NAME}:generateContent?key={settings.AI_PROVIDER_API_KEY}"
        
        body = {
            "contents": [{"parts": [{"text": payload}]}],
            "systemInstruction": {"parts": [{"text": self._SYSTEM_PROMPT}]},
            "generationConfig": {"responseMimeType": "application/json"}
        }
        
        async with httpx.AsyncClient(timeout=settings.AI_TIMEOUT_SECONDS) as client:
            resp = await client.post(url, json=body)
            resp.raise_for_status()
            res_data = resp.json()
            
        try:
            text = res_data['candidates'][0]['content']['parts'][0]['text']
            parsed = json.loads(text)
            return AIInsight(
                hypothesis=parsed.get("hypothesis", "Unknown"),
                confidence=parsed.get("confidence", 0.0),
                recommendation=parsed.get("recommendation", "N/A"),
                trigger=trigger,
                generated_at=datetime.now(UTC)
            )
        except (KeyError, json.JSONDecodeError) as e:
            logger.error(f"Failed to parse AI response: {e}")
            return None

    def _build_payload(self, kwargs: dict, trigger: str, logs: str, sanitizer: LogSanitizer) -> str:
        data = {
            "service": kwargs.get("service_name"),
            "code": kwargs.get("status_code"),
            "latency": kwargs.get("response_time_ms"),
            "trigger": trigger,
            "context": logs
        }
        return sanitizer.sanitize(json.dumps(data))

    def _read_logs(self, sanitizer: LogSanitizer) -> str:
        """Reads local logs to provide context to the AI."""
        log_path = Path("logs/sentinel.log")
        if not log_path.exists(): return ""
        try:
            with open(log_path, "r") as f:
                content = "".join(f.readlines()[-20:]) # Last 20 lines
                return sanitizer.sanitize(content)
        except: return ""