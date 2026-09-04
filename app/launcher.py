"""
Desktop launcher: runs the local server with NO console window and a
system-tray icon.

What happens when the investigator starts the program:

1. Logging is routed to a rotating file in the data folder (there is no
   console to print to), plus the console when one happens to be attached
   (developer runs `python run.py`).
2. If the program is ALREADY RUNNING, the browser tab is (re)opened and
   this second copy exits - double-clicking twice never starts two servers.
   If the port is held by something else, a message box says so.
3. The server starts in a background thread; once it answers, the default
   browser opens the UI.
4. A tray icon (Bitcoin + magnifying glass) stays in the notification
   area with "Open Crypto Investigator" and "Quit". Quit stops the server
   cleanly; interrupted traces are marked failed at the next start.

The web UI is unchanged - the browser remains the interface. The tray icon
only replaces the console window as the program's presence and off switch.
"""

import logging
import logging.handlers
import socket
import sys
import threading
import time
import webbrowser

from app import config

APP_URL = f"http://{config.SERVER_HOST}:{config.SERVER_PORT}"
READY_TIMEOUT_SECONDS = 30.0

log = logging.getLogger("crypto_investigator.launcher")


# ---------------------------------------------------------------------------
# Logging (file first; console only if one is attached)
# ---------------------------------------------------------------------------

def setup_logging() -> None:
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s")
    file_handler = logging.handlers.RotatingFileHandler(
        config.LOG_PATH, maxBytes=config.LOG_MAX_BYTES,
        backupCount=config.LOG_BACKUP_COUNT, encoding="utf-8")
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)
    # pythonw.exe / a windowed PyInstaller build have no console: stdout is
    # None. Only attach a stream handler when there is something to write to.
    if sys.stdout is not None and sys.stderr is not None:
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(formatter)
        root.addHandler(stream)
    # uvicorn's own loggers propagate to root once we clear their handlers.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers = []
        logger.propagate = True
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    # httpx logs every request at INFO; the custody log is the record of
    # data pulls, so keep the program log for events and problems only.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# Single-instance detection
# ---------------------------------------------------------------------------

def _probe_running_instance() -> str:
    """'ours' when this app already answers on the port, 'other' when the
    port is busy with something else, 'free' otherwise."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        if probe.connect_ex((config.SERVER_HOST, config.SERVER_PORT)) != 0:
            return "free"
    try:
        import httpx
        response = httpx.get(f"{APP_URL}/api/meta", timeout=3.0)
        if response.status_code == 200 and \
                response.json().get("app") == config.APP_NAME:
            return "ours"
    except Exception:
        pass
    return "other"


def _message_box(title: str, text: str) -> None:
    """Native message box on Windows; log elsewhere (no console to use)."""
    log.error("%s: %s", title, text)
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, title, 0x10)  # MB_ICONERROR


# ---------------------------------------------------------------------------
# Server thread
# ---------------------------------------------------------------------------

class _Server:
    def __init__(self):
        import uvicorn
        from app import api
        self._api = api
        self._config = uvicorn.Config(
            api.app, host=config.SERVER_HOST, port=config.SERVER_PORT,
            log_level="warning", log_config=None)
        self._server = uvicorn.Server(self._config)
        self._thread = threading.Thread(target=self._server.run,
                                        name="uvicorn", daemon=True)
        self.failed = None

    def start(self) -> None:
        self._api.shutdown_hook = self.stop
        self._thread.start()

    def wait_ready(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self._thread.is_alive():
                return False
            if self._server.started:
                return True
            time.sleep(0.1)
        return False

    def stop(self) -> None:
        self._server.should_exit = True

    def join(self, timeout: float = 10.0) -> None:
        self._thread.join(timeout)


# ---------------------------------------------------------------------------
# Tray icon
# ---------------------------------------------------------------------------

def _tray_image():
    from PIL import Image
    try:
        return Image.open(config.ICON_PATH)
    except OSError:
        # Icon missing (should not happen in a build): plain orange square.
        return Image.new("RGBA", (64, 64), (247, 147, 26, 255))


def _run_tray(server: _Server) -> None:
    """Block on the tray icon until Quit. Returns when the icon is gone."""
    import pystray

    def open_ui(icon=None, item=None):
        webbrowser.open(APP_URL)

    def quit_app(icon, item=None):
        log.info("Quit requested from the tray icon")
        server.stop()
        icon.visible = False
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem("Open Crypto Investigator", open_ui, default=True),
        pystray.MenuItem(f"Running at {APP_URL}", None, enabled=False),
        pystray.MenuItem("Version " + config.APP_VERSION, None,
                         enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", quit_app),
    )
    icon = pystray.Icon("crypto-investigator", _tray_image(),
                        title=f"{config.APP_NAME} - running at {APP_URL}",
                        menu=menu)

    # The API's shutdown endpoint (a Quit button in the UI) must also
    # dismiss the icon, not just stop the server.
    def stop_from_api():
        log.info("Quit requested from the web UI")
        server.stop()
        icon.visible = False
        icon.stop()
    server._api.shutdown_hook = stop_from_api

    def on_ready(icon_obj):
        icon_obj.visible = True
        try:
            icon_obj.notify(f"Running in the background. Find this icon in "
                            f"the notification area to reopen or quit.",
                            config.APP_NAME)
        except Exception:
            pass    # notifications are a nicety; not every backend has them

    icon.run(setup=on_ready)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

_mutex_handle = None


def _hold_instance_mutex() -> None:
    """Hold a named Windows mutex for the life of the process. The
    installer (packaging/installer.iss, AppMutex) uses it to detect a
    running copy and ask the user to quit before upgrading."""
    global _mutex_handle
    if sys.platform == "win32":
        import ctypes
        _mutex_handle = ctypes.windll.kernel32.CreateMutexW(
            None, False, "CryptoInvestigatorRunning")


def main(open_browser: bool = True) -> int:
    setup_logging()
    log.info("%s v%s starting (%s)", config.APP_NAME, config.APP_VERSION,
             "installed build" if config.FROZEN else "source checkout")

    state = _probe_running_instance()
    if state == "ours":
        log.info("Already running; opening the browser and exiting")
        if open_browser:
            webbrowser.open(APP_URL)
        return 0
    if state == "other":
        _message_box(
            f"{config.APP_NAME} cannot start",
            f"Port {config.SERVER_PORT} on this computer is in use by "
            f"another program, so {config.APP_NAME} cannot listen there. "
            f"Close that program and try again.")
        return 2

    _hold_instance_mutex()
    try:
        server = _Server()
        server.start()
    except Exception as exc:
        _message_box(f"{config.APP_NAME} failed to start",
                     f"{exc}\n\nDetails are in the log file:\n"
                     f"{config.LOG_PATH}")
        return 1

    if not server.wait_ready(READY_TIMEOUT_SECONDS):
        _message_box(f"{config.APP_NAME} failed to start",
                     f"The local server did not come up. Details are in "
                     f"the log file:\n{config.LOG_PATH}")
        server.stop()
        return 1

    if open_browser:
        webbrowser.open(APP_URL)

    try:
        _run_tray(server)
    except Exception as exc:
        # No tray backend (unusual on Windows): keep serving in the
        # foreground so the tool still works; Ctrl+C / closing the console
        # stops it.
        log.warning("Tray icon unavailable (%s); running in the "
                    "foreground", exc)
        try:
            while server._thread.is_alive():
                time.sleep(0.5)
        except KeyboardInterrupt:
            server.stop()

    server.stop()
    server.join()
    log.info("Stopped")
    return 0
