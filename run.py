"""
Crypto Investigator launcher (source checkout).

Usage:  python run.py          - with a console (developer use; logs echo)
        pythonw run.pyw        - no console window (what the shortcut and
                                 the installed build use)

Starts the local server on http://127.0.0.1:8321, opens the browser, and
places an icon in the notification area (system tray) with "Open" and
"Quit". The server binds to localhost only - case data never leaves this
machine except for the block-explorer API calls the tool makes on your
behalf.
"""

import sys

from app import launcher

if __name__ == "__main__":
    sys.exit(launcher.main(open_browser="--no-browser" not in sys.argv))
