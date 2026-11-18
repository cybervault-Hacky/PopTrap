"""Tests for POPTRAP Phase 2 configuration validation, limits, and safe IDs."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from poptrap.core.exceptions import PrankConfigError, SessionIdentifierError
from poptrap.engine.models import (
    AnimationSpeed,
    DEFAULT_POPUP_COUNT,
    MAX_CUSTOM_MESSAGE_ITEM_LENGTH,
    MAX_CUSTOM_MESSAGES,
    MAX_DURATION_SECONDS,
    MAX_MESSAGE_LENGTH,
    MAX_POPUP_COUNT,
    MAX_REVEAL_LENGTH,
    MAX_TITLE_LENGTH,
    MIN_DURATION_SECONDS,
    MIN_POPUP_COUNT,
    PrankConfig,
    PrankTheme,
    TemplateId,
    VisualIntensity,
)
from poptrap.engine.validation import (
    generate_session_id,
    parse_positive_int_input,
    parse_semicolon_messages,
    validate_prank_config,
    validate_session_id,
)


def _payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "template_id": "glitch",
        "title": "SIMULATION TITLE",
        "custom_message": "Harmless visual simulation message.",
    }
    base.update(overrides)
    return base


class TestPrankConfigValidation:
    """Verify strict schema rules, field sanitization, and boundary enforcement."""

    def test_valid_configuration_and_immutability(self) -> None:
        cfg = validate_prank_config(
            _payload(
                template_id="glitch",
                title="  SIGNAL TEST  ",
                custom_message="  Simulated frame drift.  ",
                reveal_message="  All good — harmless simulation.  ",
                theme="ice-dark",
                duration_seconds=30,
                intensity="high",
                animation_speed="fast",
                sound_enabled=True,
                auto_terminate=True,
            )
        )
        assert cfg.template_id is TemplateId.GLITCH
        assert cfg.title == "SIGNAL TEST"
        assert cfg.custom_message == "Simulated frame drift."
        assert cfg.reveal_message == "All good — harmless simulation."
        assert cfg.theme is PrankTheme.ICE_DARK
        assert cfg.duration_seconds == 30
        assert cfg.intensity is VisualIntensity.HIGH
        assert cfg.animation_speed is AnimationSpeed.FAST
        assert cfg.sound_enabled is True
        assert cfg.popup_count is None
        assert cfg.hard_lifetime_seconds == 30

        with pytest.raises(ValidationError):
            cfg.duration_seconds = 60  # type: ignore[misc]

    def test_missing_required_title_or_message_rejected(self) -> None:
        with pytest.raises(PrankConfigError) as exc_info:
            validate_prank_config({"template_id": "glitch"})
        joined = " ".join(exc_info.value.field_errors)
        assert "title:" in joined
        assert "custom_message:" in joined

    def test_no_infinite_sessions_even_when_auto_terminate_disabled(self) -> None:
        short = validate_prank_config(
            _payload(
                template_id="countdown", duration_seconds=20, auto_terminate=False
            )
        )
        assert short.auto_terminate is False
        assert short.hard_lifetime_seconds == 50

        near_cap = validate_prank_config(
            _payload(
                template_id="countdown",
                duration_seconds=MAX_DURATION_SECONDS,
                auto_terminate=False,
            )
        )
        assert near_cap.hard_lifetime_seconds == MAX_DURATION_SECONDS

    def test_boundary_durations_accepted(self) -> None:
        low = validate_prank_config(_payload(duration_seconds=MIN_DURATION_SECONDS))
        high = validate_prank_config(_payload(duration_seconds=MAX_DURATION_SECONDS))
        assert low.duration_seconds == MIN_DURATION_SECONDS
        assert high.duration_seconds == MAX_DURATION_SECONDS

    @pytest.mark.parametrize(
        "bad_duration",
        [
            -10,
            -1,
            0,
            1,
            MIN_DURATION_SECONDS - 1,
            MAX_DURATION_SECONDS + 1,
            3600,
            999_999,
            12.5,
            True,
            False,
            "15",
            None,
        ],
    )
    def test_invalid_durations_rejected(self, bad_duration: object) -> None:
        with pytest.raises(PrankConfigError) as exc_info:
            validate_prank_config(_payload(duration_seconds=bad_duration))
        assert any(
            err.startswith("duration_seconds:") for err in exc_info.value.field_errors
        )

    def test_popup_count_defaults_and_limits(self) -> None:
        defaulted = validate_prank_config(_payload(template_id="popup_storm"))
        assert defaulted.popup_count == DEFAULT_POPUP_COUNT

        min_cfg = validate_prank_config(
            _payload(template_id="popup_storm", popup_count=MIN_POPUP_COUNT)
        )
        max_cfg = validate_prank_config(
            _payload(template_id="popup_storm", popup_count=MAX_POPUP_COUNT)
        )
        assert min_cfg.popup_count == MIN_POPUP_COUNT
        assert max_cfg.popup_count == MAX_POPUP_COUNT

        # Valid popup_count passed to a non-popup template normalizes cleanly to None.
        other = validate_prank_config(_payload(template_id="glitch", popup_count=8))
        assert other.popup_count is None

    @pytest.mark.parametrize(
        "bad_popups",
        [
            -5,
            0,
            1,
            MIN_POPUP_COUNT - 1,
            MAX_POPUP_COUNT + 1,
            100,
            10_000,
            8.5,
            True,
            "10",
        ],
    )
    def test_invalid_popup_count_rejected(self, bad_popups: object) -> None:
        with pytest.raises(PrankConfigError) as exc_info:
            validate_prank_config(
                _payload(template_id="popup_storm", popup_count=bad_popups)
            )
        assert (
            "popup_count" in exc_info.value.message.lower()
            or "popup count" in exc_info.value.message.lower()
        )

    def test_out_of_range_popup_count_rejected_even_on_non_popup_template(self) -> None:
        with pytest.raises(PrankConfigError):
            validate_prank_config(
                _payload(template_id="glitch", popup_count=MAX_POPUP_COUNT + 50)
            )

    def test_custom_messages_limits_and_parsing(self) -> None:
        parsed = parse_semicolon_messages(" Card A ; Card B ;  ")
        assert parsed == ("Card A", "Card B")
        assert parse_semicolon_messages("   ") == ()

        cfg = validate_prank_config(
            _payload(template_id="popup_storm", custom_messages=list(parsed))
        )
        assert cfg.custom_messages == ("Card A", "Card B")

        # Whitespace-padded items are trimmed; empty list entries are rejected strictly.
        trimmed = validate_prank_config(
            _payload(template_id="popup_storm", custom_messages=["  Card A  "])
        )
        assert trimmed.custom_messages == ("Card A",)
        with pytest.raises(PrankConfigError):
            validate_prank_config(
                _payload(template_id="popup_storm", custom_messages=["Card A", "   "])
            )

        too_many = [f"Card {i}" for i in range(MAX_CUSTOM_MESSAGES + 1)]
        with pytest.raises(PrankConfigError):
            validate_prank_config(
                _payload(template_id="popup_storm", custom_messages=too_many)
            )
        with pytest.raises(PrankConfigError):
            parse_semicolon_messages(";".join(too_many))

        too_long = ["x" * (MAX_CUSTOM_MESSAGE_ITEM_LENGTH + 1)]
        with pytest.raises(PrankConfigError):
            validate_prank_config(
                _payload(template_id="popup_storm", custom_messages=too_long)
            )

    @pytest.mark.parametrize(
        "bad_fields",
        [
            {"template_id": "nonexistent"},
            {"title": ""},
            {"title": "   "},
            {"title": "T" * (MAX_TITLE_LENGTH + 1)},
            {"title": "Bad\x00Title"},
            {"title": "Spoof\u202eTitle"},
            {"custom_message": ""},
            {"custom_message": "M" * (MAX_MESSAGE_LENGTH + 1)},
            {"custom_message": "Bad\x07Bell"},
            {"reveal_message": ""},
            {"reveal_message": "R" * (MAX_REVEAL_LENGTH + 1)},
            {"theme": "neon-rainbow"},
            {"intensity": "extreme"},
            {"animation_speed": "ludicrous"},
            {"sound_enabled": "yes"},
            {"auto_terminate": 1},
            {"unexpected_field": True},
        ],
    )
    def test_rejects_malformed_or_disallowed_fields(
        self, bad_fields: dict[str, object]
    ) -> None:
        with pytest.raises(PrankConfigError):
            validate_prank_config(_payload(**bad_fields))

    def test_rejects_non_mapping_input_and_bad_int_text(self) -> None:
        with pytest.raises(PrankConfigError):
            validate_prank_config(["not", "a", "dict"])  # type: ignore[arg-type]

        existing = PrankConfig.model_validate(_payload())
        assert validate_prank_config(existing) is existing

        assert (
            parse_positive_int_input(
                " 42 ", field_name="duration_seconds", label="Duration"
            )
            == 42
        )
        assert (
            parse_positive_int_input(
                "-5", field_name="duration_seconds", label="Duration"
            )
            == -5
        )
        for bad_text in ("", "   ", "abc", "12.5", "10s", "--5"):
            with pytest.raises(PrankConfigError):
                parse_positive_int_input(
                    bad_text, field_name="duration_seconds", label="Duration"
                )


class TestSessionIdSecurity:
    """Verify session IDs are cryptographically random and traversal-safe."""

    def test_generated_ids_are_unique_and_valid(self) -> None:
        ids = {generate_session_id() for _ in range(200)}
        assert len(ids) == 200
        for sid in ids:
            assert validate_session_id(sid) == sid

    @pytest.mark.parametrize(
        "bad_id",
        [
            "",
            "short",
            "a" * 19,
            "a" * 33,
            "../etc/passwd_padding_1234",
            "abcdefghijklmnopqrst/uvwx",
            "abcdefghijklmnopqrst\\uvwx",
            "abcdefghijklmnopqrst\x00uvwx",
            "abcdefghijklmnopqrst uvwx",
            "<script>alert(1)</script>",
            "abcdefghijklmnopqrst;drop",
            None,
            12345678901234567890,
        ],
    )
    def test_rejects_unsafe_or_malformed_session_ids(self, bad_id: object) -> None:
        with pytest.raises(SessionIdentifierError):
            validate_session_id(bad_id)  # type: ignore[arg-type]
