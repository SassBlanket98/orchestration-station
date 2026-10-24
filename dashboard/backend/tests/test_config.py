from pathlib import Path

import pytest
from pydantic import ValidationError

from stagewatch.config import (
    CONFIG_ENV,
    QUOTA_SERVICE_SECRET_ENV,
    Config,
    ConfigError,
    config_path,
    load_config,
    load_quota_service_secret,
)

SECRET = "tw-secret-value-123"


def test_missing_file_gives_defaults(tmp_path: Path) -> None:
    cfg = load_config(tmp_path / "absent.toml")

    assert cfg == Config()
    assert cfg.port == 8744
    assert cfg.scan_interval_s == 1.5
    assert cfg.discover_interval_s == 15


def test_legacy_discovery_config_keys_are_ignored(tmp_path: Path) -> None:
    target = tmp_path / "config.toml"
    target.write_text(
        'roots = ["~/work", "/srv/repos"]\n'
        "port = 9001\n"
        "scan_interval_s = 3.0\n"
        'exclude = ["old-*"]\n',
        encoding="utf-8",
    )

    cfg = load_config(target)

    assert cfg.port == 9001
    assert cfg.scan_interval_s == 3.0
    assert cfg.discover_interval_s == 15
    assert "roots" not in cfg.model_dump()
    assert "exclude" not in cfg.model_dump()


def test_env_var_overrides_config_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    override = tmp_path / "custom.toml"
    monkeypatch.setenv(CONFIG_ENV, str(override))

    assert config_path() == override


def test_default_config_path_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(CONFIG_ENV, raising=False)

    assert config_path().parts[-3:] == (".config", "stagewatch", "config.toml")


def test_quota_service_defaults_to_offline() -> None:
    cfg = Config()

    assert cfg.quota_service_url is None
    assert cfg.quota_service_secret_file is None


@pytest.mark.parametrize(
    "url",
    ["http://127.0.0.1:8787", "http://localhost:8787/", "https://[::1]:9000"],
)
def test_loopback_quota_service_url_is_accepted(url: str) -> None:
    cfg = Config(quota_service_url=url)

    assert cfg.quota_service_url is not None
    assert not cfg.quota_service_url.endswith("/")


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com",
        "http://10.0.0.5:8787",
        "ftp://127.0.0.1",
        "http://user:pw@127.0.0.1:8787",
        "http://127.0.0.1:8787/api",
        "not a url",
    ],
)
def test_non_loopback_or_malformed_quota_service_url_is_a_config_error(url: str) -> None:
    with pytest.raises(ValidationError):
        Config(quota_service_url=url)


def test_monitor_defaults_to_the_loopback_state_server() -> None:
    assert Config().monitor_url == "http://127.0.0.1:8743"


def test_monitor_url_can_be_disabled_with_none() -> None:
    assert Config(monitor_url=None).monitor_url is None


@pytest.mark.parametrize("url", ["http://localhost:8743/", "http://[::1]:8743"])
def test_loopback_monitor_url_is_accepted(url: str) -> None:
    cfg = Config(monitor_url=url)

    assert cfg.monitor_url is not None
    assert not cfg.monitor_url.endswith("/")


@pytest.mark.parametrize(
    "url",
    ["https://example.com", "http://10.0.0.5:8743", "http://127.0.0.1:8743/api/state"],
)
def test_non_loopback_monitor_url_is_a_config_error(url: str) -> None:
    with pytest.raises(ValidationError):
        Config(monitor_url=url)


def test_monitor_url_loads_from_toml(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('monitor_url = "http://127.0.0.1:9740"\n', encoding="utf-8")

    assert load_config(path).monitor_url == "http://127.0.0.1:9740"


def test_quota_service_keys_load_from_toml(tmp_path: Path) -> None:
    target = tmp_path / "config.toml"
    target.write_text(
        'quota_service_url = "http://127.0.0.1:8787"\n'
        'quota_service_secret_file = "/run/secrets/quota_service"\n',
        encoding="utf-8",
    )

    cfg = load_config(target)

    assert cfg.quota_service_url == "http://127.0.0.1:8787"
    assert cfg.quota_service_secret_file == Path("/run/secrets/quota_service")


def test_env_secret_wins_over_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    secret_file = tmp_path / "tw.secret"
    secret_file.write_text("from-file\n", encoding="utf-8")
    monkeypatch.setenv(QUOTA_SERVICE_SECRET_ENV, "from-env")

    secret = load_quota_service_secret(Config(quota_service_secret_file=secret_file))

    assert secret is not None
    assert secret.get_secret_value() == "from-env"


def test_file_secret_used_when_env_unset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    secret_file = tmp_path / "tw.secret"
    secret_file.write_text(f"  {SECRET}\n", encoding="utf-8")
    monkeypatch.delenv(QUOTA_SERVICE_SECRET_ENV, raising=False)

    secret = load_quota_service_secret(Config(quota_service_secret_file=secret_file))

    assert secret is not None
    assert secret.get_secret_value() == SECRET


def test_blank_env_secret_falls_back_to_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret_file = tmp_path / "tw.secret"
    secret_file.write_text(SECRET, encoding="utf-8")
    monkeypatch.setenv(QUOTA_SERVICE_SECRET_ENV, "   ")

    secret = load_quota_service_secret(Config(quota_service_secret_file=secret_file))

    assert secret is not None
    assert secret.get_secret_value() == SECRET


def test_env_secret_without_file_is_used(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(QUOTA_SERVICE_SECRET_ENV, SECRET)

    secret = load_quota_service_secret(Config())

    assert secret is not None
    assert secret.get_secret_value() == SECRET


def test_no_secret_source_gives_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(QUOTA_SERVICE_SECRET_ENV, raising=False)

    assert load_quota_service_secret(Config()) is None


def test_missing_secret_file_is_a_config_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(QUOTA_SERVICE_SECRET_ENV, raising=False)
    missing = tmp_path / "absent.secret"

    with pytest.raises(ConfigError, match="could not be read"):
        load_quota_service_secret(Config(quota_service_secret_file=missing))


@pytest.mark.parametrize("content", [b"", b"  \n\t\n"])
def test_empty_secret_file_is_a_config_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content: bytes
) -> None:
    monkeypatch.delenv(QUOTA_SERVICE_SECRET_ENV, raising=False)
    secret_file = tmp_path / "tw.secret"
    secret_file.write_bytes(content)

    with pytest.raises(ConfigError, match="is empty"):
        load_quota_service_secret(Config(quota_service_secret_file=secret_file))


def test_non_utf8_secret_file_error_does_not_leak_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(QUOTA_SERVICE_SECRET_ENV, raising=False)
    secret_file = tmp_path / "tw.secret"
    secret_file.write_bytes(b"\xff\xfe" + SECRET.encode("utf-8"))

    with pytest.raises(ConfigError, match="not UTF-8") as info:
        load_quota_service_secret(Config(quota_service_secret_file=secret_file))

    assert SECRET not in str(info.value)
    assert info.value.__suppress_context__


def test_secret_is_absent_from_repr_and_str(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(QUOTA_SERVICE_SECRET_ENV, SECRET)
    cfg = Config(quota_service_url="http://127.0.0.1:8787")

    secret = load_quota_service_secret(cfg)

    assert secret is not None
    for rendered in (repr(secret), str(secret), repr(cfg), str(cfg)):
        assert SECRET not in rendered
