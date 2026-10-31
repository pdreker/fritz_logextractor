"""Tracker tests. The scenarios follow captures of a FRITZ!Box 7690 with a mesh repeater."""

from datetime import UTC, datetime, timedelta

import pytest

from fritzlogextractor.eventlog import Event
from fritzlogextractor.tracker import EventLogTracker

BOOT = datetime(2026, 10, 30, 23, 15, 37, tzinfo=UTC)
# The same boot on the box's local clock (CET).
BOX_BOOT = datetime(2026, 10, 31, 0, 15, 37)


def at(text: str) -> datetime:
    return datetime.strptime(text, "%d.%m.%y %H:%M:%S")


def row(event_id, text, time, msg="m", count=1, since=None, group="sys"):
    return Event(event_id, group, at(time), msg, count, at(since) if since else None)


def take(tracker, events, boot=BOOT, box_boot=BOX_BOOT):
    """Collect the new entries and commit, as the extractor does."""
    new = tracker.new_entries(events, boot, box_boot)
    tracker.commit(events, boot, box_boot)
    return [(e.event.id, e.event.timestamp.strftime("%H:%M:%S"), e.repeat) for e in new]


LOGIN = row(506, "", "31.10.26 03:13:56", "Anmeldung einer App des Benutzers u.")
PREFIX = row(26, "", "31.10.26 00:33:38", "IPv6-Präfix wurde erfolgreich aktualisiert.", group="net")
SYNC = row(2104, "", "31.10.26 00:18:40", "Die Systemzeit wurde erfolgreich aktualisiert.")


class TestNewEntries:
    def test_first_snapshot_is_new_oldest_first_and_folded_rows_count(self):
        folded = row(507, "", "31.10.26 05:26:53", "[fritz.repeater] App", 3, "31.10.26 03:13:58")

        assert take(EventLogTracker(), [folded, LOGIN, PREFIX, SYNC]) == [
            (2104, "00:18:40", 1),
            (26, "00:33:38", 1),
            (506, "03:13:56", 1),
            (507, "05:26:53", 3),
        ]

    def test_same_snapshot_again_is_not_new(self):
        tracker = EventLogTracker()
        take(tracker, [LOGIN, PREFIX])

        assert take(tracker, [LOGIN, PREFIX]) == []

    def test_nothing_changes_before_commit(self):
        tracker = EventLogTracker()
        assert len(tracker.new_entries([LOGIN], BOOT, BOX_BOOT)) == 1
        assert len(tracker.new_entries([LOGIN], BOOT, BOX_BOOT)) == 1

        tracker.commit([LOGIN], BOOT, BOX_BOOT)

        assert tracker.new_entries([LOGIN], BOOT, BOX_BOOT) == []

    def test_row_folding_at_the_top_counts_the_new_occurrences(self):
        tracker = EventLogTracker()
        once = row(506, "", "31.10.26 06:45:31", "App login")
        take(tracker, [once, PREFIX])
        twice = row(506, "", "31.10.26 07:45:00", "App login", 2, "31.10.26 06:45:31")
        five = row(506, "", "31.10.26 08:16:27", "App login", 5, "31.10.26 06:45:31")

        assert take(tracker, [twice, PREFIX]) == [(506, "07:45:00", 1)]
        assert take(tracker, [five, PREFIX]) == [(506, "08:16:27", 3)]

    def test_repeat_below_another_row_is_a_new_row(self):
        tracker = EventLogTracker()
        take(tracker, [LOGIN, PREFIX])
        again = row(506, "", "31.10.26 03:45:49", LOGIN.msg)
        other = row(26, "", "31.10.26 03:20:00", "other", group="net")

        assert take(tracker, [again, other, LOGIN, PREFIX]) == [
            (26, "03:20:00", 1),
            (506, "03:45:49", 1),
        ]

    def test_mirrored_row_sorted_in_below_a_known_row_is_new(self):
        tracker = EventLogTracker()
        take(tracker, [LOGIN, PREFIX])
        newer = row(506, "", "31.10.26 03:45:49", "App login from elsewhere")
        take(tracker, [newer, LOGIN, PREFIX])
        mirrored = row(507, "", "31.10.26 03:13:58", "[fritz.repeater] App")

        assert take(tracker, [newer, mirrored, LOGIN, PREFIX]) == [(507, "03:13:58", 1)]

    def test_jitter_of_a_second_is_not_new(self):
        tracker = EventLogTracker()
        a = row(506, "", "16.10.26 01:31:44", "same")
        b = row(506, "", "16.10.26 01:31:43", "same")
        take(tracker, [a, b])
        a1 = row(506, "", "16.10.26 01:31:43", "same")
        b1 = row(506, "", "16.10.26 01:31:42", "same")

        assert take(tracker, [a1, b1]) == []
        assert take(tracker, [a, b]) == []

    def test_rows_dropping_off_the_end_are_not_new_and_not_back(self):
        tracker = EventLogTracker()
        take(tracker, [LOGIN, PREFIX, SYNC])

        assert take(tracker, [LOGIN, PREFIX]) == []
        assert take(tracker, [LOGIN, PREFIX]) == []


class TestReboot:
    def test_rows_before_clock_sync_are_matched_after_the_box_rewrote_them(self):
        tracker = EventLogTracker()
        training = row(12, "", "01.01.70 00:00:34", "DSL training", group="net")
        dsl = row(11, "", "01.01.70 00:01:43", "DSL up", group="net")
        # Before sync, the box does not know its boot time on its own clock.
        assert take(tracker, [training], box_boot=None) == [(12, "00:00:34", 1)]
        assert take(tracker, [dsl, training], box_boot=None) == [(11, "00:01:43", 1)]

        # On sync, the box rewrites both to boot + offset, a second off.
        rewritten = [
            row(2104, "", "31.10.26 00:18:40", "sync"),
            row(11, "", "31.10.26 00:17:19", "DSL up", group="net"),
            row(12, "", "31.10.26 00:16:12", "DSL training", group="net"),
        ]
        box_boot = datetime(2026, 10, 31, 0, 15, 37)

        assert take(tracker, rewritten, box_boot=box_boot) == [(2104, "00:18:40", 1)]
        assert take(tracker, rewritten, box_boot=box_boot) == []

    def test_new_boot_makes_every_row_new(self):
        tracker = EventLogTracker()
        training = row(12, "", "01.01.70 00:00:34", "DSL training", group="net")
        take(tracker, [training], box_boot=None)

        later = BOOT + timedelta(minutes=10)
        assert take(tracker, [training], boot=later, box_boot=None) == [(12, "00:00:34", 1)]

    @pytest.mark.parametrize("seconds", [-60, -1, 1, 60])
    def test_boot_estimate_wobbling_is_the_same_boot(self, seconds):
        tracker = EventLogTracker()
        take(tracker, [LOGIN])

        assert take(tracker, [LOGIN], boot=BOOT + timedelta(seconds=seconds)) == []

    def test_is_same_boot(self):
        tracker = EventLogTracker()
        assert not tracker.is_same_boot(BOOT)
        take(tracker, [])
        assert tracker.is_same_boot(BOOT + timedelta(seconds=30))
        assert not tracker.is_same_boot(BOOT + timedelta(seconds=61))


class TestJson:
    def test_round_trip_keeps_what_was_emitted(self):
        tracker = EventLogTracker()
        folded = row(507, "", "31.10.26 05:26:53", "App", 3, "31.10.26 03:13:58")
        take(tracker, [folded, LOGIN])

        restored = EventLogTracker.from_json(tracker.to_json())

        assert restored.boot == BOOT
        assert take(restored, [folded, LOGIN]) == []
        more = row(507, "", "31.10.26 06:44:30", "App", 4, "31.10.26 03:13:58")
        assert take(restored, [more, LOGIN]) == [(507, "06:44:30", 1)]

    @pytest.mark.parametrize(
        "data",
        [
            {},
            {"version": 99, "boot": None, "rows": []},
            {"version": 1, "rows": []},
            {"version": 1, "boot": None, "rows": [[1, "sys"]]},
            {"version": 1, "boot": "yesterday", "rows": []},
            {"version": 1, "boot": None, "rows": [[1, "sys", "m", "now", 1]]},
        ],
    )
    def test_malformed_state_raises_value_error(self, data):
        with pytest.raises(ValueError):  # noqa: PT011
            EventLogTracker.from_json(data)
