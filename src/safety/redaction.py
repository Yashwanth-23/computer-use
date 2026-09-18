import re
from typing import Any

# Banking & Financial PII patterns
SSN_PATTERN = re.compile(r"\b(?!000|666|9\d{2})\d{3}[- ]?(?!00)\d{2}[- ]?(?!0000)\d{4}\b")
CARD_PATTERN = re.compile(r"\b(?:\d{4}[ -]?){3}\d{4}\b")
JWT_PATTERN = re.compile(r"\beyJ[a-zA-Z0-9_-]+\.eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\b")
SECRET_KEY_PATTERN = re.compile(r"(?i)\b(password|secret|api[_-]?key|token|auth_token|bearer)\s*[:=]\s*['\"]?([^\s'\",}]+)")


def redact_text(text: str) -> str:
    """Sanitize sensitive financial PII and credentials from a text string."""
    if not isinstance(text, str) or not text:
        return text

    sanitized = SSN_PATTERN.sub("[REDACTED_SSN]", text)
    sanitized = CARD_PATTERN.sub("[REDACTED_CARD]", sanitized)
    sanitized = JWT_PATTERN.sub("[REDACTED_JWT]", sanitized)
    sanitized = SECRET_KEY_PATTERN.sub(r"\1: [REDACTED_SECRET]", sanitized)
    return sanitized


def redact_data(obj: Any) -> Any:
    """Recursively redact sensitive data from strings, dictionaries, lists, and primitives."""
    if isinstance(obj, str):
        return redact_text(obj)
    elif isinstance(obj, dict):
        redacted_dict = {}
        for k, v in obj.items():
            if any(secret_term in k.lower() for secret_term in ["password", "secret", "token", "ssn", "cvv", "api_key"]):
                redacted_dict[k] = "[REDACTED_SECRET]"
            else:
                redacted_dict[k] = redact_data(v)
        return redacted_dict
    elif isinstance(obj, list):
        return [redact_data(item) for item in obj]
    elif isinstance(obj, tuple):
        return tuple(redact_data(item) for item in obj)
    return obj
