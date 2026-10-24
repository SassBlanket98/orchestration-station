"""Config loading: TOML file with defaults, path overridable by env var."""

import os
import tomllib
from pathlib import Path

from pydantic import BaseModel, SecretStr, field_validator

from stagewatch.quota import validate_base_url

CONFIG_ENV = "STAGEWATCH_CONFIG"
DEFAULT_CONFIG_PATH = Path("~/.config/stagewatch/config.toml")
QUOTA_SERVICE_SECRET_ENV = "QUOTA_SERVICE_SECRET"


class ConfigError(ValueError):
    """A configured secret that cannot be used. The message never carries the secret."""


class Config(BaseModel):
    port: int = 8744
    scan_interval_s: float = 1.5
    discover_interval_s: float = 15
    # Loopback origin of the local quota_service daemon. Unset means quota is offline.
    quota_service_url: str | None = None
    # The bearer lives in this file, or in $QUOTA_SERVICE_SECRET instead.
    quota_service_secret_file: Path | None = None
    # Loopback origin of the local Monitor state server. None means the state proxy is off.
    monitor_url: str | None = "http://127.0.0.1:8743"
    # Optional VAPID setup for browser push. Private key paths are never exposed by the API.
    push_vapid_private_key_file: Path | None = None
    push_subject: str = "mailto:admin@localhost"
    push_store_file: Path = Path("~/.config/stagewatch/push.json")

    @field_validator("quota_service_url", "monitor_url")
    @classmethod
    def _loopback_upstream(cls, value: str | None) -> str | None:
        return None if value is None else validate_base_url(value)


def load_quota_service_secret(cfg: Config) -> SecretStr | None:
    """The quota_service bearer: the env var when set, else the secret file, else None.

    The value is never included in an error message or a log line.
    """
    from_env = os.environ.get(QUOTA_SERVICE_SECRET_ENV, "").strip()
    if from_env:
        return SecretStr(from_env)
    path = cfg.quota_service_secret_file
    if path is None:
        return None
    try:
        raw = path.expanduser().read_bytes()
    except OSError:
        raise ConfigError(f"quota_service secret file could not be read: {path}") from None
    try:
        text = raw.decode("utf-8").strip()
    except UnicodeDecodeError:
        raise ConfigError(f"quota_service secret file is not UTF-8 text: {path}") from None
    if not text:
        raise ConfigError(f"quota_service secret file is empty: {path}")
    return SecretStr(text)


def config_path() -> Path:
    """Config file location: `$STAGEWATCH_CONFIG` if set, else the default."""
    override = os.environ.get(CONFIG_ENV)
    if override:
        return Path(override).expanduser()
    return DEFAULT_CONFIG_PATH.expanduser()


def load_config(path: Path | None = None) -> Config:
    """Load the config. A missing file means defaults."""
    target = path if path is not None else config_path()
    if not target.is_file():
        return Config()
    with target.open("rb") as fh:
        data = tomllib.load(fh)
    return Config.model_validate(data)
