"""Phase 4 tests for local preset persistence, validation, atomicity, and security."""

from __future__ import annotations

import json
import os
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from poptrap.core.exceptions import (
    PresetExistsError,
    PresetFormatError,
    PresetIdentifierError,
    PresetNotFoundError,
    PresetStorageError,
    StudioValidationError,
)
from poptrap.engine.models import TemplateId
from poptrap.engine.registry import create_default_registry
from poptrap.engine.studio import (
    MAX_PRESET_FILE_BYTES,
    PRESET_SCHEMA_VERSION,
    PresetStore,
    build_default_stages,
    build_default_studio_config,
    validate_preset_id,
)
from poptrap.utils.paths import ENV_PRESETS_DIR, get_user_presets_dir


@pytest.fixture()
def registry():
    return create_default_registry()


@pytest.fixture()
def clock_state():
    current = [datetime(2026, 11, 18, 10, 0, 0, tzinfo=UTC)]

    def _now() -> datetime:
        val = current[0]
        current[0] = val + timedelta(seconds=10)
        return val

    return _now


@pytest.fixture()
def store(tmp_path: Path, registry, clock_state) -> PresetStore:
    return PresetStore(
        tmp_path / "presets",
        registry=registry,
        wall_clock=clock_state,
    )


class TestPresetPathsAndPrivacy:
    def test_presets_dir_env_override_and_default_subpath(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        custom = tmp_path / "custom_presets"
        monkeypatch.setenv(ENV_PRESETS_DIR, str(custom))
        assert get_user_presets_dir() == custom

        monkeypatch.delenv(ENV_PRESETS_DIR, raising=False)
        monkeypatch.setenv("POPTRAP_CONFIG_DIR", str(tmp_path / "cfg"))
        assert get_user_presets_dir() == tmp_path / "cfg" / "presets"

    def test_saved_preset_uses_private_permissions_and_stores_no_personal_data(
        self, store: PresetStore, registry
    ) -> None:
        cfg = build_default_studio_config(TemplateId.GLITCH, registry=registry)
        rec = store.save_preset(cfg)
        preset_file = store.presets_dir / f"{rec.preset_id}.json"
        assert preset_file.is_file()

        if os.name == "posix":
            dir_mode = stat.S_IMODE(store.presets_dir.stat().st_mode)
            file_mode = stat.S_IMODE(preset_file.stat().st_mode)
            assert dir_mode == 0o700
            assert file_mode == 0o600

        raw = json.loads(preset_file.read_text(encoding="utf-8"))
        assert set(raw.keys()) == {
            "schema_version",
            "preset_id",
            "name",
            "created_at",
            "updated_at",
            "config",
        }
        assert raw["schema_version"] == PRESET_SCHEMA_VERSION
        # No leftover temporary files after save.
        leftovers = [p for p in store.presets_dir.iterdir() if p.suffix == ".tmp"]
        assert leftovers == []


class TestPresetCRUDAndPersistence:
    def test_save_load_list_edit_rename_duplicate_delete_and_reload(
        self, tmp_path: Path, registry, clock_state
    ) -> None:
        presets_dir = tmp_path / "presets"
        store = PresetStore(presets_dir, registry=registry, wall_clock=clock_state)

        # Initially empty and does not touch disk until first save.
        summaries, warnings = store.list_presets()
        assert summaries == ()
        assert warnings == ()
        assert not presets_dir.exists()

        # Save multi-stage preset
        base = build_default_studio_config(TemplateId.POPUP_STORM, registry=registry)
        stages = build_default_stages(base, registry=registry)
        saved = store.save_preset(
            {
                **base.model_dump(mode="json"),
                "name": "Office Card Cascade",
                "theme": "neon",
                "popup_storm_settings": {
                    "popup_count": 14,
                    "card_style": "grid",
                    "message_preset": "office_humor",
                    "cleanup_duration_seconds": 3,
                },
                "stages": [s.model_dump(mode="json") for s in stages],
            }
        )
        assert saved.preset_id == "office-card-cascade"
        assert saved.name == "Office Card Cascade"

        # Persist across a brand-new PresetStore instance (simulating restart)
        reopened = PresetStore(presets_dir, registry=registry, wall_clock=clock_state)
        loaded = reopened.load_preset(saved.preset_id)
        assert loaded == saved
        assert loaded.config.popup_storm_settings is not None
        assert loaded.config.popup_storm_settings.popup_count == 14
        assert len(loaded.config.stages) == 4

        # Overwrite protection: refuses unconfirmed overwrite by ID and by name
        edited_cfg = loaded.config.model_copy(
            update={"reveal_message": "Updated prank reveal message!"}
        )
        with pytest.raises(PresetExistsError) as exc_info:
            reopened.save_preset(edited_cfg, preset_id=saved.preset_id, overwrite=False)
        assert exc_info.value.existing_id == saved.preset_id

        with pytest.raises(PresetExistsError):
            reopened.save_preset(edited_cfg, overwrite=False)

        # Confirmed overwrite updates file atomically & preserves created_at
        updated = reopened.save_preset(
            edited_cfg, preset_id=saved.preset_id, overwrite=True
        )
        assert updated.created_at == saved.created_at
        assert updated.updated_at > saved.updated_at
        assert updated.config.reveal_message == "Updated prank reveal message!"

        # Duplicate preset (auto-naming & repeat copy numbering)
        dup1 = reopened.duplicate_preset(saved.preset_id)
        dup2 = reopened.duplicate_preset(saved.preset_id)
        assert dup1.preset_id != saved.preset_id
        assert dup2.preset_id not in {saved.preset_id, dup1.preset_id}
        assert dup1.name == "Office Card Cascade (Copy)"
        assert dup2.name == "Office Card Cascade (Copy 2)"
        assert dup1.config.stages == updated.config.stages

        # Rename preset & verify collision protection
        renamed = reopened.rename_preset(dup1.preset_id, "Friday Team Prank")
        assert renamed.name == "Friday Team Prank"
        assert renamed.config.name == "Friday Team Prank"

        with pytest.raises(PresetExistsError):
            reopened.rename_preset(dup2.preset_id, "friday team prank")

        with pytest.raises(StudioValidationError):
            reopened.rename_preset(dup2.preset_id, "   ")

        # List ordered most-recently-updated first
        listed, list_warnings = reopened.list_presets()
        assert list_warnings == ()
        assert len(listed) == 3
        assert listed[0].preset_id == renamed.preset_id

        # Delete preset
        assert reopened.delete_preset(dup2.preset_id) is True
        with pytest.raises(PresetNotFoundError):
            reopened.load_preset(dup2.preset_id)
        with pytest.raises(PresetNotFoundError):
            reopened.delete_preset(dup2.preset_id)

    def test_max_saved_presets_limit_enforced(
        self, tmp_path: Path, registry, clock_state
    ) -> None:
        capped = PresetStore(
            tmp_path / "capped",
            registry=registry,
            wall_clock=clock_state,
            max_presets=2,
        )
        base = build_default_studio_config(TemplateId.COUNTDOWN, registry=registry)
        p1 = capped.save_preset(base.model_copy(update={"name": "First"}))
        capped.save_preset(base.model_copy(update={"name": "Second"}))

        with pytest.raises(PresetStorageError, match="limit"):
            capped.save_preset(base.model_copy(update={"name": "Third"}))

        # Updating an existing preset with overwrite=True still works at capacity.
        updated = capped.save_preset(
            p1.config.model_copy(update={"duration_seconds": 12}),
            preset_id=p1.preset_id,
            overwrite=True,
        )
        assert updated.config.duration_seconds == 12


class TestPresetSecurityMalformedAndAtomicity:
    @pytest.mark.parametrize(
        "malicious_id",
        [
            "",
            "   ",
            "../secret",
            "..\\secret",
            "../../etc/passwd",
            "/etc/passwd",
            "sub/preset",
            "sub\\preset",
            "bad\x00id",
            ".hidden",
            "-leading",
            "UPPERCASE",
            "has spaces",
            "con",
            "nul",
            "com1",
            "a" * 64,
        ],
    )
    def test_path_traversal_and_unsafe_preset_ids_blocked(
        self, store: PresetStore, registry, malicious_id: str
    ) -> None:
        with pytest.raises(PresetIdentifierError):
            validate_preset_id(malicious_id)
        with pytest.raises(PresetIdentifierError):
            store.load_preset(malicious_id)
        with pytest.raises(PresetIdentifierError):
            store.delete_preset(malicious_id)

        cfg = build_default_studio_config(TemplateId.GLITCH, registry=registry)
        with pytest.raises(PresetIdentifierError):
            store.save_preset(cfg, preset_id=malicious_id, overwrite=True)

    @pytest.mark.skipif(os.name != "posix", reason="POSIX symlink check")
    def test_symlink_preset_file_and_directory_rejected(
        self, tmp_path: Path, store: PresetStore, registry
    ) -> None:
        cfg = build_default_studio_config(TemplateId.GLITCH, registry=registry)
        good = store.save_preset(cfg.model_copy(update={"name": "Good Preset"}))

        outside = tmp_path / "outside.json"
        outside.write_text((store.presets_dir / f"{good.preset_id}.json").read_text(), encoding="utf-8")
        link = store.presets_dir / "linked.json"
        link.symlink_to(outside)

        with pytest.raises(PresetIdentifierError):
            store.load_preset("linked")

        listed, warnings = store.list_presets()
        assert [s.preset_id for s in listed] == [good.preset_id]
        assert len(warnings) == 1

        # Symlinked presets directory itself is rejected on write.
        dir_link = tmp_path / "symlink_dir"
        dir_link.symlink_to(store.presets_dir)
        evil_store = PresetStore(dir_link, registry=registry)
        with pytest.raises(PresetStorageError):
            evil_store.save_preset(cfg)

    def test_malformed_corrupted_and_oversized_preset_files_handled_safely(
        self, store: PresetStore, registry
    ) -> None:
        good_cfg = build_default_studio_config(TemplateId.VIRUS_ALERT, registry=registry)
        good = store.save_preset(good_cfg.model_copy(update={"name": "Healthy Preset"}))
        pdir = store.presets_dir

        # 1. Truncated JSON
        (pdir / "broken-json.json").write_text("{not valid json", encoding="utf-8")
        # 2. Invalid UTF-8 bytes
        (pdir / "bad-utf8.json").write_bytes(b"\xff\xfe\x00\x80")
        # 3. Non-object JSON
        (pdir / "array-root.json").write_text("[1, 2, 3]", encoding="utf-8")
        # 4. Unsupported schema version
        (pdir / "wrong-ver.json").write_text(
            json.dumps({"schema_version": 999, "preset_id": "wrong-ver"}),
            encoding="utf-8",
        )
        # 5. Mismatched ID inside file
        good_raw = json.loads((pdir / f"{good.preset_id}.json").read_text(encoding="utf-8"))
        (pdir / "mismatch.json").write_text(
            json.dumps({**good_raw, "preset_id": "someone-else"}),
            encoding="utf-8",
        )
        # 6. Tampered config exceeding Phase 4 safety cap (popup_count = 999)
        tampered = {
            **good_raw,
            "preset_id": "tampered",
            "config": {
                **good_raw["config"],
                "duration_seconds": 9999,
            },
        }
        (pdir / "tampered.json").write_text(json.dumps(tampered), encoding="utf-8")
        # 7. Oversized file
        (pdir / "huge.json").write_text("x" * (MAX_PRESET_FILE_BYTES + 100), encoding="utf-8")

        for bad_id in (
            "broken-json",
            "bad-utf8",
            "array-root",
            "wrong-ver",
            "mismatch",
            "tampered",
            "huge",
        ):
            with pytest.raises(PresetFormatError):
                store.load_preset(bad_id)

        # Listing stays healthy, returning valid presets and reporting skipped files.
        listed, warnings = store.list_presets()
        assert [item.preset_id for item in listed] == [good.preset_id]
        assert len(warnings) == 7

    def test_atomic_write_preserves_existing_file_and_cleans_tmp_on_failure(
        self, store: PresetStore, registry, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = build_default_studio_config(TemplateId.COUNTDOWN, registry=registry)
        original = store.save_preset(cfg.model_copy(update={"name": "Atomic Check"}))
        target_file = store.presets_dir / f"{original.preset_id}.json"
        before_bytes = target_file.read_bytes()

        def _boom(_src, _dst):
            raise OSError("simulated disk replacement error")

        monkeypatch.setattr("poptrap.engine.studio.presets.os.replace", _boom)

        with pytest.raises(PresetStorageError):
            store.save_preset(
                original.config.model_copy(update={"reveal_message": "Should not land"}),
                preset_id=original.preset_id,
                overwrite=True,
            )

        # Original file is byte-for-byte untouched and no temp file leaked.
        assert target_file.read_bytes() == before_bytes
        assert [p for p in store.presets_dir.iterdir() if p.suffix == ".tmp"] == []
