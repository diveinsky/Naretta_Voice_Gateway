"""Entry script for running from source and for PyInstaller."""

import sys

from naretta.app import main

if __name__ == "__main__":
    sys.exit(main())
