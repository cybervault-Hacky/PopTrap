"""Tests for POPTRAP Phase 2 template registry and all five prank templates."""

from __future__ import annotations

import pytest

from poptrap.core.exceptions import (
    PrankConfigError,
    TemplateNotFoundError,
    TemplateRegistrationError,
)
from poptrap.engine.models import (
    AnimationSpeed,
    MAX_POPUP_COUNT,
    PrankConfig,
    PreviewStage,
    TemplateId,
    TemplateMetadata,
    VisualIntensity,
)
from poptrap.engine.registry import TemplateRegistry, create_default_registry
from poptrap.engine.templates import (
    BUILTIN_TEMPLATES,
    COMMON_SAFETY_CONTROLS,
    CountdownPanicTemplate,
    FakeSystemCrashTemplate,
    FakeVirusAlertTemplate,
    GlitchAttackTemplate,
    PopupStormTemplate,
    PrankTemplate,
)


EXPECTED_TEMPLATE_ORDER: list[TemplateId] = [
    TemplateId.GLITCH,
    TemplateId.VIRUS_ALERT,
    TemplateId.SYSTEM_CRASH,
    TemplateId.POPUP_STORM,
    TemplateId.COUNTDOWN,
]


def _cfg_for(template: PrankTemplate, **overrides: object) -> PrankConfig:
    base = template.build_default_config().model_dump()
    base.update(overrides)
    return PrankConfig.model_validate(base)


class TestTemplateRegistry:
    """Verify template registration, lookup, ordering, and error handling."""

    def test_default_registry_contains_all_five_templates_in_order(self) -> None:
        registry = create_default_registry()
        assert len(registry) == 5
        assert len(BUILTIN_TEMPLATES) == 5
        metadata_list = registry.list_metadata()
        assert [m.template_id for m in metadata_list] == EXPECTED_TEMPLATE_ORDER
        assert [t.template_id for t in registry.list_templates()] == EXPECTED_TEMPLATE_ORDER
        for tid in EXPECTED_TEMPLATE_ORDER:
            assert registry.contains(tid)
            assert registry.contains(tid.value)
            template = registry.get(tid)
            assert registry.get(tid.value) is template
            assert template.template_id is tid
            assert template.metadata.name
            assert template.metadata.summary
            assert len(template.metadata.safety_controls) >= len(COMMON_SAFETY_CONTROLS)

    def test_register_duplicate_rejected(self) -> None:
        registry = TemplateRegistry()
        first = GlitchAttackTemplate()
        duplicate = GlitchAttackTemplate()

        registry.register(first)
        assert len(registry) == 1

        with pytest.raises(TemplateRegistrationError, match="already registered"):
            registry.register(duplicate)
        assert registry.get(TemplateId.GLITCH) is first
        assert len(registry) == 1

    def test_register_rejects_non_template_object(self) -> None:
        registry = TemplateRegistry()
        with pytest.raises(TemplateRegistrationError, match="PrankTemplate"):
            registry.register("not-a-template")  # type: ignore[arg-type]

    @pytest.mark.parametrize("bad_id", ["unknown", "", "bluetooth", 42, None])
    def test_unknown_or_invalid_template_lookup_raises(self, bad_id: object) -> None:
        registry = create_default_registry()
        assert registry.contains(bad_id) is False  # type: ignore[arg-type]
        with pytest.raises(TemplateNotFoundError):
            registry.get(bad_id)  # type: ignore[arg-type]

    def test_default_config_builds_valid_configuration_for_every_template(self) -> None:
        registry = create_default_registry()
        for tid in EXPECTED_TEMPLATE_ORDER:
            cfg = registry.get(tid).build_default_config()
            assert cfg.template_id is tid
            assert cfg.auto_terminate is True
            if tid is TemplateId.POPUP_STORM:
                assert cfg.popup_count is not None
                assert cfg.popup_count <= MAX_POPUP_COUNT
            else:
                assert cfg.popup_count is None


class TestFivePrankTemplates:
    """Verify concrete previews, timelines, and runtime payloads for all 5 templates."""

    @pytest.mark.parametrize("template_id", EXPECTED_TEMPLATE_ORDER)
    @pytest.mark.parametrize("intensity", list(VisualIntensity))
    @pytest.mark.parametrize("speed", list(AnimationSpeed))
    def test_preview_and_runtime_payload_across_matrix(
        self,
        template_id: TemplateId,
        intensity: VisualIntensity,
        speed: AnimationSpeed,
    ) -> None:
        registry = create_default_registry()
        template = registry.get(template_id)
        cfg = _cfg_for(
            template,
            duration_seconds=20,
            intensity=intensity,
            animation_speed=speed,
        )

        preview = template.build_preview(cfg)
        assert preview.template_id is template_id
        assert preview.template_name == template.metadata.name
        assert preview.config == cfg
        assert len(preview.stages) >= 2
        assert preview.stages[0].start_second == 0.0
        assert preview.stages[-1].end_second == float(cfg.duration_seconds)
        for stage in preview.stages:
            assert stage.end_second >= stage.start_second
            assert stage.label
            assert stage.description
        assert len(preview.safety_controls) >= len(COMMON_SAFETY_CONTROLS)

        payload = template.build_runtime_payload(cfg)
        assert payload["template_id"] == template_id.value
        assert isinstance(payload["simulation_badge"], str)
        assert "SIMULAT" in str(payload["simulation_badge"]).upper()

    def test_glitch_template_scales_intensity_and_recovers(self) -> None:
        template = GlitchAttackTemplate()
        low = template.build_runtime_payload(
            _cfg_for(template, intensity=VisualIntensity.LOW)
        )
        high = template.build_runtime_payload(
            _cfg_for(template, intensity=VisualIntensity.HIGH)
        )
        assert int(high["rgb_offset_px"]) > int(low["rgb_offset_px"])
        assert int(high["slice_count"]) > int(low["slice_count"])
        assert 0.5 < float(high["recovery_start_ratio"]) < 1.0
        preview = template.build_preview(_cfg_for(template))
        assert preview.effect_summary["auto_recovery"] is True

    def test_virus_alert_uses_only_fictional_targets_and_funny_verdict(self) -> None:
        template = FakeVirusAlertTemplate()
        cfg = _cfg_for(template, intensity=VisualIntensity.HIGH)
        payload = template.build_runtime_payload(cfg)

        scan_items = payload["scan_items"]
        assert isinstance(scan_items, list) and len(scan_items) >= 4
        for target in scan_items:
            assert str(target).startswith("sim://")

        findings = payload["findings"]
        assert isinstance(findings, list) and len(findings) >= 3
        for card in findings:
            assert str(card["code"]).startswith("SIM-")
            assert card["badge"] in {
                "FICTIONAL",
                "HARMLESS",
                "SIMULATED",
                "SIMULATION",
                "THEATRICAL",
            }
        assert "0" in str(payload["funny_verdict"])
        assert "real" in str(payload["funny_verdict"]).lower()

    def test_system_crash_includes_fictional_diagnostics_and_restart_sequence(
        self,
    ) -> None:
        template = FakeSystemCrashTemplate()
        payload = template.build_runtime_payload(template.build_default_config())

        assert "SIM" in str(payload["fault_code"]).upper()
        assert len(payload["diagnostics"]) >= 4
        assert len(payload["restart_steps"]) >= 3
        assert float(payload["restart_start_ratio"]) < float(
            payload["recovery_start_ratio"]
        )
        assert "recover" in str(payload["recover_button_label"]).lower()

    def test_popup_storm_respects_count_cap_speed_and_custom_messages(self) -> None:
        template = PopupStormTemplate()
        slow_cfg = _cfg_for(
            template,
            popup_count=6,
            animation_speed=AnimationSpeed.SLOW,
            custom_messages=("First playful card", "Second playful card"),
        )
        fast_cfg = _cfg_for(
            template,
            popup_count=MAX_POPUP_COUNT,
            animation_speed=AnimationSpeed.FAST,
        )
        slow_payload = template.build_runtime_payload(slow_cfg)
        fast_payload = template.build_runtime_payload(fast_cfg)

        assert slow_payload["popup_count"] == 6
        assert fast_payload["popup_count"] == MAX_POPUP_COUNT
        assert fast_payload["max_popup_limit"] == MAX_POPUP_COUNT
        assert int(fast_payload["spawn_interval_ms"]) < int(
            slow_payload["spawn_interval_ms"]
        )
        assert "First playful card" in slow_payload["messages"]
        assert "Second playful card" in slow_payload["messages"]

        preview = template.build_preview(fast_cfg)
        assert preview.effect_summary["browser_windows_opened"] == 0

    def test_countdown_reflects_duration_and_optional_sound_settings(self) -> None:
        template = CountdownPanicTemplate()
        silent = _cfg_for(template, duration_seconds=12, sound_enabled=False)
        audible = _cfg_for(template, duration_seconds=25, sound_enabled=True)

        p_silent = template.build_runtime_payload(silent)
        p_audible = template.build_runtime_payload(audible)

        assert p_silent["countdown_seconds"] == 12
        assert p_audible["countdown_seconds"] == 25
        assert len(p_audible["reveal_chime_hz"]) == 3
        assert len(p_audible["stage_captions"]) >= 3
        assert template.build_preview(silent).effect_summary["sound_cues_enabled"] is False
        assert template.build_preview(audible).effect_summary["sound_cues_enabled"] is True

    def test_template_rejects_mismatched_config_template_id(self) -> None:
        glitch = GlitchAttackTemplate()
        countdown_cfg = CountdownPanicTemplate().build_default_config()
        with pytest.raises(PrankConfigError, match="does not match"):
            glitch.build_preview(countdown_cfg)

    def test_custom_subclass_contract(self) -> None:
        class _CustomTemplate(PrankTemplate):
            @property
            def metadata(self) -> TemplateMetadata:
                return TemplateMetadata(
                    template_id=TemplateId.GLITCH,
                    name="Custom Glitch",
                    summary="Custom test template.",
                    default_title="CUSTOM",
                    default_message="Custom message.",
                    default_reveal_message="Custom reveal.",
                    default_duration_seconds=10,
                    safety_controls=COMMON_SAFETY_CONTROLS,
                )

            def build_stages(self, config: PrankConfig) -> tuple[PreviewStage, ...]:
                return (
                    PreviewStage(
                        label="Only",
                        start_second=0.0,
                        end_second=float(config.duration_seconds),
                        description="Single stage.",
                    ),
                )

            def build_effect_summary(
                self, config: PrankConfig
            ) -> dict[str, str | int | float | bool]:
                return {"ok": True}

            def build_runtime_payload(self, config: PrankConfig) -> dict[str, object]:
                return {"template_id": "glitch", "simulation_badge": "SIMULATION"}

        reg = TemplateRegistry()
        reg.register(_CustomTemplate())
        assert reg.get("glitch").metadata.name == "Custom Glitch"
