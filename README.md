# fritz_logextractor

> [!WARNING]
> **Proof of concept. Not ready for production use.**
>
> It prints each new entry once, also across restarts of the tool and reboots of the box, but:
>
> - **Entries are lost** if the box logs them and then reboots before the next poll.
> - Checked against few devices: a FRITZ!Box 7690 (FRITZ!OS 8.50) with a FRITZ!Repeater 1200
>   AX over a day with reboots, and a FRITZ!Box 6660 Cable (FRITZ!OS 8.25) once. Other models
>   and firmware may change their log in ways not handled yet. Remote access is not supported.
>
> The design is in [AGENTS.md](AGENTS.md).

Reads the event log of AVM Fritz! devices over TR-064 and writes each new entry once, as a
structured log line, so a log stack (Loki, Elasticsearch, journald, …) can keep it.

The Fritz!Box keeps its event log in RAM: a reboot or a firmware update empties it, and the box
has no syslog push. This tool polls the log and forwards what is new.

Companion to [fritz_exporter](https://github.com/pdreker/fritz_exporter), which exports the
device's metrics to Prometheus.

## Usage

```bash
export FRITZ_USERNAME=logreader FRITZ_PASSWORD='...'
fritzlogextractor --once          # print the current buffer once and exit
fritzlogextractor                 # keep polling, print new entries as they appear
```

Events go to stdout, one JSON object per line; the tool's own messages go to stderr:

```json
{"event_time": "2026-10-09T08:31:57+02:00", "time_estimated": false, "box_time": "09.10.26 08:31:57", "device": "fritz.box", "host": "fritz.box", "serial": "...", "group": "net", "id": 22, "msg": "Internetverbindung wurde erfolgreich hergestellt. ...", "repeat": 1}
```

| Field | Meaning |
|---|---|
| `event_time` | When it happened, ISO 8601 with the box's UTC offset (without one if the box does not tell its time zone) |
| `time_estimated` | `true` for entries the box logged after a reboot, before it had synced its clock (the box dates them 01.01.70); the tool works out the time from the uptime, to within a second or so |
| `box_time` | The time as the box shows it |
| `device`, `host`, `serial` | The device's name from the configuration, its hostname and its serial number |
| `group` | `sys`, `net`, `wlan`, … |
| `id` | The message type; does not depend on the box's language |
| `msg` | The message as the box words it, in its language, without the counter of repeats; DynDNS update tokens (`q=...`) are redacted |
| `repeat` | How many occurrences this line stands for, see below |

**Repeats.** When the same message comes again right away, the box does not add a row: it bumps
a counter on the existing row (`[3 Meldungen seit …]`) and moves the row to the time of the
latest occurrence. The tool prints one line per such change, with `repeat` set to the number of
new occurrences and `event_time` the latest one; the times in between are lost on the box.

**What the tool remembers.** Per device it keeps a state file in `state_dir`, so a restart does
not print the log again. A reboot of the box empties its log; the tool notices it from the
uptime and prints everything the box logs after it. Without a writable `state_dir` the tool
warns and remembers only in memory, so each start prints the whole log once more.

**Mesh.** A FRITZ!Box that is mesh master also shows the log of its repeaters, as rows starting
with `[fritz.repeater]`. Polling the box is enough; polling the repeaters as well gives their
entries twice.

**Own logins.** Each login of the tool shows up in the log as "Anmeldung einer App" (`id` 506 or
507), once per session: at start and after connection errors.

### Capturing snapshots

To study how a box changes its log over time (folded entries, reboots, timestamps before time
sync), `--capture DIR` also stores what the box sends, next to the normal output:

```bash
fritzlogextractor --capture ./capture
```

Each device gets a directory below `DIR`. Every poll adds a line to `polls.jsonl` with poll time,
uptime, model, firmware and the SHA-256 of the log, or with `error` if the log could not be read
(e.g. while the device reboots); the log itself is stored once per distinct
content as `<sha256 prefix>.xml`. In the container, use a directory in the volume, e.g.
`--capture /var/lib/fritzlogextractor/capture`.

> [!CAUTION]
> The snapshots are **not redacted**: they contain addresses, host and user names and DynDNS
> tokens. Redact them before you share them.

## Container image

Images for linux/amd64 and linux/arm64 are on Docker Hub as
[`pdreker/fritz_logextractor`](https://hub.docker.com/r/pdreker/fritz_logextractor):

| Tag | Built from |
|---|---|
| `develop` | every push to `main` |
| `<version>`, `<major>.<minor>`, `<major>`, `latest` | each release, also for linux/arm/v6 and linux/arm/v7 |

No release has been built as an image yet, so only `develop` exists for now.

```bash
podman pull docker.io/pdreker/fritz_logextractor:develop     # or: docker pull pdreker/fritz_logextractor:develop
podman run --rm \
  -e FRITZ_HOSTNAME=fritz.box -e FRITZ_USERNAME=logreader -e FRITZ_PASSWORD='...' \
  docker.io/pdreker/fritz_logextractor:develop
```

`docker run` takes the same options. Arguments after the image name go to the tool, e.g. `--once`
or `--config /etc/fritz/config.yaml` with the file mounted under `/etc/fritz` (the `-e` variables
are then ignored, see [Configuration](#configuration)).

The image runs as `nobody` (65534): mounted config and password files must be readable by that
user. `/var/lib/fritzlogextractor` is a volume for the state files; a host directory mounted
there must be writable by 65534.

## Configuration

The tool reads its settings from **either** a YAML file **or** environment variables, never
from both:

- With `--config FILE`, only the file counts. **All `FRITZ_*` environment variables are
  ignored**, including `FRITZ_POLL_INTERVAL` and `FRITZ_LOG_LEVEL`: set these in the file.
- Without `--config`, the environment configures exactly one device.

`--log-level` on the command line overrides the log level from either source.

### Config file

The file is the only way to poll several devices. Top-level keys apply to the tool, each entry
under `devices` describes one device:

```yaml
log_level: INFO
poll_interval: 60              # seconds between polls, at least 10
state_dir: /var/lib/fritzlogextractor

devices:
  - name: Fritz!Box 7690       # shown as "device" in each event; defaults to the hostname
    hostname: fritz.box
    username: logreader
    password_file: /etc/fritz/password
    use_tls: true
  - name: Repeater
    hostname: fritz.repeater
    username: logreader
    password: "..."
    connection_timeout: 0      # no timeout
```

Only `username` and one of `password` or `password_file` are required per device; every other
key falls back to the defaults below. An unknown key, such as a typo, stops the tool with an
error that names the closest known key. This also means a config file of fritz_exporter does not
work here: give each tool its own file. A file read with `password_file` may end in a
newline; the tool strips it.

### Environment variables

```bash
export FRITZ_HOSTNAME=fritz.box FRITZ_USERNAME=logreader FRITZ_PASSWORD_FILE=/run/secrets/fritz
export FRITZ_POLL_INTERVAL=30
fritzlogextractor
```

### Reference

| YAML key | Environment variable | Default |
|---|---|---|
| `log_level` | `FRITZ_LOG_LEVEL` | `INFO` |
| `poll_interval` | `FRITZ_POLL_INTERVAL` | `60` (seconds, at least 10) |
| `state_dir` | `FRITZ_STATE_DIR` | `/var/lib/fritzlogextractor` |
| `devices[].hostname` | `FRITZ_HOSTNAME` | `fritz.box` |
| `devices[].name` | `FRITZ_NAME` | the hostname |
| `devices[].username` | `FRITZ_USERNAME` | *(required)* |
| `devices[].password` | `FRITZ_PASSWORD` | *(required unless password_file is set)* |
| `devices[].password_file` | `FRITZ_PASSWORD_FILE` | |
| `devices[].connection_timeout` | `FRITZ_CONNECTION_TIMEOUT` | `10` (`0` disables it) |
| `devices[].use_tls` | `FRITZ_USE_TLS` | `false` |

Use a separate Fritz!Box user for the tool, with a long random password (Fritz!OS accepts at most
32 characters for TR-064): TR-064 logs in with MD5 digest authentication.

## Development

```bash
uv sync --all-groups
uv run ruff check . && uv run ruff format --check .
uv run ty check
uv run pytest
```

## License

Copyright 2026 Patrick Dreker and Rafael Kansy.

Apache License 2.0, see [LICENSE.md](LICENSE.md) and [NOTICE](NOTICE). The event log parsing is
adapted from Rafael Kansy's work in
[fritz_exporter #719](https://github.com/pdreker/fritz_exporter/pull/719).
