"""Minimal POPTRAP branding and screen section header widgets."""

from __future__ import annotations

from rich.console import RenderableType
from rich.table import Table
from rich.text import Text
from textual.widgets import Static

_POPTRAP_GLYPH_LINES: tuple[str, str, str] = (
    "┏━┓┏━┓┏━┓╺┳╸┏━┓┏━┓┏━┓",
    "┣━┛┃ ┃┣━┛ ┃ ┣┳┛┣━┫┣━┛",
    "╹  ┗━┛╹   ╹ ╹┗╸╹ ╹╹  ",
)


class BrandHeader(Static):
    """Restrained geometric POPTRAP wordmark with adaptive compact mode."""

    def __init__(
        self,
        app_name: str,
        version: str,
        *,
        unicode_enabled: bool = True,
        compact: bool = False,
        id: str | None = None,
    ) -> None:
        super().__init__(id=id)
        self._app_name = app_name
        self._version = version
        self._unicode_enabled = unicode_enabled
        self._compact = compact

    def update_branding(
        self,
        app_name: str,
        version: str,
        *,
        unicode_enabled: bool | None = None,
        compact: bool | None = None,
    ) -> None:
        """Refresh displayed brand text, version, or compactness."""
        self._app_name = app_name
        self._version = version
        if unicode_enabled is not None:
            self._unicode_enabled = unicode_enabled
        if compact is not None:
            self._compact = compact
        self.refresh(layout=True)

    def set_compact(self, compact: bool) -> None:
        """Toggle single-line compact branding for small terminal windows."""
        if self._compact != compact:
            self._compact = compact
            self.refresh(layout=True)

    def render(self) -> RenderableType:
        width = max(24, self.size.width or 52)
        primary = self.app.current_theme.primary or "#22d3ee"
        muted = (
            self.app.current_theme.variables.get("text-dim", "#6e7f94")
            if self.app.current_theme.variables
            else "#6e7f94"
        )
        rule_color = (
            self.app.current_theme.variables.get("border-subtle", "#1b2533")
            if self.app.current_theme.variables
            else "#1b2533"
        )
        rule_char = "─" if self._unicode_enabled else "-"
        version_tag = f"v{self._version}"

        use_wordmark = (
            self._unicode_enabled
            and not self._compact
            and self._app_name.strip().upper() == "POPTRAP"
            and width >= 34
        )

        grid = Table.grid(expand=True)
        grid.add_column(ratio=1)

        if use_wordmark:
            grid.add_row(Text(_POPTRAP_GLYPH_LINES[0], style=f"bold {primary}"))
            grid.add_row(Text(_POPTRAP_GLYPH_LINES[1], style=f"bold {primary}"))
            bottom_row = Table.grid(expand=True)
            bottom_row.add_column(justify="left", ratio=1)
            bottom_row.add_column(justify="right")
            bottom_row.add_row(
                Text(_POPTRAP_GLYPH_LINES[2], style=f"bold {primary}"),
                Text(version_tag, style=muted),
            )
            grid.add_row(bottom_row)
        else:
            title_row = Table.grid(expand=True)
            title_row.add_column(justify="left", ratio=1)
            title_row.add_column(justify="right")
            title_row.add_row(
                Text(self._app_name.upper(), style=f"bold {primary}"),
                Text(version_tag, style=muted),
            )
            grid.add_row(title_row)

        grid.add_row(Text(rule_char * width, style=rule_color))
        return grid


class SectionHeader(Static):
    """Minimal single-line screen title with right-aligned metadata and divider rule."""

    def __init__(
        self,
        title: str,
        meta: str = "",
        *,
        unicode_enabled: bool = True,
        accent_meta: bool = False,
        id: str | None = None,
    ) -> None:
        super().__init__(id=id, classes="section-header-block")
        self._title = title
        self._meta = meta
        self._unicode_enabled = unicode_enabled
        self._accent_meta = accent_meta

    def set_content(
        self,
        title: str,
        meta: str = "",
        *,
        accent_meta: bool | None = None,
    ) -> None:
        """Update header title and right-aligned tag."""
        self._title = title
        self._meta = meta
        if accent_meta is not None:
            self._accent_meta = accent_meta
        self.refresh(layout=True)

    def render(self) -> RenderableType:
        width = max(24, self.size.width or 60)
        fg = self.app.current_theme.foreground or "#e6edf3"
        primary = self.app.current_theme.primary or "#22d3ee"
        muted = (
            self.app.current_theme.variables.get("text-dim", "#6e7f94")
            if self.app.current_theme.variables
            else "#6e7f94"
        )
        rule_color = (
            self.app.current_theme.variables.get("border-subtle", "#1b2533")
            if self.app.current_theme.variables
            else "#1b2533"
        )
        rule_char = "─" if self._unicode_enabled else "-"
        meta_style = primary if self._accent_meta else muted

        top = Table.grid(expand=True)
        top.add_column(justify="left", ratio=1)
        top.add_column(justify="right")
        top.add_row(
            Text(self._title.upper(), style=f"bold {fg}"),
            Text(self._meta, style=meta_style),
        )

        outer = Table.grid(expand=True)
        outer.add_column(ratio=1)
        outer.add_row(top)
        outer.add_row(Text(rule_char * width, style=rule_color))
        return outer
