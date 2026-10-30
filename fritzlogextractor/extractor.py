"""Fetch the event log of one device and return the entries not emitted before."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fritzconnection import FritzConnection  # type: ignore[import]
from fritzconnection.core.exceptions import (  # type: ignore[import]
    FritzActionError,
    FritzConnectionException,
    FritzServiceError,
)
from requests.exceptions import RequestException

from fritzlogextractor.capture import SnapshotCapture
from fritzlogextractor.eventlog import (
    EventLogParseError,
    EventLogTracker,
    event_record,
    parse_event_log,
    resolve_utc_offset,
)

if TYPE_CHECKING:
    from fritzlogextractor.config import DeviceConfig

logger = logging.getLogger("fritzlogextractor.extractor")


class ExtractionError(Exception):
    """The log of a device could not be read this round; the text is safe to log."""


def _password(config: DeviceConfig) -> str:
    if config.password_file is not None:
        return Path(config.password_file).read_text().strip()
    return config.password or ""


class DeviceExtractor:
    """Polls one device. The connection is opened on first use and reopened after errors.

    Duplicate handling lives only in memory: after a restart of the tool, the
    whole buffer of the box is emitted once more.
    """

    def __init__(self, config: DeviceConfig, capture_dir: Path | None = None) -> None:
        self.config = config
        self.name = config.name or config.hostname
        self.tracker = EventLogTracker()
        self.capture = SnapshotCapture(capture_dir, self.name) if capture_dir else None
        self._fc: FritzConnection | None = None

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

    def _time_zone(self, fc: FritzConnection) -> tuple[str | None, timedelta | None]:
        try:
            info = fc.call_action("Time1", "GetInfo")
        except FritzServiceError, FritzActionError, FritzConnectionException, RequestException:
            logger.debug("No time zone from %s, event times have no offset", self.name)
            return None, None
        try:
            current = datetime.fromisoformat(info["NewCurrentLocalTime"]).utcoffset()
        except KeyError, ValueError:
            current = None
        return info.get("NewLocalTimeZoneName"), current

    def _capture(self, fc: FritzConnection, body: bytes) -> None:
        if self.capture is None:
            return
        try:
            info = fc.call_action("DeviceInfo1", "GetInfo")
        except FritzServiceError, FritzActionError, FritzConnectionException, RequestException:
            logger.debug("No device info from %s, capturing without uptime", self.name)
            info = {}
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

    def poll(self) -> list[dict[str, Any]]:
        """Return the records of all entries not emitted before, oldest first."""
        try:
            fc = self._connection()
            body = self._fetch(fc)
        except (FritzConnectionException, RequestException, KeyError) as e:
            self._fc = None
            msg = f"could not read the event log ({type(e).__name__})"
            self._capture_failure(msg)
            raise ExtractionError(msg) from None
        except ExtractionError as e:
            self._capture_failure(str(e))
            raise

        # Capture before parsing, so a body the parser rejects is kept too.
        self._capture(fc, body)
        try:
            parsed = parse_event_log(body)
        except EventLogParseError as e:
            msg = f"could not parse the event log ({e})"
            raise ExtractionError(msg) from None

        new_events = self.tracker.new_events(parsed)
        records: list[dict[str, Any]] = []
        if new_events:
            tz_rule, current_offset = self._time_zone(fc)
            records = [
                event_record(
                    event,
                    resolve_utc_offset(event.timestamp, tz_rule, current_offset),
                    self.name,
                    self.config.hostname,
                )
                for event in new_events
            ]
        self.tracker.commit(parsed)
        return records


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
