# Configuration

The tool reads its settings from **either** a YAML file **or** environment variables, never
from both:

- With `--config FILE`, only the file counts. **All `FRITZ_*` environment variables are
  ignored**, including `FRITZ_POLL_INTERVAL` and `FRITZ_LOG_LEVEL`: set these in the file.
- Without `--config`, the environment configures exactly one device.

`--log-level` on the command line overrides the log level from either source.

## Config file

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
  - name: Office box
    hostname: 192.168.123.1
    username: logreader
    password: "..."
    connection_timeout: 0      # no timeout
```

Only `username` and one of `password` or `password_file` are required per device; every other
key falls back to the defaults below. An unknown key, such as a typo, stops the tool with an
error that names the closest known key. This also means a config file of fritz_exporter does not
work here: give each tool its own file. A file read with `password_file` may end in a newline;
the tool strips it.

## Environment variables

```bash
export FRITZ_HOSTNAME=fritz.box FRITZ_USERNAME=logreader FRITZ_PASSWORD_FILE=/run/secrets/fritz
export FRITZ_POLL_INTERVAL=30
fritzlogextractor
```

## Reference

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

`state_dir` holds one file per device, `<hostname>.json`. It must survive restarts and be
writable; see [Running](running.md). The file holds message text from the box (addresses, host
and user names) and is created readable by its owner only.

`use_tls` talks to the box over HTTPS. The tool does not check the box's certificate, since
Fritz!Boxes come with a self-signed one.
