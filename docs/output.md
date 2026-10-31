# Output

Each new log entry becomes one JSON object on one line of stdout, marked `"kind": "event"`. The
tool's own messages go to stderr, also as JSON lines but marked `"kind": "log"`, see
[Own log lines](#own-log-lines).

```json
{"kind": "event", "event_time": "2026-10-09T08:31:57+02:00", "time_estimated": false, "box_time": "09.10.26 08:31:57", "device": "fritz.box", "host": "fritz.box", "serial": "...", "group": "net", "id": 22, "msg": "Internetverbindung wurde erfolgreich hergestellt. ...", "repeat": 1}
```

## Fields

| Field | Meaning |
|---|---|
| `kind` | Always `event`; the tool's own log lines say `log` |
| `event_time` | When it happened, ISO 8601 with the box's UTC offset (without one if the box does not tell its time zone) |
| `time_estimated` | `true` for entries the box logged after a reboot, before it had synced its clock; see [below](#entries-before-time-sync) |
| `box_time` | The time as the box shows it |
| `device`, `host`, `serial` | The device's name from the configuration, its hostname and its serial number |
| `group` | `sys`, `net`, `wlan`, … |
| `id` | The message type; does not depend on the box's language |
| `msg` | The message as the box words it, in its language, without the counter of repeats |
| `repeat` | How many occurrences this line stands for, see [Repeats](#repeats) |

Every field comes from the box and may hold text typed by someone else, for example a user name
in a failed login. JSON encoding escapes it, so it cannot break a line. URLs keep only scheme
and host (`https://ddns.example.com/<redacted>`): a DynDNS update URL can carry user name,
password or token anywhere after the host. A DynDNS token outside a URL (`q=...`) becomes
`q=<redacted>`.

## Repeats

When the same message comes again right away, the box does not add a row: it bumps a counter on
the existing row (`[3 Meldungen seit …]`) and moves the row to the time of the latest
occurrence. The tool prints one line per such change, with `repeat` set to the number of new
occurrences and `event_time` the latest one; the times in between are lost on the box.

A repeat with another message in between gets a row of its own on the box, and a line with
`repeat: 1` from the tool.

## Restarts and reboots

The tool keeps a state file per device in `state_dir`, so a restart does not print the log
again. A reboot of the box empties its log; the tool notices it from the uptime and prints
everything the box logs after it. Entries the box logs in the last moments before a reboot are
lost if no poll fetched them.

## Entries before time sync

After a reboot, the box dates its entries 01.01.70 until it has synced its clock, with the time
since boot as time of day. Once synced, it rewrites them to the real time. The tool works out
the real time from the uptime right away and marks these lines with `time_estimated: true`. The
estimate is within a second or so of what the box shows later. While the box has no time of its
own yet, `event_time` is in UTC.

## Mesh

A FRITZ!Box that is mesh master also shows the log of its repeaters, as rows starting with
`[fritz.repeater]`. Polling the box is enough; polling the repeaters as well gives their entries
twice.

## Own log lines

The tool logs to stderr, by default as one JSON object per line:

```json
{"time": "2026-10-31T16:30:51.485+00:00", "kind": "log", "level": "INFO", "logger": "fritzlogextractor", "msg": "Configured 1 device(s), polling every 60s"}
```

| Field | Meaning |
|---|---|
| `time` | When it was logged, in UTC |
| `kind` | Always `log` |
| `level` | `DEBUG`, `INFO`, `WARNING`, `ERROR` or `CRITICAL` |
| `logger` | Which part logged it, e.g. `fritzlogextractor.extractor`, or a library such as `urllib3` |
| `msg` | The message |
| `exc` | Only on errors with a traceback: the traceback, in the same line |

So a log stack that gets stdout and stderr mixed can still sort events and log lines apart on
`kind`. For reading in a terminal, `--log-format text` switches to the classic format:

```text
2026-10-31 17:30:51,485     INFO fritzlogextractor | Configured 1 device(s), polling every 60s
```

## Own logins

Each login of the tool shows up in the log as "Anmeldung einer App" (`id` 506 or 507), once per
session: at start and after connection errors.
