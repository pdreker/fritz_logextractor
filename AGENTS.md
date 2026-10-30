# Agent Guidelines for fritz_logextractor

Project conventions, planned architecture and tooling for contributors and AI agents. Conventions
follow [fritz_exporter](https://github.com/pdreker/fritz_exporter); where this file is silent, do
what fritz_exporter does.

## Project Overview

**fritz_logextractor** reads the event log of AVM Fritz! devices over TR-064 and writes each new
entry once as a structured log line (JSON on stdout), so a log stack can keep it across device
reboots.

- **Entrypoint**: `fritzlogextractor/__main__.py` → `main()`
- **Python version**: 3.14+
- **Status**: proof of concept. Fetch, parse, in-memory duplicate filter and JSON output work;
  see "Planned Architecture" for what is missing.

---

## Language, Typing, Data Classes

- Python 3.14+, `from __future__ import annotations`, type annotations everywhere.
- **ty** for type checking (`[tool.ty]` in `pyproject.toml`).
- **attrs** `@define` for data/config classes, not `dataclasses`.
- Config validation lives on the attrs fields (converters + validators), never in `from_config()`
  or the env reader. Both config paths build the same objects.

## Dual Configuration

YAML file (`--config`, several devices) **or** environment variables (one device), never merged.
The mapping table is in `README.md`. **When adding an option, update both paths, both test
classes in `tests/test_config.py`, and the README table.**

## Linting, Testing

```bash
uv run ruff check . && uv run ruff format --check .
uv run ty check
uv run pytest
```

Ruff config is copied from fritz_exporter (line length 100, broad rule set, tests excluded).
Use `logging`, never `print()`. Exception messages are plain strings assigned to `msg` first.

## Commits, Branching

- **Conventional Commits** (`feat:`, `fix:`, `docs:`, …); release-please derives versions from
  them. Breaking changes: `!` or `BREAKING CHANGE:`.
- Changes go through a feature branch and a PR (`<type>/<short-description>`).
- Dependencies: `uv add` / `uv remove`, never edit `uv.lock` by hand.

---

## Planned Architecture

Records the design agreed when the project was started (October 2026).

**Built (PoC):** `eventlog.py` (strict parser, in-memory tracker keyed on time + message,
POSIX time zone rules, output record; adapted from fritz_exporter PR #719), `extractor.py` (fetch
per device through fritzconnection's session), poll loop with `--once` in `__main__.py`,
`capture.py` (`--capture DIR`: raw snapshots plus uptime per poll, to build test data from real
boxes).

**Missing:** state file, reboot detection, handling of pre-sync timestamps, identity by position
instead of time (below), remote access, tests against a real box.

### Data source

- TR-064 `DeviceInfo:X_AVM-DE_GetDeviceLogPath` returns `/devicelog.lua?sid=…`, which serves the
  **complete** log as XML (unlike `DeviceInfo:GetDeviceLog`, which omits entries with
  addresses). fritzconnection wraps it as `FritzStatus.get_avm_device_log()`.
- The page lives on the **TR-064 server** (port 49000 locally; under `/tr064` on the shared port
  with remote access), not in the web UI. Fetch it through fritzconnection's session so the
  remote prefix stays right.
- Each `Event` has `id` (a message *type*, language independent), `group` (`sys`, `net`,
  `wlan`, …), `date` (`DD.MM.YY`), `time` (`HH:MM:SS`), `msg` (localized text). Newest first.

### Quirks to handle

- **Not append-only.** A repeated message is folded into one row: its time moves to the latest
  occurrence and `msg` gains a counter suffix (German: `[N Meldungen seit DD.MM.YY HH:MM:SS]`).
- **Bounded buffer.** Old rows drop off; a reboot empties the log.
- **Timestamps before time sync.** After a reboot, the first entries carry a bogus date and a
  time starting at `00:00:00`; they keep it after the box syncs its clock. Open question: does
  the time count up from `00:00:00` (usable for ordering) or stay fixed? Needs a sample log
  taken right after a reboot.
- **Sensitive content.** Messages embed WAN addresses, host and user names, and DynDNS update
  URLs including the token (`q=…`), which must be redacted.

### Duplicate handling

- Do **not** key on the timestamp. Identity = `(id, group, msg without counter suffix)` plus
  position in the list; the order of the log stays reliable even when times do not.
- Diff each snapshot against the stored previous one: rows above the newest known row are new; a
  folded row whose counter rose counts as `new N - old N` occurrences.
- **Reboot detection** via `DeviceInfo:GetInfo` → `NewUpTime`: uptime going down means a restart,
  and every current row is new.
- **Persist state** per device in `state_dir`, so a restart of the tool does not log everything
  again.

### Output

- One JSON object per event on stdout: device name and serial, `id`, `group`, `msg`, box
  timestamp (raw), resolved timestamp with UTC offset (time zone from `Time1:GetInfo`), and
  `time_estimated: true` for pre-sync rows (estimated as boot time = now - uptime).
- Own diagnostics go to stderr, so the event stream stays clean.

### Out of scope

- No native outputs for log stacks (Loki, Elasticsearch, …). Stdout is the interface; collectors
  pick it up from the container or journal. journald and syslog may come later, low priority.
- No metrics: event counters belong in fritz_exporter, if anywhere.
- No TLS certificate verification yet (Fritz!Box certs are self-signed); see
  fritz_exporter issue #736.
