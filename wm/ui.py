"""Terminal look & feel: colours, logo, menus, prompts, spinner. Stdlib only, works on Windows."""
import itertools
import os
import shutil
import sys
import threading
import time

if os.name == "nt":
    os.system("")  # enables ANSI escape codes in Windows consoles
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

COLOR = (sys.stdout.isatty() or bool(os.environ.get("FORCE_COLOR"))) and not os.environ.get("NO_COLOR")


def _c(code):
    return (lambda s: f"\033[{code}m{s}\033[0m") if COLOR else (lambda s: str(s))


bold, dim, italic = _c("1"), _c("2"), _c("3")
red, green, yellow, blue, magenta, cyan, gray = _c("31"), _c("32"), _c("33"), _c("34"), _c("35"), _c("36"), _c("90")
bcyan, byellow = _c("1;36"), _c("1;33")

LOGO_BLADES = r"""
        \\                //
          \\            //
            \\   __   //
              \\(  )//
               (_()_)
              //(  )\\
            //  |  |  \\
          //   /|[]|\   \\
        //    / |  | \    \\
"""
LOGO_TOWER = r"""             /  |  |  \
            /   |  |   \
           /____|__|____\
"""


def logo(tagline="local AI that builds things"):
    print(bcyan(LOGO_BLADES.rstrip("\n")))
    print(byellow(LOGO_TOWER.rstrip("\n")))
    print("          " + bold("W I N D M I L L"))
    print("     " + dim(tagline))
    print()


def width():
    return min(shutil.get_terminal_size((100, 30)).columns, 120)


def rule(title=""):
    w = width()
    if title:
        print(gray("── ") + bold(title) + " " + gray("─" * max(0, w - len(title) - 4)))
    else:
        print(gray("─" * w))


def info(m):
    print(cyan("› ") + m)


def ok(m):
    print(green("✓ ") + m)


def warn(m):
    print(yellow("! ") + m)


def err(m):
    print(red("✗ ") + m)


def ask(prompt, default=None):
    d = f" {dim('[' + str(default) + ']')}" if default not in (None, "") else ""
    try:
        v = input(f"{cyan('?')} {prompt}{d} ").strip()
    except EOFError:
        v = ""
    return v or (default if default is not None else "")


def confirm(prompt, default=True):
    v = ask(prompt + (" (Y/n)" if default else " (y/N)")).lower()
    return default if not v else v.startswith("y")


def menu(title, options, allow_back=True):
    """options: list of (key, label, hint). Returns chosen key or None."""
    rule(title)
    keys = {}
    for i, (key, label, hint) in enumerate(options, 1):
        keys[str(i)] = key
        pad = max((len(l) for _, l, _ in options if "\033" not in l), default=0)
        lab = label.ljust(min(pad, 34)) if "\033" not in label else label
        print(f"  {bcyan(str(i).rjust(2))}  {lab}" + (f"  {dim(hint)}" if hint else ""))
    if allow_back:
        print(f"  {bcyan(' 0')}  {dim('back / exit')}")
    while True:
        v = ask("Choose")
        if v in ("0", "q", "exit") and allow_back:
            return None
        if v in keys:
            return keys[v]
        for k, label, _ in options:  # allow typing the name
            if v and (v.lower() == str(k).lower() or label.lower().startswith(v.lower())):
                return k
        warn("Type one of the numbers above.")


def swatch(hex_color, label=""):
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    block = f"\033[48;2;{r};{g};{b}m      \033[0m" if COLOR else "[    ]"
    return f"{block} {label or hex_color}"


class Spinner:
    """`with Spinner("thinking"):` - shows activity while the model works."""

    def __init__(self, text="thinking"):
        self.text, self._stop, self.t = text, threading.Event(), None
        self.extra = ""

    def __enter__(self):
        if not sys.stdout.isatty():
            return self
        def spin():
            start = time.time()
            for ch in itertools.cycle("◐◓◑◒"):
                if self._stop.is_set():
                    break
                sys.stdout.write(f"\r{cyan(ch)} {dim(self.text)} {dim(f'{time.time() - start:4.0f}s')} {dim(self.extra)}   ")
                sys.stdout.flush()
                time.sleep(0.15)
            sys.stdout.write("\r" + " " * (len(self.text) + len(self.extra) + 20) + "\r")
            sys.stdout.flush()
        self.t = threading.Thread(target=spin, daemon=True)
        self.t.start()
        return self

    def __exit__(self, *a):
        self._stop.set()
        if self.t:
            self.t.join(timeout=1)


def ago(ts):
    d = time.time() - ts
    for n, unit in ((86400, "d"), (3600, "h"), (60, "m")):
        if d >= n:
            return f"{int(d // n)}{unit} ago"
    return "just now"
