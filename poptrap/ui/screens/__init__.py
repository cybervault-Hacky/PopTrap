"""Interactive screen classes for the POPTRAP terminal UI."""

from __future__ import annotations

from poptrap.ui.screens.base import BasePopTrapScreen
from poptrap.ui.screens.bluetooth import BluetoothScreen
from poptrap.ui.screens.create_prank import CreatePrankScreen, PrankPreviewScreen
from poptrap.ui.screens.main_menu import MainMenuScreen
from poptrap.ui.screens.module_status import ModuleStatusScreen
from poptrap.ui.screens.prank_studio import PrankStudioScreen, PresetManagerScreen
from poptrap.ui.screens.settings import SettingsScreen

__all__ = [
    "BasePopTrapScreen",
    "BluetoothScreen",
    "CreatePrankScreen",
    "MainMenuScreen",
    "ModuleStatusScreen",
    "PrankPreviewScreen",
    "PrankStudioScreen",
    "PresetManagerScreen",
    "SettingsScreen",
]
