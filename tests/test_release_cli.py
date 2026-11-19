"""Release identity, noninteractive configuration and no-clobber persistence."""

from __future__ import annotations

import os
import stat
import tomllib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from poptrap import APP_NAME, __version__, cli
from poptrap.core.config import AppConfig, load_default_config, save_config
from poptrap.core.exceptions import ConfigSaveError
from poptrap.utils.paths import get_default_config_path, get_log_file_path, get_user_config_path


def test_diagnostics_with_no_user_config_are_read_only(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--version"]) == cli.EXIT_OK
    assert capsys.readouterr().out.strip() == f"{APP_NAME} {__version__}"
    assert cli.main(["--check"]) == cli.EXIT_OK
    assert f"v{__version__}" in capsys.readouterr().out
    assert cli.main(["--validate-config"]) == cli.EXIT_OK
    assert "Bundled defaults are valid" in capsys.readouterr().out
    assert not get_user_config_path().parent.exists()
    assert not get_log_file_path().parent.exists()


@pytest.mark.parametrize("contents", [None, "[ui\n", '[ui]\ntheme = "outside-enum"\n'])
def test_strict_validation_failure_keeps_check_fallback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], contents: str | None,
) -> None:
    path = tmp_path / "custom.toml"
    if contents is not None:
        path.write_text(contents, encoding="utf-8")
    assert cli.main(["--validate-config", "--config", str(path)]) == cli.EXIT_CONFIG_ERROR
    assert capsys.readouterr().err
    assert cli.main(["--check", "--config", str(path)]) == cli.EXIT_OK
    assert "Notice:" in capsys.readouterr().out
    assert not get_log_file_path().parent.exists()


def test_legacy_preference_version_cannot_override_cli(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "legacy.toml"
    path.write_text('[app]\nname = "Custom label"\nversion = "1.2.3"\n', encoding="utf-8")
    assert cli.main(["--config", str(path), "--version"]) == cli.EXIT_OK
    assert capsys.readouterr().out.strip() == f"{APP_NAME} {__version__}"
    assert cli.main(["--config", str(path), "--validate-config"]) == cli.EXIT_OK
    legacy = AppConfig.model_validate(tomllib.loads(path.read_text(encoding="utf-8")))
    save_config(legacy.with_preferences(sound_enabled=True), path)
    assert tomllib.loads(path.read_text(encoding="utf-8"))["app"]["version"] == "1.2.3"


def test_init_creates_private_defaults_only_once(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--init-config"]) == cli.EXIT_OK
    assert "Created user configuration" in capsys.readouterr().out
    path = get_user_config_path()
    saved = path.read_bytes()
    parsed = tomllib.loads(saved.decode())
    assert "version" not in parsed["app"]
    assert AppConfig.model_validate(parsed) == load_default_config(strict=True)
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert cli.main(["--init-config"]) == cli.EXIT_CONFIG_ERROR
    assert "left unchanged" in capsys.readouterr().err
    assert path.read_bytes() == saved
    assert list(path.parent.iterdir()) == [path]
    assert not get_log_file_path().parent.exists()


def test_initialize_refuses_to_replace_a_dangling_symlink(tmp_path: Path) -> None:
    path, absent = tmp_path / "user.toml", tmp_path / "absent.toml"
    try:
        path.symlink_to(absent)
    except OSError:
        pytest.skip("Creating symlinks is unavailable on this host.")
    with pytest.raises(ConfigSaveError, match="left unchanged"):
        save_config(AppConfig(), path, overwrite=False)
    assert path.is_symlink() and not absent.exists()
    assert list(tmp_path.iterdir()) == [path]


def test_concurrent_initializers_leave_one_complete_file(tmp_path: Path) -> None:
    path = tmp_path / "new.toml"

    def initialize() -> bool:
        try:
            save_config(AppConfig(), path, overwrite=False)
        except ConfigSaveError:
            return False
        return True

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(lambda _: initialize(), range(4))) == 1
    assert list(tmp_path.iterdir()) == [path]
    assert AppConfig.model_validate(tomllib.loads(path.read_text(encoding="utf-8"))) == AppConfig()


def test_bundled_defaults_cannot_be_saved_over() -> None:
    defaults = get_default_config_path()
    contents = defaults.read_bytes()
    with pytest.raises(ConfigSaveError, match="Bundled defaults are read-only"):
        save_config(AppConfig(), defaults)
    assert defaults.read_bytes() == contents


def test_strict_cli_requires_bundled_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "valid.toml"
    path.write_text('[ui]\ntheme = "cyan-dark"\n', encoding="utf-8")
    monkeypatch.setattr(cli, "get_default_config_path", lambda: tmp_path / "missing-default.toml")
    assert cli.main(["--validate-config", "--config", str(path)]) == cli.EXIT_CONFIG_ERROR
