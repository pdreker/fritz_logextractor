"""Write the Grafana dashboard in this directory.

    uv run python examples/grafana/build_dashboard.py

``fritz-logextractor.json`` shows whether the tool keeps reading each device (its metrics,
from Prometheus) and the events it read (from Loki), with filters and a search.

Edit this file, not the JSON: ``tests/test_grafana_dashboard.py`` checks that the JSON is up
to date and that it uses every metric of the tool.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent
OUTPUT = HERE / "fritz-logextractor.json"

PROM = {"type": "prometheus", "uid": "${datasource}"}
LOKI = {"type": "loki", "uid": "${loki}"}
DEV = 'device=~"$device"'
# The streams of the tool in Loki. The collector examples label them job="fritzlogextractor";
# the variable "stream" changes that for other setups.
EVENTS = '{${stream:raw}, kind="event", ' + DEV + ', group=~"$group"}'
OWN_LOG = '{${stream:raw}, kind="log", level=~"$level"}'
# The fields Loki parses from an event line; not "device", "group", "kind": they are labels.
EVENT_FIELDS = '| json id="id", msg="msg", repeat="repeat", time_estimated="time_estimated"'
# Like the alert in docs/metrics.md.
STALLED_AFTER = 600

# Same hue for the same thing in every panel, whatever else is on screen. Steps of the
# categorical palette that keep their contrast on Grafana's dark and light theme.
BLUE, ORANGE, AQUA, YELLOW, MAGENTA, VIOLET = (
    "#3987e5",
    "#d95926",
    "#199e70",
    "#c98500",
    "#d55181",
    "#9085e9",
)
GROUP_COLORS = {"sys": BLUE, "net": ORANGE, "wlan": AQUA, "fon": YELLOW, "usb": MAGENTA}
REASON_COLORS = {
    "password": BLUE,
    "auth": ORANGE,
    "connection": AQUA,
    "response": YELLOW,
    "parse": MAGENTA,
    "unexpected": VIOLET,
}

Panel = dict[str, Any]


# ---------------------------------------------------------------------------
# Building blocks
# ---------------------------------------------------------------------------


class Layout:
    """Hands out ids and grid positions, left to right, row by row."""

    def __init__(self) -> None:
        self.panels: list[Panel] = []
        self._y = 0
        self._x = 0
        self._height = 0

    def row(self, title: str) -> None:
        self._newline()
        self._add({"type": "row", "title": title, "collapsed": False, "panels": []}, 24, 1)
        self._newline()

    def add(self, panel: Panel, width: int, height: int) -> None:
        if self._x + width > 24:  # noqa: PLR2004
            self._newline()
        self._add(panel, width, height)

    def _add(self, panel: Panel, width: int, height: int) -> None:
        panel["id"] = len(self.panels) + 1
        panel["gridPos"] = {"x": self._x, "y": self._y, "w": width, "h": height}
        self.panels.append(panel)
        self._x += width
        self._height = max(self._height, height)

    def _newline(self) -> None:
        self._y += self._height
        self._x = 0
        self._height = 0


def prom(expr: str, legend: str = "{{device}}", *, now: bool = False, ref: str = "A") -> dict:
    query: dict[str, Any] = {"datasource": PROM, "expr": expr, "refId": ref}
    query["legendFormat"] = legend
    if now:
        query.update(instant=True, range=False)
    return query


def loki(expr: str, legend: str = "", *, table: bool = False, ref: str = "A") -> dict:
    query: dict[str, Any] = {"datasource": LOKI, "expr": expr, "refId": ref}
    if legend:
        query["legendFormat"] = legend
    if table:
        query.update(queryType="instant", instant=True, range=False)
    else:
        query["queryType"] = "range"
    return query


def thresholds(*steps: tuple[float | None, str]) -> dict:
    return {
        "mode": "absolute",
        "steps": [{"color": color, "value": value} for value, color in steps],
    }


def fixed_colors(colors: dict[str, str]) -> list[dict]:
    """Overrides that give each series named in ``colors`` its own color."""
    return [
        {
            "matcher": {"id": "byName", "options": name},
            "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": color}}],
        }
        for name, color in colors.items()
    ]


def stat(
    title: str,
    description: str,
    query: dict,
    *,
    unit: str = "none",
    decimals: int | None = 0,
    steps: dict | None = None,
    text_mode: str = "value_and_name",
) -> Panel:
    defaults: dict[str, Any] = {
        "unit": unit,
        "color": {"mode": "thresholds"},
        "thresholds": steps or thresholds((None, "text")),
        "noValue": "-",
    }
    if decimals is not None:
        defaults["decimals"] = decimals
    return {
        "type": "stat",
        "title": title,
        "description": description,
        "datasource": query["datasource"],
        "targets": [query],
        "fieldConfig": {"defaults": defaults, "overrides": []},
        "options": {
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "colorMode": "background" if steps else "none",
            "graphMode": "none",
            "textMode": text_mode,
            "justifyMode": "center",
            "orientation": "auto",
            "wideLayout": True,
        },
    }


def timeseries(
    title: str,
    description: str,
    queries: list[dict],
    *,
    unit: str = "none",
    bars: bool = False,
    stacked: bool = False,
    colors: dict[str, str] | None = None,
    steps: dict | None = None,
    min_interval: str | None = None,
) -> Panel:
    custom: dict[str, Any] = {
        "drawStyle": "bars" if bars else "line",
        "lineWidth": 2,
        "fillOpacity": 80 if bars else 10,
        "showPoints": "never",
        "spanNulls": False,
        "stacking": {"mode": "normal" if stacked else "none", "group": "A"},
        "axisSoftMin": 0,
    }
    defaults: dict[str, Any] = {
        "unit": unit,
        "color": {"mode": "palette-classic"},
        "custom": custom,
    }
    if steps:
        # A dashed line at the threshold, the series keep their color.
        defaults["thresholds"] = steps
        custom["thresholdsStyle"] = {"mode": "dashed"}
    panel: Panel = {
        "type": "timeseries",
        "title": title,
        "description": description,
        "datasource": queries[0]["datasource"],
        "targets": queries,
        "fieldConfig": {"defaults": defaults, "overrides": fixed_colors(colors or {})},
        "options": {
            "legend": {"displayMode": "list", "placement": "bottom", "showLegend": True},
            "tooltip": {"mode": "multi", "sort": "desc"},
        },
    }
    if min_interval:
        panel["interval"] = min_interval
    return panel


def logs(title: str, description: str, query: dict) -> Panel:
    return {
        "type": "logs",
        "title": title,
        "description": description,
        "datasource": query["datasource"],
        "targets": [query],
        "options": {
            "showTime": True,
            "showLabels": False,
            "showCommonLabels": False,
            "wrapLogMessage": True,
            "prettifyLogMessage": False,
            "enableLogDetails": True,
            "dedupStrategy": "none",
            "sortOrder": "Descending",
        },
    }


# ---------------------------------------------------------------------------
# Variables and annotations
# ---------------------------------------------------------------------------


def datasource_variable(name: str, label: str, kind: str) -> dict:
    return {
        "name": name,
        "label": label,
        "type": "datasource",
        "query": kind,
        "current": {},
        "hide": 0,
        "refresh": 1,
        "options": [],
    }


def textbox(name: str, label: str, default: str, description: str) -> dict:
    return {
        "name": name,
        "label": label,
        "type": "textbox",
        "query": default,
        "current": {"text": default, "value": default},
        "description": description,
        "hide": 0,
        "options": [{"selected": True, "text": default, "value": default}],
    }


def query_variable(name: str, label: str, datasource: dict, query: dict, definition: str) -> dict:
    return {
        "name": name,
        "label": label,
        "type": "query",
        "datasource": datasource,
        "definition": definition,
        "query": query,
        "refresh": 2,
        "sort": 1,
        "multi": True,
        "includeAll": True,
        # "All" matches whatever there is, also devices or groups not seen yet.
        "allValue": ".+",
        "current": {"text": "All", "value": "$__all"},
        "hide": 0,
        "options": [],
    }


def variables() -> list[dict]:
    levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    return [
        datasource_variable("datasource", "Prometheus", "prometheus"),
        datasource_variable("loki", "Loki", "loki"),
        textbox(
            "stream",
            "Loki streams",
            'job="fritzlogextractor"',
            "Label matcher for the tool's lines in Loki, as your collector labels them",
        ),
        query_variable(
            "device",
            "Device",
            PROM,
            {"query": "label_values(fritzlogextractor_polls_total, device)", "refId": "device"},
            "label_values(fritzlogextractor_polls_total, device)",
        ),
        query_variable(
            "group",
            "Group",
            LOKI,
            {
                "type": 1,
                "label": "group",
                "stream": '{${stream:raw}, kind="event"}',
                "refId": "group",
            },
            'label_values({${stream:raw}, kind="event"}, group)',
        ),
        textbox("id", "Message id", ".+", "Regular expression on the message id, e.g. 50[0-9]"),
        textbox("search", "Search", "", "Text the message contains, any case"),
        {
            "name": "level",
            "label": "Tool log level",
            "type": "custom",
            "query": ",".join(levels),
            "multi": True,
            "includeAll": True,
            "allValue": ".+",
            "current": {"text": ["WARNING", "ERROR", "CRITICAL"], "value": levels[2:]},
            "options": [
                {"text": level, "value": level, "selected": level in levels[2:]} for level in levels
            ],
            "hide": 0,
        },
    ]


def annotations() -> dict:
    return {
        "list": [
            {
                "builtIn": 1,
                "datasource": {"type": "grafana", "uid": "-- Grafana --"},
                "enable": True,
                "hide": True,
                "iconColor": "rgba(0, 211, 255, 1)",
                "name": "Annotations & Alerts",
                "type": "dashboard",
            },
            {
                "name": "Box reboots",
                "datasource": PROM,
                "enable": True,
                "iconColor": "red",
                "expr": f"changes(fritzlogextractor_box_reboots_total{{{DEV}}}[2m]) > 0",
                "step": "1m",
                "titleFormat": "Reboot of {{device}}",
                "textFormat": "The box restarted; its log starts afresh",
            },
        ]
    }


# ---------------------------------------------------------------------------
# Panels
# ---------------------------------------------------------------------------


def status(layout: Layout) -> None:
    layout.row("Status")
    layout.add(
        stat(
            "Last read",
            "Time since the last poll that read the log and wrote its events.",
            prom(f"time() - fritzlogextractor_last_success_timestamp_seconds{{{DEV}}}", now=True),
            unit="s",
            steps=thresholds((None, "green"), (180, "orange"), (STALLED_AFTER, "red")),
        ),
        3,
        4,
    )
    layout.add(
        stat(
            "Polls OK",
            "Share of successful polls in the selected time range.",
            prom(
                f"1 - sum by (device) (increase(fritzlogextractor_poll_errors_total{{{DEV}}}"
                f"[$__range])) / sum by (device) (increase(fritzlogextractor_polls_total{{{DEV}}}"
                "[$__range]))",
                now=True,
            ),
            unit="percentunit",
            decimals=1,
            steps=thresholds((None, "red"), (0.9, "orange"), (0.99, "green")),
        ),
        3,
        4,
    )
    layout.add(
        stat(
            "Events",
            "Event lines written in the selected time range.",
            prom(
                f"round(sum by (device) (increase(fritzlogextractor_events_total{{{DEV}}}"
                "[$__range])))",
                now=True,
            ),
        ),
        3,
        4,
    )
    layout.add(
        stat(
            "Rows on the box",
            "Rows in the device's event log at the last poll. It drops old rows when full and "
            "empties on a reboot.",
            prom(f"fritzlogextractor_log_rows{{{DEV}}}", now=True),
        ),
        3,
        4,
    )
    layout.add(
        stat(
            "Rejected rows",
            "Rows of the last poll that failed validation. They are not emitted; the tool's log "
            "shows the first one.",
            prom(f"fritzlogextractor_rejected_rows{{{DEV}}}", now=True),
            steps=thresholds((None, "green"), (1, "red")),
        ),
        3,
        4,
    )
    layout.add(
        stat(
            "State write errors",
            "Failed writes of the state file in the selected time range. Without the state, a "
            "restart emits the whole log again.",
            prom(
                f"round(sum by (device) (increase("
                f"fritzlogextractor_state_write_errors_total{{{DEV}}}[$__range])))",
                now=True,
            ),
            steps=thresholds((None, "green"), (1, "red")),
        ),
        3,
        4,
    )
    layout.add(
        stat(
            "Reboots",
            "Reboots of the box the tool noticed in the selected time range.",
            prom(
                f"round(sum by (device) (increase(fritzlogextractor_box_reboots_total{{{DEV}}}"
                "[$__range])))",
                now=True,
            ),
        ),
        2,
        4,
    )
    layout.add(
        stat(
            "Unwatched",
            "For the latest reboot: time from the last poll before it to the reboot. What the "
            "box logged in that time is lost.",
            prom(f"fritzlogextractor_reboot_unwatched_seconds{{{DEV}}}", now=True),
            unit="s",
            steps=thresholds((None, "green"), (180, "orange"), (STALLED_AFTER, "red")),
        ),
        2,
        4,
    )
    layout.add(
        stat(
            "Version",
            "Version of fritz_logextractor.",
            prom("fritzlogextractor_build_info", "{{version}}", now=True),
            decimals=None,
            text_mode="name",
        ),
        2,
        4,
    )


def polling(layout: Layout) -> None:
    layout.row("Polling")
    layout.add(
        timeseries(
            "Time since the last read",
            "Grows between polls and drops on each successful one. Past the dashed line the "
            "alert in the docs fires.",
            [prom(f"time() - fritzlogextractor_last_success_timestamp_seconds{{{DEV}}}")],
            unit="s",
            steps=thresholds((None, "transparent"), (STALLED_AFTER, "red")),
        ),
        8,
        8,
    )
    layout.add(
        timeseries(
            "Failed polls by reason",
            "password: password file unreadable; auth: login refused; connection: no answer; "
            "response: unexpected answer; parse: log not parsable; unexpected: see the tool's "
            "log.",
            [
                prom(
                    "sum by (reason) (increase("
                    f"fritzlogextractor_poll_errors_total{{{DEV}}}[$__rate_interval])) > 0",
                    "{{reason}}",
                )
            ],
            bars=True,
            stacked=True,
            colors=REASON_COLORS,
            min_interval="1m",
        ),
        8,
        8,
    )
    layout.add(
        timeseries(
            "Rows on the box",
            "Rows in the box's event log. A drop to a few rows is a reboot (red marks).",
            [prom(f"fritzlogextractor_log_rows{{{DEV}}}")],
        ),
        8,
        8,
    )


def events(layout: Layout) -> None:
    layout.row("Events")
    layout.add(
        timeseries(
            "Events by group",
            "Event lines in Loki per group. A line with a repeat count stands for several "
            "occurrences.",
            [loki(f"sum by (group) (count_over_time({EVENTS} [$__auto]))", "{{group}}")],
            bars=True,
            stacked=True,
            colors=GROUP_COLORS,
            min_interval="1m",
        ),
        16,
        8,
    )
    layout.add(
        {
            "type": "table",
            "title": "Message ids",
            "description": "Event lines per message id in the selected time range. The id names "
            "the kind of message, whatever the box's language; filter on it above.",
            "datasource": LOKI,
            "targets": [
                loki(
                    f"sort_desc(sum by (id) (count_over_time({EVENTS} {EVENT_FIELDS} [$__range])))",
                    table=True,
                )
            ],
            "fieldConfig": {
                "defaults": {},
                "overrides": [
                    {
                        "matcher": {"id": "byType", "options": "number"},
                        "properties": [{"id": "displayName", "value": "Lines"}],
                    }
                ],
            },
            "options": {
                "showHeader": True,
                "cellHeight": "sm",
                "sortBy": [{"displayName": "Lines", "desc": True}],
            },
            "transformations": [
                {
                    "id": "organize",
                    "options": {"excludeByName": {"Time": True}, "renameByName": {"id": "Id"}},
                }
            ],
        },
        8,
        8,
    )
    layout.add(
        logs(
            "Event log",
            "The box's events, newest first: [group id] message (\u00d7 repeats). Open a line for "
            "all its fields. Filter with Device, Group, Message id and Search above.",
            loki(
                f'{EVENTS} {EVENT_FIELDS} | id=~"$id" |~ `(?i)${{search:regex}}` '
                '| line_format "[{{.group}} {{.id}}] {{.msg}}'
                '{{ if ne .repeat \\"1\\" }} (\u00d7{{.repeat}}){{ end }}"'
            ),
        ),
        24,
        14,
    )


def own_log(layout: Layout) -> None:
    layout.row("Tool log")
    layout.add(
        logs(
            "Tool log",
            "The tool's own log lines (stderr), by Tool log level above. Warnings about polls "
            "name the device.",
            loki(
                f'{OWN_LOG} | json logger="logger", msg="msg" '
                '| line_format "{{.level}} {{.logger}}: {{.msg}}"'
            ),
        ),
        24,
        10,
    )


def dashboard() -> dict:
    layout = Layout()
    status(layout)
    polling(layout)
    events(layout)
    own_log(layout)
    return {
        "uid": "fritz-logextractor",
        "title": "Fritz!LogExtractor",
        "description": "Whether fritz_logextractor keeps reading each device, and the events "
        "it read, with filters and search.",
        "tags": ["fritz", "fritz_logextractor", "logs"],
        "editable": True,
        "graphTooltip": 1,
        "schemaVersion": 41,
        "version": 1,
        "refresh": "1m",
        "time": {"from": "now-24h", "to": "now"},
        "timepicker": {},
        "timezone": "browser",
        "annotations": annotations(),
        "links": [
            {
                "title": "Documentation",
                "type": "link",
                "url": "https://pdreker.github.io/fritz_logextractor/",
                "targetBlank": True,
                "icon": "doc",
            }
        ],
        "templating": {"list": variables()},
        "panels": layout.panels,
    }


def render() -> str:
    return json.dumps(dashboard(), indent=2, ensure_ascii=False) + "\n"


def main() -> None:
    if "--check" in sys.argv:
        sys.exit(0 if OUTPUT.read_text() == render() else 1)
    OUTPUT.write_text(render())


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
