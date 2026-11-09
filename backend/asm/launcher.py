"""Desktop launcher used by the Windows .exe.

Starts the local server on a background thread, opens the app in the default browser, and shows a small
control window (Open app / Open Outputs folder / Quit). Closing the window stops the server.
`AjaxSubmittalManager.exe --selftest` starts the server, checks /api/health and exits (used by CI).
"""
from __future__ import annotations

import logging
import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser

APP_NAME = "Ajax Submittal Manager"


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) != 0


def _asm_running(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1.5) as r:
            return b'"ok":true' in r.read().replace(b" ", b"")
    except Exception:
        return False


def _wait_ready(port: int, timeout: float = 60) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        if _asm_running(port):
            return True
        time.sleep(0.4)
    return False


def _setup_logging() -> str:
    from .config import data_dir
    path = data_dir() / "asm.log"
    logging.basicConfig(filename=str(path), level=logging.INFO, encoding="utf-8",
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return str(path)


def _start_server(port: int):
    import uvicorn
    from .api.app import app
    cfg = uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None, loop="asyncio", http="h11",
                         ws="none", lifespan="on", access_log=False)
    server = uvicorn.Server(cfg)
    th = threading.Thread(target=server.run, name="asm-server", daemon=True)
    th.start()
    return server, th


def _pick_port(preferred: int) -> tuple[int, bool]:
    """Return (port, already_running)."""
    if _asm_running(preferred):
        return preferred, True
    for p in [preferred] + list(range(preferred + 1, preferred + 20)):
        if _port_free(p):
            return p, False
    return preferred, False


APP_USER_MODEL_ID = "AjaxBuildingCompany.SubmittalManager"


def _windows_shell_setup() -> None:
    """Crisp taskbar icon and text on Windows: own taskbar identity + per-monitor DPI awareness
    (without it Windows bitmap-stretches the window and its icon on scaled displays)."""
    if sys.platform != "win32":
        return
    import ctypes

    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _icon_dir() -> str:
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return base
    return os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "packaging")


def _set_window_icon(root) -> None:
    import tkinter as tk

    d = _icon_dir()
    ico = os.path.join(d, "asm.ico")
    try:
        if sys.platform == "win32" and os.path.exists(ico):
            root.iconbitmap(default=ico)
    except Exception:
        pass
    try:
        imgs = [tk.PhotoImage(file=os.path.join(d, f"asm-{s}.png")) for s in (256, 48, 32, 16)
                if os.path.exists(os.path.join(d, f"asm-{s}.png"))]
        if imgs:
            root.iconphoto(True, *imgs)  # large + small: taskbar and title bar each pick a sharp size
            root._asm_icons = imgs  # keep references alive
    except Exception:
        pass


def _control_window(url: str, server, log_path: str) -> None:
    import tkinter as tk
    from tkinter import ttk

    from .config import outputs_dir

    _windows_shell_setup()
    root = tk.Tk()
    root.title(APP_NAME)
    scale = 1.0
    try:
        scale = max(1.0, root.winfo_fpixels("1i") / 96.0)
    except Exception:
        pass
    root.geometry(f"{int(380 * scale)}x{int(210 * scale)}")
    root.resizable(False, False)
    root.configure(bg="#ffffff")
    _set_window_icon(root)
    bar = tk.Frame(root, bg="#FF5200", height=5)
    bar.pack(fill="x")
    tk.Label(root, text="AJAX  Submittal Manager", font=("Segoe UI", 13, "bold"), bg="#ffffff", fg="#232729").pack(pady=(14, 2))
    status = tk.Label(root, text=f"Running at {url}", font=("Segoe UI", 9), bg="#ffffff", fg="#6A767C")
    status.pack()
    tk.Label(root, text="Keep this window open while you use the app.", font=("Segoe UI", 9), bg="#ffffff",
             fg="#6A767C").pack(pady=(2, 10))
    row = tk.Frame(root, bg="#ffffff")
    row.pack()
    style = ttk.Style(root)
    try:
        style.theme_use("vista")
    except Exception:
        pass
    ttk.Button(row, text="Open app", command=lambda: webbrowser.open(url)).grid(row=0, column=0, padx=4)
    ttk.Button(row, text="Outputs folder", command=lambda: _open_path(str(outputs_dir()))).grid(row=0, column=1, padx=4)
    ttk.Button(row, text="Log", command=lambda: _open_path(log_path)).grid(row=0, column=2, padx=4)

    def quit_app() -> None:
        status.config(text="Stopping…")
        root.update()
        if server is not None:
            server.should_exit = True
        root.after(600, root.destroy)

    ttk.Button(root, text="Quit", command=quit_app).pack(pady=(12, 0))
    root.protocol("WM_DELETE_WINDOW", quit_app)
    root.mainloop()


def _open_path(p: str) -> None:
    try:
        if os.name == "nt":
            os.startfile(p)  # type: ignore[attr-defined]
        else:
            webbrowser.open(f"file://{p}")
    except Exception:
        pass


def main() -> None:
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    log_path = _setup_logging()
    from .config import settings_cfg
    preferred = int(settings_cfg().get("server", {}).get("port", 8765))

    if "--selftest" in sys.argv:
        port = next(p for p in range(18765, 18800) if _port_free(p))
        server, _ = _start_server(port)
        ok = _wait_ready(port, 90)
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as r:
            ui_ok = b"<div id=\"root\">" in r.read()
        icons = all(os.path.exists(os.path.join(_icon_dir(), f)) for f in ("asm.ico", "asm-256.png", "asm-32.png"))
        server.should_exit = True
        time.sleep(1)
        good = ok and ui_ok and icons
        print("SELFTEST", "OK" if good else "FAIL", f"health={ok} ui={ui_ok} icons={icons}")
        sys.exit(0 if good else 1)

    port, running = _pick_port(preferred)
    url = f"http://localhost:{port}"
    if running:  # second double-click: just open the existing instance
        webbrowser.open(url)
        return
    server, th = _start_server(port)
    if not _wait_ready(port):
        logging.error("server did not start")
    if "--no-browser" not in sys.argv:
        webbrowser.open(url)
    try:
        _control_window(url, server, log_path)
    except Exception as exc:  # no Tk available: keep serving until the process is killed
        logging.warning("control window unavailable (%s); serving headless", exc)
        th.join()
    server.should_exit = True
    th.join(timeout=5)


if __name__ == "__main__":
    main()
