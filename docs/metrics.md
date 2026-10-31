# Metrics

The tool can serve metrics for Prometheus. They tell whether it keeps up with each device: when
it last read the log, why polls fail, whether the box rebooted while nobody watched. What the
box logged is in the events; a log stack counts those better (see [Log collectors](collectors.md)).

The [Grafana dashboard](dashboard.md) shows them together with the events.

The metrics are off unless you set a port:

```yaml
metrics_port: 9788
```

or `FRITZ_METRICS_PORT=9788`. The tool then serves `http://<host>:9788/metrics`, on all
addresses unless `metrics_address` says otherwise. `--once` serves nothing.

In a container, publish the port (`-p 9788:9788` with Podman or Docker), or scrape it over the
container network.

## Series

Each series of a device carries the label `device`, its name from the configuration, as in the
events.

| Metric | Type | Meaning |
|---|---|---|
| `fritzlogextractor_last_success_timestamp_seconds` | gauge | When a poll last succeeded and its events were written (Unix time) |
| `fritzlogextractor_polls_total` | counter | Polls, successful or not |
| `fritzlogextractor_poll_errors_total` | counter | Failed polls, label `reason`, see below |
| `fritzlogextractor_events_total` | counter | Event lines written to stdout |
| `fritzlogextractor_log_rows` | gauge | Rows in the box's log at the last poll |
| `fritzlogextractor_rejected_rows` | gauge | Rows of the last poll that failed validation and were skipped |
| `fritzlogextractor_box_reboots_total` | counter | Reboots of the box the tool noticed, also those while it was not running |
| `fritzlogextractor_reboot_unwatched_seconds` | gauge | For the latest reboot: time from the last poll before it to the reboot. What the box logged in that time is lost |
| `fritzlogextractor_state_write_errors_total` | counter | Failed writes of the state file |
| `fritzlogextractor_build_info` | info | Label `version` |

The usual process and Python series of the Prometheus client come along (`process_*`,
`python_*`).

Values of `reason`:

| Reason | Meaning |
|---|---|
| `password` | The password file could not be read |
| `auth` | The box refused the login |
| `connection` | The box did not answer, or the connection broke |
| `response` | The box answered, but not as expected (HTTP error, missing fields) |
| `parse` | The log could not be parsed |
| `unexpected` | An error nobody expected; the tool's log has the traceback |

`fritzlogextractor_reboot_unwatched_seconds` needs the time of the last poll from the state file;
state files written before this metric existed lack it, and the first reboot after an update
leaves the series out.

## Alerts

```yaml
groups:
  - name: fritzlogextractor
    rules:
      - alert: FritzLogExtractorStalled
        # Also fires when the process hangs but still answers the scrape.
        expr: time() - fritzlogextractor_last_success_timestamp_seconds > 600
        for: 5m
        annotations:
          summary: "No event log read from {{ $labels.device }} for 10 minutes"
      - alert: FritzLogExtractorStateNotWritten
        expr: increase(fritzlogextractor_state_write_errors_total[15m]) > 0
        annotations:
          summary: "State file of {{ $labels.device }} cannot be written; a restart repeats events"
      - alert: FritzLogExtractorRowsRejected
        expr: fritzlogextractor_rejected_rows > 0
        for: 15m
        annotations:
          summary: "{{ $labels.device }}: {{ $value }} log rows failed validation and are not emitted"
```

A last success that is far behind needs a first success: a device that never answered since
the start has no `last_success` series. Catch that with `poll_errors_total`, e.g.
`increase(fritzlogextractor_poll_errors_total[15m]) > 10`.
