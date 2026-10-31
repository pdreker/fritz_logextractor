"""Parser, record and time zone tests, adapted from fritz_exporter PR #719 (@Pirat83)."""

import json
from datetime import datetime, timedelta

import pytest

from fritzlogextractor.eventlog import (
    Event,
    EventLogParseError,
    event_record,
    parse_event_log,
    resolve_utc_offset,
    with_offset,
)

CET = timedelta(hours=1)
CEST = timedelta(hours=2)
TZ_RULE = "CET-1CEST,M3.5.0,M10.5.0/3"


def event_xml(*events: tuple[str, str, str, str, str]) -> bytes:
    body = "".join(
        f"<Event><id>{i}</id><group>{g}</group><date>{d}</date><time>{t}</time><msg>{m}</msg></Event>"
        for i, g, d, t, m in events
    )
    return f'<?xml version="1.0" encoding="UTF-8"?><DeviceLog><!-- filter :all -->{body}</DeviceLog>'.encode()


LOGIN = (
    "504",
    "sys",
    "09.10.26",
    "21:49:06",
    "Anmeldung des Benutzers exporter an der FRITZ!Box-Benutzeroberfläche "
    "von IP-Adresse 192.0.2.10. [2 Meldungen seit 09.10.26 21:48:36]",
)
CONNECTED = (
    "22",
    "net",
    "09.10.26",
    "08:31:57",
    "Internetverbindung wurde erfolgreich hergestellt. IP-Adresse: 198.51.100.7, "
    "DNS-Server: 198.51.100.1 und 198.51.100.2, Gateway: 198.51.100.1",
)
PROVIDER = (
    "332",
    "sys",
    "09.10.26",
    "08:31:40",
    "Der Dienstanbieter hat erfolgreich Einstellungen an dieses Gerät übertragen.",
)


class TestParse:
    def test_parses_all_fields_newest_first(self):
        parsed = parse_event_log(event_xml(LOGIN, CONNECTED, PROVIDER))

        assert parsed.rejected == []
        assert [e.id for e in parsed.events] == [504, 22, 332]
        first = parsed.events[0]
        assert first.group == "sys"
        assert first.timestamp == datetime(2026, 10, 9, 21, 49, 6)
        assert first.msg.endswith("von IP-Adresse 192.0.2.10.")
        assert first.count == 2
        assert first.since == first.first == datetime(2026, 10, 9, 21, 48, 36)
        assert parsed.events[1].count == 1
        assert parsed.events[1].since is None
        assert parsed.events[1].first == parsed.events[1].timestamp

    def test_counter_in_another_language_is_split_off(self):
        row = ("504", "sys", "09.10.26", "21:49:06", "Login failed. [3 messages since 09.10.26 21:40:00]")

        event = parse_event_log(event_xml(row)).events[0]

        assert (event.msg, event.count) == ("Login failed.", 3)
        assert event.since == datetime(2026, 10, 9, 21, 40)

    def test_brackets_inside_a_message_are_not_a_counter(self):
        row = ("719", "wlan", "09.10.26", "21:49:06", "[fritz.repeater] Anmeldung [2 Meldungen] gescheitert.")

        event = parse_event_log(event_xml(row)).events[0]

        assert event.msg == row[4]
        assert event.count == 1

    def test_row_before_clock_sync_is_marked(self):
        row = ("12", "net", "01.01.70", "00:00:34", "DSL-Synchronisierung beginnt (Training).")

        event = parse_event_log(event_xml(row, PROVIDER)).events

        assert event[0].pre_sync
        assert not event[1].pre_sync

    @pytest.mark.parametrize(
        "bad",
        [
            ("x", "sys", "09.10.26", "08:31:40", "m"),
            ("1", "sys", "2026-10-09", "08:31:40", "m"),
            ("1", "sys", "31.02.26", "08:31:40", "m"),
            ("1", "sys", "09.10.26", "8:31", "m"),
            ("1", "sys", "09.10.26", "25:00:00", "m"),
            ("1", "sys group", "09.10.26", "08:31:40", "m"),
            ("1", "", "09.10.26", "08:31:40", "m"),
        ],
    )
    def test_bad_entry_is_rejected_and_good_ones_survive(self, bad):
        parsed = parse_event_log(event_xml(bad, PROVIDER))

        assert [e.id for e in parsed.events] == [332]
        assert len(parsed.rejected) == 1

    def test_entry_with_missing_field_is_rejected(self):
        xml = b"<DeviceLog><Event><id>1</id><group>sys</group></Event></DeviceLog>"

        parsed = parse_event_log(xml)

        assert parsed.events == []
        assert "missing date, time, msg" in parsed.rejected[0]

    @pytest.mark.parametrize(
        "xml",
        [
            b"not xml",
            b"<html><body>login</body></html>",
            b'<!DOCTYPE x [<!ENTITY a "b">]><DeviceLog>&a;</DeviceLog>',
        ],
    )
    def test_document_that_is_not_a_device_log_raises(self, xml):
        with pytest.raises(EventLogParseError):
            parse_event_log(xml)

    def test_empty_log_is_valid(self):
        assert parse_event_log(b"<DeviceLog></DeviceLog>").events == []


STAMP = datetime(2026, 10, 9, 22, 11, 35)


def record(event: Event, event_time: datetime = STAMP, **kwargs):
    values = {"repeat": 1, "estimated": False, "device": "d", "host": "h", "serial": None}
    values.update(kwargs)
    return event_record(event, event_time, **values)


class TestRecord:
    def event(self, msg: str) -> Event:
        return Event(id=504, group="sys", timestamp=STAMP, msg=msg)

    def test_layout(self):
        rec = record(
            self.event("hello"),
            with_offset(STAMP, CEST),
            repeat=3,
            device="Fritz!Box",
            host="fritz.box",
            serial="SERIAL1",
        )

        assert rec == {
            "kind": "event",
            "event_time": "2026-10-09T22:11:35+02:00",
            "time_estimated": False,
            "box_time": "09.10.26 22:11:35",
            "device": "Fritz!Box",
            "host": "fritz.box",
            "serial": "SERIAL1",
            "group": "sys",
            "id": 504,
            "msg": "hello",
            "repeat": 3,
        }

    def test_unknown_offset_gives_time_without_offset(self):
        assert record(self.event("hello"), with_offset(STAMP, None))["event_time"] == (
            "2026-10-09T22:11:35"
        )

    def test_control_characters_cannot_forge_a_json_line(self):
        evil = 'user\n{"event_time": "2000-01-01T00:00:00Z"}\r\x1b[2J\x00'

        line = json.dumps(record(self.event(evil)), ensure_ascii=False)

        assert "\n" not in line
        assert "\r" not in line
        assert "\x1b" not in line
        assert "\x00" not in line
        assert json.loads(line)["msg"] == evil

    def test_ddns_token_is_redacted(self):
        msg = "update https://ddns.example.invalid/u?domain=h&q=not-a-real-token&ip=192.0.2.1"

        rec = record(self.event(msg))

        assert "not-a-real-token" not in rec["msg"]
        assert "&q=<redacted>&ip=192.0.2.1" in rec["msg"]

    def test_token_at_start_is_redacted_but_other_q_words_are_not(self):
        assert record(self.event("q=abc123"))["msg"] == "q=<redacted>"
        assert record(self.event("freq=5 seq=7"))["msg"] == "freq=5 seq=7"


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
        [None, "", "Europe/Berlin", "CET-1CEST", "CET-1CEST,J60,J300", "CET-1CEST,M13.5.0,M10.5.0", "CET-30CEST,M3.5.0,M10.5.0"],
    )
    def test_unparsable_rule_falls_back_to_current_offset(self, rule):
        assert resolve_utc_offset(datetime(2026, 7, 1), rule, CET) == CET
        assert resolve_utc_offset(datetime(2026, 7, 1), rule, None) is None
