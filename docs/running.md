# Running

The tool polls each configured device, prints new log entries to **stdout** and its own
messages to **stderr**. It keeps a state file per device in `state_dir`, so that a restart does
not print the whole log again; give it a place where that file survives.

## Preparing the box

Create a user on the Fritz!Box for the tool (*System → FRITZ!Box Users*, German *FRITZ!Box-Benutzer*),
for example `logextractor`, and give it the right *FRITZ!Box settings* (*FRITZ!Box
Einstellungen*). Fritz!OS has no finer rights for this; it is the same right fritz_exporter
needs.

You may reuse the user of fritz_exporter. A separate one lets you change or revoke either tool's
access on its own, and the box's log tells their logins apart.

Use a long random password of at most 32 characters: TR-064 logs in with MD5 digest
authentication, and Fritz!OS accepts no longer passwords there.

!!! tip "Mesh"

    In a mesh, poll the master only: it shows the entries of its repeaters too. Polling a
    repeater as well gives its entries twice. See [Output](output.md#mesh).

## Container

Images for linux/amd64 and linux/arm64 are on Docker Hub as
[`pdreker/fritz_logextractor`](https://hub.docker.com/r/pdreker/fritz_logextractor):

| Tag | Built from |
|---|---|
| `develop` | every push to `main` |
| `<version>`, `<major>.<minor>`, `<major>`, `latest` | each release, also for linux/arm/v6 and linux/arm/v7 |

The image runs as `nobody` (UID and GID 65534) and keeps its state in
`/var/lib/fritzlogextractor`, which is also the default `state_dir`.

!!! warning "Mount a volume for the state"

    The image declares `/var/lib/fritzlogextractor` as a volume. Without `-v`, Podman and
    Docker create an anonymous volume, which is gone when the container is created anew, for
    example on an update of the image. The tool then prints the whole log of the box once more.
    Always mount a named volume or a host directory there.

### Podman or Docker

With one device, the environment is enough:

```bash
podman run -d --name fritzlogextractor --restart unless-stopped \
  -v fritzlogextractor-state:/var/lib/fritzlogextractor \
  -e FRITZ_HOSTNAME=fritz.box \
  -e FRITZ_USERNAME=logreader \
  -e FRITZ_PASSWORD_FILE=/etc/fritz/password \
  -v ./password:/etc/fritz/password:ro \
  docker.io/pdreker/fritz_logextractor:latest
```

`docker run` takes the same options. Arguments after the image name go to the tool, for example
`--once` or `--config /etc/fritz/config.yaml` (the `FRITZ_*` variables are then ignored, see
[Configuration](configuration.md)):

```bash
podman run -d --name fritzlogextractor --restart unless-stopped \
  -v fritzlogextractor-state:/var/lib/fritzlogextractor \
  -v ./fritz:/etc/fritz:ro \
  docker.io/pdreker/fritz_logextractor:latest --config /etc/fritz/config.yaml
```

Files mounted into the container must be readable by UID 65534. A named volume takes over the
owner of the directory in the image, so it is writable right away. A host directory mounted on
`/var/lib/fritzlogextractor` must be writable by 65534: with rootless Podman, add `:U` to the
mount (`-v ./state:/var/lib/fritzlogextractor:U`), elsewhere `chown 65534:65534 ./state`.

### Compose

```yaml
services:
  fritzlogextractor:
    image: docker.io/pdreker/fritz_logextractor:latest
    restart: unless-stopped
    command: ["--config", "/etc/fritz/config.yaml"]
    volumes:
      - ./config.yaml:/etc/fritz/config.yaml:ro
      - state:/var/lib/fritzlogextractor
    secrets:
      - fritz_password

volumes:
  state:

secrets:
  fritz_password:
    file: ./password
```

with `password_file: /run/secrets/fritz_password` in `config.yaml`.

### Kubernetes

Run a single replica: two copies would each print every entry. `Recreate` makes sure the old pod
is gone before the new one starts, and `fsGroup` makes the volume writable for UID 65534.

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: fritzlogextractor
stringData:
  password: "..."
  config.yaml: |
    devices:
      - hostname: fritz.box
        username: logreader
        password_file: /etc/fritz/password
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: fritzlogextractor-state
spec:
  accessModes: [ReadWriteOnce]
  resources:
    requests:
      storage: 10Mi
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: fritzlogextractor
spec:
  replicas: 1
  strategy:
    type: Recreate
  selector:
    matchLabels:
      app: fritzlogextractor
  template:
    metadata:
      labels:
        app: fritzlogextractor
    spec:
      securityContext:
        runAsUser: 65534
        runAsGroup: 65534
        fsGroup: 65534
      containers:
        - name: fritzlogextractor
          image: docker.io/pdreker/fritz_logextractor:latest
          args: ["--config", "/etc/fritz/config.yaml"]
          volumeMounts:
            - name: config
              mountPath: /etc/fritz
              readOnly: true
            - name: state
              mountPath: /var/lib/fritzlogextractor
      volumes:
        - name: config
          secret:
            secretName: fritzlogextractor
        - name: state
          persistentVolumeClaim:
            claimName: fritzlogextractor-state
```

The state file takes about 150 bytes per row of the box's log: 85 KB for the 567 rows a
FRITZ!Box 7690 held after two weeks.

## Without a container

The tool is not on PyPI. Install it with [uv](https://docs.astral.sh/uv/) straight from GitHub,
or run it from a clone:

```bash
uv tool install git+https://github.com/pdreker/fritz_logextractor
fritzlogextractor --config config.yaml
```

```bash
git clone https://github.com/pdreker/fritz_logextractor && cd fritz_logextractor
uv run fritzlogextractor --config config.yaml
```

The default `state_dir`, `/var/lib/fritzlogextractor`, is usually not writable for a normal
user. Set `state_dir` to a directory of your own; otherwise the tool warns, keeps its state in
memory only and prints the whole log again on every start.

### Options

| Option | Meaning |
|---|---|
| `--config FILE` | Read the configuration from `FILE` instead of the environment |
| `--once` | Read each device once, print what is new and exit |
| `--log-level LEVEL` | Override the log level from the configuration |
| `--capture DIR` | Also store the raw log of each poll, see [Capturing snapshots](capture.md) |
| `--version` | Print the version and exit |

`--once` uses the state file as well, so a run from cron prints only what came since the last
run.

## Keeping events and diagnostics apart

In a terminal, stdout and stderr show up together. Send them to different places to keep the
events clean:

```bash
fritzlogextractor --config config.yaml > events.jsonl      # events to a file, diagnostics on screen
fritzlogextractor --config config.yaml 2> extractor.log    # diagnostics to a file
```

Container runtimes keep the two apart as well: Docker's `json-file` log driver and the CRI log
format of Kubernetes record for each line whether it came from stdout or stderr, and log
collectors such as Grafana Alloy, Promtail, Fluent Bit or Vector can filter on that. Collect
stdout as events and treat stderr as the tool's own log.
