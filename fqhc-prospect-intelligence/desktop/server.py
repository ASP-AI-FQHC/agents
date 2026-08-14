"""Runs the FastAPI app in a background thread for the desktop window.

Kept separate from the window itself so the whole lifecycle -- pick a port,
start, wait until it answers, shut down -- is testable without a display or a
webview runtime.
"""

from __future__ import annotations

import socket
import threading
import time
from typing import Any

import httpx


def free_port(host: str = "127.0.0.1") -> int:
    """Ask the OS for an unused port.

    Binding to port 0 and reading back the assignment avoids both a hard-coded
    port clashing with something else and a second copy of the app fighting the
    first for it.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


class ServerController:
    """Starts uvicorn on a background thread and stops it cleanly."""

    def __init__(
        self,
        app: Any,
        *,
        host: str = "127.0.0.1",
        port: int | None = None,
        log_level: str = "warning",
    ) -> None:
        self.host = host
        self.port = port or free_port(host)
        self._app = app
        self._log_level = log_level
        self._server: Any = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def start(self) -> None:
        import uvicorn

        config = uvicorn.Config(
            self._app,
            host=self.host,
            port=self.port,
            log_level=self._log_level,
            # The window is the only client; access logs would just be noise.
            access_log=False,
        )
        self._server = uvicorn.Server(config)
        # install_signal_handlers only works on the main thread, and the window
        # owns that here.
        self._server.install_signal_handlers = lambda: None

        self._thread = threading.Thread(
            target=self._server.run, name="fqhc-uvicorn", daemon=True
        )
        self._thread.start()

    def wait_until_ready(self, timeout: float = 30.0, interval: float = 0.1) -> bool:
        """Poll /healthz until the server answers. False if it never does."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._thread is not None and not self._thread.is_alive():
                return False  # the server died during startup
            try:
                response = httpx.get(f"{self.url}/healthz", timeout=1.0)
                if response.status_code == 200:
                    return True
            except httpx.HTTPError:
                pass
            time.sleep(interval)
        return False

    def stop(self, timeout: float = 10.0) -> None:
        """Ask uvicorn to exit and wait for the thread to finish."""
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout)

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def __enter__(self) -> "ServerController":
        self.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.stop()
