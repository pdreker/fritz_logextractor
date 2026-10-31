# fritz_logextractor

Reads the event log of AVM Fritz! devices and writes each new entry once, as a JSON line on
stdout, so a log stack (Loki, Elasticsearch, journald, …) can keep it.

The Fritz!Box keeps its event log in RAM: a reboot or a firmware update empties it, and the box
has no syslog push. This tool polls the log, works out what is new and prints it. It remembers
what it printed, so a restart of the tool or a reboot of the box does not print entries twice.

It talks to the box's TR-064 server only: TR-064 calls give it the uptime, the time zone and
the address of the full log (`/devicelog.lua` with a session ID), which it then fetches from
the same server, over HTTP or HTTPS like the TR-064 calls. It does not log in to the web interface.

It is a companion to [fritz_exporter](https://github.com/pdreker/fritz_exporter), which exports
the device's metrics to Prometheus.

!!! warning "Early days"

    - **Entries are lost** if the box logs them and then reboots before the next poll. The
      [metrics](metrics.md) tell how long such a gap was.
    - Checked against few devices: a FRITZ!Box 7690 (FRITZ!OS 8.50) with a FRITZ!Repeater
      1200 AX over a day with reboots, and a FRITZ!Box 6660 Cable (FRITZ!OS 8.25) once. Other
      models and firmware may change their log in ways not handled yet.
    - Remote access to the box is not supported.

## Quick start

```bash
podman run --rm \
  -v fritzlogextractor-state:/var/lib/fritzlogextractor \
  -e FRITZ_HOSTNAME=fritz.box -e FRITZ_USERNAME=logreader -e FRITZ_PASSWORD='...' \
  docker.io/pdreker/fritz_logextractor:latest
```

```json
{"kind": "event", "event_time": "2026-10-09T08:31:57+02:00", "time_estimated": false, "box_time": "09.10.26 08:31:57", "device": "fritz.box", "host": "fritz.box", "serial": "...", "group": "net", "id": 22, "msg": "Internetverbindung wurde erfolgreich hergestellt. ...", "repeat": 1}
```

- [Running](running.md): locally, in a container, with Compose or on Kubernetes.
- [Configuration](configuration.md): config file or environment, all options.
- [Output](output.md): what each field means, repeats, mesh, own logins.
- [Metrics](metrics.md): optional Prometheus endpoint about the tool's own work.
- [Grafana dashboard](dashboard.md): metrics and events on one page, with filters and search.
