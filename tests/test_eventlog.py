"""Parser, record and time zone tests, adapted from fritz_exporter PR #719 (@Pirat83)."""

import json
from datetime import datetime, timedelta

import pytest

from fritzlogextractor.clock import with_offset
from fritzlogextractor.eventlog import (
    Event,
    EventLogParseError,
    Source,
    event_record,
    parse_event_log,
)

CEST = timedelta(hours=2)


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


def record(event: Event, event_time: datetime = STAMP, repeat: int = 1, source=None):
    return event_record(event, event_time, repeat, source or Source("d", "h", None))


class TestRecord:
    def event(self, msg: str) -> Event:
        return Event(id=504, group="sys", timestamp=STAMP, msg=msg)

    def test_layout(self):
        rec = record(
            self.event("hello"),
            with_offset(STAMP, CEST),
            repeat=3,
            source=Source("Fritz!Box", "fritz.box", "SERIAL1"),
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

    @pytest.mark.parametrize(
        "url",
        [
            "https://ddns.example.invalid/u?domain=h&q=not-a-real-token&ip=192.0.2.1",
            "https://ddns.example.invalid/nic/update?hostname=h&password=not-a-real-token",
            "https://ddns.example.invalid/update/not-a-real-token",
            "http://user:not-a-real-token@ddns.example.invalid:8080/nic/update",
        ],
    )
    def test_urls_keep_only_scheme_and_host(self, url):
        rec = record(self.event(f"DynDNS-Fehler: {url} antwortet nicht."))

        assert "not-a-real-token" not in rec["msg"]
        host = url.split("@")[-1].split("://")[-1].split("/")[0]
        scheme = url.split("://")[0]
        assert rec["msg"] == f"DynDNS-Fehler: {scheme}://{host}/<redacted> antwortet nicht."

    def test_url_without_path_stays(self):
        assert record(self.event("see https://ddns.example.invalid"))["msg"] == (
            "see https://ddns.example.invalid"
        )

    def test_token_at_start_is_redacted_but_other_q_words_are_not(self):
        assert record(self.event("q=abc123"))["msg"] == "q=<redacted>"
        assert record(self.event("freq=5 seq=7"))["msg"] == "freq=5 seq=7"
