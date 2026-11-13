"""Native Android apps (.apk) from Rust - set up, build, sign, install, debug.

How an Android Rust app is put together (the approach Bevy documents):
  1. Rust is compiled to a shared library per CPU type with cargo-ndk -> android/app/src/main/jniLibs
  2. A small Gradle project (Java/Kotlin Activity that loads that library) packages it into an APK.
The Gradle project is copied from the Bevy release your project actually uses, so the Android-side
versions always match the Rust-side `android-activity` crate. Everything here is automatic."""
import io
import json
import os
import platform
import random
import re
import shutil
import stat
import string
import struct
import subprocess
import tarfile
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
import zlib
from pathlib import Path

from . import config, ui

AND = config.KIT / "android"
CATALOG = "https://dl.google.com/android/repository/repository2-3.xml"
REPO = "https://dl.google.com/android/repository/"
ABI_TARGET = {"arm64-v8a": "aarch64-linux-android", "armeabi-v7a": "armv7-linux-androideabi",
              "x86_64": "x86_64-linux-android", "x86": "i686-linux-android"}
EXE = ".exe" if os.name == "nt" else ""
BAT = ".bat" if os.name == "nt" else ""
TEXT_EXT = {".gradle", ".kts", ".toml", ".xml", ".java", ".kt", ".properties", ".pro", ".txt"}


# ------------------------------------------------------------------ locating the toolchain
def sdk_dir(cfg=None):
    cfg = cfg or config.load()
    home = Path.home()
    cands = [cfg.get("android_sdk"), os.environ.get("ANDROID_HOME"), os.environ.get("ANDROID_SDK_ROOT"),
             AND / "sdk", home / "AppData" / "Local" / "Android" / "Sdk", home / "Android" / "Sdk",
             home / "Library" / "Android" / "sdk"]
    for c in cands:
        if c and (Path(c) / "platform-tools").is_dir() or c and (Path(c) / "cmdline-tools").is_dir():
            return Path(c)
    return Path(cfg.get("android_sdk") or AND / "sdk")


def jdk_dir(cfg=None):
    cfg = cfg or config.load()
    for c in (cfg.get("android_jdk"), os.environ.get("JAVA_HOME")):
        if c and (Path(c) / "bin" / f"java{EXE}").exists():
            return Path(c)
    for d in sorted(AND.glob("jdk*"), reverse=True) if AND.exists() else []:
        home = d / "Contents" / "Home" if (d / "Contents" / "Home").exists() else d
        if (home / "bin" / f"java{EXE}").exists():
            return home
    return None


def java_major(jdk):
    if not jdk:
        return 0
    r = subprocess.run([str(jdk / "bin" / f"java{EXE}"), "-version"], capture_output=True, text=True)
    m = re.search(r'version "(\d+)', r.stderr + r.stdout)
    return int(m.group(1)) if m else 0


def ndk_dir(sdk):
    d = Path(sdk) / "ndk"
    vers = sorted((p for p in d.iterdir() if p.is_dir()), key=lambda p: _vkey(p.name)) if d.is_dir() else []
    return vers[-1] if vers else None


def build_tools(sdk):
    d = Path(sdk) / "build-tools"
    vers = sorted((p for p in d.iterdir() if p.is_dir() and not re.search(r"rc|beta|alpha", p.name)),
                  key=lambda p: _vkey(p.name)) if d.is_dir() else []
    return vers[-1] if vers else None


def adb(sdk=None):
    p = Path(sdk or sdk_dir()) / "platform-tools" / f"adb{EXE}"
    return str(p) if p.exists() else shutil.which("adb")


def env(cfg=None):
    sdk, jdk = sdk_dir(cfg), jdk_dir(cfg)
    e = dict(os.environ)
    e["ANDROID_HOME"] = e["ANDROID_SDK_ROOT"] = str(sdk)
    ndk = ndk_dir(sdk)
    if ndk:
        e["ANDROID_NDK_HOME"] = e["ANDROID_NDK_ROOT"] = str(ndk)
    extra = [str(sdk / "platform-tools")]
    if jdk:
        e["JAVA_HOME"] = str(jdk)
        extra.insert(0, str(jdk / "bin"))
    e["PATH"] = os.pathsep.join(extra + [e.get("PATH", "")])
    return e


def _vkey(v):
    return [int(x) if x.isdigit() else -1 for x in re.split(r"[.\-;]", v)]


def status(cfg=None):
    cfg = cfg or config.load()
    sdk, jdk = sdk_dir(cfg), jdk_dir(cfg)
    rows = [("Java (JDK)", jdk is not None, f"{jdk} (Java {java_major(jdk)})" if jdk else "missing"),
            ("Android SDK", (sdk / "platform-tools").is_dir(), str(sdk)),
            ("Android NDK", ndk_dir(sdk) is not None, str(ndk_dir(sdk) or "missing")),
            ("Build tools", build_tools(sdk) is not None, str(build_tools(sdk) or "missing")),
            ("cargo-ndk", shutil.which("cargo-ndk") is not None, "installed" if shutil.which("cargo-ndk") else "missing")]
    targets = subprocess.run(["rustup", "target", "list", "--installed"], capture_output=True, text=True).stdout \
        if shutil.which("rustup") else ""
    rows.append(("Rust Android target", "aarch64-linux-android" in targets,
                 "aarch64-linux-android" if "aarch64-linux-android" in targets else "missing"))
    return rows


# ------------------------------------------------------------------ installing the toolchain
def catalog():
    """Google's Android SDK package list -> {path: {"stable": bool, "archives": {os: url}}}."""
    req = urllib.request.Request(CATALOG, headers={"User-Agent": "windmill"})
    with urllib.request.urlopen(req, timeout=60) as r:
        root = ET.fromstring(r.read())
    out = {}
    for p in root.iter():
        if not p.tag.endswith("remotePackage"):
            continue
        ch = p.find("channelRef")
        arcs = {}
        for a in p.iter("archive"):
            arcs.setdefault(a.findtext("host-os") or "any", []).append(a.findtext("complete/url"))
        out[p.get("path")] = {"stable": (ch is not None and ch.get("ref") == "channel-0") and p.find("obsolete") is None,
                              "archives": arcs}
    return out


def latest_stable(cat, prefix):
    c = [k for k, v in cat.items() if k.startswith(prefix) and v["stable"]
         and not re.search(r"(rc|beta|alpha|preview|ext)", k.split(";", 1)[-1], re.I)]
    return sorted(c, key=_vkey)[-1] if c else None


def host_os():
    return {"Windows": "windows", "Darwin": "macosx"}.get(platform.system(), "linux")


def download(url, label):
    from .server import download as dl
    ui.info(f"Downloading {label}...")
    return dl(url)


def extract(data, dest, name_hint=""):
    dest.mkdir(parents=True, exist_ok=True)
    if name_hint.endswith((".tar.gz", ".tgz")):
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as t:
            t.extractall(dest, filter="data") if hasattr(tarfile, "data_filter") else t.extractall(dest)
    else:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for m in z.infolist():
                p = dest / m.filename
                if m.is_dir():
                    p.mkdir(parents=True, exist_ok=True)
                    continue
                p.parent.mkdir(parents=True, exist_ok=True)
                with z.open(m) as s, open(p, "wb") as o:
                    shutil.copyfileobj(s, o)
                mode = (m.external_attr >> 16) & 0o777
                if mode and os.name != "nt":
                    p.chmod(mode)


def install_jdk(major=21):
    osn = {"Windows": "windows", "Darwin": "mac"}.get(platform.system(), "linux")
    arch = "aarch64" if platform.machine().lower() in ("arm64", "aarch64") else "x64"
    api = (f"https://api.adoptium.net/v3/assets/latest/{major}/hotspot?architecture={arch}&image_type=jdk"
           f"&os={osn}&vendor=eclipse")
    with urllib.request.urlopen(urllib.request.Request(api, headers={"User-Agent": "windmill"}), timeout=60) as r:
        pkg = json.loads(r.read())[0]["binary"]["package"]
    data = download(pkg["link"], f"Java {major} (Eclipse Temurin, {pkg['size'] // 2**20} MB)")
    tmp = AND / "_jdk_tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    extract(data, tmp, pkg["name"])
    top = next(tmp.iterdir())
    dest = AND / f"jdk-{major}"
    shutil.rmtree(dest, ignore_errors=True)
    top.rename(dest)
    shutil.rmtree(tmp, ignore_errors=True)
    home = dest / "Contents" / "Home" if (dest / "Contents" / "Home").exists() else dest
    config.set_value("android_jdk", str(home))
    ui.ok(f"Java {major} installed.")
    return home


def install_cmdline_tools(sdk, cat):
    key = latest_stable(cat, "cmdline-tools;")
    urls = cat[key]["archives"].get(host_os(), [])
    if host_os() == "macosx" and len(urls) > 1:
        want = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x86_64"
        urls = [u for u in urls if want in u] or urls
    data = download(REPO + urls[0], f"Android command-line tools ({key.split(';')[1]})")
    tmp = sdk / "_clt"
    shutil.rmtree(tmp, ignore_errors=True)
    extract(data, tmp, urls[0])
    dest = sdk / "cmdline-tools" / "latest"
    shutil.rmtree(dest, ignore_errors=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    (tmp / "cmdline-tools").rename(dest)
    shutil.rmtree(tmp, ignore_errors=True)
    for f in (dest / "bin").iterdir():
        f.chmod(f.stat().st_mode | stat.S_IEXEC)


def sdkmanager(sdk, args, cfg=None):
    exe = Path(sdk) / "cmdline-tools" / "latest" / "bin" / f"sdkmanager{BAT}"
    return subprocess.run([str(exe), f"--sdk_root={sdk}", *args], input="y\n" * 60, text=True, env=env(cfg))


def setup(root=None):
    """Install everything needed to build APKs. Safe to re-run."""
    ui.rule("Android toolchain")
    if not shutil.which("cargo"):
        ui.err("Rust isn't installed. Run  windmill setup  first.")
        return False
    cfg = config.load()
    AND.mkdir(parents=True, exist_ok=True)
    need_java = required_java(Path(root) / "android") if root and (Path(root) / "android").is_dir() else 21
    jdk = jdk_dir(cfg)
    if java_major(jdk) < need_java:
        install_jdk(need_java)
    else:
        ui.ok(f"Java {java_major(jdk)}")
    cfg = config.load()
    sdk = sdk_dir(cfg)
    sdk.mkdir(parents=True, exist_ok=True)
    config.set_value("android_sdk", str(sdk))
    with ui.Spinner("reading Google's Android package list"):
        cat = catalog()
    if not (sdk / "cmdline-tools" / "latest" / "bin").is_dir():
        install_cmdline_tools(sdk, cat)
    ui.info("Accepting the Android SDK licences (required by Google to download the SDK)...")
    sdkmanager(sdk, ["--licenses"], cfg)
    compile_sdk = None
    if root and (Path(root) / "android").is_dir():
        compile_sdk = gradle_value(Path(root) / "android", "compileSdk")
    plat = f"platforms;android-{compile_sdk}" if compile_sdk else latest_stable(cat, "platforms;android-")
    pkgs = ["platform-tools", plat, latest_stable(cat, "build-tools;"), latest_stable(cat, "ndk;")]
    pkgs = [p for p in pkgs if p]
    ui.info("Installing: " + ", ".join(pkgs) + "  (a few GB the first time)")
    r = sdkmanager(sdk, ["--install", *pkgs], cfg)
    if r.returncode != 0:
        ui.err("The Android SDK install failed - see the messages above.")
        return False
    targets = ["aarch64-linux-android", "armv7-linux-androideabi", "x86_64-linux-android"]
    subprocess.run(["rustup", "target", "add", *targets])
    if not shutil.which("cargo-ndk"):
        ui.info("Installing cargo-ndk (builds Rust for Android; takes a minute)...")
        subprocess.run(["cargo", "install", "cargo-ndk", "--locked"])
    ok = all(r[1] for r in status())
    (ui.ok if ok else ui.warn)("Android toolchain ready." if ok else "Some parts are still missing - see windmill android status")
    return ok


# ------------------------------------------------------------------ project template (Gradle wrapper)
def gradle_files(android):
    return [p for p in Path(android).rglob("*") if p.is_file() and p.suffix in TEXT_EXT and ".gradle" not in p.parts[:-1]]


def gradle_value(android, key):
    for f in ("app/build.gradle.kts", "app/build.gradle"):
        p = Path(android) / f
        if p.exists():
            m = re.search(rf"\b{key}\s*=?\s*(\d+)", p.read_text(encoding="utf-8"))
            if m:
                return int(m.group(1))
    return None


def abis(android):
    for f in ("app/build.gradle.kts", "app/build.gradle"):
        p = Path(android) / f
        if p.exists():
            m = re.search(r"abiFilters[^\n]*", p.read_text(encoding="utf-8"))
            if m:
                found = re.findall(r"(arm64-v8a|armeabi-v7a|x86_64|x86)\b", m.group(0))
                if found:
                    return found
    return ["arm64-v8a"]


def required_java(android):
    android = Path(android)
    jvm = android / "gradle" / "gradle-daemon-jvm.properties"
    if jvm.exists():
        m = re.search(r"toolchainVersion=(\d+)", jvm.read_text())
        if m:
            return int(m.group(1))
    for f in ("app/build.gradle.kts", "app/build.gradle"):
        p = android / f
        if p.exists():
            m = re.search(r"JavaLanguageVersion\.of\((\d+)\)", p.read_text(encoding="utf-8"))
            if m:
                return int(m.group(1))
    props = android / "gradle" / "wrapper" / "gradle-wrapper.properties"
    if props.exists():
        m = re.search(r"gradle-(\d+)\.(\d+)", props.read_text())
        if m and (int(m.group(1)), int(m.group(2))) < (8, 5):
            return 17  # Gradle older than 8.5 can't run on Java 21
    return 21


def bevy_version(root):
    lock = Path(root) / "Cargo.lock"
    if lock.exists():
        m = re.search(r'name = "bevy"\nversion = "([^"]+)"', lock.read_text(encoding="utf-8"))
        if m:
            return m.group(1)
    toml = Path(root) / "Cargo.toml"
    if toml.exists():
        m = re.search(r'^bevy\s*=\s*(?:"|\{[^}]*version\s*=\s*")([\d.]+)', toml.read_text(encoding="utf-8"), re.M)
        if m:
            return m.group(1)
    return None


def latest_bevy_tag():
    r = subprocess.run(["git", "ls-remote", "--tags", "https://github.com/bevyengine/bevy.git"], capture_output=True, text=True)
    tags = re.findall(r"refs/tags/(v\d+\.\d+\.\d+)$", r.stdout, re.M)
    return sorted(tags, key=lambda t: _vkey(t[1:]))[-1] if tags else None


def fetch_template(tag):
    """Sparse-download examples/mobile from a Bevy release (MIT/Apache) and cache it."""
    dest = AND / "templates" / f"bevy-{tag}"
    if (dest / "examples" / "mobile").is_dir():
        return dest / "examples" / "mobile"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(dest, ignore_errors=True)
    with ui.Spinner(f"downloading the Android project template from Bevy {tag}"):
        r = subprocess.run(["git", "clone", "-q", "--depth", "1", "--branch", tag, "--filter=blob:none", "--sparse",
                            "https://github.com/bevyengine/bevy.git", str(dest)], capture_output=True, text=True)
        if r.returncode == 0:
            subprocess.run(["git", "-C", str(dest), "sparse-checkout", "set", "examples/mobile"], capture_output=True)
    m = dest / "examples" / "mobile"
    return m if m.is_dir() else None


def pick_gradle(mobile, native):
    cands = [p.parent for p in Path(mobile).rglob("settings.gradle*") if (p.parent / f"gradlew").exists()]
    def score(p):
        toml = p / "gradle" / "libs.versions.toml"
        game = toml.exists() and "games-activity" in toml.read_text(encoding="utf-8")
        is_native = "native" in p.name or not game
        return (is_native == native, len(p.parts) * -1)
    cands.sort(key=score, reverse=True)
    return cands[0] if cands else None


def create_wrapper(root, app_name, app_id, lib_name, kind="game", palette=None, tag=None):
    """Copy a Gradle Android wrapper into <root>/android and make it this app's."""
    root = Path(root)
    tag = tag or (f"v{bevy_version(root)}" if kind == "game" and bevy_version(root) else None) or latest_bevy_tag()
    if not tag:
        ui.err("Couldn't reach GitHub to get the Android template.")
        return False
    mobile = fetch_template(tag)
    src = pick_gradle(mobile, native=(kind != "game")) if mobile else None
    if not src:
        ui.err(f"Bevy {tag} has no Android Gradle template.")
        return False
    dst = root / "android"
    shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("build", ".gradle", "jniLibs", "local.properties"))
    for f in gradle_files(dst):
        t = f.read_text(encoding="utf-8", errors="replace")
        t2 = (t.replace("org.bevyengine.example", app_id).replace("bevy_mobile_example", lib_name)
              .replace("Bevy Example", app_name.replace('"', "")).replace("'../../../../assets", "'../../assets")
              .replace('"../../../../assets', '"../../assets').replace("../../../../assets", "../../assets"))
        t2 = re.sub(r'rootProject\.name\s*=\s*["\'][^"\']*["\']', f'rootProject.name = "{lib_name}"', t2)
        if t2 != t:
            f.write_text(t2, encoding="utf-8")
    for lang in ("java", "kotlin"):
        old = dst / "app" / "src" / "main" / lang / "org" / "bevyengine" / "example"
        if old.is_dir():
            new = dst / "app" / "src" / "main" / lang / Path(*app_id.split("."))
            new.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(old), str(new))
            for empty in (old.parent, old.parent.parent):
                if empty.is_dir() and not any(empty.iterdir()):
                    empty.rmdir()
    (dst / "app" / "proguard-rules.pro").touch()
    for g in ("gradlew",):
        p = dst / g
        if p.exists():
            p.chmod(p.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    make_icons(dst / "app" / "src" / "main" / "res", palette or {}, app_name)
    (root / "assets").mkdir(exist_ok=True)
    meta = {"app_id": app_id, "lib": lib_name, "name": app_name, "kind": kind, "template": f"bevy {tag}"}
    (root / ".windmill").mkdir(exist_ok=True)
    (root / ".windmill" / "android.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    gi = root / ".gitignore"
    extra = "\nandroid/.gradle/\nandroid/build/\nandroid/app/build/\nandroid/app/src/main/jniLibs/\nandroid/local.properties\ndist/\n"
    gi.write_text((gi.read_text(encoding="utf-8") if gi.exists() else "") + extra, encoding="utf-8")
    ui.ok(f"Android project created (template: Bevy {tag}, {'GameActivity' if kind == 'game' else 'NativeActivity'}).")
    return True


# ------------------------------------------------------------------ launcher icons (PNG, no dependencies)
def _png(w, h, pixels):
    raw = b"".join(b"\x00" + bytes(c for px in row for c in px) for row in pixels)
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def _hex(c, default):
    c = (c or default).lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def make_icons(res, palette, name):
    """Rounded-square launcher icon in the project's brand colours with a simple geometric mark."""
    bg = _hex(palette.get("primary"), "#3b6ef5")
    fg = _hex(palette.get("on-primary"), "#ffffff")
    ac = _hex(palette.get("accent"), "#ffb020")
    seed = sum(map(ord, name))
    def colour_at(px, py, s):
        rad = s * 0.22
        dx, dy = max(rad - px, 0, px - (s - rad)), max(rad - py, 0, py - (s - rad))
        if dx * dx + dy * dy > rad * rad:
            return None
        cx, cy = px - s / 2, py - s / 2
        if (cx - s * 0.18 * (1 if seed % 2 else -1)) ** 2 + (cy + s * 0.18) ** 2 < (s * 0.09) ** 2:
            return ac
        if (s * 0.18) ** 2 < cx * cx + cy * cy < (s * 0.30) ** 2:
            return fg
        return bg

    for folder, s in (("mipmap-mdpi", 48), ("mipmap-hdpi", 72), ("mipmap-xhdpi", 96), ("mipmap-xxhdpi", 144),
                      ("mipmap-xxxhdpi", 192)):
        rows, n = [], 4  # 4x4 supersampling = smooth edges
        for y in range(s):
            row = []
            for x in range(s):
                acc, hits = [0, 0, 0], 0
                for sy in range(n):
                    for sx in range(n):
                        c = colour_at(x + (sx + 0.5) / n, y + (sy + 0.5) / n, s)
                        if c:
                            hits += 1
                            acc = [a + b for a, b in zip(acc, c)]
                row.append((0, 0, 0, 0) if not hits else (*(round(a / hits) for a in acc), round(255 * hits / n / n)))
            rows.append(row)
        d = Path(res) / folder
        d.mkdir(parents=True, exist_ok=True)
        (d / "ic_launcher.png").write_bytes(_png(s, s, rows))


# ------------------------------------------------------------------ build / sign / install / debug
def meta(root):
    f = Path(root) / ".windmill" / "android.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


def is_android(root):
    return (Path(root) / "android" / "app").is_dir() and (Path(root) / "Cargo.toml").exists()


def run(cmd, cwd, cfg=None, timeout=3600):
    ui.info(" ".join(str(c) for c in cmd)[:160])
    return subprocess.run([str(c) for c in cmd], cwd=cwd, env=env(cfg), timeout=timeout)


def keystore(app_id, cfg=None):
    """Release signing key per app, created once. Keep it: Android only accepts updates signed with it."""
    kdir = config.HOME / "keys"
    kdir.mkdir(parents=True, exist_ok=True)
    ks, info = kdir / f"{app_id}.jks", kdir / f"{app_id}.json"
    if ks.exists() and info.exists():
        return ks, json.loads(info.read_text())
    pw = "".join(random.SystemRandom().choice(string.ascii_letters + string.digits) for _ in range(24))
    jdk = jdk_dir(cfg)
    keytool = jdk / "bin" / f"keytool{EXE}"
    r = subprocess.run([str(keytool), "-genkeypair", "-keystore", str(ks), "-alias", "release", "-keyalg", "RSA",
                        "-keysize", "4096", "-validity", "10000", "-storepass", pw, "-keypass", pw,
                        "-dname", f"CN={app_id}, O=Windmill"], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("keytool failed: " + r.stderr[:300])
    data = {"alias": "release", "password": pw}
    info.write_text(json.dumps(data), encoding="utf-8")
    ui.warn(f"Created your app's signing key: {ks}\n  BACK IT UP (with {info.name}). Updates to your app must be signed with it.")
    return ks, data


def build_apk(root=None, release=True):
    root = Path(root or config.project_root())
    cfg = config.load()
    m = meta(root)
    if not m or not is_android(root):
        ui.err("This isn't a Windmill Android project. Make one with  windmill new  (choose an Android app/game),\n"
               "  or add Android to this Rust project with  windmill android init")
        return None
    missing = [r[0] for r in status(cfg) if not r[1]]
    if missing:
        ui.warn("Missing: " + ", ".join(missing) + " - installing the Android toolchain first.")
        if not setup(root):
            return None
    android = root / "android"
    min_sdk = gradle_value(android, "minSdk") or 26
    targets = abis(android)
    cmd = ["cargo", "ndk"]
    for a in targets:
        cmd += ["-t", a]
    cmd += ["-P", str(min_sdk), "-o", str(android / "app" / "src" / "main" / "jniLibs"), "build"]
    if release:
        cmd.append("--release")
    if run(cmd, root, cfg).returncode != 0:
        ui.err("The Rust part didn't compile for Android. Run  windmill debug  and paste the error, or ask in chat.")
        return None
    gradlew = android / f"gradlew{BAT}"
    task = "assembleRelease" if release else "assembleDebug"
    if run([gradlew, task, "--console=plain"], android, cfg).returncode != 0:
        ui.err("Packaging failed (Gradle). The messages above say why; `windmill debug` can fix it.")
        return None
    outdir = android / "app" / "build" / "outputs" / "apk" / ("release" if release else "debug")
    apks = sorted(outdir.glob("*.apk"), key=lambda p: p.stat().st_mtime)
    if not apks:
        ui.err(f"No APK found in {outdir}")
        return None
    apk = apks[-1]
    dist = root / "dist"
    dist.mkdir(exist_ok=True)
    ver = re.search(r'versionName\s*=?\s*["\']([^"\']+)', (android / "app" / ("build.gradle.kts" if (android / "app" / "build.gradle.kts").exists() else "build.gradle")).read_text(encoding="utf-8"))
    out = dist / f"{m['lib']}-{ver.group(1) if ver else '1.0'}{'' if release else '-debug'}.apk"
    if release:
        bt = build_tools(sdk_dir(cfg))
        ks, k = keystore(m["app_id"], cfg)
        aligned = dist / "_aligned.apk"
        if run([bt / f"zipalign{EXE}", "-f", "-p", "4", apk, aligned], root, cfg).returncode != 0:
            return None
        signer = bt / f"apksigner{BAT}"
        if run([signer, "sign", "--ks", ks, "--ks-key-alias", k["alias"], "--ks-pass", f"pass:{k['password']}",
                "--key-pass", f"pass:{k['password']}", "--out", out, aligned], root, cfg).returncode != 0:
            return None
        aligned.unlink(missing_ok=True)
        Path(str(out) + ".idsig").unlink(missing_ok=True)
    else:
        shutil.copy(apk, out)
    ui.ok(f"APK ready: {ui.bold(str(out))}  ({out.stat().st_size / 2**20:.1f} MB)")
    print(ui.dim("  Put it on a phone: plug it in (USB debugging on) and run  windmill android install,\n"
                 "  or send the file to the phone and open it (allow 'install unknown apps')."))
    return out


def devices(cfg=None):
    a = adb(sdk_dir(cfg))
    if not a:
        return []
    r = subprocess.run([a, "devices"], capture_output=True, text=True, env=env(cfg))
    return [l.split()[0] for l in r.stdout.splitlines()[1:] if l.strip().endswith("device")]


def install(root=None, release=True):
    root = Path(root or config.project_root())
    cfg = config.load()
    devs = devices(cfg)
    if not devs:
        ui.warn("No phone found. On the phone: Settings > About phone > tap 'Build number' 7 times, then\n"
                "  Settings > Developer options > turn on 'USB debugging'. Plug it in and accept the prompt.")
        return False
    apks = sorted((root / "dist").glob("*.apk"), key=lambda p: p.stat().st_mtime) if (root / "dist").exists() else []
    apk = apks[-1] if apks else build_apk(root, release)
    if not apk:
        return False
    a = adb(sdk_dir(cfg))
    if run([a, "install", "-r", apk], root, cfg).returncode != 0:
        ui.warn("Install failed. If an older version signed differently is on the phone, uninstall it first.")
        return False
    m = meta(root)
    subprocess.run([a, "logcat", "-c"], env=env(cfg))
    subprocess.run([a, "shell", "monkey", "-p", m["app_id"], "-c", "android.intent.category.LAUNCHER", "1"],
                   capture_output=True, env=env(cfg))
    ui.ok(f"Installed and launched on {devs[0]}.  Crash or weird behaviour?  windmill android logs")
    return True


LOG_TAGS = re.compile(r"(RustStdoutStderr|AndroidRuntime|FATAL|DEBUG\s*:|libc|bevy|wgpu|winit|egui|eframe|"
                      r"android_activity|GameActivity|NativeActivity|panicked|tombstone|SIGSEGV|SIGABRT|"
                      r"UnsatisfiedLinkError|dlopen)", re.I)


def logs(root=None, lines=200):
    """Recent, relevant log lines from the connected phone (crashes, panics, Rust output)."""
    cfg = config.load()
    a = adb(sdk_dir(cfg))
    if not a or not devices(cfg):
        return "No phone connected (USB debugging must be on)."
    r = subprocess.run([a, "logcat", "-d", "-v", "brief", "-t", "4000"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env(cfg))
    keep = [l for l in r.stdout.splitlines() if LOG_TAGS.search(l)]
    return "\n".join(keep[-lines:]) or "No relevant log lines (the app may not have run yet)."


def check(root):
    """Compile-check the Android build of the Rust code (catches Android-only errors). Returns (ok, out)."""
    cfg = config.load()
    if not shutil.which("cargo-ndk") or not ndk_dir(sdk_dir(cfg)):
        return True, "(android check skipped: toolchain not installed - windmill android setup)"
    android = Path(root) / "android"
    min_sdk = gradle_value(android, "minSdk") or 26
    r = subprocess.run(["cargo", "ndk", "-t", "arm64-v8a", "-P", str(min_sdk), "clippy", "--lib", "--", "-D", "warnings"],
                       cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env(cfg))
    return r.returncode == 0, (r.stdout + r.stderr)[-6000:]
