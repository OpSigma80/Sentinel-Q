"""
Security Sanitizer: Zero-Trust Outbound Filter.
Rationale: Prevents PII and Credentials leakage to external AI providers.
"""

from __future__ import annotations
import re

class LogSanitizer:
    """Mask sensitive data before leaving the VPS boundary."""

    _PATTERNS = [
        # Authorization headers: Bearer/Basic
        (re.compile(r"(authorization\s*:\s*(?:bearer|basic)\s+)[^\s]+", re.IGNORECASE), r"\1[MASKED_AUTH]"),
        
        # Cookies and Session identifiers
        (re.compile(r"((?:session|sessionid|csrftoken|auth_token|id)\s*[:=]\s*)[^;\s]+", re.IGNORECASE), r"\1[MASKED_SESSION]"),
        
        # Generic Secret/Key-Value pairs
        (re.compile(r"((?:password|passwd|secret|api[_-]?key|token)\s*[:=]\s*)[^\s,;]+", re.IGNORECASE), r"\1[MASKED_CREDENTIAL]"),
        
        # Emails (RFC 5322ish)
        (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "[MASKED_EMAIL]"),
        
        # Public IP Addresses (Prevent infrastructure mapping)
        (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "[MASKED_IP]"),
        
        # High-Entropy Tokens (Long alphanumeric strings)
        (re.compile(r"\b[a-zA-Z0-9_\-]{24,}\b"), "[MASKED_TOKEN]"),
    ]

    def sanitize(self, content: str) -> str:
        """
        Instance method for sanitization.
        Rationale: Allows injection and stateful configuration if needed later.
        """
        if not content:
            return ""
            
        sanitized = content
        for pattern, replacement in self._PATTERNS:
            sanitized = pattern.sub(replacement, sanitized)
        
        return sanitized

    @classmethod
    def quick_sanitize(cls, content: str) -> str:
        """Static helper for one-off sanitization tasks."""
        return cls().sanitize(content)