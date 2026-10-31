# Agent Guidelines for fritz_logextractor

Project conventions, architecture, findings from real boxes and tooling for contributors and AI
agents. Conventions follow [fritz_exporter](https://github.com/pdreker/fritz_exporter); where this
file is silent, do what fritz_exporter does.

## Project Overview

**fritz_logextractor** reads the event log of AVM Fritz! devices and writes each new entry once as
a JSON line on stdout, so a log stack can keep it across reboots of the box and restarts of the
tool. Companion to fritz_exporter (metrics).

- **Entrypoint**: `fritzlogextractor/__main__.py` → `main()`
- **Python version**: 3.14+
- **User docs**: `docs/`, published at <https://pdreker.github.io/fritz_logextractor/>
- **Status**: works against a FRITZ!Box 7690; not released yet. The maintainer releases once it
  is an MVP that "just works" without attention (see "Open" below).

---

## Language, Typing, Data Classes

- Python 3.14+, `from __future__ import annotations`, type annotations everywhere.
- **ty** for type checking (`[tool.ty]` in `pyproject.toml`).
- **attrs** `@define` for data/config classes, not `dataclasses`.
- Config validation lives on the attrs fields (converters + validators), never in `from_config()`
  or the env reader. Both config paths build the same objects. Unknown config keys are an error
  with a suggestion, as in fritz_exporter.
- **License blocks go at the end of each source file**, not the top: the maintainer wants code,
  not boilerplate, when opening a file. Do not move them up.

## Dual Configuration

YAML file (`--config`, several devices) **or** environment variables (one device), never merged.
The mapping table is in `docs/configuration.md`. **When adding an option, update both paths,
both test classes in `tests/test_config.py`, and that table.** Command line switches that only
change how the tool runs (`--once`, `--log-format`, `--capture`) are not config options.

## Documentation

User docs live in `docs/` (MkDocs with Material, versioned with mike on `gh-pages`, GitHub Pages
from that branch); the README only introduces the tool and links there. A push to `main` that
touches the docs publishes `dev` (`docs.yaml`); a release publishes `<major>.<minor>` as `latest`
(`release.yaml`, because releases that release-please creates with `GITHUB_TOKEN` do not trigger
other workflows). CI runs `mkdocs build --strict` on every pull request. Write docs for users;
the design and the findings from real boxes stay here.

## Linting, Testing

```bash
uv run ruff check . && uv run ruff format --check .
uv run ty check
uv run pytest
uv run mkdocs build --strict
```

Ruff config is copied from fritz_exporter (line length 100, broad rule set, tests excluded).
Use `logging`, never `print()`. Exception messages are plain strings assigned to `msg` first.

Tests use synthetic event logs that follow patterns seen on real boxes. **The repo is public:**
never commit captures, state files or anything else from a real box (`capture/` and `state/`
are git-ignored). Check changes to the tracker by replaying local captures through it.

## Commits, Branching

- **Conventional Commits** (`feat:`, `fix:`, `docs:`, …); release-please derives versions from
  them. Breaking changes: `!` or `BREAKING CHANGE:`.
- Changes go through a feature branch and a PR (`<type>/<short-description>`).
- Dependencies: `uv add` / `uv remove`, never edit `uv.lock` by hand.

---

## Architecture

| Module | Does |
|---|---|
| `__main__.py` | Command line, poll loop, writes records to stdout, then commits |
| `logs.py` | The tool's own log lines on stderr: JSON (`"kind": "log"`) or text |
| `config/` | Config file or environment → `ExtractorConfig` |
| `extractor.py` | One device: TR-064 calls, fetching the log, times of pre-sync rows, records |
| `eventlog.py` | Strict parser (splits off the fold counter), output record, POSIX time zone rules; adapted from fritz_exporter PR #719 |
| `tracker.py` | Which rows are new (see "Duplicate handling") |
| `state.py` | State file per device |
| `capture.py` | `--capture DIR`: raw snapshots plus uptime per poll, to study real boxes |

One poll of one device:

1. `DeviceInfo1:X_AVM-DE_GetDeviceLogPath`, then GET that path on the TR-064 server.
2. `DeviceInfo1:GetInfo`: uptime and serial. Required; without them the poll fails.
3. `Time1:GetInfo`: time zone rule, current offset and box-local time (`None` before sync).
4. Parse, ask the tracker what is new, build records.
5. `poll_all()` writes and flushes the records, then calls `commit()`: the tracker takes the
   snapshot and the state file is written. A failure before the commit repeats records on the
   next poll, never loses them.

Each device is polled on its own; an error in one (expected or not) is logged and the others go
on.

### Data source

- TR-064 `DeviceInfo:X_AVM-DE_GetDeviceLogPath` returns `/devicelog.lua?sid=…`, which serves the
  **complete** log as XML (unlike `DeviceInfo:GetDeviceLog`, which omits entries with
  addresses). fritzconnection wraps it as `FritzStatus.get_avm_device_log()`.
- The page lives on the **TR-064 server** (port 49000, 49443 with TLS), not in the web UI.
  Fetch it through fritzconnection's session.
- Each `Event` has `id` (a message *type*, language independent), `group` (`sys`, `net`,
  `wlan`, …), `date` (`DD.MM.YY`), `time` (`HH:MM:SS`), `msg` (localized text). Newest first.
- The box user needs the right "FRITZ!Box-Einstellungen", the same as fritz_exporter; Fritz!OS
  has nothing finer.

### Quirks of the log

Seen on a 7690 with FRITZ!OS 8.50 and a FRITZ!Repeater 1200 AX (captures of October 2026).
Other models and firmware not yet checked.

- **Not append-only.** A message that repeats while its row is the newest one is folded into
  that row: its time moves to the latest occurrence and `msg` gains a counter suffix (German:
  `[N Meldungen seit DD.MM.YY HH:MM:SS]`, the time of the first occurrence, which stays put).
  With any other row in between, the repeat gets a row of its own.
- **Mesh.** The mesh master shows the rows of its repeaters too, as `[fritz.repeater] …`,
  folded as on the repeater. It sorts them in by their time, so a new row can land below rows
  already seen. Polling the master and a repeater gives each repeater row twice.
- **Bounded buffer.** Old rows drop off; a reboot empties the log.
- **Timestamps before time sync.** Until the box syncs its clock, entries are dated `01.01.70`
  and their time is the time since boot, counting up from `00:00:00`. On sync, the box rewrites
  all of them at once to boot time + that offset, in the same snapshot that first shows the
  `id` 2104 sync row. From then on it computes these times afresh on each request, so they
  jitter by ±1 s between polls. The order of the rows stays stable throughout. The repeater,
  which gets its time from the box over the LAN, logged nothing before its sync row.
- **Time info before sync.** `Time1:GetInfo` answers, but `NewCurrentLocalTime` is not an ISO
  string (fritzconnection hands over `None` or a naive datetime).
- **Reboot.** The log is empty afterwards; there is no row for the shutdown or the restart.
  Rows logged between the last poll and the reboot are lost.
- **Own logins.** The tool's TR-064 login shows up as a row (`id` 506/507, "Anmeldung einer
  App"), once per session, not on every poll.
- **Sensitive content.** Messages embed WAN addresses, host and user names, and DynDNS update
  URLs including the token (`q=…`). URLs are cut down to scheme and host, since other providers
  put credentials in the user info, path or other parameters.

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
- **State** per device in `<state_dir>/<hostname>.json` (serial, boot, rows; about 150 bytes per
  row), written atomically, readable by the owner only. A state file with another serial is
  ignored. If `state_dir` is not writable, the tool warns once and keeps the state in memory.

### Output

- **Events** on stdout, one JSON object per new row or fold: `kind: "event"`, `event_time` with
  UTC offset (time zone from `Time1:GetInfo`), `time_estimated`, `box_time` (raw), `device`,
  `host`, `serial`, `group`, `id`, `msg` (without counter, token redacted), `repeat`.
- Pre-sync rows get `time_estimated: true`: box boot + the row's time since boot on the box's
  clock once it has synced (matches the box's own rewrite to within a second), else our boot
  estimate + offset, in UTC.
- **Own log lines** on stderr, by default JSON too: `time` (UTC), `kind: "log"`, `level`,
  `logger`, `msg`, and `exc` with the traceback in the same line. A log stack that gets both
  streams mixed (container logs, journald) sorts them apart on `kind`. `--log-format text`
  gives the classic format for terminals.

### Container

The image runs as UID/GID 65534, declares `/var/lib/fritzlogextractor` (the default
`state_dir`) as a volume and owns it. Without a mounted volume, a new container starts without
state and prints the whole log again; the docs warn about it.

### Log collector examples

`examples/` holds configs for Grafana Alloy (→ Loki; one `pipeline.alloy` plus a source file
each for Docker/Podman socket, journald, Kubernetes), Vector, Fluentd and Logstash (→
Elasticsearch). `docs/collectors.md` includes them with `pymdownx.snippets`, so the docs show
exactly the checked files. All sort on `kind` and take the time from the line. When changing
the output format, check them:

- Alloy: `alloy validate <dir>` for pipeline + each source; the Docker source was run end to end
  with Podman and Loki (a container printing sample lines, Alloy with
  `--security-opt label=disable` for the socket under SELinux).
- Vector: `vector test vector.yaml vector-tests.yaml`.
- Fluentd: `fluentd --dry-run` (with `fluent-plugin-elasticsearch`), parsing with sample
  records through `fluent-cat` and a stdout match.
- Logstash: `logstash --config.test_and_exit`, the filter with sample records from stdin.

### Open

- Checks against other models and firmware.

### Out of scope

- **Remote access** (TR-064 over the internet): users reach the box over their own VPN.
- No native outputs for log stacks (Loki, Elasticsearch, …). Stdout is the interface; collectors
  pick it up from the container or journal. journald and syslog may come later, low priority.
- No metrics: event counters belong in fritz_exporter, if anywhere.
- No TLS certificate verification yet (Fritz!Box certs are self-signed); see
  fritz_exporter issue #736.
