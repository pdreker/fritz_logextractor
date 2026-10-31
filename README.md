# fritz_logextractor

Reads the event log of AVM Fritz! devices and writes each new entry once, as a JSON line on
stdout, so a log stack (Loki, Elasticsearch, journald, …) can keep it.

The Fritz!Box keeps its event log in RAM: a reboot or a firmware update empties it, and the box
has no syslog push. This tool polls the log, works out what is new and prints it, also across
restarts of the tool and reboots of the box.

Companion to [fritz_exporter](https://github.com/pdreker/fritz_exporter), which exports the
device's metrics to Prometheus.

**Documentation: <https://pdreker.github.io/fritz_logextractor/>**

> [!WARNING]
> Early days. Entries the box logs right before a reboot are lost if no poll fetched them, and
> only few models and firmware versions have been checked. See the documentation.

## Quick start

```bash
podman run -d --name fritzlogextractor --restart unless-stopped \
  -v fritzlogextractor-state:/var/lib/fritzlogextractor \
  -e FRITZ_HOSTNAME=fritz.box -e FRITZ_USERNAME=logreader -e FRITZ_PASSWORD='...' \
  docker.io/pdreker/fritz_logextractor:latest
```

Mount a volume on `/var/lib/fritzlogextractor` as shown: the tool keeps its state there, and
without it every new container prints the whole log again.

```json
{"event_time": "2026-10-09T08:31:57+02:00", "time_estimated": false, "box_time": "09.10.26 08:31:57", "device": "fritz.box", "host": "fritz.box", "serial": "...", "group": "net", "id": 22, "msg": "Internetverbindung wurde erfolgreich hergestellt. ...", "repeat": 1}
```

Configuration, running without a container, Compose, Kubernetes and the meaning of each field:
see the [documentation](https://pdreker.github.io/fritz_logextractor/).

## Development

```bash
uv sync --all-groups
uv run ruff check . && uv run ruff format --check .
uv run ty check
uv run pytest
uv run mkdocs serve
```

Conventions and design: [AGENTS.md](AGENTS.md).

## License

Copyright 2026 Patrick Dreker and Rafael Kansy.

Apache License 2.0, see [LICENSE.md](LICENSE.md) and [NOTICE](NOTICE). The event log parsing is
adapted from Rafael Kansy's work in
[fritz_exporter #719](https://github.com/pdreker/fritz_exporter/pull/719).
