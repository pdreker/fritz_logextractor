import json
import socket
from unittest.mock import MagicMock, patch
from urllib.request import urlopen

import pytest
from fritzconnection.core.exceptions import FritzAuthorizationError, FritzConnectionException
from prometheus_client import REGISTRY

from fritzlogextractor.__main__ import main, poll_all
from fritzlogextractor.extractor import DeviceExtractor
from tests.test_extractor import CONNECTED, INFO, LOGIN, TRAINING, device, log_xml, poll, setup_fc


def value(name: str, device: str, **labels: str) -> float | None:
    """A sample of the default registry; metrics are global, so each test uses its own device."""
    return REGISTRY.get_sample_value(f"fritzlogextractor_{name}", {"device": device, **labels})


def positive(name: str, device: str) -> bool:
    sample = value(name, device)
    return sample is not None and sample > 0


@patch("fritzlogextractor.extractor.FritzConnection")
class TestMetrics:
    def test_series_exist_at_zero_before_the_first_poll(self, mock_fc_cls):
        DeviceExtractor(device(name="m-init"))

        assert value("polls_total", "m-init") == 0
        assert value("events_total", "m-init") == 0
        assert value("poll_errors_total", "m-init", reason="auth") == 0
        assert value("last_success_timestamp_seconds", "m-init") is None

    def test_successful_poll_counts_events_rows_and_success(self, mock_fc_cls, capsys):
        bad = ("x", "sys", "09.10.26", "08:31:40", "m")
        setup_fc(mock_fc_cls, log_xml(LOGIN, CONNECTED, bad))

        poll_all([DeviceExtractor(device(name="m-ok"))])

        assert len(capsys.readouterr().out.splitlines()) == 2
        assert value("polls_total", "m-ok") == 1
        assert value("events_total", "m-ok") == 2
        assert value("log_rows", "m-ok") == 2
        assert value("rejected_rows", "m-ok") == 1
        assert positive("last_success_timestamp_seconds", "m-ok")

    @pytest.mark.parametrize(
        ("error", "reason"),
        [
            (FritzAuthorizationError("401"), "auth"),
            (FritzConnectionException("down"), "connection"),
        ],
    )
    def test_failed_poll_counts_its_reason(self, mock_fc_cls, error, reason):
        mock_fc_cls.side_effect = error
        name = f"m-{reason}"

        poll_all([DeviceExtractor(device(name=name))])

        assert value("polls_total", name) == 1
        assert value("poll_errors_total", name, reason=reason) == 1
        assert value("last_success_timestamp_seconds", name) is None

    def test_parse_and_unexpected_errors_have_their_reasons(self, mock_fc_cls):
        setup_fc(mock_fc_cls, b"<html>login</html>")
        buggy = MagicMock(spec=DeviceExtractor)
        buggy.name = "m-buggy"
        buggy.poll.side_effect = TypeError("boom")

        poll_all([DeviceExtractor(device(name="m-parse")), buggy])

        assert value("poll_errors_total", "m-parse", reason="parse") == 1
        assert value("poll_errors_total", "m-buggy", reason="unexpected") == 1

    def test_reboot_counts_once_with_the_unwatched_time(self, mock_fc_cls, capsys):
        # Uptime 3600 s, then 60 s: the box rebooted about a minute before the second poll.
        setup_fc(
            mock_fc_cls,
            log_xml(CONNECTED),
            log_xml(TRAINING),
            log_xml(TRAINING),
            device_info=[INFO, {**INFO, "NewUpTime": 60}, {**INFO, "NewUpTime": 120}],
        )
        extractor = DeviceExtractor(device(name="m-reboot"))
        poll(extractor)

        # The reboot is seen, but the records are not written: it must not count yet.
        extractor.poll()
        assert value("box_reboots_total", "m-reboot") == 0
        poll(extractor)

        assert value("box_reboots_total", "m-reboot") == 1
        # The test's boot lies before its first poll; a negative time comes out as 0.
        assert value("reboot_unwatched_seconds", "m-reboot") == 0

    def test_reboot_while_the_tool_was_down_is_counted_from_the_state(self, mock_fc_cls, tmp_path):
        setup_fc(
            mock_fc_cls,
            log_xml(CONNECTED),
            log_xml(TRAINING),
            device_info=[INFO, {**INFO, "NewUpTime": 60}],
        )
        poll(DeviceExtractor(device(name="m-down"), tmp_path))
        state = tmp_path / "fritz.box.json"
        data = json.loads(state.read_text())
        # The last poll before the tool stopped was two hours earlier.
        data["tracker"]["polled"] = "2000-01-01T00:00:00+00:00"
        state.write_text(json.dumps(data))

        poll(DeviceExtractor(device(name="m-down"), tmp_path))

        assert value("box_reboots_total", "m-down") == 1
        unwatched = value("reboot_unwatched_seconds", "m-down")
        assert unwatched is not None
        assert unwatched > 3600

    def test_state_without_poll_time_still_counts_the_reboot(self, mock_fc_cls, tmp_path):
        setup_fc(
            mock_fc_cls,
            log_xml(CONNECTED),
            log_xml(TRAINING),
            device_info=[INFO, {**INFO, "NewUpTime": 60}],
        )
        poll(DeviceExtractor(device(name="m-old"), tmp_path))
        state = tmp_path / "fritz.box.json"
        data = json.loads(state.read_text())
        del data["tracker"]["polled"]
        state.write_text(json.dumps(data))

        poll(DeviceExtractor(device(name="m-old"), tmp_path))

        assert value("box_reboots_total", "m-old") == 1
        assert value("reboot_unwatched_seconds", "m-old") is None

    def test_state_write_errors_are_counted_every_time(self, mock_fc_cls, tmp_path):
        blocked = tmp_path / "file"
        blocked.write_text("")
        setup_fc(mock_fc_cls, log_xml(CONNECTED), log_xml(CONNECTED))
        extractor = DeviceExtractor(device(name="m-state"), blocked)

        poll(extractor)
        poll(extractor)

        assert value("state_write_errors_total", "m-state") == 2


@pytest.fixture
def metrics_config(tmp_path):
    def write(port):
        path = tmp_path / "config.yaml"
        path.write_text(
            f"state_dir: {tmp_path / 'state'}\n"
            f"metrics_port: {port}\nmetrics_address: 127.0.0.1\n"
            "devices:\n  - name: m-main\n    hostname: fritz.box\n    username: u\n    password: p\n"
        )
        return str(path)

    return write


@patch("fritzlogextractor.extractor.FritzConnection")
class TestServing:
    def test_port_is_served_with_build_info(self, mock_fc_cls, metrics_config, unused_port):
        with patch("fritzlogextractor.__main__.run"):
            main(["--config", metrics_config(unused_port)])

        body = urlopen(f"http://127.0.0.1:{unused_port}/metrics").read().decode()  # noqa: S310
        assert "fritzlogextractor_build_info{" in body
        assert "fritzlogextractor_polls_total{" in body
        assert "process_" in body or "python_info" in body
        assert "_created" not in body

    def test_once_does_not_serve(self, mock_fc_cls, metrics_config):
        with (
            patch("fritzlogextractor.__main__.run"),
            patch("fritzlogextractor.metrics.serve") as serve,
        ):
            main(["--config", metrics_config(9788), "--once"])

        serve.assert_not_called()

    def test_port_in_use_exits_1(self, mock_fc_cls, metrics_config, capsys):
        with (
            patch("fritzlogextractor.__main__.run") as run,
            patch("fritzlogextractor.metrics.serve", side_effect=OSError(98, "Address in use")),
            pytest.raises(SystemExit) as exc,
        ):
            main(["--config", metrics_config(9788)])

        assert exc.value.code == 1
        assert "Cannot serve metrics on 127.0.0.1:9788 (Address in use)" in capsys.readouterr().err
        run.assert_not_called()


@pytest.fixture
def unused_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
