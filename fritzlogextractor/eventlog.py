"""Fritz!Box event log: strict parser and output records.

Adapted from fritz_exporter PR #719 by Rafael Kansy (@Pirat83), Apache-2.0:
https://github.com/pdreker/fritz_exporter/pull/719


TR-064 ``DeviceInfo:X_AVM-DE_GetDeviceLogPath`` returns the URL of the complete
log as XML (``<DeviceLog><Event><id/><group/><date/><time/><msg/></Event>...``).
Fetching it is done in :mod:`fritzlogextractor.extractor`, picking out the rows
not yet emitted in :mod:`fritzlogextractor.tracker`, the times in
:mod:`fritzlogextractor.clock`.

The box folds a message that repeats right away into the row it already has: the
row moves to the time of the latest occurrence and the message gains a suffix
like ``[3 Meldungen seit 31.10.26 03:13:58]``. The parser splits the suffix off
into :attr:`Event.count` and :attr:`Event.since`.

Every field comes from the box and may contain text typed by a third party
(for example a user name in a failed login), so fields are validated and
credentials are redacted from the message. JSON encoding takes care of escaping.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from xml.etree.ElementTree import Element

from attrs import define
from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException

from fritzlogextractor.clock import is_pre_sync

__all__ = [
    "Event",
    "EventLogParseError",
    "ParsedEventLog",
    "Source",
    "event_record",
    "parse_event_log",
    "redact",
]

_GROUP_RE = re.compile(r"[A-Za-z0-9_-]{1,32}")
_ID_RE = re.compile(r"[0-9]{1,9}")
_DATE_RE = re.compile(r"[0-9]{2}\.[0-9]{2}\.[0-9]{2}")
_TIME_RE = re.compile(r"[0-9]{2}:[0-9]{2}:[0-9]{2}")
# A DynDNS update URL in a message may carry credentials anywhere after the
# scheme, depending on the provider: ``user:pass@``, the path or the query. Only
# scheme and host stay.
_URL_RE = re.compile(
    r"\b(?P<origin>[A-Za-z][A-Za-z0-9+.-]*://)(?:[^/?#@\s]*@)?(?P<host>[^/?#@\s]*)(?P<rest>\S*)"
)
# One provider's token, also seen outside a URL.
_TOKEN_RE = re.compile(r"(?<!\w)q=[^&\s]+")
_REDACTED = "<redacted>"
# Counter of a folded row; the words between count and date depend on the box's language.
_FOLD_RE = re.compile(
    r"\s*\[([0-9]{1,9}) [^\[\]]*?([0-9]{2}\.[0-9]{2}\.[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2})\]\s*$"
)
_BOX_TIME_FORMAT = "%d.%m.%y %H:%M:%S"


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
        return is_pre_sync(self.timestamp)


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


def _redact_url(url: re.Match[str]) -> str:
    rest = f"/{_REDACTED}" if url["rest"] else ""
    return f"{url['origin']}{url['host']}{rest}"


def redact(msg: str) -> str:
    """``msg`` with credentials in URLs and DynDNS tokens replaced."""
    return _TOKEN_RE.sub(f"q={_REDACTED}", _URL_RE.sub(_redact_url, msg))


@define(frozen=True)
class Source:
    """The device a record comes from."""

    device: str
    host: str
    serial: str | None


def event_record(event: Event, event_time: datetime, repeat: int, source: Source) -> dict[str, Any]:
    """Build the output record for ``repeat`` new occurrences of one row.

    ``event_time`` is the time of the latest occurrence, ideally with its UTC
    offset; ``time_estimated`` says it was derived from the uptime because the box
    logged the row before syncing its clock. ``box_time`` is the time as the box
    showed it. ``kind`` tells events apart from the tool's own log lines.
    """
    return {
        "kind": "event",
        "event_time": event_time.isoformat(),
        "time_estimated": event.pre_sync,
        "box_time": event.timestamp.strftime(_BOX_TIME_FORMAT),
        "device": source.device,
        "host": source.host,
        "serial": source.serial,
        "group": event.group,
        "id": event.id,
        "msg": redact(event.msg),
        "repeat": repeat,
    }


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
