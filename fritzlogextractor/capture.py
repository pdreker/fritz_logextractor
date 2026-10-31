"""Raw snapshots of the event log, to study how a box changes its log over time.

Each device gets a directory below the capture directory. Every poll appends one
line to ``polls.jsonl`` (poll time, uptime, model, firmware, SHA-256 of the
body, or the error if the log could not be read); the body itself is stored once
per distinct content as ``<sha256 prefix>.xml``. A box whose log did not change
thus costs one line per poll, and a restart of the tool does not store the same
snapshot again.

The snapshots are stored as the box sent them: they hold addresses, host and user
names and DynDNS tokens. Files are created readable by the owner only.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from fritzlogextractor.files import safe_file_name, write_private

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["SnapshotCapture"]

_DIGEST_PREFIX = 16


class SnapshotCapture:
    """Stores the snapshots of one device below ``directory``."""

    def __init__(self, directory: Path, device: str) -> None:
        self.directory = directory / safe_file_name(device)

    def record(self, body: bytes, info: dict[str, Any]) -> None:
        """Store ``body`` unless already stored and log the poll; ``info`` is DeviceInfo:GetInfo."""
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        digest = hashlib.sha256(body).hexdigest()
        snapshot = self.directory / f"{digest[:_DIGEST_PREFIX]}.xml"
        if not snapshot.exists():
            write_private(snapshot, body)

        self._log_poll(
            {
                "uptime": info.get("NewUpTime"),
                "model": info.get("NewModelName"),
                "firmware": info.get("NewSoftwareVersion"),
                "sha256": digest,
                "file": snapshot.name,
            }
        )

    def record_failure(self, error: str) -> None:
        """Log a poll that got no event log, so gaps (e.g. a reboot) show in the data."""
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._log_poll({"error": error})

    def _log_poll(self, fields: dict[str, Any]) -> None:
        line = {"poll_time": datetime.now(UTC).isoformat(timespec="seconds"), **fields}
        polls = self.directory / "polls.jsonl"
        polls.touch(mode=0o600)
        with polls.open("a") as f:
            f.write(json.dumps(line) + "\n")


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
