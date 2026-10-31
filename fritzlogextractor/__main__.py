from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
from pathlib import Path

from fritzlogextractor.config import ConfigError, ExtractorConfig, get_config
from fritzlogextractor.extractor import DeviceExtractor, ExtractionError
from fritzlogextractor.logs import LOG_FORMATS, setup_logging

from . import __version__

logger = logging.getLogger("fritzlogextractor")


def parse_cmdline(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract the event log of AVM Fritz! devices as structured log lines."
    )
    parser.add_argument(
        "--config", type=str, help="Path to config file (default: read from environment)"
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Override the log level from the configuration",
    )
    parser.add_argument(
        "--log-format",
        choices=LOG_FORMATS,
        default="json",
        help="Format of the tool's own log lines on stderr: json (default, marked "
        '"kind": "log") or text, easier to read in a terminal',
    )
    parser.add_argument(
        "--once", action="store_true", help="Read each device once, print its log and exit"
    )
    parser.add_argument(
        "--capture",
        type=Path,
        metavar="DIR",
        help="Also store each raw event log snapshot below DIR, unredacted (for test data)",
    )
    parser.add_argument("--version", action="version", version=__version__)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_cmdline(argv)
    # Diagnostics go to stderr; stdout is reserved for the extracted events.
    setup_logging(args.log_level or "INFO", args.log_format)

    try:
        config = get_config(args.config)
    except ConfigError, ValueError, TypeError:
        logger.exception("Error while reading config:")
        sys.exit(1)

    logging.getLogger().setLevel(args.log_level or config.log_level)
    logger.info(
        "Configured %d device(s), polling every %ds", len(config.devices), config.poll_interval
    )
    if args.capture:
        logger.warning("Storing unredacted event log snapshots below %s", args.capture)
    run(config, once=args.once, capture_dir=args.capture)


def poll_all(extractors: list[DeviceExtractor]) -> None:
    """Poll every device once and print new entries as JSON lines on stdout."""
    for extractor in extractors:
        try:
            records = extractor.poll()
            for record in records:
                sys.stdout.write(json.dumps(record, ensure_ascii=False) + "\n")
            sys.stdout.flush()
            # Only now that the records are out: a failure before this loses nothing.
            extractor.commit()
        except ExtractionError as e:
            logger.warning("%s: %s, will retry", extractor.name, e)
            continue
        except Exception:
            # An answer nobody expected must not stop the other devices or later polls.
            # The tracker commits only after a successful poll, so nothing is lost.
            logger.exception("%s: unexpected error, will retry", extractor.name)
            continue


def run(
    config: ExtractorConfig,
    *,
    once: bool = False,
    stop: threading.Event | None = None,
    capture_dir: Path | None = None,
) -> None:
    extractors = [
        DeviceExtractor(device, Path(config.state_dir), capture_dir) for device in config.devices
    ]
    stop = stop or threading.Event()
    while True:
        poll_all(extractors)
        if once or stop.wait(config.poll_interval):
            return


if __name__ == "__main__":
    main()

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
