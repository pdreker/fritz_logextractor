"""Fetch the event log of one device and return the entries not emitted before."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from attrs import define
from fritzconnection import FritzConnection  # type: ignore[import]
from fritzconnection.core.exceptions import (  # type: ignore[import]
    FritzActionError,
    FritzConnectionException,
    FritzServiceError,
)
from requests.exceptions import RequestException

from fritzlogextractor.capture import SnapshotCapture
from fritzlogextractor.eventlog import (
    PRE_SYNC_EPOCH,
    EventLogParseError,
    ParsedEventLog,
    event_record,
    parse_event_log,
    resolve_utc_offset,
    with_offset,
)
from fritzlogextractor.state import StateFile
from fritzlogextractor.tracker import EventLogTracker, NewEntry

if TYPE_CHECKING:
    from fritzlogextractor.config import DeviceConfig

logger = logging.getLogger("fritzlogextractor.extractor")

_MAX_REJECT_REPR = 80


class ExtractionError(Exception):
    """The log of a device could not be read this round; the text is safe to log."""


def _password(config: DeviceConfig) -> str:
    if config.password_file is not None:
        return Path(config.password_file).read_text().strip()
    return config.password or ""


class DeviceExtractor:
    """Polls one device. The connection is opened on first use and reopened after errors.

    :meth:`poll` returns the new records; :meth:`commit` records them as emitted,
    in memory and in the state file, and is called once they are written.
    """

    def __init__(
        self, config: DeviceConfig, state_dir: Path | None = None, capture_dir: Path | None = None
    ) -> None:
        self.config = config
        self.name = config.name
        self.tracker = EventLogTracker()
        self.state = StateFile(state_dir, config.hostname) if state_dir else None
        self.capture = SnapshotCapture(capture_dir, self.name) if capture_dir else None
        self._fc: FritzConnection | None = None
        self._serial: str | None = None
        self._state_loaded = False
        self._state_failing = False
        self._pending: _Snapshot | None = None
        self._rejected_seen: set[str] = set()

    def _connection(self) -> FritzConnection:
        if self._fc is None:
            self._fc = FritzConnection(
                address=self.config.hostname,
                user=self.config.username,
                password=_password(self.config),
                timeout=self.config.connection_timeout,
                use_tls=self.config.use_tls,
            )
        return self._fc

    def _fetch(self, fc: FritzConnection) -> bytes:
        # Same request as fritzconnection's FritzStatus.get_avm_device_log(), but
        # the raw body is kept, so the strict parser in eventlog.py can check it.
        path = fc.call_action("DeviceInfo1", "X_AVM-DE_GetDeviceLogPath")["NewDeviceLogPath"]
        response = fc.session.get(
            f"{fc.address}:{fc.port}{path}", timeout=self.config.connection_timeout
        )
        if not response.ok:
            msg = f"HTTP {response.status_code}"
            raise ExtractionError(msg)
        return response.content

    def _time_info(
        self, fc: FritzConnection
    ) -> tuple[str | None, timedelta | None, datetime | None]:
        """Time zone rule, current UTC offset and current box-local time (``None`` before sync)."""
        try:
            info = fc.call_action("Time1", "GetInfo")
        except FritzServiceError, FritzActionError, FritzConnectionException, RequestException:
            logger.debug("No time zone from %s, event times have no offset", self.name)
            return None, None, None
        # An ISO string with offset normally; right after a reboot, before the clock is
        # synced, fritzconnection hands over None or a naive datetime instead.
        value = info.get("NewCurrentLocalTime")
        if isinstance(value, str):
            try:
                value = datetime.fromisoformat(value)
            except ValueError:
                value = None
        if not isinstance(value, datetime):
            return info.get("NewLocalTimeZoneName"), None, None
        synced = value.year != PRE_SYNC_EPOCH.year
        return (
            info.get("NewLocalTimeZoneName"),
            value.utcoffset(),
            value.replace(tzinfo=None) if synced else None,
        )

    def _capture(self, body: bytes, info: dict[str, Any]) -> None:
        if self.capture is None:
            return
        try:
            self.capture.record(body, info)
        except OSError as e:
            logger.warning("%s: could not store the snapshot (%s)", self.name, e)

    def _capture_failure(self, error: str) -> None:
        if self.capture is None:
            return
        try:
            self.capture.record_failure(error)
        except OSError as e:
            logger.warning("%s: could not log the failed poll (%s)", self.name, e)

    def _load_state(self, serial: str | None) -> None:
        # Once per run: later polls go on from the tracker in memory.
        if self._state_loaded or self.state is None:
            return
        self._state_loaded = True
        tracker = self.state.load(serial)
        if tracker is not None:
            self.tracker = tracker

    def poll(self) -> list[dict[str, Any]]:
        """Return the records of all occurrences not emitted before, oldest first."""
        try:
            fc = self._connection()
            body = self._fetch(fc)
            info = fc.call_action("DeviceInfo1", "GetInfo")
            uptime = int(info["NewUpTime"])
        except (FritzConnectionException, RequestException, KeyError, TypeError, ValueError) as e:
            self._fc = None
            msg = f"could not read the event log ({type(e).__name__})"
            self._capture_failure(msg)
            raise ExtractionError(msg) from None
        except ExtractionError as e:
            self._capture_failure(str(e))
            raise
        now = datetime.now(UTC)

        # Capture before parsing, so a body the parser rejects is kept too.
        self._capture(body, info)
        try:
            parsed = parse_event_log(body)
        except EventLogParseError as e:
            msg = f"could not parse the event log ({e})"
            raise ExtractionError(msg) from None

        self._serial = info.get("NewSerialNumber")
        self._load_state(self._serial)
        boot = now - timedelta(seconds=uptime)
        tz_rule, current_offset, box_now = self._time_info(fc)
        box_boot = box_now - timedelta(seconds=uptime) if box_now else None
        if self.tracker.boot is not None and not self.tracker.is_same_boot(boot):
            logger.info("%s has restarted, its whole log is new", self.name)

        records = [
            self._record(entry, boot, box_boot, tz_rule, current_offset)
            for entry in self.tracker.new_entries(parsed.events, boot, box_boot)
        ]
        self._pending = _Snapshot(parsed, boot, box_boot)
        return records

    def _record(
        self,
        entry: NewEntry,
        boot: datetime,
        box_boot: datetime | None,
        tz_rule: str | None,
        current_offset: timedelta | None,
    ) -> dict[str, Any]:
        event = entry.event
        if not event.pre_sync:
            local = event.timestamp
            event_time = with_offset(local, resolve_utc_offset(local, tz_rule, current_offset))
        elif box_boot is not None:
            local = box_boot + (event.timestamp - PRE_SYNC_EPOCH)
            event_time = with_offset(local, resolve_utc_offset(local, tz_rule, current_offset))
        else:
            # The box does not know the time yet: estimate it on our clock, in UTC.
            event_time = boot + (event.timestamp - PRE_SYNC_EPOCH)
        return event_record(
            event,
            event_time,
            repeat=entry.repeat,
            estimated=event.pre_sync,
            device=self.name,
            host=self.config.hostname,
            serial=self._serial,
        )

    def commit(self) -> None:
        """Record the snapshot of the last :meth:`poll` as emitted."""
        if self._pending is None:
            return
        parsed, boot, box_boot = self._pending.parsed, self._pending.boot, self._pending.box_boot
        self._pending = None
        self.tracker.commit(parsed.events, boot, box_boot)
        self._warn_rejected(parsed)
        if self.state is None:
            return
        try:
            self.state.save(self._serial, self.tracker)
        except OSError as e:
            # Warn once per failing streak, not on every poll.
            if not self._state_failing:
                logger.warning("%s: could not write the state file (%s)", self.name, e)
            self._state_failing = True
        else:
            self._state_failing = False

    def _warn_rejected(self, parsed: ParsedEventLog) -> None:
        rejected = set(parsed.rejected)
        if rejected - self._rejected_seen:
            logger.warning(
                "%s: skipping %d event log entries that failed validation (first: %s)",
                self.name,
                len(rejected),
                min(rejected)[:_MAX_REJECT_REPR],
            )
        self._rejected_seen = rejected


@define(frozen=True)
class _Snapshot:
    parsed: ParsedEventLog
    boot: datetime
    box_boot: datetime | None


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
