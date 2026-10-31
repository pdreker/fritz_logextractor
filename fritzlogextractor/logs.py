"""The tool's own log lines on stderr.

By default they are JSON objects with ``"kind": "log"``, while the events on
stdout carry ``"kind": "event"``: a log stack that gets both streams mixed can
still sort them apart on one field. A traceback goes into the same line as
``exc``, so no entry spans several lines. ``text`` is the classic format, for
reading in a terminal.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime

__all__ = ["LOG_FORMATS", "JsonFormatter", "setup_logging"]

LOG_FORMATS = ("json", "text")
_TEXT_FORMAT = "%(asctime)s %(levelname)8s %(name)s | %(message)s"


class JsonFormatter(logging.Formatter):
    """One JSON object per record."""

    def format(self, record: logging.LogRecord) -> str:
        line = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "kind": "log",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            line["exc"] = self.formatException(record.exc_info)
        return json.dumps(line, ensure_ascii=False)


def setup_logging(level: str, log_format: str = "json") -> None:
    """Send all log records, also those of libraries, to stderr in ``log_format``."""
    handler = logging.StreamHandler(sys.stderr)
    formatter = JsonFormatter() if log_format == "json" else logging.Formatter(_TEXT_FORMAT)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)


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
