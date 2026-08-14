"""Desktop entry point: a native window wrapped around the local app.

    python -m desktop.main

The order here matters. ``bootstrap()`` must run before ``app.main`` is
imported, because that module reads its configuration at import time -- and in
a packaged build the configuration has to come from the user's writable data
directory rather than the read-only bundle.
"""

from __future__ import annotations

import os
import sys
import time

from desktop.paths import APP_NAME, bootstrap, is_frozen
from desktop.server import ServerController

WINDOW_WIDTH = 1440
WINDOW_HEIGHT = 900
MIN_WIDTH = 900
MIN_HEIGHT = 600


def main() -> int:
    config_path, data_dir = bootstrap()

    # Imported only now: bootstrap has just pointed FQHC_CONFIG at the user's
    # own copy of config.yaml.
    from app.main import app as fastapi_app

    if not is_frozen():
        print(f"Config:    {config_path}")
        print(f"Data:      {data_dir}")

    server = ServerController(fastapi_app)
    server.start()

    if not server.wait_until_ready():
        print(
            "The application server did not start. Run "
            "`uvicorn app.main:app` to see the underlying error.",
            file=sys.stderr,
        )
        server.stop()
        return 1

    if not is_frozen():
        print(f"Serving:   {server.url}")

    # Escape hatch: serve without opening a window, for debugging or for a
    # machine whose webview runtime is broken.
    if "--no-window" in sys.argv or os.environ.get("FQHC_NO_WINDOW"):
        print(f"Serving at {server.url} -- press Ctrl-C to stop.", flush=True)
        try:
            while server.is_running:
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            server.stop()
        return 0

    try:
        import webview
    except ImportError:
        print(
            "pywebview is not installed. Install the desktop extras with\n"
            "    pip install -r requirements-desktop.txt\n"
            f"or just open {server.url} in a browser.",
            file=sys.stderr,
        )
        server.stop()
        return 1

    try:
        webview.create_window(
            APP_NAME,
            server.url,
            width=WINDOW_WIDTH,
            height=WINDOW_HEIGHT,
            min_size=(MIN_WIDTH, MIN_HEIGHT),
            text_select=True,
        )
        # Blocks until the user closes the window.
        webview.start()
    finally:
        # Always stop the server, including on an unexpected window failure, so
        # closing the app never leaves a port bound.
        server.stop()

    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
