import re
from typing import Any, Callable

def strip_currency_symbol(val: str) -> str:
    """Strip currency symbols, commas, and whitespace from monetary values."""
    if not isinstance(val, str):
        return val
    # Remove $, commas, whitespace
    cleaned = re.sub(r"[$\s,]", "", val.strip())
    return cleaned


def parse_float(val: str) -> float:
    """Clean and parse a numeric string to float."""
    cleaned = strip_currency_symbol(val)
    return float(cleaned)


def strip_whitespace(val: str) -> str:
    """Trim leading and trailing whitespace."""
    return val.strip() if isinstance(val, str) else val


TRANSFORM_REGISTRY: dict[str, Callable[[str], Any]] = {
    "strip_currency_symbol": strip_currency_symbol,
    "parse_float": parse_float,
    "strip": strip_whitespace,
}


def apply_transform(val: str, transform_name: str | None) -> Any:
    """Apply a registered transformation function by name."""
    if not transform_name or val is None:
        return val
    transform_fn = TRANSFORM_REGISTRY.get(transform_name)
    if not transform_fn:
        return val
    return transform_fn(val)
