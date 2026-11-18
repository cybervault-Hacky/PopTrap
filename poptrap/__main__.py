"""Module entry point enabling ``python -m poptrap``."""

from __future__ import annotations

import sys

from poptrap.cli import main

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
