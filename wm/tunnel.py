"""Reach Windmill from anywhere, with nothing else to install: Tailscale built in.

Runs the small windmill-tunnel helper (built from tunnel/ with Tailscale's tsnet library, downloaded from the release
the first time). It joins your Tailscale network as its own device and publishes Windmill's phone entry point at a
permanent HTTPS address (Tailscale Funnel). The phone app uses that address whenever it isn't on your home Wi-Fi.

Safety: requests that come through it carry "X-Windmill-Via: tunnel" (set by the helper, can't be faked from outside).
Over that address Windmill only accepts phones that were already paired; pairing itself only works on the home network."""
import json
import os
import platform
import re
import signal
import socket
import subprocess
import threading
import time
import urllib.request

from . import config

BIN = config.KIT / "bin"
STATE_DIR = config.HOME / "tailscale"
PIDFILE = config.HOME / "tunnel.pid"
LOG = config.LOGS / "tunnel.log"
_s = {"proc": None, "state": "off", "url": "", "message": "", "step": "", "interactive": False, "opened": set()}


def _open_in_browser(url):
    """Open Tailscale's sign-in / approval page for the user (only for a setup they just started)."""
    if not url or not _s.get("interactive") or url in _s["opened"]:
        return
    _s["opened"].add(url)
    try:
        if os.name == "nt":
            os.startfile(url)  # noqa: S606 - the user's default browser
        else:
            import webbrowser
            webbrowser.open(url)
    except Exception:  # noqa: BLE001
        pass


def _asset():
    osn, arch = platform.system().lower(), platform.machine().lower()
    arch = "arm64" if arch in ("arm64", "aarch64") else "amd64"
    if osn == "windows":
        return f"WindmillTunnel-windows-{arch}.exe"
    return f"WindmillTunnel-{'darwin' if osn == 'darwin' else 'linux'}-{arch}"


def helper():
    """The helper for this computer and Windmill version (downloaded once from the release), or None."""
    from . import update, version
    exe = BIN / ("windmill-tunnel.exe" if os.name == "nt" else "windmill-tunnel")
    ver = exe.with_suffix(".version") if os.name == "nt" else exe.parent / "windmill-tunnel.version"
    if exe.exists() and ver.exists() and ver.read_text().strip() == version.current():
        return exe
    src, _ = update.source()
    m = re.search(r"github\.com[/:]([^/]+)/([^/.]+)", src or "")
    if m:
        for tag in (f"v{version.current()}", None):
            url = (f"https://github.com/{m.group(1)}/{m.group(2)}/releases/download/{tag}/{_asset()}" if tag else
                   f"https://github.com/{m.group(1)}/{m.group(2)}/releases/latest/download/{_asset()}")
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "windmill"}), timeout=180) as r:
                    data = r.read()
                if data[:2] in (b"MZ", b"\x7fE") or data[:4] in (b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe"):
                    BIN.mkdir(parents=True, exist_ok=True)
                    tmp = exe.with_name(exe.name + ".new")
                    tmp.write_bytes(data)
                    tmp.chmod(0o755)
                    os.replace(tmp, exe)
                    ver.write_text(version.current())
                    return exe
            except Exception:  # noqa: BLE001
                continue
    return exe if exe.exists() else None


def _kill_old():
    try:
        pid = int(PIDFILE.read_text().strip())
    except (OSError, ValueError):
        return
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, creationflags=0x08000000)
        else:
            os.kill(pid, signal.SIGTERM)
    except OSError:
        pass


def _hostname():
    h = re.sub(r"[^a-z0-9-]+", "-", socket.gethostname().lower()).strip("-")[:40] or "pc"
    return f"windmill-{h}"


def _read(proc):
    for raw in iter(proc.stdout.readline, b""):
        try:
            ev = json.loads(raw.decode("utf-8", errors="replace"))
        except ValueError:
            continue
        _s.update(state=ev.get("state", _s["state"]), url=ev.get("url", ""), message=ev.get("message", ""),
                  step=ev.get("step", ""))
        if ev.get("state") in ("login", "setup"):
            _open_in_browser(ev.get("url", ""))
        if ev.get("state") == "ready":
            config.set_value("tunnel_url", ev.get("url", ""))
            _s["interactive"] = False
    code = proc.wait()
    if _s.get("proc") is proc:
        _s.update(proc=None, state="error" if code else "off",
                  message=_s.get("message") or (f"the connection stopped (code {code})" if code else ""))


def start(port=8765, interactive=False):
    """Start (or keep) the connection. Returns None, or a reason it can't start.
    interactive: the user just clicked Turn on - open sign-in / approval pages in their browser by themselves."""
    _s["interactive"] = interactive or _s.get("interactive", False)
    if _s.get("proc") and _s["proc"].poll() is None:
        return None
    _s.update(state="downloading", url="", message="")
    exe = helper()
    if not exe:
        _s.update(state="error", message="Couldn't download Windmill's Tailscale helper - check the internet connection.")
        return _s["message"]
    _kill_old()
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    config.LOGS.mkdir(parents=True, exist_ok=True)
    kw = {"creationflags": 0x08000000 | 0x00000200} if os.name == "nt" else {"start_new_session": True}
    proc = subprocess.Popen([str(exe), "--state", str(STATE_DIR), "--hostname", _hostname(), "--target", f"127.0.0.1:{port}"],
                            stdout=subprocess.PIPE, stderr=open(LOG, "a", encoding="utf-8"), stdin=subprocess.DEVNULL, **kw)
    PIDFILE.write_text(str(proc.pid))
    _s.update(proc=proc, state="starting")
    threading.Thread(target=_read, args=(proc,), daemon=True).start()
    return None


def stop():
    p = _s.get("proc")
    _s.update(proc=None, state="off", url="", message="")
    if p and p.poll() is None:
        try:
            p.terminate()
            p.wait(5)
        except Exception:  # noqa: BLE001
            p.kill()
    try:
        PIDFILE.unlink()
    except OSError:
        pass


def status():
    url = _s["url"] if _s["state"] == "ready" else ""
    return {"state": _s["state"], "url": _s["url"], "message": _s["message"], "step": _s.get("step", ""), "public": url or ""}


def public_url():
    return _s["url"] if _s["state"] == "ready" else ""


def wait_ready(timeout=0):
    end = time.time() + timeout
    while time.time() < end and _s["state"] not in ("ready", "error", "login"):
        time.sleep(0.5)
    return status()
