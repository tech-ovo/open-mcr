"""Launcher for the packaged application.

PyInstaller needs a plain script to start from, and it runs that script as
``__main__`` with no parent package - which the modules under ``src/`` cannot
be, because they import each other relatively. This file exists to be that
script: it imports the package properly and hands over.

Running the software from source does not need it; use ``python -m
src.main_gui`` (graphical) or ``python -m src.main`` (command line).
"""

import sys

from src.main_gui import main

if __name__ == "__main__":
    sys.exit(main())
