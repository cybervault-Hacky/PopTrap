#!/usr/bin/env python3
"""Primary command-line entry point for POPTRAP.

Launch with:
    python poptrap.py
"""

from __future__ import annotations

import sys

_MIN_PYTHON = (3, 11)


def _run() -> int:
    """Guard Python compatibility, then delegate to ``poptrap.cli.main``."""
    if sys.version_info < _MIN_PYTHON:
        current = f"{sys.version_info[0]}.{sys.version_info[1]}.{sys.version_info[2]}"
        sys.stderr.write(
            f"POPTRAP error: Python {_MIN_PYTHON[0]}.{_MIN_PYTHON[1]}+ is required "
            f"(detected Python {current}).\n"
            "Guidance: Install Python 3.11 or newer and run 'python poptrap.py'.\n"
        )
        return 2

    from poptrap.cli import main

    return main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(_run())
