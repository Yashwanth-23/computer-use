"""
ApexCore Banking Portal - simulated core banking data layer.

This stands in for a real core banking system (Fiserv/Jack Henry/FIS-style).
State is in-memory and process-local by design: this is a proxy target for
automation, not a real system, and must never hold real PII.
"""
from __future__ import annotations

import itertools
import random
import threading
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Member:
    member_id: str
    first_name: str
    last_name: str
    savings_balance: float
    checking_balance: float
    status: str = "ACTIVE"  # ACTIVE | FROZEN
    sub_accounts: list[dict] = field(default_factory=list)

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"


# --- Seed data -------------------------------------------------------------
# Deliberately fake, non-identifying names/numbers. No real PII, ever.
_MEMBERS: dict[str, Member] = {
    "1001": Member("1001", "John", "Doe", 24500.00, 4120.00, status="ACTIVE"),
    "1002": Member("1002", "Sarah", "Smith", 8899.50, 1250.75, status="ACTIVE"),
    "1003": Member("1003", "Miguel", "Alvarez", 150.00, 60.10, status="FROZEN"),
    # 9999 intentionally absent -> triggers "Member Record Not Found" business outcome
}

_lock = threading.Lock()
_receipt_counter = itertools.count(100000)

# Toggled by the /admin/maintenance endpoint to simulate an interstitial
# that legitimately appears at runtime (recoverable condition).
MAINTENANCE_MODE = {"enabled": False, "trigger_count": 0}

# Simulated transient slow load: next N requests to member-detail will be
# delayed to exercise wait/retry handling.
SLOW_LOAD = {"remaining": 0, "delay_seconds": 2.5}


def get_member(member_id: str) -> Member | None:
    return _MEMBERS.get(member_id.strip())


def open_sub_account(member_id: str, product_type: str, initial_deposit: float) -> dict:
    """Simulate opening a sub-account (holiday club, etc). Returns a receipt."""
    member = _MEMBERS.get(member_id)
    if member is None:
        raise KeyError(member_id)
    with _lock:
        receipt_id = f"APX-{next(_receipt_counter)}"
        record = {
            "receipt_id": receipt_id,
            "product_type": product_type,
            "initial_deposit": initial_deposit,
            "opened_at": datetime.utcnow().isoformat() + "Z",
        }
        member.sub_accounts.append(record)
    return record


def maybe_trigger_maintenance() -> bool:
    """Randomized/forced maintenance interstitial, simulating a real
    intermittent condition rather than constant UI drift."""
    if MAINTENANCE_MODE["enabled"]:
        MAINTENANCE_MODE["trigger_count"] += 1
        return True
    return False


def consume_slow_load() -> float:
    """Returns extra delay (seconds) to apply, decrementing the counter."""
    if SLOW_LOAD["remaining"] > 0:
        SLOW_LOAD["remaining"] -= 1
        return SLOW_LOAD["delay_seconds"]
    return 0.0


def reset_all():
    """Reset simulated runtime conditions between demo runs."""
    MAINTENANCE_MODE["enabled"] = False
    MAINTENANCE_MODE["trigger_count"] = 0
    SLOW_LOAD["remaining"] = 0
