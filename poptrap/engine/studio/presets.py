"""Secure, atomic local preset persistence for Phase 4 Prank Studio.

Presets are stored strictly on the local filesystem inside the user's private
POPTRAP presets directory as validated UTF-8 JSON documents. All preset data is
declarative and validated with Pydantic before loading; path traversal,
symlinks, malformed JSON, unconfirmed overwrites, and oversized files are
explicitly rejected.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError

from poptrap.core.exceptions import (
    PresetExistsError,
    PresetFormatError,
    PresetIdentifierError,
    PresetNotFoundError,
    PresetStorageError,
    StudioValidationError,
)
from poptrap.core.logging_setup import get_logger
from poptrap.engine.models import PrankTheme, TemplateId, _clean_safe_text
from poptrap.engine.registry import TemplateRegistry
from poptrap.engine.http_safety import strict_json
from poptrap.engine.studio.composer import validate_studio_config
from poptrap.engine.studio.models import (
    MAX_EXPERIENCE_NAME_LENGTH as STUDIO_MAX_NAME_LENGTH,
    StudioConfig,
)
from poptrap.utils.paths import ensure_private_directory, get_user_presets_dir, read_bounded_file

PRESET_SCHEMA_VERSION: int = 1
MAX_PRESET_ID_LENGTH: int = 48
MAX_PRESET_FILE_BYTES: int = 65_536
MAX_SAVED_PRESETS: int = 100
MAX_PRESET_DIRECTORY_ENTRIES: int = 256

_PRESET_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")
_SLUG_STRIP_RE = re.compile(r"[^a-z0-9]+")
_WINDOWS_RESERVED_NAMES: frozenset[str] = frozenset(
    {
        "con",
        "prn",
        "aux",
        "nul",
        *(f"com{n}" for n in range(1, 10)),
        *(f"lpt{n}" for n in range(1, 10)),
    }
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def validate_preset_id(preset_id: Any) -> str:
    """Validate a strict traversal-safe preset identifier.

    Raises:
        PresetIdentifierError: If ``preset_id`` is empty, malformed, or unsafe.
    """
    if not isinstance(preset_id, str):
        raise PresetIdentifierError(
            "Preset identifier must be a string.",
            hint="Select a valid local preset identifier.",
        )
    if "\x00" in preset_id or "/" in preset_id or "\\" in preset_id or ".." in preset_id:
        raise PresetIdentifierError(
            "Preset identifier contains forbidden path characters.",
            hint="Use only lowercase letters, numbers, hyphens, and underscores.",
        )
    candidate = preset_id.strip()
    if (
        not candidate
        or len(candidate) > MAX_PRESET_ID_LENGTH
        or not _PRESET_ID_RE.fullmatch(candidate)
        or candidate in _WINDOWS_RESERVED_NAMES
    ):
        raise PresetIdentifierError(
            f"Invalid preset identifier '{preset_id}'.",
            hint="Preset IDs must be 1–48 lowercase alphanumeric, '-', or '_' characters.",
        )
    return candidate


def slugify_preset_name(name: str) -> str:
    """Derive a safe deterministic ``preset_id`` slug from an experience name."""
    lowered = name.strip().lower()
    slug = _SLUG_STRIP_RE.sub("-", lowered).strip("-_")
    if not slug:
        slug = "preset"
    slug = slug[:40].rstrip("-_") or "preset"
    if slug in _WINDOWS_RESERVED_NAMES:
        slug = f"preset-{slug}"
    return slug


class PresetRecord(BaseModel):
    """Full validated local preset document."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: StrictInt = Field(default=PRESET_SCHEMA_VERSION)
    preset_id: str = Field(min_length=1, max_length=MAX_PRESET_ID_LENGTH)
    name: str = Field(min_length=1, max_length=STUDIO_MAX_NAME_LENGTH)
    created_at: datetime
    updated_at: datetime
    config: StudioConfig


class PresetSummary(BaseModel):
    """Compact validated preset metadata for list views."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    preset_id: str
    name: str
    template_id: TemplateId
    theme: PrankTheme
    duration_seconds: int
    stage_count: int
    updated_at: datetime

    @classmethod
    def from_record(cls, record: PresetRecord) -> PresetSummary:
        return cls(
            preset_id=record.preset_id,
            name=record.name,
            template_id=record.config.template_id,
            theme=record.config.theme,
            duration_seconds=record.config.effective_duration_seconds,
            stage_count=len(record.config.stages),
            updated_at=record.updated_at,
        )


class PresetStore:
    """Thread-safe local filesystem manager for validated Prank Studio presets."""

    def __init__(
        self,
        presets_dir: Path | None = None,
        *,
        registry: TemplateRegistry | None = None,
        wall_clock: Callable[[], datetime] = _utc_now,
        max_presets: int = MAX_SAVED_PRESETS,
    ) -> None:
        if max_presets < 1:
            raise ValueError("max_presets must be at least 1.")
        self._dir = get_user_presets_dir() if presets_dir is None else Path(presets_dir)
        self._registry = registry
        self._wall_clock = wall_clock
        self._max_presets = int(max_presets)
        self._lock = threading.RLock()
        self._logger = get_logger("engine.studio.presets")

    @property
    def presets_dir(self) -> Path:
        """Filesystem directory storing user-local preset JSON files."""
        return self._dir

    def _ensure_dir(self) -> Path:
        try:
            if self._dir.is_symlink():
                raise PresetStorageError(
                    "Presets directory cannot be a symbolic link.",
                    path=self._dir,
                )
            ensure_private_directory(self._dir)
            return self._dir.resolve(strict=True)
        except PresetStorageError:
            raise
        except OSError as exc:
            raise PresetStorageError(
                f"Unable to prepare local presets directory ({exc}).",
                path=self._dir,
                hint="Check directory permissions or set POPTRAP_PRESETS_DIR.",
            ) from exc

    def _resolve_preset_file(self, preset_id: Any, *, root: Path | None = None) -> tuple[str, Path]:
        """Resolve ``<root>/<preset_id>.json`` with strict confinement verification."""
        checked = validate_preset_id(preset_id)
        base = self._dir.resolve(strict=False) if root is None else root
        candidate = base / f"{checked}.json"
        if candidate.is_symlink():
            raise PresetIdentifierError(
                f"Preset '{checked}' cannot reference a symbolic link.",
                path=candidate,
            )
        resolved = candidate.resolve(strict=False)
        if resolved.parent != base:
            raise PresetIdentifierError(
                "Preset path escaped the local presets directory.",
                path=candidate,
            )
        return checked, resolved

    def _read_and_validate_file(self, file_path: Path, expected_id: str) -> PresetRecord:
        if file_path.is_symlink():
            raise PresetFormatError(
                f"Preset file '{file_path.name}' is a symbolic link and was rejected.",
                path=file_path,
            )
        try:
            text = read_bounded_file(file_path, MAX_PRESET_FILE_BYTES, follow_symlinks=False).decode("utf-8")
        except FileNotFoundError as exc:
            raise PresetNotFoundError(
                f"Preset '{expected_id}' was not found.", path=file_path,
                hint="Refresh the preset list or save a new preset.",
            ) from exc
        except (OSError, UnicodeDecodeError) as exc:
            raise PresetFormatError(f"Preset '{expected_id}' could not be read: {exc}", path=file_path) from exc

        try:
            parsed = strict_json(text)
        except (ValueError, RecursionError) as exc:
            raise PresetFormatError(
                f"Preset '{expected_id}' contains invalid or ambiguous JSON.", path=file_path,
                hint="Fix or delete the damaged preset file.",
            ) from exc

        if not isinstance(parsed, dict):
            raise PresetFormatError(
                f"Preset file '{file_path.name}' must contain a JSON object.",
                path=file_path,
            )

        if parsed.get("schema_version") != PRESET_SCHEMA_VERSION:
            raise PresetFormatError(
                f"Unsupported preset schema_version in '{file_path.name}'.",
                path=file_path,
            )

        file_preset_id = parsed.get("preset_id")
        if file_preset_id != expected_id:
            raise PresetFormatError(
                f"Preset ID mismatch in '{file_path.name}' (expected '{expected_id}').",
                path=file_path,
            )

        raw_cfg = parsed.get("config")
        try:
            validated_cfg = validate_studio_config(raw_cfg, registry=self._registry)
            record = PresetRecord.model_validate({**parsed, "config": validated_cfg})
        except (StudioValidationError, ValidationError) as exc:
            raise PresetFormatError(
                f"Preset '{expected_id}' failed configuration validation: {exc}",
                path=file_path,
            ) from exc

        return record

    def _write_record_atomic(self, file_path: Path, record: PresetRecord) -> None:
        """Persist ``record`` using a same-directory temp file, fsync, and os.replace."""
        payload = json.dumps(
            record.model_dump(mode="json"),
            indent=2,
            ensure_ascii=False,
            sort_keys=False,
        ) + "\n"
        encoded = payload.encode("utf-8")
        if len(encoded) > MAX_PRESET_FILE_BYTES:
            raise PresetStorageError(
                f"Serialized preset exceeds maximum size ({MAX_PRESET_FILE_BYTES} bytes).",
                path=file_path,
            )

        tmp_path: Path | None = None
        try:
            fd, raw_tmp = tempfile.mkstemp(
                prefix=f".{record.preset_id}.",
                suffix=".tmp",
                dir=str(file_path.parent),
            )
            tmp_path = Path(raw_tmp)
            if os.name == "posix":
                try:
                    os.fchmod(fd, 0o600)
                except OSError:
                    pass
            with os.fdopen(fd, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, file_path)
            tmp_path = None
        except OSError as exc:
            raise PresetStorageError(
                f"Failed to write preset '{record.name}' to disk ({exc}).",
                path=file_path,
                hint="Verify available disk space and write permissions.",
            ) from exc
        finally:
            if tmp_path is not None and tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass

    def _scan_records_locked(self) -> tuple[list[PresetRecord], list[str]]:
        entries: list[Path] = []
        try:
            if self._dir.is_symlink():
                raise PresetStorageError("Presets directory cannot be a symbolic link.", path=self._dir)
            with os.scandir(self._dir) as children:
                for count, child in enumerate(children, start=1):
                    if count > MAX_PRESET_DIRECTORY_ENTRIES:
                        raise PresetStorageError(
                            "Presets directory has too many entries. Move unused files out and retry.", path=self._dir,
                        )
                    entries.append(Path(child.path))
        except FileNotFoundError:
            return [], []
        except OSError as exc:
            raise PresetStorageError("Could not read the local presets directory.", path=self._dir) from exc

        records: list[PresetRecord] = []
        warnings: list[str] = []
        entries.sort(key=lambda p: p.name)

        for entry in entries:
            if entry.name.startswith(".") or entry.suffix != ".json":
                continue
            stem = entry.stem
            try:
                checked = validate_preset_id(stem)
                if entry.is_symlink() or not entry.is_file():
                    raise PresetFormatError(
                        f"Skipped non-regular preset entry '{entry.name}'.",
                        path=entry,
                    )
                records.append(self._read_and_validate_file(entry, checked))
            except (PresetIdentifierError, PresetFormatError, PresetStorageError, PresetNotFoundError):
                self._logger.warning("Skipped an invalid local preset file.")
                warnings.append(f"Could not load preset file {entry.name!r}.")

        records.sort(key=lambda r: (r.updated_at, r.name.lower()), reverse=True)
        return records, warnings

    def list_presets(self) -> tuple[tuple[PresetSummary, ...], tuple[str, ...]]:
        """List all valid saved local presets alongside any corrupt-file warnings."""
        with self._lock:
            try:
                records, warnings = self._scan_records_locked()
            except PresetStorageError as exc:
                return (), (exc.message,)
            summaries = tuple(PresetSummary.from_record(rec) for rec in records)
            return summaries, tuple(warnings)

    def load_preset(self, preset_id: str) -> PresetRecord:
        """Load and fully validate a single saved preset by ID."""
        with self._lock:
            checked, file_path = self._resolve_preset_file(preset_id)
            if not file_path.exists():
                raise PresetNotFoundError(
                    f"Preset '{checked}' was not found.",
                    path=file_path,
                    hint="Check saved presets before loading.",
                )
            return self._read_and_validate_file(file_path, checked)

    def save_preset(
        self,
        raw_config: StudioConfig | Mapping[str, Any],
        *,
        preset_id: str | None = None,
        overwrite: bool = False,
    ) -> PresetRecord:
        """Validate and save a local preset atomically.

        Raises ``PresetExistsError`` unless ``overwrite=True`` when a preset with
        the same ID or case-insensitive name already exists.
        """
        config = validate_studio_config(raw_config, registry=self._registry)
        with self._lock:
            root = self._ensure_dir()
            existing_records, _ = self._scan_records_locked()
            by_id = {rec.preset_id: rec for rec in existing_records}
            name_key = config.name.casefold()

            name_match = next(
                (rec for rec in existing_records if rec.name.casefold() == name_key),
                None,
            )

            if preset_id is not None:
                target_id, target_path = self._resolve_preset_file(preset_id, root=root)
                if name_match is not None and name_match.preset_id != target_id:
                    raise PresetExistsError(
                        f"A preset named '{config.name}' already exists.",
                        existing_id=name_match.preset_id,
                        path=target_path,
                        hint="Choose a unique name or confirm overwriting that preset.",
                    )
                if (target_id in by_id or target_path.exists()) and not overwrite:
                    raise PresetExistsError(
                        f"Preset '{config.name}' already exists. Confirm overwrite to replace it.",
                        existing_id=target_id,
                        path=target_path,
                        hint="Confirm overwrite or save under a different name.",
                    )
            else:
                if name_match is not None:
                    if not overwrite:
                        raise PresetExistsError(
                            f"A preset named '{config.name}' already exists. Confirm overwrite to replace it.",
                            existing_id=name_match.preset_id,
                            hint="Confirm overwrite or choose another preset name.",
                        )
                    target_id, target_path = self._resolve_preset_file(
                        name_match.preset_id, root=root
                    )
                else:
                    base_slug = slugify_preset_name(config.name)
                    candidate_id = base_slug
                    suffix = 2
                    while candidate_id in by_id or (root / f"{candidate_id}.json").exists():
                        candidate_id = f"{base_slug[:40]}-{suffix}"
                        suffix += 1
                    target_id, target_path = self._resolve_preset_file(
                        candidate_id, root=root
                    )

            prior = by_id.get(target_id)
            if prior is None and len(existing_records) >= self._max_presets:
                raise PresetStorageError(
                    f"Maximum saved preset limit ({self._max_presets}) reached.",
                    path=root,
                    hint="Delete unused presets before saving new ones.",
                )

            now = self._wall_clock()
            created_at = prior.created_at if prior is not None else now
            record = PresetRecord(
                schema_version=PRESET_SCHEMA_VERSION,
                preset_id=target_id,
                name=config.name,
                created_at=created_at,
                updated_at=now,
                config=config,
            )
            self._write_record_atomic(target_path, record)
            self._logger.info(
                "Preset saved: template=%s",
                config.template_id.value,
            )
            return record

    def rename_preset(self, preset_id: str, new_name: str) -> PresetRecord:
        """Rename an existing preset safely while checking name collisions."""
        try:
            cleaned_name = _clean_safe_text(
                new_name,
                field_label="Preset name",
                max_len=STUDIO_MAX_NAME_LENGTH,
            )
        except ValueError as exc:
            raise StudioValidationError(
                str(exc),
                field_errors=(f"name: {exc}",),
            ) from exc

        with self._lock:
            current = self.load_preset(preset_id)
            root = self._ensure_dir()
            existing_records, _ = self._scan_records_locked()
            for rec in existing_records:
                if (
                    rec.preset_id != current.preset_id
                    and rec.name.casefold() == cleaned_name.casefold()
                ):
                    raise PresetExistsError(
                        f"A preset named '{cleaned_name}' already exists.",
                        existing_id=rec.preset_id,
                        hint="Choose a distinct preset name.",
                    )

            _, file_path = self._resolve_preset_file(current.preset_id, root=root)
            updated_cfg = current.config.model_copy(update={"name": cleaned_name})
            updated = PresetRecord(
                schema_version=PRESET_SCHEMA_VERSION,
                preset_id=current.preset_id,
                name=cleaned_name,
                created_at=current.created_at,
                updated_at=self._wall_clock(),
                config=updated_cfg,
            )
            self._write_record_atomic(file_path, updated)
            self._logger.info("Preset renamed.")
            return updated

    def duplicate_preset(
        self,
        preset_id: str,
        *,
        new_name: str | None = None,
    ) -> PresetRecord:
        """Create an independent validated copy of ``preset_id``."""
        with self._lock:
            source = self.load_preset(preset_id)
            existing_records, _ = self._scan_records_locked()
            taken_names = {rec.name.casefold() for rec in existing_records}

            if new_name is not None and str(new_name).strip():
                try:
                    resolved_name = _clean_safe_text(
                        new_name,
                        field_label="Duplicate preset name",
                        max_len=STUDIO_MAX_NAME_LENGTH,
                    )
                except ValueError as exc:
                    raise StudioValidationError(
                        str(exc),
                        field_errors=(f"name: {exc}",),
                    ) from exc
            else:
                stem = source.name[: STUDIO_MAX_NAME_LENGTH - 10].rstrip()
                candidate = f"{stem} (Copy)"
                counter = 2
                while candidate.casefold() in taken_names:
                    candidate = f"{stem} (Copy {counter})"
                    counter += 1
                resolved_name = candidate

            dup_cfg = source.config.model_copy(update={"name": resolved_name})
            return self.save_preset(dup_cfg, overwrite=False)

    def delete_preset(self, preset_id: str) -> bool:
        """Remove a saved local preset by validated identifier."""
        with self._lock:
            checked, file_path = self._resolve_preset_file(preset_id)
            if not file_path.exists():
                raise PresetNotFoundError(
                    f"Preset '{checked}' was not found.",
                    path=file_path,
                )
            try:
                file_path.unlink()
            except OSError as exc:
                raise PresetStorageError(
                    f"Unable to delete preset '{checked}' ({exc}).",
                    path=file_path,
                ) from exc
            self._logger.info("Preset deleted.")
            return True
