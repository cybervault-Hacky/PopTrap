"""Phase 4 tests for Prank Studio customization models, validation, and composer."""

from __future__ import annotations

import pytest

from poptrap.core.exceptions import StudioValidationError, TemplateNotFoundError
from poptrap.engine.models import (
    MAX_POPUP_COUNT,
    MIN_POPUP_COUNT,
    AnimationSpeed,
    PrankTheme,
    TemplateId,
    VisualIntensity,
)
from poptrap.engine.registry import create_default_registry
from poptrap.engine.studio import (
    MAX_COMPOSITION_DURATION_SECONDS,
    MAX_STUDIO_STAGES,
    POPUP_MESSAGE_PRESETS,
    CardPresentationStyle,
    CompletionStyle,
    PopupMessagePreset,
    RingPresentationStyle,
    StageKind,
    StudioConfig,
    add_stage,
    build_default_stages,
    build_default_studio_config,
    build_studio_catalog,
    build_studio_timeline,
    compile_studio_runtime,
    move_stage,
    remove_stage,
    reorder_stages,
    studio_to_prank_config,
    update_stage,
    validate_studio_config,
)


@pytest.fixture()
def registry():
    return create_default_registry()


class TestStudioCatalogAndDefaults:
    def test_catalog_contains_exact_five_registered_templates(self, registry) -> None:
        catalog = build_studio_catalog(registry)
        ids = [entry["template_id"] for entry in catalog["templates"]]
        assert ids == [
            "glitch",
            "virus_alert",
            "system_crash",
            "popup_storm",
            "countdown",
        ]
        assert len(catalog["stage_kinds"]) == 5
        assert catalog["limits"]["max_stages"] == MAX_STUDIO_STAGES == 5
        assert (
            catalog["limits"]["max_composition_duration_seconds"]
            == MAX_COMPOSITION_DURATION_SECONDS
            == 120
        )
        assert catalog["limits"]["min_popup_count"] == MIN_POPUP_COUNT == 3
        assert catalog["limits"]["max_popup_count"] == MAX_POPUP_COUNT == 24

    @pytest.mark.parametrize("template_id", list(TemplateId))
    def test_default_studio_config_validates_and_compiles_for_each_template(
        self, registry, template_id: TemplateId
    ) -> None:
        cfg = build_default_studio_config(template_id, registry=registry)
        assert cfg.template_id is template_id
        assert cfg.active_template_settings() is not None
        assert not cfg.has_stages

        # Confirm all other template settings are None (strict context relevance).
        for other in TemplateId:
            field = f"{other.value}_settings"
            if other is template_id:
                assert getattr(cfg, field) is not None
            else:
                assert getattr(cfg, field) is None

        runtime = compile_studio_runtime(cfg, registry=registry)
        assert runtime["template_id"] == template_id.value
        assert runtime["studio"]["experience_name"] == cfg.name
        assert runtime["studio"]["has_stages"] is False
        assert "composition" not in runtime

    @pytest.mark.parametrize("theme", list(PrankTheme))
    def test_general_settings_support_all_primary_and_legacy_themes(
        self, registry, theme: PrankTheme
    ) -> None:
        base = build_default_studio_config(
            TemplateId.GLITCH, registry=registry, theme=theme, sound_enabled=True
        )
        updated = validate_studio_config(
            {
                **base.model_dump(mode="json"),
                "name": "Late Night Pixel Drift",
                "duration_seconds": 30,
                "intensity": VisualIntensity.HIGH.value,
                "animation_speed": AnimationSpeed.FAST.value,
                "sound_enabled": True,
            },
            registry=registry,
        )
        assert updated.theme is theme
        assert updated.duration_seconds == 30
        assert updated.intensity is VisualIntensity.HIGH
        assert updated.animation_speed is AnimationSpeed.FAST
        assert updated.sound_enabled is True
        prank_cfg = studio_to_prank_config(updated)
        assert prank_cfg.theme is theme
        assert prank_cfg.duration_seconds == 30
        assert prank_cfg.sound_enabled is True


class TestTemplateSpecificCustomization:
    def test_glitch_attack_customization_and_safety_bounds(self, registry) -> None:
        base = build_default_studio_config(TemplateId.GLITCH, registry=registry)
        cfg = validate_studio_config(
            {
                **base.model_dump(mode="json"),
                "duration_seconds": 20,
                "intensity": "high",
                "animation_speed": "fast",
                "glitch_settings": {
                    "scanline_opacity": 0.26,
                    "rgb_offset_px": 12,
                    "recovery_duration_seconds": 6,
                },
            },
            registry=registry,
        )
        runtime = compile_studio_runtime(cfg, registry=registry)
        assert runtime["scanline_opacity"] == 0.26
        assert runtime["rgb_offset_px"] == 12
        assert runtime["recovery_duration_seconds"] == 6
        assert 0.35 <= runtime["recovery_start_ratio"] <= 0.86

        # Reject unsafe scanline opacity, RGB separation, NaN, and recovery >= duration.
        for bad in (
            {"scanline_opacity": 0.5, "rgb_offset_px": 6, "recovery_duration_seconds": 4},
            {"scanline_opacity": float("nan"), "rgb_offset_px": 6, "recovery_duration_seconds": 4},
            {"scanline_opacity": True, "rgb_offset_px": 6, "recovery_duration_seconds": 4},
            {"scanline_opacity": 0.2, "rgb_offset_px": 25, "recovery_duration_seconds": 4},
            {"scanline_opacity": 0.2, "rgb_offset_px": 0, "recovery_duration_seconds": 4},
            {"scanline_opacity": 0.2, "rgb_offset_px": 6, "recovery_duration_seconds": 20},
        ):
            with pytest.raises(StudioValidationError):
                validate_studio_config(
                    {
                        **base.model_dump(mode="json"),
                        "duration_seconds": 20,
                        "glitch_settings": bad,
                    },
                    registry=registry,
                )

    @pytest.mark.parametrize("stage_count", [2, 3, 4, 5])
    @pytest.mark.parametrize("style", list(CompletionStyle))
    def test_virus_alert_customization_and_bounds(
        self, registry, stage_count: int, style: CompletionStyle
    ) -> None:
        base = build_default_studio_config(TemplateId.VIRUS_ALERT, registry=registry)
        cfg = validate_studio_config(
            {
                **base.model_dump(mode="json"),
                "theme": "terminal",
                "animation_speed": "fast",
                "reveal_message": "Zero files touched — pure comedy!",
                "virus_alert_settings": {
                    "scan_stage_count": stage_count,
                    "completion_style": style.value,
                },
            },
            registry=registry,
        )
        runtime = compile_studio_runtime(cfg, registry=registry)
        assert runtime["scan_stage_count"] == stage_count
        assert len(runtime["phase_labels"]) == stage_count
        assert runtime["completion_style"] == style.value
        assert len(runtime["findings"]) <= stage_count
        if style is CompletionStyle.MINIMAL:
            assert runtime["particle_count"] == 0

    def test_virus_alert_rejects_out_of_range_scan_stages(self, registry) -> None:
        base = build_default_studio_config(TemplateId.VIRUS_ALERT, registry=registry)
        for bad_count in (0, 1, 6, -2):
            with pytest.raises(StudioValidationError):
                validate_studio_config(
                    {
                        **base.model_dump(mode="json"),
                        "virus_alert_settings": {
                            "scan_stage_count": bad_count,
                            "completion_style": "standard",
                        },
                    },
                    registry=registry,
                )

    def test_system_crash_customization_and_bounds(self, registry) -> None:
        base = build_default_studio_config(TemplateId.SYSTEM_CRASH, registry=registry)
        cfg = validate_studio_config(
            {
                **base.model_dump(mode="json"),
                "theme": "minimal",
                "duration_seconds": 22,
                "system_crash_settings": {
                    "error_title": "SIMULATED COFFEE SUBSYSTEM HALT",
                    "transition_ms": 500,
                    "recovery_duration_seconds": 7,
                    "recovery_message": "All systems nominal — prank complete.",
                },
            },
            registry=registry,
        )
        prank_cfg = studio_to_prank_config(cfg)
        assert prank_cfg.title == "SIMULATED COFFEE SUBSYSTEM HALT"
        runtime = compile_studio_runtime(cfg, registry=registry)
        assert runtime["error_title"] == "SIMULATED COFFEE SUBSYSTEM HALT"
        assert runtime["transition_ms"] == 500
        assert runtime["recovery_duration_seconds"] == 7
        assert runtime["recovered_status"] == "All systems nominal — prank complete."

        for bad in (
            {"transition_ms": 50},
            {"transition_ms": 2000},
            {"recovery_duration_seconds": 22},
            {"error_title": "   "},
            {"recovery_message": ""},
        ):
            merged = {
                **cfg.system_crash_settings.model_dump(mode="json"),
                **bad,
            }
            with pytest.raises(StudioValidationError):
                validate_studio_config(
                    {
                        **base.model_dump(mode="json"),
                        "duration_seconds": 22,
                        "system_crash_settings": merged,
                    },
                    registry=registry,
                )

    @pytest.mark.parametrize("card_style", list(CardPresentationStyle))
    @pytest.mark.parametrize("preset", list(PopupMessagePreset))
    def test_popup_storm_customization_and_hard_popup_cap(
        self,
        registry,
        card_style: CardPresentationStyle,
        preset: PopupMessagePreset,
    ) -> None:
        base = build_default_studio_config(TemplateId.POPUP_STORM, registry=registry)
        cfg = validate_studio_config(
            {
                **base.model_dump(mode="json"),
                "duration_seconds": 20,
                "animation_speed": "fast",
                "popup_storm_settings": {
                    "popup_count": 24,
                    "card_style": card_style.value,
                    "message_preset": preset.value,
                    "custom_messages": ["Friendly card A", "Friendly card B"],
                    "cleanup_duration_seconds": 4,
                },
            },
            registry=registry,
        )
        runtime = compile_studio_runtime(cfg, registry=registry)
        assert runtime["popup_count"] == 24
        assert runtime["card_style"] == card_style.value
        assert runtime["message_preset"] == preset.value
        assert runtime["cleanup_duration_seconds"] == 4
        assert "Friendly card A" in runtime["messages"]
        if preset in (PopupMessagePreset.OFFICE_HUMOR, PopupMessagePreset.PIXEL_DRAMA):
            assert any(
                m in runtime["messages"] for m in POPUP_MESSAGE_PRESETS[preset]
            )

    @pytest.mark.parametrize("bad_count", [-5, 0, 1, 2, 25, 100])
    def test_popup_storm_rejects_card_counts_outside_3_to_24(
        self, registry, bad_count: int
    ) -> None:
        base = build_default_studio_config(TemplateId.POPUP_STORM, registry=registry)
        with pytest.raises(StudioValidationError):
            validate_studio_config(
                {
                    **base.model_dump(mode="json"),
                    "popup_storm_settings": {"popup_count": bad_count},
                },
                registry=registry,
            )

    @pytest.mark.parametrize("ring_style", list(RingPresentationStyle))
    def test_countdown_panic_customization(
        self, registry, ring_style: RingPresentationStyle
    ) -> None:
        base = build_default_studio_config(TemplateId.COUNTDOWN, registry=registry)
        cfg = validate_studio_config(
            {
                **base.model_dump(mode="json"),
                "duration_seconds": 15,
                "animation_speed": "slow",
                "sound_enabled": True,
                "countdown_settings": {
                    "ring_style": ring_style.value,
                    "completion_message": "PLOT TWIST — HAPPY FRIDAY!",
                },
            },
            registry=registry,
        )
        runtime = compile_studio_runtime(cfg, registry=registry)
        assert runtime["countdown_seconds"] == 15
        assert runtime["ring_style"] == ring_style.value
        assert runtime["surprise_headline"] == "PLOT TWIST — HAPPY FRIDAY!"
        assert runtime["sound_enabled"] is True

    def test_mismatched_template_settings_strictly_rejected(self, registry) -> None:
        base = build_default_studio_config(TemplateId.GLITCH, registry=registry)
        raw = base.model_dump(mode="json")
        raw["popup_storm_settings"] = {"popup_count": 8}
        with pytest.raises(StudioValidationError, match="popup_storm_settings"):
            validate_studio_config(raw, registry=registry)

    def test_unknown_fields_and_control_chars_rejected(self, registry) -> None:
        base = build_default_studio_config(TemplateId.GLITCH, registry=registry)
        raw = base.model_dump(mode="json")
        raw["execute_js"] = "alert(1)"
        with pytest.raises(StudioValidationError):
            validate_studio_config(raw, registry=registry)

        raw2 = base.model_dump(mode="json")
        raw2["name"] = "Bad\x00Name"
        with pytest.raises(StudioValidationError):
            validate_studio_config(raw2, registry=registry)

        with pytest.raises(StudioValidationError):
            validate_studio_config("not-a-dict", registry=registry)  # type: ignore[arg-type]


class TestMultiStageExperienceComposer:
    def test_starter_add_edit_reorder_remove_and_compile(self, registry) -> None:
        base = build_default_studio_config(TemplateId.GLITCH, registry=registry)
        starter = build_default_stages(base, registry=registry)
        assert len(starter) == 4

        cfg = validate_studio_config(
            {
                **base.model_dump(mode="json"),
                "stages": [s.model_dump(mode="json") for s in starter],
            },
            registry=registry,
        )
        assert cfg.has_stages
        assert len(cfg.stages) == 4

        # Add 5th stage (countdown)
        cfg = add_stage(
            cfg,
            {
                "kind": "countdown",
                "label": "Suspense Timer",
                "duration_seconds": 6,
                "caption": "Final countdown before reveal",
            },
            index=2,
            registry=registry,
        )
        assert len(cfg.stages) == 5
        assert cfg.stages[2].kind is StageKind.COUNTDOWN

        # Reject 6th stage (hard limit = 5)
        with pytest.raises(StudioValidationError, match="maximum of 5 stages"):
            add_stage(
                cfg,
                {"kind": "intro", "label": "Too Many", "duration_seconds": 3},
                registry=registry,
            )

        # Edit stage duration & label
        cfg = update_stage(
            cfg,
            0,
            {"label": "Opening Card", "duration_seconds": 4, "caption": "Buckle up!"},
            registry=registry,
        )
        assert cfg.stages[0].label == "Opening Card"
        assert cfg.stages[0].duration_seconds == 4

        # Move stage & reorder by explicit ID list
        moved = move_stage(cfg, 2, 1, registry=registry)
        assert moved.stages[1].kind is StageKind.COUNTDOWN

        reversed_ids = [s.stage_id for s in reversed(moved.stages)]
        reordered = reorder_stages(moved, reversed_ids, registry=registry)
        assert [s.stage_id for s in reordered.stages] == reversed_ids

        # Compile and verify strictly contiguous non-overlapping schedule
        runtime = compile_studio_runtime(moved, registry=registry, preview_mode=True)
        assert runtime["studio"]["preview_mode"] is True
        comp = runtime["composition"]
        assert comp["stage_count"] == 5
        assert comp["total_duration_seconds"] == moved.effective_duration_seconds
        prev_end = 0
        for item in comp["stages"]:
            assert item["start_second"] == prev_end
            assert item["end_second"] == prev_end + item["duration_seconds"]
            prev_end = item["end_second"]
        assert prev_end == moved.effective_duration_seconds <= 120

        timeline = build_studio_timeline(moved, registry=registry)
        assert len(timeline) == 5
        assert timeline[-1].end_second == float(moved.effective_duration_seconds)

        # Remove stage cleanly
        shorter = remove_stage(moved, 1, registry=registry)
        assert len(shorter.stages) == 4

    def test_enforces_max_120s_total_and_rejects_invalid_stage_timings(
        self, registry
    ) -> None:
        base = build_default_studio_config(TemplateId.COUNTDOWN, registry=registry)

        # Exact 120s boundary is allowed.
        exact = validate_studio_config(
            {
                **base.model_dump(mode="json"),
                "stages": [
                    {"stage_id": "s1", "kind": "intro", "label": "Intro", "duration_seconds": 20},
                    {"stage_id": "s2", "kind": "countdown", "label": "Timer", "duration_seconds": 80},
                    {"stage_id": "s3", "kind": "reveal", "label": "Reveal", "duration_seconds": 20},
                ],
            },
            registry=registry,
        )
        assert exact.effective_duration_seconds == 120

        # 121s total is rejected.
        with pytest.raises(StudioValidationError, match="120 seconds"):
            validate_studio_config(
                {
                    **base.model_dump(mode="json"),
                    "stages": [
                        {"stage_id": "s1", "kind": "intro", "label": "Intro", "duration_seconds": 21},
                        {"stage_id": "s2", "kind": "countdown", "label": "Timer", "duration_seconds": 80},
                        {"stage_id": "s3", "kind": "reveal", "label": "Reveal", "duration_seconds": 20},
                    ],
                },
                registry=registry,
            )

        # Negative, zero, 1s, float, bool durations are rejected.
        for bad_dur in (-5, 0, 1, 4.5, True, "10"):
            with pytest.raises(StudioValidationError):
                validate_studio_config(
                    {
                        **base.model_dump(mode="json"),
                        "stages": [
                            {
                                "stage_id": "s1",
                                "kind": "effect",
                                "label": "Main",
                                "duration_seconds": bad_dur,
                            }
                        ],
                    },
                    registry=registry,
                )

        # Duplicate stage IDs rejected; invalid reorder rejected; out-of-range index rejected.
        with pytest.raises(StudioValidationError, match="Duplicate stage_id"):
            validate_studio_config(
                {
                    **base.model_dump(mode="json"),
                    "stages": [
                        {"stage_id": "dup", "kind": "intro", "label": "A", "duration_seconds": 4},
                        {"stage_id": "dup", "kind": "reveal", "label": "B", "duration_seconds": 4},
                    ],
                },
                registry=registry,
            )

        with pytest.raises(StudioValidationError):
            reorder_stages(exact, ["s1", "s2"], registry=registry)
        with pytest.raises(StudioValidationError):
            remove_stage(exact, 99, registry=registry)
        with pytest.raises(StudioValidationError):
            move_stage(exact, -1, 0, registry=registry)

    def test_multi_template_effect_sequence_compiles_each_stage_runtime(
        self, registry
    ) -> None:
        base = build_default_studio_config(TemplateId.POPUP_STORM, registry=registry)
        cfg = validate_studio_config(
            {
                **base.model_dump(mode="json"),
                "popup_storm_settings": {
                    "popup_count": 12,
                    "card_style": "grid",
                    "message_preset": "office_humor",
                    "cleanup_duration_seconds": 2,
                },
                "stages": [
                    {"stage_id": "a", "kind": "intro", "label": "Intro", "duration_seconds": 3},
                    {
                        "stage_id": "b",
                        "kind": "effect",
                        "label": "Glitch Warmup",
                        "duration_seconds": 6,
                        "template_id": "glitch",
                    },
                    {
                        "stage_id": "c",
                        "kind": "effect",
                        "label": "Office Cards",
                        "duration_seconds": 10,
                        "template_id": "popup_storm",
                    },
                    {"stage_id": "d", "kind": "recovery", "label": "Calm Down", "duration_seconds": 3},
                    {"stage_id": "e", "kind": "reveal", "label": "Reveal", "duration_seconds": 3},
                ],
            },
            registry=registry,
        )
        runtime = compile_studio_runtime(cfg, registry=registry)
        stages = runtime["composition"]["stages"]
        assert stages[1]["effect_runtime"]["template_id"] == "glitch"
        assert stages[2]["effect_runtime"]["template_id"] == "popup_storm"
        assert stages[2]["effect_runtime"]["card_style"] == "grid"
        assert stages[2]["effect_runtime"]["popup_count"] == 12
        assert stages[3]["effect_runtime"] is None
        assert stages[4]["effect_runtime"] is None

    def test_script_like_user_text_rejected_for_arbitrary_stage_kind_and_never_executed(
        self, registry
    ) -> None:
        base = build_default_studio_config(TemplateId.GLITCH, registry=registry)
        # Unknown stage kind like "script" or "html" is strictly rejected.
        for forbidden_kind in ("script", "html", "iframe", "eval", "custom_js"):
            with pytest.raises(StudioValidationError):
                validate_studio_config(
                    {
                        **base.model_dump(mode="json"),
                        "stages": [
                            {
                                "stage_id": "s1",
                                "kind": forbidden_kind,
                                "label": "Nope",
                                "duration_seconds": 6,
                            }
                        ],
                    },
                    registry=registry,
                )

        # Invalid template ID rejected.
        with pytest.raises(TemplateNotFoundError):
            build_default_studio_config("nonexistent", registry=registry)
