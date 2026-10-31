import pytest

from fritzlogextractor.config import (
    ConfigFileUnreadableError,
    ConfigNotAMappingError,
    EmptyConfigError,
    FritzPasswordFileDoesNotExistError,
    FritzPasswordMissingError,
    FritzPasswordTooLongError,
    NoDevicesFoundError,
    UnknownConfigKeyError,
    get_config,
)
from fritzlogextractor.config.config import DEFAULT_POLL_INTERVAL, DEFAULT_STATE_DIR, ExtractorConfig


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in (
        "FRITZ_HOSTNAME", "FRITZ_NAME", "FRITZ_USERNAME", "FRITZ_PASSWORD", "FRITZ_PASSWORD_FILE",
        "FRITZ_USE_TLS", "FRITZ_CONNECTION_TIMEOUT", "FRITZ_LOG_LEVEL", "FRITZ_POLL_INTERVAL",
        "FRITZ_STATE_DIR",
    ):
        monkeypatch.delenv(var, raising=False)


class TestFileConfig:
    def test_valid_config(self):
        config = get_config("tests/conffiles/validconfig.yaml")

        assert config.log_level == "DEBUG"
        assert config.poll_interval == 30
        assert config.state_dir == "/tmp/fritzlogextractor"
        assert [d.hostname for d in config.devices] == ["fritz.box", "repeater.local"]
        assert config.devices[0].use_tls is True
        assert config.devices[0].connection_timeout == 10
        assert config.devices[1].connection_timeout == 5

    def test_unreadable_file(self):
        with pytest.raises(ConfigFileUnreadableError, match="cannot read .*does-not-exist.yaml"):
            get_config("tests/conffiles/does-not-exist.yaml")

    def test_invalid_yaml(self, tmp_path):
        bad = tmp_path / "bad.yaml"
        bad.write_text("devices: [\n")

        with pytest.raises(ConfigFileUnreadableError, match="not valid YAML"):
            get_config(str(bad))

    @pytest.mark.parametrize(
        ("data", "where"),
        [
            (["a", "b"], "config"),
            ({"devices": "fritz.box"}, "devices must be a list"),
            ({"devices": ["fritz.box"]}, "device 1"),
        ],
    )
    def test_wrong_structure_is_a_config_error(self, data, where):
        with pytest.raises(ConfigNotAMappingError, match=where):
            ExtractorConfig.from_config(data)

    @pytest.mark.parametrize("timeout", [0, -1])
    def test_connection_timeout_must_be_positive(self, timeout):
        device = {"username": "u", "password": "p", "connection_timeout": timeout}
        with pytest.raises(ValueError, match="connection_timeout"):
            ExtractorConfig.from_config({"devices": [device]})

    def test_log_level_ignores_case(self):
        device = {"username": "u", "password": "p"}
        assert ExtractorConfig.from_config({"devices": [device], "log_level": "debug"}).log_level == (
            "DEBUG"
        )

    def test_empty_config(self):
        with pytest.raises(EmptyConfigError):
            ExtractorConfig.from_config(None)

    def test_no_devices(self):
        with pytest.raises(NoDevicesFoundError):
            ExtractorConfig.from_config({"devices": []})

    def test_poll_interval_too_short(self):
        device = {"username": "u", "password": "p"}
        with pytest.raises(ValueError, match="poll_interval"):
            ExtractorConfig.from_config({"devices": [device], "poll_interval": 5})


    def test_name_defaults_to_the_hostname(self):
        device = {"hostname": "Fritz.Repeater", "username": "u", "password": "p"}

        config = ExtractorConfig.from_config({"devices": [device]})

        assert config.devices[0].name == "fritz.repeater"

    def test_unknown_device_key_is_an_error_with_a_suggestion(self):
        device = {"hostname": "fritz.box", "username": "u", "pasword": "p"}
        with pytest.raises(
            UnknownConfigKeyError,
            match=r"^device 1 \(fritz.box\): unknown key 'pasword', did you mean 'password'\?$",
        ):
            ExtractorConfig.from_config({"devices": [device]})

    def test_unknown_top_level_key_is_an_error_with_a_suggestion(self):
        device = {"username": "u", "password": "p"}
        with pytest.raises(
            UnknownConfigKeyError,
            match=r"^config: unknown key 'poll_intervall', did you mean 'poll_interval'\?$",
        ):
            ExtractorConfig.from_config({"devices": [device], "poll_intervall": 10})

    def test_unknown_key_without_close_match(self):
        device = {"username": "u", "password": "p", "colour": "blue"}
        with pytest.raises(UnknownConfigKeyError, match=r"^device 1: unknown key 'colour'$"):
            ExtractorConfig.from_config({"devices": [device]})


class TestEnvConfig:
    def test_minimal_env(self, monkeypatch):
        monkeypatch.setenv("FRITZ_USERNAME", "logreader")
        monkeypatch.setenv("FRITZ_PASSWORD", "secret")

        config = get_config(None)

        assert config.poll_interval == DEFAULT_POLL_INTERVAL
        assert config.state_dir == DEFAULT_STATE_DIR
        dev = config.devices[0]
        assert dev.hostname == "fritz.box"
        assert dev.name == "fritz.box"
        assert dev.use_tls is False

    def test_full_env(self, monkeypatch):
        monkeypatch.setenv("FRITZ_USERNAME", "logreader")
        monkeypatch.setenv("FRITZ_PASSWORD", "secret")
        monkeypatch.setenv("FRITZ_HOSTNAME", "192.168.178.1")
        monkeypatch.setenv("FRITZ_NAME", "Keller")
        monkeypatch.setenv("FRITZ_USE_TLS", "true")
        monkeypatch.setenv("FRITZ_CONNECTION_TIMEOUT", "5")
        monkeypatch.setenv("FRITZ_POLL_INTERVAL", "120")
        monkeypatch.setenv("FRITZ_STATE_DIR", "/data")
        monkeypatch.setenv("FRITZ_LOG_LEVEL", "WARNING")

        config = get_config(None)

        assert config.poll_interval == 120
        assert config.state_dir == "/data"
        assert config.log_level == "WARNING"
        assert config.devices[0].hostname == "192.168.178.1"
        assert config.devices[0].name == "Keller"
        assert config.devices[0].use_tls is True
        assert config.devices[0].connection_timeout == 5

    def test_missing_username(self, monkeypatch):
        monkeypatch.setenv("FRITZ_PASSWORD", "secret")
        with pytest.raises(ValueError, match="username"):
            get_config(None)

    def test_missing_password(self, monkeypatch):
        monkeypatch.setenv("FRITZ_USERNAME", "logreader")
        with pytest.raises(FritzPasswordMissingError):
            get_config(None)

    def test_password_too_long(self, monkeypatch):
        monkeypatch.setenv("FRITZ_USERNAME", "logreader")
        monkeypatch.setenv("FRITZ_PASSWORD", "x" * 33)
        with pytest.raises(FritzPasswordTooLongError):
            get_config(None)

    def test_password_file_missing(self, monkeypatch):
        monkeypatch.setenv("FRITZ_USERNAME", "logreader")
        monkeypatch.setenv("FRITZ_PASSWORD_FILE", "/does/not/exist")
        with pytest.raises(FritzPasswordFileDoesNotExistError):
            get_config(None)

    def test_password_file(self, monkeypatch, tmp_path):
        pw = tmp_path / "pw"
        pw.write_text("secret\n")
        monkeypatch.setenv("FRITZ_USERNAME", "logreader")
        monkeypatch.setenv("FRITZ_PASSWORD_FILE", str(pw))

        assert get_config(None).devices[0].password_file == str(pw)
