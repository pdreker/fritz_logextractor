"""The box's clock: times logged before it synced, time zone and UTC offset.

The box logs in local wall-clock time and does not say which offset applies. Until
it has synced its clock after a reboot, it dates its entries 1970-01-01 with the
time since boot as time of day. :class:`BoxClock` holds what one poll learned
about the clock and turns a row's time into the time of the event.

The POSIX TZ rules are adapted from fritz_exporter PR #719 by Rafael Kansy
(@Pirat83), Apache-2.0: https://github.com/pdreker/fritz_exporter/pull/719
"""

from __future__ import annotations

import calendar
import re
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from attrs import define

if TYPE_CHECKING:
    from fritzlogextractor.eventlog import Event

__all__ = [
    "PRE_SYNC_EPOCH",
    "BoxClock",
    "is_pre_sync",
    "on_box_clock",
    "resolve_utc_offset",
    "with_offset",
]

# Until a box has synced its clock after a reboot, it dates its entries on this day,
# with the time since boot as time of day.
PRE_SYNC_EPOCH = datetime(1970, 1, 1)  # noqa: DTZ001


def is_pre_sync(time: datetime) -> bool:
    """``time`` was logged or read before the box had synced its clock."""
    return time.year == PRE_SYNC_EPOCH.year


def on_box_clock(time: datetime, box_boot: datetime | None) -> datetime:
    """Put a 1970 time on the box's clock, once the box knows its boot time."""
    if box_boot is None or not is_pre_sync(time):
        return time
    return box_boot + (time - PRE_SYNC_EPOCH)


@define(frozen=True)
class BoxClock:
    """What one poll learned about the box's clock.

    ``boot`` is now - uptime on our clock (UTC) and identifies the boot.
    ``box_boot`` is the boot on the box's local clock, ``None`` while the box has
    not synced. ``tz_rule`` is the POSIX TZ rule the box reports and
    ``utc_offset`` the offset in force now; either may be unknown.
    """

    boot: datetime
    box_boot: datetime | None = None
    tz_rule: str | None = None
    utc_offset: timedelta | None = None

    def event_time(self, event: Event) -> datetime:
        """Time of the row's latest occurrence, with UTC offset where known."""
        if event.pre_sync and self.box_boot is None:
            # The box does not know the time yet: estimate it on our clock, in UTC.
            return self.boot + (event.timestamp - PRE_SYNC_EPOCH)
        local = on_box_clock(event.timestamp, self.box_boot)
        return with_offset(local, resolve_utc_offset(local, self.tz_rule, self.utc_offset))


def with_offset(local: datetime, utc_offset: timedelta | None) -> datetime:
    """Attach the UTC offset to a box-local time; left naive when the offset is unknown."""
    return local if utc_offset is None else local.replace(tzinfo=timezone(utc_offset))


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
