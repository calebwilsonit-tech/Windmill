"""Use Windmill from your phone: the phone app is a window onto Windmill running on this computer (the AI stays here).

Off until you turn it on (desktop app > Phone). Then:
- a second, separate entry point listens on your network (port 8765) and accepts ONLY paired phones,
- phones find this computer on the Wi-Fi by themselves (a small reply on UDP port 8766),
- pairing is like a TV: type the 6-digit code shown on the computer; each phone then gets its own long key
  (only a fingerprint of it is stored here) that you can remove any time,
- outside home, use Tailscale (a free, encrypted private network between your own devices)."""
import hashlib
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

from . import config

PORT = 8765
DISCOVERY_PORT = 8766
CODE_TTL = 600
MAX_TRIES = 8
APK = config.KIT / "bin" / "WindmillPhone.apk"
_state = {"httpd": None, "udp": None, "code": None, "code_at": 0, "tries": 0}
_lock = threading.Lock()


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def devices():
    return config.load().get("phone_devices") or {}


def check_token(token):
    """The paired phone's id for a key, or None."""
    if not token:
        return None
    h = _hash(token)
    for did, d in devices().items():
        if secrets.compare_digest(d.get("hash", ""), h):
            return did
    return None


def touch(did):
    devs = devices()
    if did in devs and time.time() - devs[did].get("seen", 0) > 60:
        devs[did]["seen"] = time.time()
        config.set_value("phone_devices", devs)


def remove(did):
    devs = devices()
    devs.pop(did, None)
    config.set_value("phone_devices", devs)


def code(new=False):
    """The current 6-digit pairing code (a new one every 10 minutes, or after too many wrong tries)."""
    with _lock:
        if new or not _state["code"] or time.time() - _state["code_at"] > CODE_TTL or _state["tries"] >= MAX_TRIES:
            _state.update(code=f"{secrets.randbelow(10**6):06d}", code_at=time.time(), tries=0)
        return _state["code"], int(CODE_TTL - (time.time() - _state["code_at"]))


def pair(entered, name="Phone"):
    """Exchange the on-screen code for a long-lived key. Returns the key or None."""
    with _lock:
        cur = _state["code"]
        if not cur or time.time() - _state["code_at"] > CODE_TTL or _state["tries"] >= MAX_TRIES:
            return None
        _state["tries"] += 1
        if not secrets.compare_digest(str(entered or "").strip(), cur):
            return None
        _state.update(code=None, tries=0)  # one code pairs one phone
    token = secrets.token_urlsafe(32)
    devs = devices()
    did = secrets.token_hex(4)
    devs[did] = {"name": re.sub(r"[^\w .'-]", "", str(name))[:40] or "Phone", "hash": _hash(token),
                 "added": time.time(), "seen": time.time()}
    config.set_value("phone_devices", devs)
    return token


# ------------------------------------------------------------------ where the phone can reach this computer
def _hidden(cmd, timeout=5):
    kw = {"creationflags": 0x08000000} if os.name == "nt" else {}
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kw)
        return r.stdout if r.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def addresses():
    """[{kind, host}] - this computer's Wi-Fi/LAN address(es) and its Tailscale address, if any."""
    out, seen = [], set()
    try:  # the address used to reach the internet = the one on the home network
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.0.2.1", 9))  # nothing is sent
        ip = s.getsockname()[0]
        s.close()
        if not ip.startswith("127."):
            out.append({"kind": "wifi", "host": ip})
            seen.add(ip)
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in seen and not ip.startswith(("127.", "169.254.")) and not ip.startswith("100."):
                out.append({"kind": "wifi", "host": ip})
                seen.add(ip)
    except OSError:
        pass
    ts = shutil.which("tailscale") or (r"C:\Program Files\Tailscale\tailscale.exe" if os.name == "nt" and
                                       Path(r"C:\Program Files\Tailscale\tailscale.exe").exists() else None)
    if ts:
        ip = _hidden([ts, "ip", "-4"]).strip().splitlines()
        if ip:
            out.append({"kind": "tailscale", "host": ip[0]})
        try:
            dns = json.loads(_hidden([ts, "status", "--json"]) or "{}").get("Self", {}).get("DNSName", "").rstrip(".")
            if dns:
                out.append({"kind": "tailscale", "host": dns})
        except ValueError:
            pass
    return out


# ------------------------------------------------------------------ the phone's entry point + Wi-Fi discovery
def _udp_responder(sock):
    reply = json.dumps({"windmill": True, "name": socket.gethostname()[:40], "port": PORT}).encode()
    while _state.get("udp") is sock:
        try:
            data, addr = sock.recvfrom(256)
        except OSError:
            break
        if data.strip() == b"WINDMILL?":
            try:
                sock.sendto(reply, addr)
            except OSError:
                pass


def start(handler_cls):
    """Open the phone entry point (all network interfaces, paired phones only) + discovery. Returns error or None."""
    from http.server import ThreadingHTTPServer
    if _state["httpd"]:
        return None
    try:
        httpd = ThreadingHTTPServer(("0.0.0.0", PORT), handler_cls)
    except OSError as e:
        return f"port {PORT} is in use ({e})"
    httpd.phone, httpd.token, httpd.daemon_threads = True, None, True
    _state["httpd"] = httpd
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        u.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        u.bind(("0.0.0.0", DISCOVERY_PORT))
        _state["udp"] = u
        threading.Thread(target=_udp_responder, args=(u,), daemon=True).start()
    except OSError:
        pass  # discovery is a convenience - typing the address still works
    return None


def stop():
    h, u = _state.get("httpd"), _state.get("udp")
    _state.update(httpd=None, udp=None)
    if h:
        try:
            h.shutdown()
            h.server_close()
        except OSError:
            pass
    if u:
        try:
            u.close()
        except OSError:
            pass


def running():
    return bool(_state.get("httpd"))


# ------------------------------------------------------------------ the phone app's install file
def apk_path():
    """WindmillPhone.apk for this version (downloaded once from the release GitHub built), or None."""
    if APK.exists() and APK.with_suffix(".version").exists() and \
            APK.with_suffix(".version").read_text().strip() == _version():
        return APK
    import urllib.request
    from . import update
    src, _ = update.source()
    m = re.search(r"github\.com[/:]([^/]+)/([^/.]+)", src or "")
    if not m:
        return APK if APK.exists() else None
    for tag in (f"v{_version()}", None):
        url = (f"https://github.com/{m.group(1)}/{m.group(2)}/releases/download/{tag}/WindmillPhone.apk" if tag else
               f"https://github.com/{m.group(1)}/{m.group(2)}/releases/latest/download/WindmillPhone.apk")
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "windmill"}), timeout=60) as r:
                data = r.read()
            if data[:2] == b"PK":  # an .apk is a zip
                APK.parent.mkdir(parents=True, exist_ok=True)
                APK.write_bytes(data)
                APK.with_suffix(".version").write_text(_version())
                try:  # the app's own version number (goes up only when the phone app's code changes)
                    with urllib.request.urlopen(urllib.request.Request(url.replace("WindmillPhone.apk", "WindmillPhone.code"),
                                                                       headers={"User-Agent": "windmill"}), timeout=30) as r:
                        APK.with_suffix(".code").write_text(str(int(r.read().decode().strip())))
                except Exception:  # noqa: BLE001
                    pass
                return APK
        except Exception:  # noqa: BLE001
            continue
    return APK if APK.exists() else None


def app_code():
    """Version number of the phone app this computer can hand out (0 = none yet)."""
    f = APK.with_suffix(".code")
    try:
        return int(f.read_text().strip()) if APK.exists() and f.exists() else 0
    except ValueError:
        return 0


def _version():
    from . import version
    return version.current()
