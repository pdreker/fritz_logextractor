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

**Built:** `eventlog.py` (strict parser that splits off the fold counter, POSIX time zone rules,
output record; adapted from fritz_exporter PR #719), `tracker.py` (which rows are new, see
"Duplicate handling"), `state.py` (state file per device), `extractor.py` (fetch per device
through fritzconnection's session, times of pre-sync rows), poll loop with `--once` in
`__main__.py`, `capture.py` (`--capture DIR`: raw snapshots plus uptime per poll, to study real
boxes).

**Missing:** remote access; checks against other models and firmware (only a 7690 with FRITZ!OS
8.50 and a FRITZ!Repeater 1200 AX were captured).

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

- **Not append-only.** A message that repeats while its row is the newest one is folded into
  that row: its time moves to the latest occurrence and `msg` gains a counter suffix (German:
  `[N Meldungen seit DD.MM.YY HH:MM:SS]`, the time of the first occurrence, which stays put).
  With any other row in between, the repeat gets a row of its own.
- **Mesh.** The mesh master shows the rows of its repeaters too, as `[fritz.repeater] …`,
  folded as on the repeater. It sorts them in by their time, so a new row can land below rows
  already seen. Polling the master and a repeater gives each repeater row twice.
- **Bounded buffer.** Old rows drop off; a reboot empties the log.
- **Timestamps before time sync.** Seen on a 7690 with FRITZ!OS 8.50 (captures of October
  2026): until the box syncs its clock, entries are dated `01.01.70` and their time is the time
  since boot, counting up from `00:00:00`. On sync, the box rewrites all of them at once to boot
  time + that offset, in the same snapshot that first shows the `id` 2104 sync row. From then
  on it computes these times afresh on each request, so they jitter by ±1 s between polls. The
  order of the rows stays stable throughout. A repeater that gets the time from the box over
  the LAN logged nothing before its sync row. Other models and firmware not yet checked.
- **Reboot.** The log is empty afterwards; there is no row for the shutdown or the restart.
  `NewUpTime` drops (seen: 1 294 670 s → 86 s), so it detects the reboot. Rows logged between
  the last poll and the reboot are lost.
- **Own logins.** The tool's TR-064 login shows up as a row (`id` 506/507, "Anmeldung einer
  App"), once per session, not on every poll.
- **Sensitive content.** Messages embed WAN addresses, host and user names, and DynDNS update
  URLs including the token (`q=…`), which must be redacted.

### Duplicate handling

Checked by replaying 17 hours of captures with two box reboots through the tracker: every
occurrence came out once.

- Identity = `(id, group, msg without counter suffix)` plus the **time of the first
  occurrence** (the counter's "since", else the row's time). Neither position (mesh rows are
  sorted in) nor the row's own time (moves on folding) works.
- Match each message's rows against the stored ones, both newest first, with ±2 s tolerance
  for the jitter. 1970 times are put on the box's clock first (box boot = box-local now -
  uptime, once the box has synced), so rows match across the rewrite on sync.
- A matched row whose counter rose yields **one** record with `repeat: new N - old N` and the
  time of the latest occurrence; the box keeps no times in between. A new row yields `repeat: N`.
- **Reboot detection:** boot = now - `NewUpTime` on our clock. It stayed within a second over two
  weeks; a shift of more than 60 s means a new boot, and every row is new. This also works
  across a restart of the tool, unlike "uptime went down".
- **State** per device in `<state_dir>/<hostname>.json` (serial, boot, rows), written atomically
  after the records are flushed to stdout. A failure before that repeats records, never loses
  them. A state file with another serial is ignored.

### Output

- One JSON object per new row or fold on stdout: `event_time` with UTC offset (time zone from
  `Time1:GetInfo`), `time_estimated`, `box_time` (raw), device name, host and serial, `group`,
  `id`, `msg` (without counter, token redacted), `repeat`.
- Pre-sync rows get `time_estimated: true`: box boot + the row's time since boot on the box's
  clock once it has synced (matches the box's own rewrite to within a second), else our boot
  estimate + offset, in UTC.
- Own diagnostics go to stderr, so the event stream stays clean.

### Out of scope

- No native outputs for log stacks (Loki, Elasticsearch, …). Stdout is the interface; collectors
  pick it up from the container or journal. journald and syslog may come later, low priority.
- No metrics: event counters belong in fritz_exporter, if anywhere.
- No TLS certificate verification yet (Fritz!Box certs are self-signed); see
  fritz_exporter issue #736.
