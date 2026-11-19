"""TOML configuration schema, validation, graceful fallback loading, and persistence."""

from __future__ import annotations

import os
import re
import tempfile
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import tomli_w
from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError, field_validator

from poptrap import APP_NAME, __version__
from poptrap.core.exceptions import (
    ConfigFormatError,
    ConfigNotFoundError,
    ConfigSaveError,
    ConfigValidationError,
)
from poptrap.utils.paths import (
    ensure_private_directory,
    get_default_config_path,
    get_user_config_path,
    read_bounded_file,
)

MAX_CONFIG_BYTES = 64 * 1024

_SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*|(?:a|b|rc)(?:0|[1-9]\d*))?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)


class ThemeName(StrEnum):
    """Supported dark terminal UI themes."""

    CYAN_DARK = "cyan-dark"
    ICE_DARK = "ice-dark"
    MONO_DARK = "mono-dark"


class LanguageCode(StrEnum):
    """Supported UI language codes."""

    EN = "en"
    ES = "es"
    FR = "fr"
    DE = "de"


class LogLevel(StrEnum):
    """Supported Python logging severity levels."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class AppSection(BaseModel):
    """Application identity settings."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    name: str = Field(default=APP_NAME, min_length=1, max_length=32)
    # Retain the legacy TOML field for round-tripping older preferences. It
    # does not define the version of the installed program.
    version: str = Field(default=__version__, min_length=5, max_length=32)

    @field_validator("name", mode="before")
    @classmethod
    def _validate_name(cls, value: Any) -> str:
        if not isinstance(value, str):
            raise ValueError("Application name must be a string.")
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Application name cannot be empty.")
        if any(not ch.isprintable() or ch in "\r\n\t\x1b" for ch in cleaned):
            raise ValueError("Application name cannot contain control or escape characters.")
        return cleaned

    @field_validator("version", mode="before")
    @classmethod
    def _validate_version(cls, value: Any) -> str:
        if not isinstance(value, str):
            raise ValueError("Application version must be a semantic version string.")
        cleaned = value.strip()
        if not _SEMVER_RE.fullmatch(cleaned):
            raise ValueError(
                "Application version must follow semantic versioning (for example '0.1.0')."
            )
        return cleaned


class UISection(BaseModel):
    """Interactive terminal interface settings."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    theme: ThemeName = Field(default=ThemeName.CYAN_DARK)
    default_language: LanguageCode = Field(default=LanguageCode.EN)

    @field_validator("theme", mode="before")
    @classmethod
    def _normalize_theme(cls, value: Any) -> Any:
        if isinstance(value, ThemeName):
            return value
        if isinstance(value, str):
            return value.strip().lower()
        raise ValueError("UI theme must be a string.")

    @field_validator("default_language", mode="before")
    @classmethod
    def _normalize_language(cls, value: Any) -> Any:
        if isinstance(value, LanguageCode):
            return value
        if isinstance(value, str):
            return value.strip().lower()
        raise ValueError("Default language must be a string.")


class AudioSection(BaseModel):
    """Terminal audio feedback settings."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    sound_enabled: StrictBool = Field(default=False)


class LoggingSection(BaseModel):
    """Application file logging settings."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    level: LogLevel = Field(default=LogLevel.INFO)

    @field_validator("level", mode="before")
    @classmethod
    def _normalize_level(cls, value: Any) -> Any:
        if isinstance(value, LogLevel):
            return value
        if isinstance(value, str):
            return value.strip().upper()
        raise ValueError("Logging level must be a string.")


class AppConfig(BaseModel):
    """Complete validated POPTRAP application configuration."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    app: AppSection = Field(default_factory=AppSection)
    ui: UISection = Field(default_factory=UISection)
    audio: AudioSection = Field(default_factory=AudioSection)
    logging: LoggingSection = Field(default_factory=LoggingSection)

    def to_toml_dict(self) -> dict[str, dict[str, Any]]:
        """Serialize preferences, retaining an explicitly provided legacy version."""
        app = {"name": self.app.name}
        if "version" in self.app.model_fields_set:
            app["version"] = self.app.version
        return {
            "app": app,
            "ui": {
                "theme": self.ui.theme.value,
                "default_language": self.ui.default_language.value,
            },
            "audio": {
                "sound_enabled": bool(self.audio.sound_enabled),
            },
            "logging": {
                "level": self.logging.level.value,
            },
        }

    def with_preferences(
        self,
        *,
        theme: ThemeName | None = None,
        sound_enabled: bool | None = None,
        default_language: LanguageCode | None = None,
        log_level: LogLevel | None = None,
    ) -> AppConfig:
        """Return a new validated ``AppConfig`` with updated user preferences."""
        data = self.to_toml_dict()
        if theme is not None:
            data["ui"]["theme"] = theme.value if isinstance(theme, ThemeName) else theme
        if sound_enabled is not None:
            data["audio"]["sound_enabled"] = sound_enabled
        if default_language is not None:
            data["ui"]["default_language"] = (
                default_language.value
                if isinstance(default_language, LanguageCode)
                else default_language
            )
        if log_level is not None:
            data["logging"]["level"] = (
                log_level.value if isinstance(log_level, LogLevel) else log_level
            )
        return validate_config_data(data)


_SECTION_MODELS: dict[str, type[BaseModel]] = {
    "app": AppSection,
    "ui": UISection,
    "audio": AudioSection,
    "logging": LoggingSection,
}


@dataclass(frozen=True, slots=True)
class ConfigLoadResult:
    """Outcome of loading configuration, including any non-fatal recovery warnings."""

    config: AppConfig
    config_path: Path
    default_path: Path
    loaded_user_config: bool
    used_fallback: bool
    warnings: tuple[str, ...]


def parse_toml_file(path: Path) -> dict[str, Any]:
    """Read and parse a UTF-8 TOML file into a dictionary.

    Raises:
        ConfigNotFoundError: When ``path`` does not exist.
        ConfigFormatError: When ``path`` cannot be read or contains malformed TOML.
    """
    if not path.exists():
        raise ConfigNotFoundError(
            f"Configuration file not found: {path}",
            path=path,
            hint="Verify the configuration path or omit --config to use safe defaults.",
        )
    if path.is_dir():
        raise ConfigFormatError(
            f"Configuration path is a directory, not a file: {path}",
            path=path,
            hint="Specify a valid .toml file path.",
        )

    try:
        raw_bytes = read_bounded_file(path, MAX_CONFIG_BYTES)
    except OSError as exc:
        raise ConfigFormatError(
            f"Cannot read configuration file '{path}': {exc}",
            path=path,
            hint="Check file permissions and ensure the file is readable.",
        ) from exc

    try:
        # Handle optional UTF-8 BOM from Windows editors cleanly.
        text = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ConfigFormatError(
            f"Configuration file '{path}' is not valid UTF-8 text: {exc}",
            path=path,
            hint="Save the configuration file using UTF-8 encoding.",
        ) from exc

    try:
        parsed = tomllib.loads(text)
    except (ValueError, RecursionError) as exc:
        raise ConfigFormatError(
            f"Malformed TOML syntax in '{path}': {exc}",
            path=path,
            hint="Fix the TOML syntax error or reset settings to defaults.",
        ) from exc

    return parsed


def _format_validation_errors(exc: ValidationError) -> tuple[str, ...]:
    """Format Pydantic validation errors into concise ``section.field: reason`` lines."""
    messages: list[str] = []
    for err in exc.errors():
        loc = ".".join(str(part) for part in err.get("loc", ())) or "config"
        msg = err.get("msg", "Invalid value")
        messages.append(f"{loc}: {msg}")
    return tuple(messages)


def validate_config_data(
    data: Mapping[str, Any],
    *,
    source_path: Path | None = None,
) -> AppConfig:
    """Strictly validate a raw mapping against ``AppConfig``.

    Raises:
        ConfigValidationError: When any section or field fails validation.
    """
    if not isinstance(data, Mapping):
        raise ConfigValidationError(
            "Configuration root must be a TOML table.",
            field_errors=("config: Expected a mapping/table.",),
            path=source_path,
        )
    try:
        return AppConfig.model_validate(dict(data))
    except ValidationError as exc:
        errors = _format_validation_errors(exc)
        summary = "; ".join(errors)
        prefix = f"Invalid configuration in '{source_path}'" if source_path else "Invalid configuration"
        raise ConfigValidationError(
            f"{prefix}: {summary}",
            field_errors=errors,
            path=source_path,
            hint="Correct the invalid fields or restore defaults from the Settings menu.",
        ) from exc


def _deep_merge_strict(
    base: dict[str, dict[str, Any]],
    override: Mapping[str, Any],
) -> dict[str, Any]:
    """Merge ``override`` onto ``base`` preserving unknown keys for strict validation."""
    merged: dict[str, Any] = {section: dict(values) for section, values in base.items()}
    for section, raw_value in override.items():
        if section in merged and isinstance(raw_value, Mapping):
            merged[section] = {**merged[section], **dict(raw_value)}
        else:
            merged[section] = raw_value
    return merged


def _merge_with_fallback(
    base: AppConfig,
    override: Mapping[str, Any],
    *,
    source_label: str,
) -> tuple[AppConfig, list[str], bool]:
    """Merge ``override`` onto ``base``, keeping valid fields and reverting bad ones."""
    warnings: list[str] = []
    used_fallback = False
    candidate = base.to_toml_dict()

    for section_name, section_value in override.items():
        section_model = _SECTION_MODELS.get(section_name)
        if section_model is None:
            warnings.append(
                f"{source_label}: ignored unknown section/key '{section_name}'."
            )
            used_fallback = True
            continue

        if not isinstance(section_value, Mapping):
            warnings.append(
                f"{source_label}: section '[{section_name}]' must be a table; using defaults."
            )
            used_fallback = True
            continue

        known_fields = set(section_model.model_fields.keys())
        for field_name, field_value in section_value.items():
            if field_name not in known_fields:
                warnings.append(
                    f"{source_label}: ignored unknown setting '{section_name}.{field_name}'."
                )
                used_fallback = True
                continue

            trial_section = {**candidate[section_name], field_name: field_value}
            try:
                validated_section = section_model.model_validate(trial_section)
            except ValidationError as exc:
                first_msg = exc.errors()[0].get("msg", "invalid value") if exc.errors() else "invalid value"
                fallback_val = getattr(getattr(base, section_name), field_name)
                warnings.append(
                    f"{source_label}: invalid '{section_name}.{field_name}' ({first_msg}); "
                    f"using '{fallback_val}'."
                )
                used_fallback = True
            else:
                raw_valid = getattr(validated_section, field_name)
                candidate[section_name][field_name] = (
                    raw_valid.value if isinstance(raw_valid, StrEnum) else raw_valid
                )

    return AppConfig.model_validate(candidate), warnings, used_fallback


def load_default_config(
    default_path: Path | None = None,
    *,
    strict: bool = False,
) -> AppConfig:
    """Load the default configuration from ``poptrap/data/default.toml`` with safe fallback."""
    target = get_default_config_path() if default_path is None else default_path
    if strict:
        parsed = parse_toml_file(target)
        return validate_config_data(parsed, source_path=target)

    builtin = AppConfig()
    try:
        parsed = parse_toml_file(target)
    except (ConfigNotFoundError, ConfigFormatError):
        return builtin
    merged, _, _ = _merge_with_fallback(builtin, parsed, source_label=target.name)
    return merged


def load_config(
    config_path: Path | None = None,
    *,
    default_path: Path | None = None,
    strict: bool = False,
) -> ConfigLoadResult:
    """Load and validate POPTRAP configuration.

    When ``strict=False`` (default startup behavior), missing, malformed, or
    partially invalid configuration files never crash the application: valid
    fields are retained where safe, invalid fields revert to defaults, and
    detailed warnings are returned for logging and user notification.

    When ``strict=True``, ``ConfigNotFoundError``, ``ConfigFormatError``, or
    ``ConfigValidationError`` is raised immediately on failure.
    """
    explicit_user_path = config_path is not None
    resolved_default = get_default_config_path() if default_path is None else default_path
    resolved_user = get_user_config_path() if config_path is None else config_path.expanduser()

    if strict:
        base_config = AppConfig()
        if default_path is not None or resolved_default.exists():
            default_raw = parse_toml_file(resolved_default)
            base_config = validate_config_data(default_raw, source_path=resolved_default)

        user_raw = parse_toml_file(resolved_user)
        merged_raw = _deep_merge_strict(base_config.to_toml_dict(), user_raw)
        final_config = validate_config_data(merged_raw, source_path=resolved_user)
        return ConfigLoadResult(
            config=final_config,
            config_path=resolved_user,
            default_path=resolved_default,
            loaded_user_config=True,
            used_fallback=False,
            warnings=(),
        )

    warnings: list[str] = []
    used_fallback = False
    active_config = AppConfig()

    # Step 1: Load bundled default.toml if available.
    try:
        default_data = parse_toml_file(resolved_default)
    except ConfigNotFoundError:
        warnings.append(
            f"Default config '{resolved_default}' not found; using built-in defaults."
        )
        used_fallback = True
    except ConfigFormatError as exc:
        warnings.append(f"{exc.message} Using built-in defaults.")
        used_fallback = True
    else:
        active_config, default_warnings, default_fell_back = _merge_with_fallback(
            active_config,
            default_data,
            source_label="default.toml",
        )
        warnings.extend(default_warnings)
        used_fallback = used_fallback or default_fell_back

    # Step 2: Overlay user config if present.
    loaded_user = False
    if not resolved_user.exists():
        if explicit_user_path:
            warnings.append(
                f"Configuration file '{resolved_user}' not found; using default settings."
            )
            used_fallback = True
    elif resolved_user.resolve() == resolved_default.resolve():
        # User explicitly pointed --config at default.toml; already loaded above.
        loaded_user = not used_fallback
    else:
        try:
            user_data = parse_toml_file(resolved_user)
        except ConfigFormatError as exc:
            warnings.append(f"{exc.message} Loaded default settings instead.")
            used_fallback = True
        else:
            loaded_user = True
            active_config, user_warnings, user_fell_back = _merge_with_fallback(
                active_config,
                user_data,
                source_label=resolved_user.name,
            )
            warnings.extend(user_warnings)
            used_fallback = used_fallback or user_fell_back

    return ConfigLoadResult(
        config=active_config,
        config_path=resolved_user,
        default_path=resolved_default,
        loaded_user_config=loaded_user,
        used_fallback=used_fallback,
        warnings=tuple(warnings),
    )


def save_config(config: AppConfig, path: Path | None = None, *, overwrite: bool = True) -> Path:
    """Validate and atomically write ``config`` as TOML to ``path``.

    With ``overwrite=False``, publish only to a previously unused path. On
    POSIX this requires hard-link support in the destination directory. An
    existing name (including a dangling symlink) is never replaced.

    Raises:
        ConfigValidationError: If ``config`` fails validation.
        ConfigSaveError: If directory creation or atomic publication fails.
    """
    validated = validate_config_data(config.to_toml_dict(), source_path=path)
    target = (get_user_config_path() if path is None else path).expanduser()

    header = "# POPTRAP Local Configuration\n\n"
    serialized = header + tomli_w.dumps(validated.to_toml_dict())

    temp_path: Path | None = None
    try:
        if target.resolve() == get_default_config_path().resolve():
            raise ConfigSaveError(
                "Bundled defaults are read-only.",
                path=target,
                hint="Omit --config or choose a path outside the installation.",
            )
        if target.is_dir():
            raise ConfigSaveError(
                f"Cannot write configuration because '{target}' is a directory.",
                path=target,
                hint="Choose a valid .toml file path.",
            )
        ensure_private_directory(target.parent)
        fd, raw_temp = tempfile.mkstemp(
            prefix=f".{target.name}.",
            suffix=".tmp",
            dir=str(target.parent),
        )
        temp_path = Path(raw_temp)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(serialized)
            handle.flush()
            if os.name == "posix":
                os.fchmod(handle.fileno(), 0o600)
            os.fsync(handle.fileno())

        if overwrite:
            os.replace(temp_path, target)
            temp_path = None
        else:
            try:
                if os.name == "nt":
                    # Windows rename, unlike POSIX rename, never replaces a file.
                    os.rename(temp_path, target)
                    temp_path = None
                else:
                    os.link(temp_path, target)
            except FileExistsError as exc:
                raise ConfigSaveError(
                    f"Configuration already exists at '{target}'; left unchanged.",
                    path=target,
                    hint="Use --validate-config to check it or choose a new path.",
                ) from exc
    except (OSError, RuntimeError) as exc:
        raise ConfigSaveError(
            f"Failed to save configuration to '{target}': {exc}",
            path=target,
            hint="Verify that the configuration directory is writable.",
        ) from exc
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass

    return target
