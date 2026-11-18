"""Registry for discovering and selecting POPTRAP prank templates."""

from __future__ import annotations

from poptrap.core.exceptions import TemplateNotFoundError, TemplateRegistrationError
from poptrap.engine.models import TemplateId, TemplateMetadata
from poptrap.engine.templates import BUILTIN_TEMPLATES, PrankTemplate


class TemplateRegistry:
    """Ordered, validated registry of available ``PrankTemplate`` implementations."""

    def __init__(self) -> None:
        self._templates: dict[TemplateId, PrankTemplate] = {}

    def register(self, template: PrankTemplate) -> None:
        """Register a ``PrankTemplate`` instance.

        Raises:
            TemplateRegistrationError: If ``template`` is invalid or duplicated.
        """
        if not isinstance(template, PrankTemplate):
            raise TemplateRegistrationError(
                f"Expected a PrankTemplate instance, got {type(template).__name__}."
            )
        tid = template.template_id
        if tid in self._templates:
            raise TemplateRegistrationError(
                f"Prank template '{tid.value}' is already registered."
            )
        self._templates[tid] = template

    def get(self, template_id: TemplateId | str) -> PrankTemplate:
        """Retrieve a registered template by ID.

        Raises:
            TemplateNotFoundError: When the template ID is unknown or unregistered.
        """
        resolved: TemplateId | None = None
        if isinstance(template_id, TemplateId):
            resolved = template_id
        elif isinstance(template_id, str):
            normalized = template_id.strip().lower()
            try:
                resolved = TemplateId(normalized)
            except ValueError:
                resolved = None

        if resolved is None or resolved not in self._templates:
            available = ", ".join(item.value for item in self._templates) or "none"
            raise TemplateNotFoundError(
                f"Unknown prank template '{template_id}'.",
                hint=f"Available templates: {available}.",
            )
        return self._templates[resolved]

    def contains(self, template_id: TemplateId | str) -> bool:
        """Return True if ``template_id`` is registered."""
        try:
            self.get(template_id)
        except TemplateNotFoundError:
            return False
        return True

    def list_templates(self) -> tuple[PrankTemplate, ...]:
        """Return all registered templates in registration order."""
        return tuple(self._templates.values())

    def list_metadata(self) -> tuple[TemplateMetadata, ...]:
        """Return public ``TemplateMetadata`` for all registered templates."""
        return tuple(tpl.metadata for tpl in self._templates.values())

    def __len__(self) -> int:
        return len(self._templates)


def create_default_registry() -> TemplateRegistry:
    """Create a ``TemplateRegistry`` populated with all built-in Phase 2 templates."""
    registry = TemplateRegistry()
    for template_cls in BUILTIN_TEMPLATES:
        registry.register(template_cls())
    return registry
