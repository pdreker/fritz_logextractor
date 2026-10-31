import json
import logging
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from fritzconnection.core.exceptions import FritzActionError, FritzConnectionException
from requests.exceptions import ConnectionError as RequestsConnectionError

from fritzlogextractor.__main__ import OutputError, main, poll_all
from fritzlogextractor.config import DeviceConfig
from fritzlogextractor.extractor import DeviceExtractor, ExtractionError

LOG_PATH = "/devicelog.lua?sid=0123456789abcdef"
TZ_RULE = "CET-1CEST,M3.5.0,M10.5.0/3"


def log_xml(*events):
    body = "".join(
        f"<Event><id>{i}</id><group>{g}</group><date>{d}</date><time>{t}</time><msg>{m}</msg></Event>"
        for i, g, d, t, m in events
    )
    return f"<DeviceLog>{body}</DeviceLog>".encode()


CONNECTED = ("22", "net", "09.10.26", "08:31:57", "Internetverbindung wurde hergestellt.")
LOGIN = ("504", "sys", "09.10.26", "21:49:06", "Anmeldung des Benutzers exporter.")
INFO = {
    "NewUpTime": 3600,
    "NewModelName": "FRITZ!Box 7690",
    "NewSoftwareVersion": "8.25",
    "NewSerialNumber": "SERIAL1",
}
NO_INFO = object()


def response(body: bytes, status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.ok = status < 400
    resp.status_code = status
    resp.iter_content.return_value = [body]
    resp.__enter__.return_value = resp
    return resp


def setup_fc(mock_fc_cls: MagicMock, *bodies: bytes, time_info=None, device_info=INFO):
    fc = mock_fc_cls.return_value
    fc.address = "http://fritz.box"
    fc.port = 49000
    infos = device_info if isinstance(device_info, list) else None

    def call_action(service, action, **kwargs):
        if action == "X_AVM-DE_GetDeviceLogPath":
            return {"NewDeviceLogPath": LOG_PATH}
        if service == "Time1":
            if time_info is None:
                raise FritzActionError("no time")
            return time_info
        if (service, action) == ("DeviceInfo1", "GetInfo"):
            info = infos.pop(0) if infos is not None else device_info
            if info is NO_INFO:
                raise FritzActionError("no info")
            return info
        raise AssertionError(f"unexpected {service} {action}")

    fc.call_action.side_effect = call_action
    fc.session.get.side_effect = [response(b) for b in bodies]
    return fc


def device(**overrides) -> DeviceConfig:
    values = {"hostname": "fritz.box", "username": "u", "password": "p", "name": "Box"}
    values.update(overrides)
    return DeviceConfig(**values)


def poll(extractor: DeviceExtractor) -> list[dict]:
    """Poll and commit, as poll_all() does once the records are written."""
    records = extractor.poll()
    extractor.commit()
    return records


@patch("fritzlogextractor.extractor.FritzConnection")
class TestDeviceExtractor:
    def test_first_poll_returns_whole_buffer_oldest_first_with_offset(self, mock_fc_cls):
        setup_fc(
            mock_fc_cls,
            log_xml(LOGIN, CONNECTED),
            time_info={
                "NewLocalTimeZoneName": TZ_RULE,
                "NewCurrentLocalTime": "2026-10-16T10:00:00+02:00",
            },
        )

        records = DeviceExtractor(device()).poll()

        assert [r["id"] for r in records] == [22, 504]
        assert records[0]["event_time"] == "2026-10-09T08:31:57+02:00"
        assert records[0]["device"] == "Box"
        assert records[0]["host"] == "fritz.box"
        assert records[0]["serial"] == "SERIAL1"
        assert records[0]["repeat"] == 1
        assert records[0]["time_estimated"] is False

    def test_fetch_uses_the_tr064_session_and_timeout(self, mock_fc_cls):
        fc = setup_fc(mock_fc_cls, log_xml())

        DeviceExtractor(device(connection_timeout=7)).poll()

        fc.session.get.assert_called_once_with(
            f"http://fritz.box:49000{LOG_PATH}", timeout=7, stream=True
        )
        assert mock_fc_cls.call_args.kwargs == {
            "address": "fritz.box",
            "user": "u",
            "password": "p",
            "timeout": 7,
            "use_tls": False,
        }

    def test_password_file_is_read(self, mock_fc_cls, tmp_path):
        pw = tmp_path / "pw"
        pw.write_text("from-file\n")
        setup_fc(mock_fc_cls, log_xml())

        DeviceExtractor(device(password=None, password_file=str(pw))).poll()

        assert mock_fc_cls.call_args.kwargs["password"] == "from-file"

    @pytest.mark.parametrize(("content", "error"), [(None, "No such file"), ("x" * 33, "longer than 32")])
    def test_bad_password_file_is_an_extraction_error(self, mock_fc_cls, tmp_path, content, error):
        pw = tmp_path / "pw"
        pw.write_text("p")
        extractor = DeviceExtractor(device(password=None, password_file=str(pw)))
        if content is None:
            pw.unlink()
        else:
            pw.write_text(content)

        with pytest.raises(ExtractionError, match=f"could not read the password .*{error}"):
            extractor.poll()
        mock_fc_cls.assert_not_called()

    def test_later_polls_return_only_new_entries(self, mock_fc_cls):
        setup_fc(mock_fc_cls, log_xml(CONNECTED), log_xml(CONNECTED), log_xml(LOGIN, CONNECTED))
        extractor = DeviceExtractor(device())

        assert len(poll(extractor)) == 1
        assert poll(extractor) == []
        assert [r["id"] for r in poll(extractor)] == [504]

    def test_entries_come_again_until_committed(self, mock_fc_cls):
        setup_fc(mock_fc_cls, log_xml(CONNECTED), log_xml(CONNECTED), log_xml(CONNECTED))
        extractor = DeviceExtractor(device())

        assert len(extractor.poll()) == 1
        assert len(extractor.poll()) == 1
        extractor.commit()
        assert extractor.poll() == []

    def test_folded_row_gives_one_record_with_repeat(self, mock_fc_cls):
        once = ("504", "sys", "09.10.26", "21:48:36", "Anmeldung gescheitert.")
        thrice = (
            "504",
            "sys",
            "09.10.26",
            "21:49:06",
            "Anmeldung gescheitert. [3 Meldungen seit 09.10.26 21:48:36]",
        )
        setup_fc(mock_fc_cls, log_xml(once), log_xml(thrice))
        extractor = DeviceExtractor(device())
        poll(extractor)

        [record] = poll(extractor)

        assert record["repeat"] == 2
        assert record["msg"] == "Anmeldung gescheitert."
        assert record["box_time"] == "09.10.26 21:49:06"

    def test_missing_time_zone_gives_times_without_offset(self, mock_fc_cls):
        setup_fc(mock_fc_cls, log_xml(CONNECTED))

        assert DeviceExtractor(device()).poll()[0]["event_time"] == "2026-10-09T08:31:57"

    @pytest.mark.parametrize(
        "current", [None, datetime(1970, 1, 1, 0, 1, 28), "not a time"], ids=repr
    )
    def test_current_time_before_clock_sync_falls_back_to_the_rule(self, mock_fc_cls, current):
        setup_fc(
            mock_fc_cls,
            log_xml(CONNECTED),
            time_info={"NewLocalTimeZoneName": TZ_RULE, "NewCurrentLocalTime": current},
        )

        assert DeviceExtractor(device()).poll()[0]["event_time"] == "2026-10-09T08:31:57+02:00"

    def test_current_time_before_clock_sync_without_rule_gives_no_offset(self, mock_fc_cls):
        setup_fc(mock_fc_cls, log_xml(CONNECTED), time_info={"NewCurrentLocalTime": None})

        assert DeviceExtractor(device()).poll()[0]["event_time"] == "2026-10-09T08:31:57"

    def test_device_info_is_required(self, mock_fc_cls):
        setup_fc(mock_fc_cls, log_xml(CONNECTED), device_info=NO_INFO)

        with pytest.raises(ExtractionError, match=r"could not read the event log \(FritzActionError\)"):
            DeviceExtractor(device()).poll()

    def test_http_error_raises_and_loses_nothing(self, mock_fc_cls):
        setup_fc(mock_fc_cls)
        mock_fc_cls.return_value.session.get.side_effect = [
            response(b"", 500),
            response(log_xml(CONNECTED)),
        ]
        extractor = DeviceExtractor(device())

        with pytest.raises(ExtractionError, match="HTTP 500"):
            extractor.poll()
        assert len(extractor.poll()) == 1

    @pytest.mark.parametrize("path", ["@evil.example/x", "//evil.example/x", "devicelog.lua", None])
    def test_log_path_must_be_a_plain_path(self, mock_fc_cls, path):
        fc = setup_fc(mock_fc_cls, log_xml(CONNECTED))
        call_action = fc.call_action.side_effect

        def odd_path(service, action, **kwargs):
            if action == "X_AVM-DE_GetDeviceLogPath":
                return {"NewDeviceLogPath": path}
            return call_action(service, action, **kwargs)

        fc.call_action.side_effect = odd_path

        with pytest.raises(ExtractionError, match="unexpected event log path"):
            DeviceExtractor(device()).poll()
        fc.session.get.assert_not_called()

    def test_oversized_log_is_refused(self, mock_fc_cls):
        setup_fc(mock_fc_cls)
        big = response(b"")
        big.iter_content.return_value = [b"x" * 1024 * 1024] * 9
        mock_fc_cls.return_value.session.get.side_effect = [big]

        with pytest.raises(ExtractionError, match="larger than"):
            DeviceExtractor(device()).poll()

    def test_unparsable_body_raises(self, mock_fc_cls):
        setup_fc(mock_fc_cls, b"<html>login</html>")

        with pytest.raises(ExtractionError, match="could not parse"):
            DeviceExtractor(device()).poll()

    def test_rejected_entries_warn_once_not_every_poll(self, mock_fc_cls, caplog):
        bad = log_xml(("x", "sys", "09.10.26", "08:31:40", "m"), CONNECTED)
        setup_fc(mock_fc_cls, bad, bad, bad)
        extractor = DeviceExtractor(device())

        for _ in range(3):
            poll(extractor)

        assert caplog.text.count("failed validation") == 1

    @pytest.mark.parametrize(
        "error", [FritzConnectionException("down"), RequestsConnectionError("refused")]
    )
    def test_connection_error_reconnects_on_next_poll(self, mock_fc_cls, error):
        mock_fc_cls.side_effect = [error, mock_fc_cls.return_value]
        setup_fc(mock_fc_cls, log_xml(CONNECTED))
        extractor = DeviceExtractor(device())

        with pytest.raises(ExtractionError) as exc:
            extractor.poll()
        assert "refused" not in str(exc.value)
        assert len(extractor.poll()) == 1
        assert mock_fc_cls.call_count == 2


TRAINING = ("12", "net", "01.01.70", "00:00:34", "DSL-Synchronisierung beginnt (Training).")


@patch("fritzlogextractor.extractor.FritzConnection")
class TestReboot:
    def test_row_before_sync_is_estimated_on_our_clock_while_the_box_has_no_time(
        self, mock_fc_cls
    ):
        setup_fc(
            mock_fc_cls,
            log_xml(TRAINING),
            device_info={**INFO, "NewUpTime": 86},
            time_info={"NewLocalTimeZoneName": TZ_RULE, "NewCurrentLocalTime": None},
        )
        before = datetime.now(UTC)

        [record] = DeviceExtractor(device()).poll()

        event_time = datetime.fromisoformat(record["event_time"])
        expected = before - timedelta(seconds=86 - 34)
        assert record["time_estimated"] is True
        assert record["box_time"] == "01.01.70 00:00:34"
        assert event_time.utcoffset() == timedelta(0)
        assert abs(event_time - expected) < timedelta(seconds=5)

    def test_row_before_sync_is_put_on_the_box_clock_once_synced(self, mock_fc_cls):
        setup_fc(
            mock_fc_cls,
            log_xml(TRAINING),
            device_info={**INFO, "NewUpTime": 192},
            time_info={
                "NewLocalTimeZoneName": TZ_RULE,
                "NewCurrentLocalTime": "2026-10-31T00:18:49+01:00",
            },
        )

        [record] = DeviceExtractor(device()).poll()

        # boot = 00:18:49 - 192 s = 00:15:37, plus 34 s
        assert record["event_time"] == "2026-10-31T00:16:11+01:00"
        assert record["time_estimated"] is True

    def test_reboot_makes_the_whole_log_new_and_is_logged(self, mock_fc_cls, caplog):
        caplog.set_level(logging.INFO)
        setup_fc(
            mock_fc_cls,
            log_xml(TRAINING),
            log_xml(TRAINING),
            device_info=[{**INFO, "NewUpTime": 1294670}, {**INFO, "NewUpTime": 86}],
        )
        extractor = DeviceExtractor(device())
        poll(extractor)

        assert len(poll(extractor)) == 1
        assert "Box has restarted" in caplog.text


@patch("fritzlogextractor.extractor.FritzConnection")
class TestState:
    def test_restart_of_the_tool_does_not_emit_the_log_again(self, mock_fc_cls, tmp_path):
        setup_fc(mock_fc_cls, log_xml(CONNECTED), log_xml(LOGIN, CONNECTED))
        poll(DeviceExtractor(device(), tmp_path))

        records = poll(DeviceExtractor(device(), tmp_path))

        assert [r["id"] for r in records] == [504]

    def test_state_is_written_only_on_commit(self, mock_fc_cls, tmp_path):
        setup_fc(mock_fc_cls, log_xml(CONNECTED), log_xml(CONNECTED))
        DeviceExtractor(device(), tmp_path).poll()

        assert len(DeviceExtractor(device(), tmp_path).poll()) == 1

    def test_state_file_is_private_and_named_after_the_host(self, mock_fc_cls, tmp_path):
        setup_fc(mock_fc_cls, log_xml(CONNECTED))

        poll(DeviceExtractor(device(hostname="Fritz.Box"), tmp_path / "state"))

        assert (tmp_path / "state").stat().st_mode & 0o777 == 0o700
        assert [f.name for f in (tmp_path / "state").iterdir()] == ["fritz.box.json"]
        assert (tmp_path / "state" / "fritz.box.json").stat().st_mode & 0o777 == 0o600

    def test_state_file_is_private_even_over_a_left_over_partial_file(self, mock_fc_cls, tmp_path):
        setup_fc(mock_fc_cls, log_xml(CONNECTED))
        (tmp_path / "state").mkdir()
        left_over = tmp_path / "state" / "fritz.box.tmp"
        left_over.write_text("partial")
        left_over.chmod(0o644)

        poll(DeviceExtractor(device(), tmp_path / "state"))

        assert (tmp_path / "state" / "fritz.box.json").stat().st_mode & 0o777 == 0o600

    def test_state_of_another_device_is_ignored(self, mock_fc_cls, tmp_path):
        setup_fc(
            mock_fc_cls,
            log_xml(CONNECTED),
            log_xml(CONNECTED),
            device_info=[INFO, {**INFO, "NewSerialNumber": "OTHER"}],
        )
        poll(DeviceExtractor(device(), tmp_path))

        assert len(poll(DeviceExtractor(device(), tmp_path))) == 1

    @pytest.mark.parametrize("content", ["not json", "[]", '{"serial": "SERIAL1"}'])
    def test_broken_state_file_is_ignored_with_a_warning(
        self, mock_fc_cls, tmp_path, caplog, content
    ):
        (tmp_path / "fritz.box.json").write_text(content)
        setup_fc(mock_fc_cls, log_xml(CONNECTED))

        assert len(poll(DeviceExtractor(device(), tmp_path))) == 1
        assert "state file" in caplog.text

    def test_unwritable_state_dir_warns_once_and_keeps_going(self, mock_fc_cls, tmp_path, caplog):
        blocked = tmp_path / "file"
        blocked.write_text("")
        setup_fc(mock_fc_cls, log_xml(CONNECTED), log_xml(CONNECTED), log_xml(LOGIN, CONNECTED))
        extractor = DeviceExtractor(device(), blocked)

        assert len(poll(extractor)) == 1
        assert poll(extractor) == []
        assert len(poll(extractor)) == 1
        assert caplog.text.count("could not write the state file") == 1


def read_polls(directory):
    return [json.loads(line) for line in (directory / "polls.jsonl").read_text().splitlines()]


@patch("fritzlogextractor.extractor.FritzConnection")
class TestCapture:
    def test_each_poll_is_logged_and_each_distinct_body_stored_once(self, mock_fc_cls, tmp_path):
        first, second = log_xml(CONNECTED), log_xml(LOGIN, CONNECTED)
        setup_fc(mock_fc_cls, first, first, second)
        extractor = DeviceExtractor(device(name="Fritz!Box 7690"), capture_dir=tmp_path)

        for _ in range(3):
            poll(extractor)

        directory = tmp_path / "Fritz_Box_7690"
        polls = read_polls(directory)
        assert [p["uptime"] for p in polls] == [3600] * 3
        assert polls[0]["model"] == "FRITZ!Box 7690"
        assert polls[0]["firmware"] == "8.25"
        assert polls[0]["file"] == polls[1]["file"] != polls[2]["file"]
        assert (directory / polls[0]["file"]).read_bytes() == first
        assert (directory / polls[2]["file"]).read_bytes() == second
        assert sorted(f.name for f in directory.iterdir() if f.suffix == ".xml") == sorted(
            {p["file"] for p in polls}
        )

    def test_files_are_private(self, mock_fc_cls, tmp_path):
        setup_fc(mock_fc_cls, log_xml(CONNECTED))

        DeviceExtractor(device(), capture_dir=tmp_path).poll()

        directory = tmp_path / "Box"
        assert directory.stat().st_mode & 0o777 == 0o700
        assert {f.stat().st_mode & 0o777 for f in directory.iterdir()} == {0o600}

    def test_body_the_parser_rejects_is_kept(self, mock_fc_cls, tmp_path):
        setup_fc(mock_fc_cls, b"<html>login</html>")

        with pytest.raises(ExtractionError):
            DeviceExtractor(device(), capture_dir=tmp_path).poll()

        polls = read_polls(tmp_path / "Box")
        assert (tmp_path / "Box" / polls[0]["file"]).read_bytes() == b"<html>login</html>"

    def test_storage_error_does_not_stop_extraction(self, mock_fc_cls, tmp_path, caplog):
        setup_fc(mock_fc_cls, log_xml(CONNECTED))
        blocked = tmp_path / "file"
        blocked.write_text("")

        assert len(DeviceExtractor(device(), capture_dir=blocked).poll()) == 1
        assert "Box: could not store the snapshot" in caplog.text

    def test_failed_polls_are_logged_with_the_error(self, mock_fc_cls, tmp_path):
        # GetInfo is asked only after the log came through.
        setup_fc(mock_fc_cls, device_info=[NO_INFO, INFO])
        mock_fc_cls.return_value.session.get.side_effect = [
            response(b"", 503),
            RequestsConnectionError("refused"),
            response(log_xml(CONNECTED)),
            response(log_xml(CONNECTED)),
        ]
        extractor = DeviceExtractor(device(), capture_dir=tmp_path)

        for _ in range(3):
            with pytest.raises(ExtractionError):
                extractor.poll()
        extractor.poll()

        polls = read_polls(tmp_path / "Box")
        assert [p.get("error") for p in polls] == [
            "HTTP 503",
            "could not read the event log (ConnectionError)",
            "could not read the event log (FritzActionError)",
            None,
        ]
        assert "uptime" not in polls[0]
        assert polls[3]["uptime"] == 3600

    def test_failure_to_log_a_failed_poll_is_only_a_warning(self, mock_fc_cls, tmp_path, caplog):
        setup_fc(mock_fc_cls)
        mock_fc_cls.return_value.session.get.side_effect = [response(b"", 500)]
        blocked = tmp_path / "file"
        blocked.write_text("")

        with pytest.raises(ExtractionError, match="HTTP 500"):
            DeviceExtractor(device(), capture_dir=blocked).poll()
        assert "Box: could not log the failed poll" in caplog.text


@pytest.fixture
def config_file(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        f"state_dir: {tmp_path / 'state'}\n"
        "devices:\n"
        "  - name: Fritz!Box 7590\n    hostname: fritz.box\n    username: u\n    password: p\n"
        "  - name: Repeater\n    hostname: repeater.local\n    username: u\n    password: p\n"
    )
    return str(path)


@patch("fritzlogextractor.extractor.FritzConnection")
class TestMain:
    def test_poll_all_prints_json_lines_and_keeps_going_on_errors(self, mock_fc_cls, capsys, caplog):
        setup_fc(mock_fc_cls, log_xml(LOGIN, CONNECTED))
        broken = MagicMock(spec=DeviceExtractor)
        broken.name = "Broken"
        broken.poll.side_effect = ExtractionError("could not read the event log (Timeout)")

        poll_all([broken, DeviceExtractor(device())])

        lines = capsys.readouterr().out.splitlines()
        assert [json.loads(line)["id"] for line in lines] == [22, 504]
        assert "Broken: could not read the event log (Timeout), will retry" in caplog.text

    def test_poll_all_commits_only_after_writing(self, mock_fc_cls, capsys):
        setup_fc(mock_fc_cls, log_xml(CONNECTED), log_xml(CONNECTED))
        extractor = DeviceExtractor(device())

        with patch("sys.stdout.write", side_effect=BrokenPipeError), pytest.raises(OutputError):
            poll_all([extractor])
        poll_all([extractor])

        assert [json.loads(line)["id"] for line in capsys.readouterr().out.splitlines()] == [22]

    def test_closed_stdout_ends_the_tool(self, mock_fc_cls, config_file, capsys):
        setup_fc(mock_fc_cls, log_xml(CONNECTED), log_xml(LOGIN))

        with (
            patch("sys.stdout.write", side_effect=BrokenPipeError),
            patch("fritzlogextractor.__main__._discard_stdout") as discard,
            pytest.raises(SystemExit) as exc,
        ):
            main(["--config", config_file, "--once"])

        assert exc.value.code == 1
        assert "Cannot write the events to stdout" in capsys.readouterr().err
        discard.assert_called_once()

    def test_poll_all_survives_unexpected_errors(self, mock_fc_cls, capsys, caplog):
        setup_fc(mock_fc_cls, log_xml(CONNECTED))
        buggy = MagicMock(spec=DeviceExtractor)
        buggy.name = "Buggy"
        buggy.poll.side_effect = TypeError("fromisoformat: argument must be str")

        poll_all([buggy, DeviceExtractor(device())])

        assert [json.loads(line)["id"] for line in capsys.readouterr().out.splitlines()] == [22]
        assert "Buggy: unexpected error, will retry" in caplog.text
        assert "TypeError: fromisoformat" in caplog.text

    def test_once_reads_each_device_and_exits(self, mock_fc_cls, capsys, config_file, tmp_path):
        setup_fc(mock_fc_cls, log_xml(CONNECTED), log_xml(LOGIN))

        main(["--config", config_file, "--once"])

        lines = capsys.readouterr().out.splitlines()
        assert [json.loads(line)["device"] for line in lines] == ["Fritz!Box 7590", "Repeater"]
        assert sorted(f.name for f in (tmp_path / "state").iterdir()) == [
            "fritz.box.json",
            "repeater.local.json",
        ]

    def test_capture_option_stores_snapshots_per_device(
        self, mock_fc_cls, tmp_path, capsys, config_file
    ):
        setup_fc(mock_fc_cls, log_xml(CONNECTED), log_xml(LOGIN))

        main(["--config", config_file, "--once", "--capture", str(tmp_path / "cap")])

        assert len(capsys.readouterr().out.splitlines()) == 2
        assert sorted(d.name for d in (tmp_path / "cap").iterdir()) == ["Fritz_Box_7590", "Repeater"]
