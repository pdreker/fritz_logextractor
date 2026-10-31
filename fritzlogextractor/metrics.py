"""Metrics about the tool's own work, for Prometheus.

They answer whether the tool keeps up with each device, not what the box logged: that is in
the events, and a log stack can count those. Served only when ``metrics_port`` is set; the
default registry also brings prometheus_client's process and Python metrics.
"""

from __future__ import annotations

import logging

from prometheus_client import (
    Counter,
    Gauge,
    Info,
    disable_created_metrics,
    start_http_server,
)

from fritzlogextractor import __version__

__all__ = [
    "BOX_REBOOTS",
    "EVENTS",
    "LAST_SUCCESS",
    "LOG_ROWS",
    "POLLS",
    "POLL_ERRORS",
    "POLL_ERROR_REASONS",
    "REBOOT_UNWATCHED",
    "REJECTED_ROWS",
    "STATE_WRITE_ERRORS",
    "init_device",
    "serve",
]

logger = logging.getLogger("fritzlogextractor.metrics")

# Why a poll failed; the label values of POLL_ERRORS.
POLL_ERROR_REASONS = ("password", "auth", "connection", "response", "parse", "unexpected")

_PREFIX = "fritzlogextractor"

# A *_created series per counter only doubles the series; nothing here needs it.
disable_created_metrics()

BUILD_INFO = Info(f"{_PREFIX}_build", "Version of fritz_logextractor")
BUILD_INFO.info({"version": __version__})

POLLS = Counter(f"{_PREFIX}_polls", "Polls of the device, successful or not", ["device"])
POLL_ERRORS = Counter(
    f"{_PREFIX}_poll_errors", "Polls that failed, by reason", ["device", "reason"]
)
LAST_SUCCESS = Gauge(
    f"{_PREFIX}_last_success_timestamp_seconds",
    "When the last poll succeeded and its events were written (Unix time)",
    ["device"],
)
EVENTS = Counter(f"{_PREFIX}_events", "Event lines written to stdout", ["device"])
LOG_ROWS = Gauge(
    f"{_PREFIX}_log_rows", "Rows in the device's event log at the last poll", ["device"]
)
REJECTED_ROWS = Gauge(
    f"{_PREFIX}_rejected_rows",
    "Rows of the last poll skipped because they failed validation; they are not emitted",
    ["device"],
)
BOX_REBOOTS = Counter(
    f"{_PREFIX}_box_reboots", "Reboots of the device the tool noticed", ["device"]
)
REBOOT_UNWATCHED = Gauge(
    f"{_PREFIX}_reboot_unwatched_seconds",
    "Time between the last poll before the device's latest reboot and that reboot; "
    "entries the device logged in it are lost",
    ["device"],
)
STATE_WRITE_ERRORS = Counter(
    f"{_PREFIX}_state_write_errors", "Failed writes of the state file", ["device"]
)


def init_device(device: str) -> None:
    """Create the series of a device at zero, so rates and alerts work from the start."""
    POLLS.labels(device)
    EVENTS.labels(device)
    BOX_REBOOTS.labels(device)
    STATE_WRITE_ERRORS.labels(device)
    for reason in POLL_ERROR_REASONS:
        POLL_ERRORS.labels(device, reason)


def serve(port: int, address: str) -> None:
    """Serve ``/metrics`` from a background thread; raises ``OSError`` if the port is taken."""
    start_http_server(port, address)
    logger.info("Serving metrics on %s:%d", address, port)


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
