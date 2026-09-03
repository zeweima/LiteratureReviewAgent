"""Deprecated shim -> `run_stage.py --step fine` (kept for back-compat).

    python run_fine.py --track frontier      # == run_stage.py --step fine --track frontier
"""
from __future__ import annotations

import sys

from run_stage import main

if __name__ == "__main__":
    if not any(a == "--step" or a.startswith("--step=") for a in sys.argv):
        sys.argv += ["--step", "fine"]
    main()
