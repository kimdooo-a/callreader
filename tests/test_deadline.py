from datetime import UTC, datetime

from callreader.axes import State
from callreader.judge import _deadline_state

NOW = datetime(2026, 10, 27, 4, 0, tzinfo=UTC)


def test_future_with_zone_passes():
    s, note = _deadline_state("2026-11-30T23:59:00-05:00", NOW)
    assert s == State.PASS and "day(s) left" in note


def test_past_fails():
    s, _ = _deadline_state("2026-10-20T23:59:00+09:00", NOW)
    assert s == State.FAIL


def test_date_only_assumes_aoe_never_closes_early():
    # 2026-10-26 AoE ends 2026-10-27 11:59 UTC — still open at 04:00 UTC.
    s, note = _deadline_state("2026-10-26", NOW)
    assert s == State.PASS and "AoE" in note


def test_missing_is_unread_not_pass():
    assert _deadline_state(None, NOW)[0] == State.UNREAD
    assert _deadline_state("next friday", NOW)[0] == State.UNREAD
