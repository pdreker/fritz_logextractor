# Capturing snapshots

To study how a box changes its log over time (folded entries, reboots, timestamps before time
sync), `--capture DIR` also stores what the box sends, next to the normal output:

```bash
fritzlogextractor --config config.yaml --capture ./capture > /dev/null
```

Each device gets a directory below `DIR`. Every poll adds a line to `polls.jsonl` with poll time,
uptime, model, firmware and the SHA-256 of the log, or with `error` if the log could not be read
(for example while the device reboots). The log itself is stored once per distinct content as
`<sha256 prefix>.xml`, so a log that does not change costs one line per poll.

In the container, use a directory in the volume, for example
`--capture /var/lib/fritzlogextractor/capture`.

!!! danger "Not redacted"

    The snapshots hold what the box sent: addresses, host and user names and DynDNS tokens.
    Redact them before you share them.

To see what happens around a reboot, set `poll_interval` to 10 for the time of the test.
