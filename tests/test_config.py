"""Automated tests for POPTRAP configuration loading, validation, and logging."""

from __future__ import annotations

import logging
import os
import stat
from pathlib import Path

import pytest

from poptrap import APP_NAME, __version__
from poptrap.core.config import (
    AppConfig,
    LanguageCode,
    LogLevel,
    ThemeName,
    load_config,
    load_default_config,
    parse_toml_file,
    save_config,
    validate_config_data,
)
from poptrap.core.exceptions import (
    ConfigFormatError,
    ConfigNotFoundError,
    ConfigSaveError,
    ConfigValidationError,
)
from poptrap.core.logging_setup import (
    configure_logging,
    get_logger,
    set_log_level,
    shutdown_logging,
)
from poptrap.utils.paths import get_default_config_path


class TestDefaultConfiguration:
    """Verify bundled and built-in default configuration integrity."""

    def test_bundled_default_toml_exists_and_validates(self) -> None:
        default_path = get_default_config_path()
        assert default_path.is_file()

        cfg = load_default_config(default_path, strict=True)
        assert cfg.app.name == APP_NAME
        assert cfg.app.version == __version__
        assert cfg.ui.theme is ThemeName.CYAN_DARK
        assert cfg.ui.default_language is LanguageCode.EN
        assert cfg.audio.sound_enabled is False
        assert cfg.logging.level is LogLevel.INFO

    def test_implicit_missing_user_config_uses_defaults_without_warnings(self) -> None:
        result = load_config()
        assert result.config == AppConfig()
        assert result.loaded_user_config is False
        assert result.used_fallback is False
        assert result.warnings == ()

    def test_explicit_missing_config_path_falls_back_with_warning(
        self, tmp_path: Path
    ) -> None:
        missing = tmp_path / "does_not_exist.toml"
        result = load_config(missing)

        assert result.config == AppConfig()
        assert result.config_path == missing
        assert result.loaded_user_config is False
        assert result.used_fallback is True
        assert len(result.warnings) == 1
        assert "not found" in result.warnings[0].lower()

    def test_missing_default_and_user_config_falls_back_to_builtin_defaults(
        self, tmp_path: Path
    ) -> None:
        missing_default = tmp_path / "missing_default.toml"
        missing_user = tmp_path / "missing_user.toml"
        result = load_config(missing_user, default_path=missing_default)

        assert result.config == AppConfig()
        assert result.used_fallback is True
        assert len(result.warnings) == 2

    def test_strict_mode_raises_on_missing_file(self, tmp_path: Path) -> None:
        missing = tmp_path / "absent.toml"
        with pytest.raises(ConfigNotFoundError) as exc_info:
            load_config(missing, strict=True)
        assert exc_info.value.path == missing
        assert exc_info.value.hint is not None


class TestConfigurationLoading:
    """Verify loading valid full, partial, and normalized TOML files."""

    def test_load_complete_valid_user_config(self, tmp_path: Path) -> None:
        cfg_file = tmp_path / "config.toml"
        cfg_file.write_text(
            '[app]\nname = "POPTRAP-LAB"\nversion = "1.2.3"\n\n'
            '[ui]\ntheme = "mono-dark"\ndefault_language = "de"\n\n'
            "[audio]\nsound_enabled = true\n\n"
            '[logging]\nlevel = "DEBUG"\n',
            encoding="utf-8",
        )

        result = load_config(cfg_file, strict=True)
        assert result.loaded_user_config is True
        assert result.used_fallback is False
        assert result.warnings == ()
        assert result.config.app.name == "POPTRAP-LAB"
        assert result.config.app.version == "1.2.3"
        assert result.config.ui.theme is ThemeName.MONO_DARK
        assert result.config.ui.default_language is LanguageCode.DE
        assert result.config.audio.sound_enabled is True
        assert result.config.logging.level is LogLevel.DEBUG

    def test_partial_user_config_inherits_defaults(self, tmp_path: Path) -> None:
        cfg_file = tmp_path / "partial.toml"
        cfg_file.write_text(
            '[ui]\ntheme = "ICE-DARK"\n\n[logging]\nlevel = "warning"\n',
            encoding="utf-8",
        )

        result = load_config(cfg_file)
        assert result.loaded_user_config is True
        assert result.used_fallback is False
        assert result.warnings == ()
        assert result.config.app.name == APP_NAME
        assert result.config.ui.theme is ThemeName.ICE_DARK
        assert result.config.ui.default_language is LanguageCode.EN
        assert result.config.audio.sound_enabled is False
        assert result.config.logging.level is LogLevel.WARNING

    def test_utf8_bom_toml_is_supported(self, tmp_path: Path) -> None:
        cfg_file = tmp_path / "bom.toml"
        cfg_file.write_bytes(b"\xef\xbb\xbf[audio]\nsound_enabled = true\n")

        result = load_config(cfg_file, strict=True)
        assert result.config.audio.sound_enabled is True


class TestMalformedAndInvalidConfiguration:
    """Verify malformed and schema-invalid configs are handled safely."""

    def test_malformed_toml_gracefully_recovers_defaults(self, tmp_path: Path) -> None:
        broken = tmp_path / "broken.toml"
        broken.write_text('[ui\ntheme = "unclosed\n', encoding="utf-8")

        result = load_config(broken)
        assert result.config == AppConfig()
        assert result.loaded_user_config is False
        assert result.used_fallback is True
        assert any("malformed toml" in msg.lower() for msg in result.warnings)

    def test_malformed_toml_raises_in_strict_mode(self, tmp_path: Path) -> None:
        broken = tmp_path / "broken.toml"
        broken.write_text("not = = valid toml", encoding="utf-8")

        with pytest.raises(ConfigFormatError):
            parse_toml_file(broken)

    def test_non_utf8_binary_config_recovers_gracefully(self, tmp_path: Path) -> None:
        binary = tmp_path / "binary.toml"
        binary.write_bytes(b"\xff\xfe\x00\x80invalid")

        result = load_config(binary)
        assert result.config == AppConfig()
        assert result.used_fallback is True
        assert any("utf-8" in msg.lower() for msg in result.warnings)

    def test_directory_as_config_path_recovers_gracefully(self, tmp_path: Path) -> None:
        folder = tmp_path / "conf_dir"
        folder.mkdir()

        result = load_config(folder)
        assert result.config == AppConfig()
        assert result.used_fallback is True
        assert any("directory" in msg.lower() for msg in result.warnings)

    def test_partial_invalid_values_keep_valid_settings_and_revert_bad_ones(
        self, tmp_path: Path
    ) -> None:
        mixed = tmp_path / "mixed.toml"
        mixed.write_text(
            '[app]\nname = ""\nversion = "not-a-version"\n\n'
            '[ui]\ntheme = "neon-rainbow"\ndefault_language = "fr"\nunknown_flag = 1\n\n'
            '[audio]\nsound_enabled = true\n\n'
            '[logging]\nlevel = "VERBOSE"\n\n'
            '[unexpected]\nfoo = "bar"\n',
            encoding="utf-8",
        )

        result = load_config(mixed)
        assert result.loaded_user_config is True
        assert result.used_fallback is True
        # Valid settings preserved:
        assert result.config.ui.default_language is LanguageCode.FR
        assert result.config.audio.sound_enabled is True
        # Invalid settings reverted to safe defaults:
        assert result.config.app.name == APP_NAME
        assert result.config.app.version == __version__
        assert result.config.ui.theme is ThemeName.CYAN_DARK
        assert result.config.logging.level is LogLevel.INFO
        assert len(result.warnings) == 6

    @pytest.mark.parametrize(
        "payload",
        [
            {"app": {"name": "   "}},
            {"app": {"name": "A" * 33}},
            {"app": {"name": "BAD\x1b[31mNAME"}},
            {"app": {"name": 123}},
            {"app": {"version": "v1.0"}},
            {"app": {"version": "1.0"}},
            {"ui": {"theme": "light-pink"}},
            {"ui": {"default_language": "zz"}},
            {"audio": {"sound_enabled": "yes"}},
            {"audio": {"sound_enabled": 1}},
            {"logging": {"level": "TRACE"}},
            {"extra_section": {"enabled": True}},
            {"ui": {"extra_field": "nope"}},
        ],
    )
    def test_strict_validation_rejects_invalid_values(
        self, payload: dict[str, object]
    ) -> None:
        with pytest.raises(ConfigValidationError) as exc_info:
            validate_config_data(payload)
        assert len(exc_info.value.field_errors) >= 1


class TestConfigurationPersistence:
    """Verify atomic TOML saving and error reporting."""

    def test_save_and_reload_round_trip(self, tmp_path: Path) -> None:
        target = tmp_path / "nested" / "config.toml"
        updated = AppConfig().with_preferences(
            theme=ThemeName.ICE_DARK,
            sound_enabled=True,
            default_language=LanguageCode.ES,
            log_level=LogLevel.ERROR,
        )

        saved_path = save_config(updated, target)
        assert saved_path == target
        assert target.is_file()
        # Confirm no temporary files remain.
        assert list(target.parent.glob(".*.tmp")) == []

        if os.name == "posix":
            mode = stat.S_IMODE(target.stat().st_mode)
            assert mode == 0o600

        reloaded = load_config(target, strict=True)
        assert reloaded.config == updated

    def test_save_to_directory_raises_config_save_error(self, tmp_path: Path) -> None:
        directory = tmp_path / "dir_target"
        directory.mkdir()

        with pytest.raises(ConfigSaveError):
            save_config(AppConfig(), directory)


class TestLoggingSetup:
    """Verify rotating local file logging and dynamic level changes."""

    def test_configure_logging_writes_records_and_updates_level(
        self, tmp_path: Path
    ) -> None:
        log_file = tmp_path / "logs" / "poptrap.log"
        state = configure_logging(LogLevel.WARNING, log_path=log_file)

        assert state.file_active is True
        assert state.log_path == log_file
        assert state.warning is None

        logger = get_logger("test")
        logger.info("suppressed-info-message")
        logger.warning("recorded-warning-message")

        set_log_level(LogLevel.DEBUG)
        assert get_logger().level == logging.DEBUG
        logger.debug("recorded-debug-message")

        shutdown_logging()
        content = log_file.read_text(encoding="utf-8")
        assert "suppressed-info-message" not in content
        assert "recorded-warning-message" in content
        assert "recorded-debug-message" in content

    def test_configure_logging_degrades_cleanly_on_invalid_path(
        self, tmp_path: Path
    ) -> None:
        blocking_file = tmp_path / "not_a_directory"
        blocking_file.write_text("blocker", encoding="utf-8")
        impossible_log = blocking_file / "poptrap.log"

        state = configure_logging(LogLevel.INFO, log_path=impossible_log)
        assert state.file_active is False
        assert state.log_path is None
        assert state.warning is not None
