"""Authoritative registry of POPTRAP main menu modules and availability.

Future-phase modules are explicitly marked ``UNAVAILABLE`` so the UI and
application logic never fabricate functionality, devices, links, or sessions.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ModuleId(StrEnum):
    """Canonical identifiers for the six POPTRAP main menu entries."""

    CREATE_PRANK = "create_prank"
    BLUETOOTH = "bluetooth"
    QR_SHARE_LINK = "qr_share_link"
    LOCAL_NETWORK = "local_network"
    SETTINGS = "settings"
    EXIT = "exit"


class ModuleAvailability(StrEnum):
    """Implementation status of a module in the current release."""

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class MenuModuleSpec:
    """Metadata describing a main menu option and its implementation status."""

    index: int
    module_id: ModuleId
    label_key: str
    availability: ModuleAvailability
    detail_key: str | None = None

    @property
    def is_available(self) -> bool:
        """Return True when the module is genuinely implemented in this release."""
        return self.availability is ModuleAvailability.AVAILABLE


MAIN_MENU_MODULES: tuple[MenuModuleSpec, ...] = (
    MenuModuleSpec(
        index=1,
        module_id=ModuleId.CREATE_PRANK,
        label_key="menu.create_prank",
        availability=ModuleAvailability.AVAILABLE,
    ),
    MenuModuleSpec(
        index=2,
        module_id=ModuleId.BLUETOOTH,
        label_key="menu.bluetooth",
        availability=ModuleAvailability.AVAILABLE,
    ),
    MenuModuleSpec(
        index=3,
        module_id=ModuleId.QR_SHARE_LINK,
        label_key="menu.qr_share_link",
        availability=ModuleAvailability.AVAILABLE,
    ),
    MenuModuleSpec(
        index=4,
        module_id=ModuleId.LOCAL_NETWORK,
        label_key="menu.local_network",
        availability=ModuleAvailability.AVAILABLE,
    ),
    MenuModuleSpec(
        index=5,
        module_id=ModuleId.SETTINGS,
        label_key="menu.settings",
        availability=ModuleAvailability.AVAILABLE,
    ),
    MenuModuleSpec(
        index=6,
        module_id=ModuleId.EXIT,
        label_key="menu.exit",
        availability=ModuleAvailability.AVAILABLE,
    ),
)

_MODULES_BY_ID: dict[ModuleId, MenuModuleSpec] = {
    spec.module_id: spec for spec in MAIN_MENU_MODULES
}


def get_module_spec(module_id: ModuleId | str) -> MenuModuleSpec:
    """Look up a ``MenuModuleSpec`` by identifier.

    Raises:
        KeyError: If ``module_id`` is not a registered menu module.
    """
    resolved = module_id if isinstance(module_id, ModuleId) else ModuleId(module_id)
    return _MODULES_BY_ID[resolved]
