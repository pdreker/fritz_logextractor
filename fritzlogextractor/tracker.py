# Copyright 2026 Patrick Dreker <patrick@dreker.de>
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
"""Pick out the event log entries not emitted before.

The box returns its whole log on every request, and the log changes in ways
that rule out simple approaches (seen on a 7690 with FRITZ!OS 8.50):

- A message that repeats right away is folded into its row: the count rises and
  the row moves to the time of the latest occurrence, while the time of the first
  occurrence stays put.
- Rows a mesh repeater forwards to the box are sorted in by their time, not added
  on top, so "everything above the newest known row" misses some.
- Rows logged before the box synced its clock are dated 1970-01-01 with the time
  since boot. On sync, the box rewrites them to boot time + that offset, and from
  then on computes them afresh on every request, so they jitter by a second.

A row is therefore known by its message (id, group, text without counter) and
the time of its first occurrence, matched with a tolerance and with 1970 times
put on the box's clock first. A matched row whose count rose yields the
difference as new occurrences. A reboot empties the log; it shows as a new boot
time (now - uptime), and then every row is new.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from attrs import define

from fritzlogextractor.eventlog import PRE_SYNC_EPOCH, Event

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = ["EventLogTracker", "NewEntry"]

# Rows jitter by a second between requests; two polls may round in opposite ways.
TIME_TOLERANCE = timedelta(seconds=2)
# now - uptime stayed within a second over two weeks; a reboot moves it by minutes.
REBOOT_TOLERANCE = timedelta(seconds=60)
STATE_VERSION = 1

type _Key = tuple[int, str, str]


@define(frozen=True)
class _Seen:
    key: _Key
    first: datetime
    count: int


@define(frozen=True)
class NewEntry:
    """``repeat`` occurrences of ``event`` not emitted before."""

    event: Event
    repeat: int


def _key(event: Event) -> _Key:
    return (event.id, event.group, event.msg)


def _on_box_clock(time: datetime, box_boot: datetime | None) -> datetime:
    """Put a 1970 time on the box's clock, once the box knows its boot time."""
    if box_boot is None or time.year != PRE_SYNC_EPOCH.year:
        return time
    return box_boot + (time - PRE_SYNC_EPOCH)


class EventLogTracker:
    """Remembers the rows of the last emitted snapshot of one device.

    :meth:`new_entries` does not change the state; :meth:`commit` is called only
    after the entries were written, so a failure in between loses nothing.
    """

    def __init__(self) -> None:
        self.boot: datetime | None = None
        self._seen: list[_Seen] = []

    def is_same_boot(self, boot: datetime) -> bool:
        return self.boot is not None and abs(boot - self.boot) <= REBOOT_TOLERANCE

    def new_entries(
        self, events: Iterable[Event], boot: datetime, box_boot: datetime | None
    ) -> list[NewEntry]:
        """Return what is new in ``events``, oldest first.

        ``boot`` is now - uptime on our clock and identifies the boot. ``box_boot``
        is the boot time on the box's local clock, ``None`` while the box has not
        synced its clock.
        """
        old: dict[_Key, list[tuple[datetime, int]]] = defaultdict(list)
        if self.is_same_boot(boot):
            for seen in self._seen:
                old[seen.key].append((_on_box_clock(seen.first, box_boot), seen.count))

        current: dict[_Key, list[Event]] = defaultdict(list)
        for event in events:
            current[_key(event)].append(event)

        new: list[NewEntry] = []
        for key, rows in current.items():
            new.extend(_match(old.get(key, []), rows, box_boot))
        return sorted(new, key=lambda n: _on_box_clock(n.event.timestamp, box_boot))

    def commit(self, events: Iterable[Event], boot: datetime, box_boot: datetime | None) -> None:
        """Record ``events`` as emitted."""
        self.boot = boot
        self._seen = [_Seen(_key(e), _on_box_clock(e.first, box_boot), e.count) for e in events]

    def to_json(self) -> dict[str, Any]:
        return {
            "version": STATE_VERSION,
            "boot": self.boot.isoformat() if self.boot else None,
            "rows": [
                [s.key[0], s.key[1], s.key[2], s.first.isoformat(), s.count] for s in self._seen
            ],
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> EventLogTracker:
        """Rebuild from :meth:`to_json`; raises ``ValueError`` on anything unexpected."""
        if data.get("version") != STATE_VERSION:
            msg = f"unknown state version {data.get('version')!r}"
            raise ValueError(msg)
        tracker = cls()
        try:
            tracker.boot = datetime.fromisoformat(data["boot"]) if data["boot"] else None
            tracker._seen = [
                _Seen((int(i), str(g), str(m)), datetime.fromisoformat(f), int(c))
                for i, g, m, f, c in data["rows"]
            ]
        except (KeyError, TypeError) as err:
            msg = f"malformed state ({type(err).__name__})"
            raise ValueError(msg) from None
        return tracker


def _match(
    old: list[tuple[datetime, int]], rows: list[Event], box_boot: datetime | None
) -> list[NewEntry]:
    """Pair the rows of one message with the known ones, both newest first."""
    old = sorted(old, reverse=True)
    rows = sorted(rows, key=lambda e: _on_box_clock(e.first, box_boot), reverse=True)
    new: list[NewEntry] = []
    i = 0
    for row in rows:
        first = _on_box_clock(row.first, box_boot)
        # Known rows newer than this one have left the log (or never come back).
        while i < len(old) and old[i][0] - first > TIME_TOLERANCE:
            i += 1
        if i < len(old) and abs(old[i][0] - first) <= TIME_TOLERANCE:
            if row.count > old[i][1]:
                new.append(NewEntry(row, row.count - old[i][1]))
            i += 1
        else:
            new.append(NewEntry(row, row.count))
    return new
