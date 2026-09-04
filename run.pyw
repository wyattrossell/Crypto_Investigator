"""Console-free entry point: identical to run.py, but the .pyw extension
makes Windows run it with pythonw.exe, so no command window appears.
Point the desktop shortcut at this file when running from source."""

import sys

from app import launcher

if __name__ == "__main__":
    sys.exit(launcher.main(open_browser="--no-browser" not in sys.argv))
