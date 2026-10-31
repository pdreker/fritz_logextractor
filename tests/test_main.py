import json
import logging
import os
import signal
import threading
from datetime import datetime, timedelta

import pytest

from fritzlogextractor import __version__
from fritzlogextractor.__main__ import main, stop_on_signals
from fritzlogextractor.logs import setup_logging


def test_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == __version__


def test_version_comes_from_the_installed_distribution():
    from importlib.metadata import version

    assert __version__ == version("fritz-logextractor")


def test_bad_config_exits_1(monkeypatch):
    monkeypatch.delenv("FRITZ_USERNAME", raising=False)
    monkeypatch.delenv("FRITZ_PASSWORD", raising=False)
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 1


def test_own_log_lines_are_json_by_default(monkeypatch, capsys):
    monkeypatch.delenv("FRITZ_USERNAME", raising=False)
    monkeypatch.delenv("FRITZ_PASSWORD", raising=False)
    with pytest.raises(SystemExit):
        main([])

    lines = capsys.readouterr().err.splitlines()
    record = json.loads(lines[-1])
    assert record["kind"] == "log"
    assert record["level"] == "ERROR"
    assert record["logger"] == "fritzlogextractor"
    assert record["msg"].startswith("Invalid configuration: ")
    assert "exc" not in record
    assert datetime.fromisoformat(record["time"]).utcoffset() == timedelta(0)


def test_text_log_format_for_terminals(monkeypatch, capsys):
    monkeypatch.delenv("FRITZ_USERNAME", raising=False)
    monkeypatch.delenv("FRITZ_PASSWORD", raising=False)
    with pytest.raises(SystemExit):
        main(["--log-format", "text"])

    err = capsys.readouterr().err
    assert "ERROR fritzlogextractor | Invalid configuration: " in err
    assert "Traceback" not in err
    assert not err.lstrip().startswith("{")


def test_json_log_line_stays_on_one_line_with_odd_text(capsys):
    setup_logging("INFO", "json")
    logging.getLogger("x").warning("two\nlines \x1b[2J and %s", "an arg")

    [line] = capsys.readouterr().err.splitlines()
    assert json.loads(line)["msg"] == "two\nlines \x1b[2J and an arg"


def test_sigterm_ends_the_run_after_the_current_poll():
    stop = threading.Event()
    stop_on_signals(stop)

    os.kill(os.getpid(), signal.SIGTERM)

    assert stop.wait(1)
