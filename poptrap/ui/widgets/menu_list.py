"""Interactive keyboard-driven main menu list for POPTRAP."""

from __future__ import annotations

from collections.abc import Sequence

from rich.text import Text
from textual.binding import Binding
from textual.message import Message
from textual.widgets import OptionList
from textual.widgets.option_list import Option

from poptrap.core.config import LanguageCode
from poptrap.core.i18n import t
from poptrap.core.modules import MAIN_MENU_MODULES, MenuModuleSpec, ModuleId


class MainMenuList(OptionList):
    """Six-option primary navigation list with crisp numeric indexing."""

    BINDINGS = [
        Binding("k", "cursor_up", "Up", show=False),
        Binding("j", "cursor_down", "Down", show=False),
    ]

    class ModuleSelected(Message):
        """Posted when the user activates one of the six main menu entries."""

        def __init__(self, module: MenuModuleSpec) -> None:
            super().__init__()
            self.module = module

    def __init__(
        self,
        *,
        modules: Sequence[MenuModuleSpec] = MAIN_MENU_MODULES,
        language: LanguageCode = LanguageCode.EN,
        id: str | None = "main-menu-list",
    ) -> None:
        self._modules: tuple[MenuModuleSpec, ...] = tuple(modules)
        self._language = language
        options = self._build_options(self._modules, self._language)
        super().__init__(*options, id=id)

    @property
    def modules(self) -> tuple[MenuModuleSpec, ...]:
        """Return the ordered module specifications rendered by this menu."""
        return self._modules

    @property
    def highlighted_module(self) -> MenuModuleSpec | None:
        """Return the currently highlighted ``MenuModuleSpec``, if any."""
        idx = self.highlighted
        if idx is None or not (0 <= idx < len(self._modules)):
            return None
        return self._modules[idx]

    @staticmethod
    def _build_options(
        modules: Sequence[MenuModuleSpec],
        language: LanguageCode,
    ) -> list[Option]:
        built: list[Option] = []
        for spec in modules:
            label = t(spec.label_key, language)
            prompt = Text()
            prompt.append(f"{spec.index}.", style="dim")
            prompt.append(f"  {label}")
            built.append(Option(prompt, id=spec.module_id.value))
        return built

    def on_mount(self) -> None:
        if self.highlighted is None and self.option_count > 0:
            self.highlighted = 0

    def update_language(self, language: LanguageCode) -> None:
        """Rebuild option labels in ``language`` while preserving cursor position."""
        previous_index = self.highlighted if self.highlighted is not None else 0
        self._language = language
        self.clear_options()
        self.add_options(self._build_options(self._modules, self._language))
        if self.option_count > 0:
            self.highlighted = min(max(0, previous_index), self.option_count - 1)

    def activate_index(self, one_based_index: int) -> None:
        """Highlight and select a menu item by its 1-based position (1..6)."""
        zero_based = one_based_index - 1
        if 0 <= zero_based < len(self._modules):
            self.highlighted = zero_based
            self.post_message(self.ModuleSelected(self._modules[zero_based]))

    def highlight_module(self, module_id: ModuleId) -> None:
        """Move highlight cursor to the entry matching ``module_id``."""
        for idx, spec in enumerate(self._modules):
            if spec.module_id is module_id:
                self.highlighted = idx
                return

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        if 0 <= event.option_index < len(self._modules):
            self.post_message(self.ModuleSelected(self._modules[event.option_index]))
