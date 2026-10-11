"""Pure parking access decision logic."""

from dataclasses import dataclass
from datetime import datetime

from backend.cache import CacheState


@dataclass(frozen=True, slots=True)
class Decision:
    """Decision result for one credential tap."""

    allow: bool
    reason_code: str
    oled_message: str
    alert: bool


_DECISIONS = {
    "ok": Decision(True, "ok", "Mời vào", False),
    "card_unknown": Decision(False, "card_unknown", "Thẻ không hợp lệ", False),
    "card_locked": Decision(False, "card_locked", "Thẻ đã khóa", True),
    "card_lost": Decision(False, "card_lost", "Thẻ đã khóa", True),
    "plan_expired": Decision(False, "plan_expired", "Thẻ hết hạn", False),
    "already_in_lot": Decision(
        False, "already_in_lot", "Xe đã ở trong bãi", True
    ),
    "not_in_lot": Decision(False, "not_in_lot", "Xe chưa vào bãi", True),
    "lot_full": Decision(False, "lot_full", "Hết chỗ", False),
}


def decide(gate: str, uid: str, now: datetime, cache: CacheState) -> Decision:
    """Return an access decision using only the supplied time and RAM cache."""
    if gate not in ("in", "out"):
        raise ValueError("gate must be 'in' or 'out'")

    credential = cache.credentials.get(uid)
    if credential is None:
        return _DECISIONS["card_unknown"]
    if credential.status == "locked":
        return _DECISIONS["card_locked"]
    if credential.status == "lost":
        return _DECISIONS["card_lost"]

    if gate == "in":
        if (
            credential.plan_expires_at is not None
            and credential.plan_expires_at < now.date()
        ):
            return _DECISIONS["plan_expired"]
        if credential.in_lot:
            return _DECISIONS["already_in_lot"]
        if cache.free_total <= 0:
            return _DECISIONS["lot_full"]
        return _DECISIONS["ok"]

    if not credential.in_lot:
        return _DECISIONS["not_in_lot"]
    return _DECISIONS["ok"]
