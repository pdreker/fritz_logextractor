"""Fritz!Box event log: parsing, de-duplication and output records.

Adapted from fritz_exporter PR #719 by Rafael Kansy (@Pirat83), Apache-2.0:
https://github.com/pdreker/fritz_exporter/pull/719


The box keeps its event log in RAM and clears it on every restart. TR-064
``DeviceInfo:X_AVM-DE_GetDeviceLogPath`` returns the URL of the complete log as
XML (``<DeviceLog><Event><id/><group/><date/><time/><msg/></Event>...``). This
module holds everything that does not touch the network: the strict parser,
the tracker that picks out entries not yet emitted, the resolution of the
box-local timestamps to a UTC offset and the output record. Fetching the XML
is done in :mod:`fritzlogextractor.extractor`, picking out the entries not yet
emitted in :mod:`fritzlogextractor.tracker`.

The box folds a message that repeats right away into the row it already has: the
row moves to the time of the latest occurrence and the message gains a suffix
like ``[3 Meldungen seit 31.10.26 03:13:58]``. The parser splits the suffix off
into :attr:`Event.count` and :attr:`Event.since`.

Every field comes from the box and may contain text typed by a third party
(for example a user name in a failed login), so fields are validated and the
DynDNS token is redacted from the message. JSON encoding takes care of escaping.
"""

from __future__ import annotations

import calendar
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from xml.etree.ElementTree import Element

from attrs import define
from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException

__all__ = [
    "PRE_SYNC_EPOCH",
    "Event",
    "EventLogParseError",
    "ParsedEventLog",
    "event_record",
    "parse_event_log",
    "resolve_utc_offset",
]

_GROUP_RE = re.compile(r"[A-Za-z0-9_-]{1,32}")
_ID_RE = re.compile(r"[0-9]{1,9}")
_DATE_RE = re.compile(r"[0-9]{2}\.[0-9]{2}\.[0-9]{2}")
_TIME_RE = re.compile(r"[0-9]{2}:[0-9]{2}:[0-9]{2}")
# The DynDNS update URL in a message carries the account token as ``q=<token>``.
_TOKEN_RE = re.compile(r"(?<!\w)q=[^&\s]+")
# Counter of a folded row; the words between count and date depend on the box's language.
_FOLD_RE = re.compile(
    r"\s*\[([0-9]{1,9}) [^\[\]]*?([0-9]{2}\.[0-9]{2}\.[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2})\]\s*$"
)
_BOX_TIME_FORMAT = "%d.%m.%y %H:%M:%S"

# Until a box has synced its clock after a reboot, it dates its entries on this day,
# with the time since boot as time of day.
PRE_SYNC_EPOCH = datetime(1970, 1, 1)  # noqa: DTZ001


class EventLogParseError(Exception):
    """Raised when the event log document as a whole cannot be parsed."""


@define(frozen=True)
class Event:
    """One validated event log row.

    ``timestamp`` is the box-local wall-clock time of the latest occurrence, ``msg``
    the message without the counter of a folded row, ``count`` the number of
    occurrences in the row and ``since`` the time of the first one if it is folded.
    """

    id: int
    group: str
    timestamp: datetime
    msg: str
    count: int = 1
    since: datetime | None = None

    @property
    def first(self) -> datetime:
        """Time of the first occurrence; it stays put when the row folds."""
        return self.since or self.timestamp

    @property
    def pre_sync(self) -> bool:
        """The box logged the row before it had synced its clock."""
        return self.timestamp.year == PRE_SYNC_EPOCH.year


@define(frozen=True)
class ParsedEventLog:
    """Valid events in document order (newest first on the box) and rejected entries."""

    events: list[Event]
    rejected: list[str]


def _parse_event(node: Element) -> Event:
    """Validate one ``<Event>`` node, raising ``ValueError`` naming the bad field."""
    fields = {name: node.findtext(name) for name in ("id", "group", "date", "time", "msg")}
    missing = [name for name, value in fields.items() if value is None]
    if missing:
        msg = f"missing {', '.join(missing)}"
        raise ValueError(msg)
    event_id = (fields["id"] or "").strip()
    group = (fields["group"] or "").strip()
    date = (fields["date"] or "").strip()
    time = (fields["time"] or "").strip()
    if not _ID_RE.fullmatch(event_id):
        msg = "invalid id"
        raise ValueError(msg)
    if not _GROUP_RE.fullmatch(group):
        msg = "invalid group"
        raise ValueError(msg)
    if not (_DATE_RE.fullmatch(date) and _TIME_RE.fullmatch(time)):
        msg = "invalid date or time"
        raise ValueError(msg)
    timestamp = datetime.strptime(f"{date} {time}", _BOX_TIME_FORMAT)  # noqa: DTZ007
    msg = fields["msg"] or ""
    fold = _FOLD_RE.search(msg)
    if fold is None:
        return Event(int(event_id), group, timestamp, msg)
    since = datetime.strptime(fold[2], _BOX_TIME_FORMAT)  # noqa: DTZ007
    return Event(int(event_id), group, timestamp, msg[: fold.start()], int(fold[1]), since)


def parse_event_log(xml: str | bytes) -> ParsedEventLog:
    """Parse the ``<DeviceLog>`` document.

    An entry that fails validation is not emitted with a guessed value; it is
    listed in ``rejected`` instead. A document that is not a ``DeviceLog`` raises
    :class:`EventLogParseError`.
    """
    try:
        root = ElementTree.fromstring(xml)
    except (ElementTree.ParseError, DefusedXmlException) as err:
        msg = f"event log is not valid XML: {err}"
        raise EventLogParseError(msg) from err
    if root.tag != "DeviceLog":
        msg = f"unexpected root element {root.tag!r}"
        raise EventLogParseError(msg)

    events: list[Event] = []
    rejected: list[str] = []
    for index, node in enumerate(root.findall("Event")):
        try:
            events.append(_parse_event(node))
        except ValueError as err:
            rejected.append(f"entry {index}: {err}")
    return ParsedEventLog(events, rejected)


def with_offset(local: datetime, utc_offset: timedelta | None) -> datetime:
    """Attach the UTC offset to a box-local time; left naive when the offset is unknown."""
    return local if utc_offset is None else local.replace(tzinfo=timezone(utc_offset))


def event_record(  # noqa: PLR0913
    event: Event,
    event_time: datetime,
    *,
    repeat: int,
    estimated: bool,
    device: str,
    host: str,
    serial: str | None,
) -> dict[str, Any]:
    """Build the output record for ``repeat`` new occurrences of one row.

    ``event_time`` is the time of the latest occurrence, ideally with its UTC
    offset; ``estimated`` says it was derived from the uptime because the box
    logged the row before syncing its clock. ``box_time`` is the time as the box
    showed it.
    """
    return {
        "event_time": event_time.isoformat(),
        "time_estimated": estimated,
        "box_time": event.timestamp.strftime(_BOX_TIME_FORMAT),
        "device": device,
        "host": host,
        "serial": serial,
        "group": event.group,
        "id": event.id,
        "msg": _TOKEN_RE.sub("q=<redacted>", event.msg),
        "repeat": repeat,
    }


# --- POSIX TZ rule (``CET-1CEST,M3.5.0,M10.5.0/3``) -------------------------------------------

_NAME = r"(?:[A-Za-z]{3,}|<[A-Za-z0-9+-]+>)"
_OFFSET = r"[+-]?(?:2[0-3]|[01]?[0-9])(?::[0-9]{2}(?::[0-9]{2})?)?"
_RULE = r"M(1[0-2]|[1-9])\.([1-5])\.([0-6])(?:/([+-]?[0-9]{1,3}(?::[0-9]{2}(?::[0-9]{2})?)?))?"
_TZ_RE = re.compile(
    rf"(?P<std>{_NAME})(?P<std_off>{_OFFSET})"
    rf"(?:(?P<dst>{_NAME})(?P<dst_off>{_OFFSET})?"
    rf",(?P<start>{_RULE}),(?P<end>{_RULE}))?"
)
_DEFAULT_SWITCH_TIME = timedelta(hours=2)


def _hms(text: str) -> timedelta:
    sign = -1 if text.startswith("-") else 1
    parts = [int(p) for p in text.lstrip("+-").split(":")]
    parts += [0] * (3 - len(parts))
    return sign * timedelta(hours=parts[0], minutes=parts[1], seconds=parts[2])


def _switch_wall_time(year: int, rule: str) -> datetime:
    """Local wall-clock time at which an ``Mm.w.d[/time]`` rule switches in ``year``."""
    match = re.fullmatch(_RULE, rule)
    if match is None:  # pragma: no cover - checked by the caller's regex
        raise ValueError(rule)
    month, week, weekday = int(match[1]), int(match[2]), int(match[3])
    first_weekday = (datetime(year, month, 1).weekday() + 1) % 7  # noqa: DTZ001
    day = 1 + (weekday - first_weekday) % 7 + (week - 1) * 7
    if day > calendar.monthrange(year, month)[1]:
        day -= 7
    switch = _hms(match[4]) if match[4] else _DEFAULT_SWITCH_TIME
    return datetime(year, month, day) + switch  # noqa: DTZ001


def resolve_utc_offset(
    local: datetime, tz_rule: str | None, fallback: timedelta | None
) -> timedelta | None:
    """UTC offset in force at the box-local wall-clock time ``local``.

    ``tz_rule`` is the POSIX TZ string the box reports as ``NewLocalTimeZoneName``,
    in the ``Mm.w.d`` form (``zoneinfo`` cannot read these). ``fallback`` (the
    current offset) is returned when the rule is missing or does not parse. A time
    in the repeated hour at the end of DST is resolved to the first occurrence (the
    DST offset); a time skipped at the start of DST gets the standard-time offset.
    """
    match = _TZ_RE.fullmatch(tz_rule or "")
    if match is None:
        return fallback
    # POSIX offsets count west of UTC, so the sign is inverted.
    std_offset = -_hms(match["std_off"])
    if match["dst"] is None:
        return std_offset
    dst_offset = -_hms(match["dst_off"]) if match["dst_off"] else std_offset + timedelta(hours=1)

    # Transition instants as naive UTC: the start is read on the standard-time clock,
    # the end on the DST clock.
    start = _switch_wall_time(local.year, match["start"]) - std_offset
    end = _switch_wall_time(local.year, match["end"]) - dst_offset
    as_dst = local - dst_offset
    is_dst = start <= as_dst < end if start < end else as_dst >= start or as_dst < end
    return dst_offset if is_dst else std_offset


# Copyright 2026 Rafael Kansy and Patrick Dreker <patrick@dreker.de>
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
