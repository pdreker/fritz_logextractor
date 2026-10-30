import json
from unittest.mock import MagicMock, patch

import pytest
from fritzconnection.core.exceptions import FritzActionError, FritzConnectionException
from requests.exceptions import ConnectionError as RequestsConnectionError

from fritzlogextractor.__main__ import main, poll_all
from fritzlogextractor.config import DeviceConfig
from fritzlogextractor.extractor import DeviceExtractor, ExtractionError

LOG_PATH = "/devicelog.lua?sid=0123456789abcdef"


def log_xml(*events):
    body = "".join(
        f"<Event><id>{i}</id><group>{g}</group><date>{d}</date><time>{t}</time><msg>{m}</msg></Event>"
        for i, g, d, t, m in events
    )
    return f"<DeviceLog>{body}</DeviceLog>".encode()


CONNECTED = ("22", "net", "09.10.26", "08:31:57", "Internetverbindung wurde hergestellt.")
LOGIN = ("504", "sys", "09.10.26", "21:49:06", "Anmeldung des Benutzers exporter.")


def response(body: bytes, status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.ok = status < 400
    resp.status_code = status
    resp.content = body
    return resp


def setup_fc(
    mock_fc_cls: MagicMock, *bodies: bytes, time_info=None, device_info=None
) -> MagicMock:
    fc = mock_fc_cls.return_value
    fc.address = "http://fritz.box"
    fc.port = 49000

    def call_action(service, action, **kwargs):
        if action == "X_AVM-DE_GetDeviceLogPath":
            return {"NewDeviceLogPath": LOG_PATH}
        if service == "Time1":
            if time_info is None:
                raise FritzActionError("no time")
            return time_info
        if action == "GetInfo" and device_info is not None:
            return device_info
        if action == "GetInfo":
            raise FritzActionError("no info")
        raise AssertionError(f"unexpected {service} {action}")

    fc.call_action.side_effect = call_action
    fc.session.get.side_effect = [response(b) for b in bodies]
    return fc


def device(**overrides) -> DeviceConfig:
    values = {"hostname": "fritz.box", "username": "u", "password": "p", "name": "Box"}
    values.update(overrides)
    return DeviceConfig(**values)


@patch("fritzlogextractor.extractor.FritzConnection")
class TestDeviceExtractor:
    def test_first_poll_returns_whole_buffer_oldest_first_with_offset(self, mock_fc_cls):
        setup_fc(
            mock_fc_cls,
            log_xml(LOGIN, CONNECTED),
            time_info={
                "NewLocalTimeZoneName": "CET-1CEST,M3.5.0,M10.5.0/3",
                "NewCurrentLocalTime": "2026-10-16T10:00:00+02:00",
            },
        )

        records = DeviceExtractor(device()).poll()

        assert [r["id"] for r in records] == [22, 504]
        assert records[0]["event_time"] == "2026-10-09T08:31:57+02:00"
        assert records[0]["device"] == "Box"
        assert records[0]["host"] == "fritz.box"

    def test_fetch_uses_the_tr064_session_and_timeout(self, mock_fc_cls):
        fc = setup_fc(mock_fc_cls, log_xml())

        DeviceExtractor(device(connection_timeout=7)).poll()

        fc.session.get.assert_called_once_with(f"http://fritz.box:49000{LOG_PATH}", timeout=7)
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

    def test_later_polls_return_only_new_entries(self, mock_fc_cls):
        setup_fc(mock_fc_cls, log_xml(CONNECTED), log_xml(CONNECTED), log_xml(LOGIN, CONNECTED))
        extractor = DeviceExtractor(device())

        assert len(extractor.poll()) == 1
        assert extractor.poll() == []
        assert [r["id"] for r in extractor.poll()] == [504]

    def test_missing_time_zone_gives_times_without_offset(self, mock_fc_cls):
        setup_fc(mock_fc_cls, log_xml(CONNECTED))

        assert DeviceExtractor(device()).poll()[0]["event_time"] == "2026-10-09T08:31:57"

    def test_http_error_raises_and_loses_nothing(self, mock_fc_cls):
        setup_fc(mock_fc_cls, b"", log_xml(CONNECTED))
        mock_fc_cls.return_value.session.get.side_effect = [
            response(b"", 500),
            response(log_xml(CONNECTED)),
        ]
        extractor = DeviceExtractor(device())

        with pytest.raises(ExtractionError, match="HTTP 500"):
            extractor.poll()
        assert len(extractor.poll()) == 1

    def test_unparsable_body_raises(self, mock_fc_cls):
        setup_fc(mock_fc_cls, b"<html>login</html>")

        with pytest.raises(ExtractionError, match="could not parse"):
            DeviceExtractor(device()).poll()

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


INFO = {"NewUpTime": 3600, "NewModelName": "FRITZ!Box 7690", "NewSoftwareVersion": "8.25"}


def read_polls(directory):
    return [json.loads(line) for line in (directory / "polls.jsonl").read_text().splitlines()]


@patch("fritzlogextractor.extractor.FritzConnection")
class TestCapture:
    def test_each_poll_is_logged_and_each_distinct_body_stored_once(self, mock_fc_cls, tmp_path):
        first, second = log_xml(CONNECTED), log_xml(LOGIN, CONNECTED)
        setup_fc(mock_fc_cls, first, first, second, device_info=INFO)
        extractor = DeviceExtractor(device(name="Fritz!Box 7690"), tmp_path)

        for _ in range(3):
            extractor.poll()

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
        setup_fc(mock_fc_cls, log_xml(CONNECTED), device_info=INFO)

        DeviceExtractor(device(), tmp_path).poll()

        directory = tmp_path / "Box"
        assert directory.stat().st_mode & 0o777 == 0o700
        assert {f.stat().st_mode & 0o777 for f in directory.iterdir()} == {0o600}

    def test_body_the_parser_rejects_is_kept(self, mock_fc_cls, tmp_path):
        setup_fc(mock_fc_cls, b"<html>login</html>", device_info=INFO)

        with pytest.raises(ExtractionError):
            DeviceExtractor(device(), tmp_path).poll()

        polls = read_polls(tmp_path / "Box")
        assert (tmp_path / "Box" / polls[0]["file"]).read_bytes() == b"<html>login</html>"

    def test_missing_device_info_is_logged_as_null(self, mock_fc_cls, tmp_path):
        setup_fc(mock_fc_cls, log_xml(CONNECTED))

        DeviceExtractor(device(), tmp_path).poll()

        assert read_polls(tmp_path / "Box")[0]["uptime"] is None

    def test_storage_error_does_not_stop_extraction(self, mock_fc_cls, tmp_path, caplog):
        setup_fc(mock_fc_cls, log_xml(CONNECTED), device_info=INFO)
        blocked = tmp_path / "file"
        blocked.write_text("")

        assert len(DeviceExtractor(device(), blocked).poll()) == 1
        assert "Box: could not store the snapshot" in caplog.text

    def test_failed_polls_are_logged_with_the_error(self, mock_fc_cls, tmp_path):
        setup_fc(mock_fc_cls, log_xml(CONNECTED), device_info=INFO)
        fc = mock_fc_cls.return_value
        fc.session.get.side_effect = [
            response(b"", 503),
            RequestsConnectionError("refused"),
            response(log_xml(CONNECTED)),
        ]
        extractor = DeviceExtractor(device(), tmp_path)

        for _ in range(2):
            with pytest.raises(ExtractionError):
                extractor.poll()
        extractor.poll()

        polls = read_polls(tmp_path / "Box")
        assert [p.get("error") for p in polls] == [
            "HTTP 503",
            "could not read the event log (ConnectionError)",
            None,
        ]
        assert "uptime" not in polls[0]
        assert polls[2]["uptime"] == 3600

    def test_failure_to_log_a_failed_poll_is_only_a_warning(self, mock_fc_cls, tmp_path, caplog):
        setup_fc(mock_fc_cls, b"", device_info=INFO)
        mock_fc_cls.return_value.session.get.side_effect = [response(b"", 500)]
        blocked = tmp_path / "file"
        blocked.write_text("")

        with pytest.raises(ExtractionError, match="HTTP 500"):
            DeviceExtractor(device(), blocked).poll()
        assert "Box: could not log the failed poll" in caplog.text

    def test_without_capture_dir_no_device_info_is_read(self, mock_fc_cls):
        fc = setup_fc(mock_fc_cls, log_xml(CONNECTED), device_info=INFO)

        DeviceExtractor(device()).poll()

        assert all(c.args[:2] != ("DeviceInfo1", "GetInfo") for c in fc.call_action.call_args_list)


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

    def test_once_reads_each_device_and_exits(self, mock_fc_cls, capsys):
        setup_fc(mock_fc_cls, log_xml(CONNECTED), log_xml(LOGIN))

        main(["--config", "tests/conffiles/validconfig.yaml", "--once"])

        lines = capsys.readouterr().out.splitlines()
        assert [json.loads(line)["device"] for line in lines] == ["Fritz!Box 7590", "Repeater"]

    def test_capture_option_stores_snapshots_per_device(self, mock_fc_cls, tmp_path, capsys):
        setup_fc(mock_fc_cls, log_xml(CONNECTED), log_xml(LOGIN), device_info=INFO)

        main(["--config", "tests/conffiles/validconfig.yaml", "--once", "--capture", str(tmp_path)])

        assert len(capsys.readouterr().out.splitlines()) == 2
        assert sorted(d.name for d in tmp_path.iterdir()) == ["Fritz_Box_7590", "Repeater"]
