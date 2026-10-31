"""Fetch the event log of one device and return the entries not emitted before."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from attrs import define
from fritzconnection import FritzConnection  # type: ignore[import]
from fritzconnection.core.exceptions import (  # type: ignore[import]
    FritzActionError,
    FritzAuthorizationError,
    FritzConnectionException,
    FritzServiceError,
)
from requests.exceptions import RequestException

from fritzlogextractor import metrics
from fritzlogextractor.capture import SnapshotCapture
from fritzlogextractor.clock import BoxClock, is_pre_sync
from fritzlogextractor.config import ConfigError
from fritzlogextractor.eventlog import (
    EventLogParseError,
    ParsedEventLog,
    Source,
    event_record,
    parse_event_log,
)
from fritzlogextractor.state import StateFile
from fritzlogextractor.tracker import EventLogTracker

if TYPE_CHECKING:
    from pathlib import Path

    from fritzlogextractor.config import DeviceConfig

logger = logging.getLogger("fritzlogextractor.extractor")

_MAX_REJECT_REPR = 80
# A full log is about 125 kB on a 7690.
_MAX_LOG_BYTES = 8 * 1024 * 1024
_CHUNK_BYTES = 64 * 1024
# Errors of the TR-064 connection; the next poll opens a new one.
_CONNECTION_ERRORS = (FritzConnectionException, RequestException)


def _read_error_reason(error: Exception) -> str:
    if isinstance(error, FritzAuthorizationError):
        return "auth"
    if isinstance(error, _CONNECTION_ERRORS):
        return "connection"
    # A required field missing or malformed in the box's answer.
    return "response"


class ExtractionError(Exception):
    """The log of a device could not be read this round; the text is safe to log.

    ``reason`` is one of :data:`metrics.POLL_ERROR_REASONS`.
    """

    def __init__(self, msg: str, reason: str = "response") -> None:
        super().__init__(msg)
        self.reason = reason


@define(frozen=True)
class _Snapshot:
    """What :meth:`DeviceExtractor.commit` records as emitted."""

    parsed: ParsedEventLog
    clock: BoxClock
    now: datetime


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
        self._tracker = EventLogTracker()
        self._state = StateFile(state_dir, config.hostname) if state_dir else None
        self._capture = SnapshotCapture(capture_dir, self.name) if capture_dir else None
        self._fc: FritzConnection | None = None
        self._serial: str | None = None
        self._state_loaded = False
        self._state_failing = False
        self._pending: _Snapshot | None = None
        self._rejected_seen: set[str] = set()
        metrics.init_device(self.name)

    def poll(self) -> list[dict[str, Any]]:
        """Return the records of all occurrences not emitted before, oldest first."""
        body, info, uptime = self._read()
        now = datetime.now(UTC)
        self._store_snapshot(body, info)
        parsed = self._parse(body)
        self._serial = info.get("NewSerialNumber")
        self._load_state()
        clock = self._clock(now, uptime)
        if self._tracker.boot is not None and not self._tracker.is_same_boot(clock.boot):
            logger.info("%s has restarted, its whole log is new", self.name)

        source = Source(self.name, self.config.hostname, self._serial)
        new = self._tracker.new_entries(parsed.events, clock.boot, clock.box_boot)
        self._pending = _Snapshot(parsed, clock, now)
        return [event_record(e.event, clock.event_time(e.event), e.repeat, source) for e in new]

    def commit(self) -> None:
        """Record the snapshot of the last :meth:`poll` as emitted."""
        if self._pending is None:
            return
        parsed, clock, now = self._pending.parsed, self._pending.clock, self._pending.now
        self._pending = None
        self._count_reboot(clock.boot)
        self._tracker.commit(parsed.events, clock.boot, clock.box_boot, now)
        metrics.LOG_ROWS.labels(self.name).set(len(parsed.events))
        metrics.REJECTED_ROWS.labels(self.name).set(len(parsed.rejected))
        self._warn_rejected(parsed)
        self._save_state()

    # --- Reading from the box ----------------------------------------------------------------

    def _read(self) -> tuple[bytes, dict[str, Any], int]:
        """The raw log, ``DeviceInfo1:GetInfo`` and the uptime from it; all are required."""
        try:
            fc = self._connection()
            body = self._fetch(fc)
            info = fc.call_action("DeviceInfo1", "GetInfo")
            uptime = int(info["NewUpTime"])
        except ExtractionError as e:
            self._store_failure(str(e))
            raise
        except (*_CONNECTION_ERRORS, KeyError, TypeError, ValueError) as e:
            self._fc = None
            msg = f"could not read the event log ({type(e).__name__})"
            self._store_failure(msg)
            raise ExtractionError(msg, _read_error_reason(e)) from None
        return body, info, uptime

    def _connection(self) -> FritzConnection:
        if self._fc is None:
            try:
                password = self.config.read_password()
            except (OSError, ConfigError) as e:
                msg = f"could not read the password ({e})"
                raise ExtractionError(msg, "password") from None
            self._fc = FritzConnection(
                address=self.config.hostname,
                user=self.config.username,
                password=password,
                timeout=self.config.connection_timeout,
                use_tls=self.config.use_tls,
            )
        return self._fc

    def _fetch(self, fc: FritzConnection) -> bytes:
        # Same request as fritzconnection's FritzStatus.get_avm_device_log(), but
        # the raw body is kept, so the strict parser in eventlog.py can check it.
        path = fc.call_action("DeviceInfo1", "X_AVM-DE_GetDeviceLogPath")["NewDeviceLogPath"]
        # Appended to our address: anything but a plain path could send the request,
        # with the session, to another host.
        if not isinstance(path, str) or not path.startswith("/") or path.startswith("//"):
            msg = "unexpected event log path"
            raise ExtractionError(msg)
        with fc.session.get(
            f"{fc.address}:{fc.port}{path}", timeout=self.config.connection_timeout, stream=True
        ) as response:
            if not response.ok:
                msg = f"HTTP {response.status_code}"
                raise ExtractionError(msg)
            body = bytearray()
            for chunk in response.iter_content(chunk_size=_CHUNK_BYTES):
                body += chunk
                if len(body) > _MAX_LOG_BYTES:
                    msg = f"event log larger than {_MAX_LOG_BYTES} bytes"
                    raise ExtractionError(msg)
        return bytes(body)

    def _parse(self, body: bytes) -> ParsedEventLog:
        try:
            return parse_event_log(body)
        except EventLogParseError as e:
            msg = f"could not parse the event log ({e})"
            raise ExtractionError(msg, "parse") from None

    def _clock(self, now: datetime, uptime: int) -> BoxClock:
        """Boot time on both clocks, time zone rule and current offset from ``Time1:GetInfo``."""
        boot = now - timedelta(seconds=uptime)
        try:
            info = self._connection().call_action("Time1", "GetInfo")
        except (FritzServiceError, FritzActionError, *_CONNECTION_ERRORS) as e:
            if isinstance(e, _CONNECTION_ERRORS):
                self._fc = None
            logger.debug("No time zone from %s, event times have no offset", self.name)
            return BoxClock(boot)
        tz_rule = info.get("NewLocalTimeZoneName")
        # An ISO string with offset normally; right after a reboot, before the clock is
        # synced, fritzconnection hands over None or a naive datetime instead.
        box_now = info.get("NewCurrentLocalTime")
        if isinstance(box_now, str):
            try:
                box_now = datetime.fromisoformat(box_now)
            except ValueError:
                box_now = None
        if not isinstance(box_now, datetime):
            return BoxClock(boot, tz_rule=tz_rule)
        if is_pre_sync(box_now):
            return BoxClock(boot, tz_rule=tz_rule, utc_offset=box_now.utcoffset())
        box_boot = box_now.replace(tzinfo=None) - timedelta(seconds=uptime)
        return BoxClock(boot, box_boot, tz_rule, box_now.utcoffset())

    # --- State, capture, warnings ----------------------------------------------------------

    def _count_reboot(self, boot: datetime) -> None:
        """At commit, so a reboot seen by a poll whose records were not written counts once."""
        if self._tracker.boot is None or self._tracker.is_same_boot(boot):
            return
        metrics.BOX_REBOOTS.labels(self.name).inc()
        if self._tracker.polled is not None:
            unwatched = max((boot - self._tracker.polled).total_seconds(), 0.0)
            metrics.REBOOT_UNWATCHED.labels(self.name).set(unwatched)

    def _load_state(self) -> None:
        # Once per run: later polls go on from the tracker in memory.
        if self._state_loaded or self._state is None:
            return
        self._state_loaded = True
        tracker = self._state.load(self._serial)
        if tracker is not None:
            self._tracker = tracker

    def _save_state(self) -> None:
        if self._state is None:
            return
        try:
            self._state.save(self._serial, self._tracker)
        except OSError as e:
            # Warn once per failing streak, not on every poll.
            if not self._state_failing:
                logger.warning("%s: could not write the state file (%s)", self.name, e)
            self._state_failing = True
            metrics.STATE_WRITE_ERRORS.labels(self.name).inc()
        else:
            self._state_failing = False

    def _store_snapshot(self, body: bytes, info: dict[str, Any]) -> None:
        # Before parsing, so a body the parser rejects is kept too.
        if self._capture is None:
            return
        try:
            self._capture.record(body, info)
        except OSError as e:
            logger.warning("%s: could not store the snapshot (%s)", self.name, e)

    def _store_failure(self, error: str) -> None:
        if self._capture is None:
            return
        try:
            self._capture.record_failure(error)
        except OSError as e:
            logger.warning("%s: could not log the failed poll (%s)", self.name, e)

    def _warn_rejected(self, parsed: ParsedEventLog) -> None:
        rejected = set(parsed.rejected)
        if rejected - self._rejected_seen:
            logger.warning(
                "%s: skipping %d event log entries that failed validation (first: %s)",
                self.name,
                len(rejected),
                parsed.rejected[0][:_MAX_REJECT_REPR],
            )
        self._rejected_seen = rejected


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
