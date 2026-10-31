"""Clock tests: times before sync, time zone rules (rules adapted from fritz_exporter PR #719)."""

from datetime import UTC, datetime, timedelta

import pytest

from fritzlogextractor.clock import BoxClock, on_box_clock, resolve_utc_offset, with_offset
from fritzlogextractor.eventlog import Event

CET = timedelta(hours=1)
CEST = timedelta(hours=2)
TZ_RULE = "CET-1CEST,M3.5.0,M10.5.0/3"
BOOT = datetime(2026, 10, 30, 23, 15, 37, tzinfo=UTC)
BOX_BOOT = datetime(2026, 10, 31, 0, 15, 37)


def row(timestamp: datetime) -> Event:
    return Event(12, "net", timestamp, "m")


class TestBoxClock:
    def test_synced_row_gets_the_offset_of_its_own_time(self):
        clock = BoxClock(BOOT, BOX_BOOT, TZ_RULE, CET)

        assert clock.event_time(row(datetime(2026, 10, 25, 1, 0))) == datetime(
            2026, 10, 25, 1, 0, tzinfo=timezone_of(CEST)
        )

    def test_row_before_sync_goes_on_the_box_clock_once_synced(self):
        clock = BoxClock(BOOT, BOX_BOOT, TZ_RULE, CET)

        assert clock.event_time(row(datetime(1970, 1, 1, 0, 0, 34))) == datetime(
            2026, 10, 31, 0, 16, 11, tzinfo=timezone_of(CET)
        )

    def test_row_before_sync_is_estimated_in_utc_while_the_box_has_no_time(self):
        clock = BoxClock(BOOT, tz_rule=TZ_RULE)

        assert clock.event_time(row(datetime(1970, 1, 1, 0, 0, 34))) == BOOT + timedelta(seconds=34)

    def test_without_rule_or_offset_the_time_stays_naive(self):
        stamp = datetime(2026, 10, 9, 22, 11, 35)

        assert BoxClock(BOOT).event_time(row(stamp)) == stamp


def timezone_of(offset: timedelta):
    return with_offset(datetime(2000, 1, 1), offset).tzinfo


def test_on_box_clock_leaves_synced_times_and_unknown_boot_alone():
    synced = datetime(2026, 10, 31, 3, 0)
    early = datetime(1970, 1, 1, 0, 1)

    assert on_box_clock(synced, BOX_BOOT) == synced
    assert on_box_clock(early, None) == early
    assert on_box_clock(early, BOX_BOOT) == BOX_BOOT + timedelta(minutes=1)


class TestUtcOffset:
    @pytest.mark.parametrize(
        ("local", "expected"),
        [
            (datetime(2026, 1, 15, 12, 0, 0), CET),
            (datetime(2026, 7, 15, 12, 0, 0), CEST),
            # spring forward: 2026-03-29 02:00 -> 03:00
            (datetime(2026, 3, 29, 1, 59, 59), CET),
            (datetime(2026, 3, 29, 2, 30, 0), CET),  # skipped hour: standard offset
            (datetime(2026, 3, 29, 3, 0, 0), CEST),
            # fall back: 2026-10-25 03:00 -> 02:00, 02:00-03:00 occurs twice
            (datetime(2026, 10, 25, 1, 59, 59), CEST),
            (datetime(2026, 10, 25, 2, 0, 0), CEST),  # repeated hour: first occurrence
            (datetime(2026, 10, 25, 2, 59, 59), CEST),
            (datetime(2026, 10, 25, 3, 0, 0), CET),
        ],
    )
    def test_both_switches(self, local, expected):
        assert resolve_utc_offset(local, TZ_RULE, timedelta(hours=9)) == expected

    def test_entry_before_last_switch_does_not_use_current_offset(self):
        # Box is in CET now (current offset +1), the entry is from summer.
        assert resolve_utc_offset(datetime(2026, 8, 1, 12, 0), TZ_RULE, CET) == CEST

    def test_southern_hemisphere_rule(self):
        rule = "AEST-10AEDT,M10.1.0,M4.1.0/3"

        assert resolve_utc_offset(datetime(2026, 1, 15, 12, 0), rule, None) == timedelta(hours=11)
        assert resolve_utc_offset(datetime(2026, 7, 15, 12, 0), rule, None) == timedelta(hours=10)

    def test_explicit_dst_offset_and_no_dst(self):
        assert resolve_utc_offset(
            datetime(2026, 7, 1), "XXX-1YYY-3,M3.5.0,M10.5.0", None
        ) == timedelta(hours=3)
        assert resolve_utc_offset(datetime(2026, 7, 1), "UTC0", CET) == timedelta(0)

    @pytest.mark.parametrize(
        "rule",
        [
            None,
            "",
            "Europe/Berlin",
            "CET-1CEST",
            "CET-1CEST,J60,J300",
            "CET-1CEST,M13.5.0,M10.5.0",
            "CET-30CEST,M3.5.0,M10.5.0",
        ],
    )
    def test_unparsable_rule_falls_back_to_current_offset(self, rule):
        assert resolve_utc_offset(datetime(2026, 7, 1), rule, CET) == CET
        assert resolve_utc_offset(datetime(2026, 7, 1), rule, None) is None
