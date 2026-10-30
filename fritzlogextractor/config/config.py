from __future__ import annotations

import difflib
import logging
import os
from pathlib import Path
from typing import Any

import attrs
import yaml
from attrs import converters, define, field, validators

from .exceptions import (
    ConfigFileUnreadableError,
    EmptyConfigError,
    FritzPasswordFileDoesNotExistError,
    FritzPasswordMissingError,
    FritzPasswordTooLongError,
    NoDevicesFoundError,
    UnknownConfigKeyError,
)

logger = logging.getLogger("fritzlogextractor.config")

# Fritz!OS rejects longer passwords for TR-064.
FRITZ_MAX_PASSWORD_LENGTH = 32

DEFAULT_CONNECTION_TIMEOUT: int = 10
DEFAULT_POLL_INTERVAL: int = 60
DEFAULT_STATE_DIR: str = "/var/lib/fritzlogextractor"


def _convert_optional_int(value: int | str | None) -> int | None:
    if value is None:
        return None
    number = int(value)
    if number == 0:
        return None
    return number


def _read_config_file(config_file_path: str) -> dict[str, Any]:
    try:
        with Path(config_file_path).open() as config_file:
            config = yaml.safe_load(config_file)
    except OSError as e:
        logger.exception("Config file specified but could not be read.")
        raise ConfigFileUnreadableError from e
    logger.info("Read configuration from %s.", config_file_path)
    return config


def _read_config_from_env() -> dict[str, Any]:
    config: dict[str, Any] = {}
    for key, env in (
        ("log_level", "FRITZ_LOG_LEVEL"),
        ("poll_interval", "FRITZ_POLL_INTERVAL"),
        ("state_dir", "FRITZ_STATE_DIR"),
    ):
        value = os.getenv(env)
        if value is not None:
            config[key] = value

    device: dict[str, Any] = {
        "username": os.getenv("FRITZ_USERNAME", ""),
        "password": os.getenv("FRITZ_PASSWORD"),
        "password_file": os.getenv("FRITZ_PASSWORD_FILE"),
        "use_tls": os.getenv("FRITZ_USE_TLS", "False"),
    }
    for key, env in (
        ("hostname", "FRITZ_HOSTNAME"),
        ("name", "FRITZ_NAME"),
        ("connection_timeout", "FRITZ_CONNECTION_TIMEOUT"),
    ):
        value = os.getenv(env)
        if value is not None:
            device[key] = value
    config["devices"] = [device]

    logger.info("No configuration file specified: configuration read from environment")
    return config


def _check_keys(config: dict[str, Any], cls: type, where: str) -> None:
    """Refuse keys the config class does not have: a typo would go unnoticed."""
    known = [a.name for a in attrs.fields(cls)]
    for key in config:
        if key not in known:
            close = difflib.get_close_matches(str(key), known, n=1)
            hint = f", did you mean '{close[0]}'?" if close else ""
            msg = f"{where}: unknown key '{key}'{hint}"
            raise UnknownConfigKeyError(msg)


def get_config(config_file_path: str | None) -> ExtractorConfig:
    config = _read_config_file(config_file_path) if config_file_path else _read_config_from_env()
    return ExtractorConfig.from_config(config)


@define
class DeviceConfig:
    hostname: str = field(validator=validators.min_len(1), converter=str.lower)
    username: str = field(validator=validators.min_len(1))
    password: str | None = field(default=None)
    password_file: str | None = field(default=None)
    # Defaults to the hostname.
    name: str = ""
    connection_timeout: int | None = field(
        default=DEFAULT_CONNECTION_TIMEOUT,
        converter=_convert_optional_int,
        validator=validators.optional(validators.ge(1)),
    )
    use_tls: bool = field(default=False, converter=converters.to_bool)

    @password.validator  # ty: ignore[unresolved-attribute]
    def check_password(self, _: attrs.Attribute, value: str | None) -> None:
        if value is not None and len(value) > FRITZ_MAX_PASSWORD_LENGTH:
            msg = f"Password is longer than {FRITZ_MAX_PASSWORD_LENGTH} characters."
            raise FritzPasswordTooLongError(msg)

    @password_file.validator  # ty: ignore[unresolved-attribute]
    def check_password_file(self, _: attrs.Attribute, value: str | None) -> None:
        if value is not None and not Path(value).is_file():
            msg = "Password file does not exist."
            raise FritzPasswordFileDoesNotExistError(msg)
        if value is None and self.password is None:
            msg = "Either password or password_file must be set."
            raise FritzPasswordMissingError(msg)

    def __attrs_post_init__(self) -> None:
        if not self.name:
            self.name = self.hostname

    @classmethod
    def from_config(cls, device: dict[str, Any], where: str = "device") -> DeviceConfig:
        hostname = device.get("hostname")
        if isinstance(hostname, str) and hostname:
            where = f"{where} ({hostname})"
        _check_keys(device, cls, where)
        return cls(
            hostname=device.get("hostname", "fritz.box"),
            username=device.get("username", ""),
            password=device.get("password"),
            password_file=device.get("password_file"),
            name=device.get("name", ""),
            connection_timeout=device.get("connection_timeout", DEFAULT_CONNECTION_TIMEOUT),
            use_tls=device.get("use_tls", False),
        )


@define
class ExtractorConfig:
    devices: list[DeviceConfig] = field(factory=list)
    log_level: str = field(
        default="INFO", validator=validators.in_(["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
    )
    poll_interval: int = field(
        default=DEFAULT_POLL_INTERVAL, converter=int, validator=validators.ge(10)
    )
    state_dir: str = field(default=DEFAULT_STATE_DIR, validator=validators.min_len(1))

    @devices.validator  # ty: ignore[unresolved-attribute]
    def check_devices(self, _: attrs.Attribute, value: list[DeviceConfig]) -> None:
        if not value:
            msg = "No devices found in config."
            raise NoDevicesFoundError(msg)

    @classmethod
    def from_config(cls, config: dict[str, Any] | None) -> ExtractorConfig:
        if config is None:
            msg = "No config found (check env vars or config file)."
            raise EmptyConfigError(msg)
        _check_keys(config, cls, "config")
        return cls(
            devices=[
                DeviceConfig.from_config(dev, f"device {n}")
                for n, dev in enumerate(config.get("devices") or [], start=1)
            ],
            log_level=config.get("log_level", "INFO"),
            poll_interval=config.get("poll_interval", DEFAULT_POLL_INTERVAL),
            state_dir=config.get("state_dir", DEFAULT_STATE_DIR),
        )


# Copyright 2026 Patrick Dreker <patrick@dreker.de>
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
