"""State file per device, so a restart of the tool does not emit the log again.

The file holds the device's serial number and the tracker's rows. Rows carry
message text from the box (addresses, host and user names), so the file is
readable by the owner only. It is replaced atomically.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from fritzlogextractor.files import safe_file_name, write_private
from fritzlogextractor.tracker import EventLogTracker

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["StateFile"]

logger = logging.getLogger("fritzlogextractor.state")


class StateFile:
    """``<state_dir>/<hostname>.json`` of one device."""

    def __init__(self, state_dir: Path, hostname: str) -> None:
        self.path = state_dir / f"{safe_file_name(hostname)}.json"

    def load(self, serial: str | None) -> EventLogTracker | None:
        """The stored tracker, or ``None`` if there is none or it belongs to another device."""
        try:
            data = json.loads(self.path.read_text())
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as err:
            logger.warning("Ignoring unreadable state file %s (%s)", self.path, err)
            return None
        if not isinstance(data, dict):
            logger.warning("Ignoring malformed state file %s", self.path)
            return None
        if data.get("serial") != serial:
            logger.info("State file %s is for another device, starting afresh", self.path)
            return None
        try:
            return EventLogTracker.from_json(data.get("tracker") or {})
        except ValueError as err:
            logger.warning("Ignoring state file %s (%s)", self.path, err)
            return None

    def save(self, serial: str | None, tracker: EventLogTracker) -> None:
        """Write the state; raises ``OSError`` if that fails."""
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        state = {"serial": serial, "tracker": tracker.to_json()}
        write_private(self.path, json.dumps(state).encode())


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
