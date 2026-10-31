# Log collectors

Starting points for picking the output up with common log collectors. They are opinionated on
purpose; adapt them to your setup. All of them follow the same plan:

- **Sort on `kind`.** Every line the tool writes is JSON: `"kind": "event"` for the box's log on
  stdout, `"kind": "log"` for the tool's own log on stderr. This works even where the two
  streams arrive mixed.
- **Take the time from the line.** Events carry the time the box logged them in `event_time`,
  the tool's own lines theirs in `time`. Without this, a log stack files events under the time
  they were read, which after a restart can be days later.
- **Few labels or indices.** Loki gets the labels `kind`, `device`, `group` (events) and `level`
  (own log); Elasticsearch gets one index for events and one for the tool's log. Everything
  else stays in the JSON.

The [Grafana dashboard](dashboard.md) expects the Loki labels of the Alloy example.

The files are in [`examples/`](https://github.com/pdreker/fritz_logextractor/tree/main/examples)
in the repository.

| Collector | Source | Target | Checked |
|---|---|---|---|
| [Grafana Alloy](#grafana-alloy-loki) | Docker/Podman socket, journald, Kubernetes | Loki | Docker source end to end with Podman and Loki; all sources with `alloy validate` |
| [Vector](#vector-elasticsearch) | Docker | Elasticsearch | Unit tests with `vector test` (included) |
| [Fluentd](#fluentd-elasticsearch) | Docker `fluentd` log driver | Elasticsearch | `--dry-run`; parsing with sample records |
| [Logstash](#logstash-elasticsearch) | Docker `gelf` log driver | Elasticsearch | `--config.test_and_exit`; filter with sample records |

None of the Elasticsearch examples was run against a real Elasticsearch.

!!! note "Podman"

    Podman has no `fluentd` or `gelf` log driver. With Podman, read the container's log from
    journald (Podman's default) or through its Docker-compatible socket, as the Alloy and Vector
    examples do.

## Grafana Alloy → Loki

Alloy loads every `.alloy` file of a directory (`alloy run /etc/alloy/`). Put `pipeline.alloy`
there together with **one** of the source files, and set the Loki URL in `pipeline.alloy`.

```hcl title="pipeline.alloy"
--8<-- "alloy/pipeline.alloy"
```

=== "Docker or Podman socket"

    ```hcl title="source-docker.alloy"
    --8<-- "alloy/source-docker.alloy"
    ```

    Mount the socket into Alloy's container, for example
    `-v /var/run/docker.sock:/var/run/docker.sock:ro`. With rootless Podman, mount
    `$XDG_RUNTIME_DIR/podman/podman.sock` there; on hosts with SELinux, also pass
    `--security-opt label=disable`.

=== "journald (Podman, Quadlets)"

    ```hcl title="source-journal.alloy"
    --8<-- "alloy/source-journal.alloy"
    ```

    If Alloy already ships the whole journal, do not add a second `loki.source.journal`: the
    lines would arrive twice. Instead, send the lines of the container (`__journal_container_name`
    is `fritzlogextractor`) from your existing source through `loki.process.fritzlogextractor`.

=== "Kubernetes"

    ```hcl title="source-kubernetes.alloy"
    --8<-- "alloy/source-kubernetes.alloy"
    ```

    Fits the [Kubernetes example](running.md#kubernetes) (`app: fritzlogextractor`). Alloy
    needs RBAC to list pods and read their logs. Only checked with `alloy validate`.

To query in Grafana:

```text
{job="fritzlogextractor", kind="event"} | json
{job="fritzlogextractor", kind="event", group="net"} | json | id = 22
{job="fritzlogextractor", kind="log", level=~"WARNING|ERROR"}
```

### Old entries and Loki

On its first start, the tool prints the whole log of the box, which can reach back weeks. Loki
rejects lines older than `reject_old_samples_max_age` (one week by default), so these are lost
unless you raise that limit for the first run. Later lines arrive in order, apart from mesh
entries a repeater hands over late; those are seconds or minutes behind, well within the hour
Loki accepts out of order.

## Vector → Elasticsearch

```yaml title="vector.yaml"
--8<-- "vector/vector.yaml"
```

The tests check the parsing and routing without Docker or Elasticsearch; run them after
changing the transforms:

```bash
vector test vector.yaml vector-tests.yaml
```

??? example "vector-tests.yaml"

    ```yaml
    --8<-- "vector/vector-tests.yaml"
    ```

## Fluentd → Elasticsearch

Run the tool's container with Docker's `fluentd` log driver, tagged `fritzlogextractor`:

```bash
docker run -d --name fritzlogextractor \
  --log-driver fluentd --log-opt fluentd-address=fluentd:24224 --log-opt tag=fritzlogextractor \
  ...
```

```text title="fluent.conf"
--8<-- "fluentd/fluent.conf"
```

Needs `fluent-plugin-elasticsearch`. The tool's own log lines keep the time Docker read them;
they are logged as they happen, so that is the time they were written.

## Logstash → Elasticsearch

Run the tool's container with Docker's `gelf` log driver:

```bash
docker run -d --name fritzlogextractor \
  --log-driver gelf --log-opt gelf-address=udp://logstash:12201 \
  ...
```

```text title="logstash.conf"
--8<-- "logstash/logstash.conf"
```

## Event times without offset

If the box does not tell its time zone, `event_time` has no UTC offset. The Fluentd and
Logstash examples then read it as UTC (or the collector's time zone), Vector takes the time it
read the line, and Alloy the time of the line before in the same stream (its default
`action_on_failure = "fudge"`). See [Output](output.md#fields).
