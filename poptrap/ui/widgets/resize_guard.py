"""Overlay notice displayed only when the terminal is shrunk below minimum bounds."""

from __future__ import annotations

from textual.widgets import Static

from poptrap.core.config import LanguageCode
from poptrap.core.i18n import t
from poptrap.utils.platform import MIN_TERMINAL_COLUMNS, MIN_TERMINAL_ROWS


class ResizeNotice(Static):
    """Minimal guidance message when terminal dimensions fall below usable size."""

    def update_dimensions(
        self,
        columns: int,
        rows: int,
        *,
        language: LanguageCode = LanguageCode.EN,
    ) -> None:
        """Refresh the notice text with current and minimum dimensions."""
        self.update(
            t(
                "notice.resize",
                language,
                min_w=MIN_TERMINAL_COLUMNS,
                min_h=MIN_TERMINAL_ROWS,
                cur_w=columns,
                cur_h=rows,
            )
        )
