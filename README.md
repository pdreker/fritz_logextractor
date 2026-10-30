# fritz_logextractor

> [!WARNING]
> **Proof of concept. Not ready for production use.**
>
> It reads the event log and prints new entries as JSON lines, but:
>
> - **Duplicates after a restart:** which entries were already printed is kept in memory only.
>   Each start prints the whole buffer of the box again.
> - **No reboot detection** and no special handling of the bogus timestamps a box logs before
>   its clock is synced.
> - **Entries are lost** if the box reboots between two polls.
> - Tested against one real box only: v0.1.0 read the log of a FRITZ!Box 6660 Cable on
>   FRITZ!OS 8.25 (`--once`, 104 entries in the groups `sys`, `net` and `wlan`). Remote access is
>   not supported.
>
> The planned design is in [AGENTS.md](AGENTS.md).

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
{"event_time": "2026-10-09T08:31:57+02:00", "device": "fritz.box", "host": "fritz.box", "group": "net", "id": 22, "msg": "Internetverbindung wurde erfolgreich hergestellt. ..."}
```

`id` is the message type and does not depend on the box's language; `msg` is localized. DynDNS
update tokens (`q=...`) are redacted.

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
user. `/var/lib/fritzlogextractor` is a volume for the state file once the tool keeps one; a
host directory mounted there must be writable by 65534.

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
| `state_dir` | `FRITZ_STATE_DIR` | `/var/lib/fritzlogextractor` (not used yet) |
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
