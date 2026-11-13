"""The watchdog that makes the model call itself out:
  - scans every edit for sloppy/unfinished code the moment it's written
  - runs the real checks when the model thinks it's done; feeds failures back
  - escalates when the same error keeps coming back: fix -> step back & diagnose -> roll back to
    the last working checkpoint and try a different approach -> stop and explain to the user
  - commits a checkpoint every time checks pass (instant, safe rollback point)
  - final fresh-eyes critic review (with a design-director checklist for UI work)"""
import json
import os
import re
import subprocess
from collections import Counter
from pathlib import Path

from . import checks, design

TAG = "[WINDMILL DEBUGGER]"

STEP_BACK = f"""{TAG} The same problem has now failed {{n}} times. Stop editing and DIAGNOSE first:
1. Re-read the full error below and the exact code it points to (read_file / find_code).
2. Write down 3 different hypotheses for the root cause (not the symptom).
3. For the dependency/API involved, call references(...) - your memory of the API may be wrong for this version.
4. Pick the most likely hypothesis, say why, then make ONE focused change and run check.
Error:
{{out}}"""

ROLLBACK = f"""{TAG} This approach is stuck ({{n}} failed attempts at the same error). I have ROLLED BACK the code to
the last version where all checks passed ({{where}}). Your previous approach did not work - do NOT repeat it.
What was tried and failed:
{{lessons}}
Take a genuinely different approach (simpler design, different API, smaller steps). Check after each change."""

SLOP_RS = [(r"\b(todo|unimplemented)!\s*\(", "UNFINISHED", "implement it fully"),
           (r"\bdbg!\s*\(", "DEBUG", "remove dbg!"),
           (r"#\[\s*ignore", "TEST-SKIP", "don't skip tests")]


def signature(out):
    """Normalise an error so 'the same error' is recognised across attempts."""
    for line in out.splitlines():
        l = line.strip()
        if re.search(r"(error|Error|FAIL|failed|panicked|Traceback|SLOP|UNFINISHED|PLACEHOLDER)", l):
            l = re.sub(r"\d+", "N", l)
            l = re.sub(r"'[^']*'|\"[^\"]*\"|`[^`]*`", "Q", l)
            return l[:120]
    return re.sub(r"\d+", "N", out.strip()[:120])


class Watchdog:
    def __init__(self, root, llm, tools, interactive=True, log=None):
        self.root = Path(root)
        self.llm, self.tools, self.interactive = llm, tools, interactive
        self.log = log or (lambda m: None)
        self.fails = Counter()
        self.total_fails = 0
        self.calls = []
        self.edit_misses = Counter()
        self.lessons = []
        self.git = (self.root / ".git").exists()
        self.base = self._head()
        self.green = self.base
        if self.base:
            os.environ["AI_KIT_BASE"] = self.base  # slop guard checks only code added in this task

    # ---------------------------------------------------------- git helpers
    def _git(self, *a):
        return subprocess.run(["git", *a], cwd=self.root, capture_output=True, text=True,
                              encoding="utf-8", errors="replace")

    def _head(self):
        if not (self.root / ".git").exists():
            return None
        r = self._git("rev-parse", "HEAD")
        return r.stdout.strip() if r.returncode == 0 else None

    def checkpoint(self, msg="windmill: checkpoint - all checks pass"):
        if not self.git or not self._git("status", "--porcelain").stdout.strip():
            return
        self._git("add", "-A")
        self._git("-c", "user.name=Windmill", "-c", "user.email=windmill@localhost", "commit", "-q", "-m", msg)
        self.green = self._head()

    def rollback(self):
        if not self.git or not self.green:
            return "no checkpoint available"
        self._git("checkout", self.green, "--", ".")
        untracked = set(self._git("ls-files", "--others", "--exclude-standard").stdout.split())
        for f in self.tools.changed:
            if f in untracked:
                try:
                    (self.root / f).unlink()
                except OSError:
                    pass
        return f"checkpoint {self.green[:8]}"

    # ---------------------------------------------------------- per tool call
    def after_tool(self, name, args, result):
        key = (name, json.dumps(args, sort_keys=True)[:2000])
        self.calls.append(key)
        if self.calls[-3:].count(key) == 3:
            return (f"{TAG} You've made the exact same {name} call 3 times in a row. It isn't working - "
                    f"change your approach (read the error, re-read the file, or try another method).")
        if name == "edit_file" and result.startswith("ERROR: old_text not found"):
            p = args.get("path", "")
            self.edit_misses[p] += 1
            if self.edit_misses[p] >= 3:
                return (f"{TAG} Your edits to {p} keep missing. Read the whole file again with read_file and copy "
                        f"old_text exactly, or rewrite the file completely with write_file.")
        if name in ("write_file", "edit_file") and not result.startswith("ERROR"):
            text = args.get("content") or args.get("new_text") or ""
            path = args.get("path", "")
            probs = checks.slop_lines(path, text)
            if path.endswith(".rs"):
                for n, line in enumerate(text.splitlines(), 1):
                    for pat, tag, fix in SLOP_RS:
                        if re.search(pat, line.split("//")[0]) and "#[cfg(test)]" not in text[:text.find(line)][-400:]:
                            probs.append((n, tag, line.strip()[:100], fix))
                    if re.search(r"//\s*(TODO|FIXME)|//\s*\.\.\.|\b(for now|rest of the (code|logic)|placeholder)\b", line, re.I):
                        probs.append((n, "PLACEHOLDER", line.strip()[:100], "finish the code instead of leaving a note"))
            if probs:
                lst = "\n".join(f"  line ~{n}: {tag}: {txt}  -> {fix}" for n, tag, txt, fix in probs[:12])
                return (f"{TAG} You just wrote unfinished/sloppy code in {path}:\n{lst}\n"
                        f"Call yourself out: fix these now, completely, before doing anything else.")
        return None

    # ---------------------------------------------------------- when the model says it's done
    def gate(self):
        """Returns (ok, message_for_model, give_up)."""
        ok, out = checks.run(self.root)
        self.last_raw = out
        if ok:
            self.checkpoint()
            self.fails.clear()
            return True, out, False
        sig = signature(out)
        self.fails[sig] += 1
        self.total_fails += 1
        n = self.fails[sig]
        self.log(f"check failed ({n}x this error, {self.total_fails} total)")
        if n >= 7 or self.total_fails >= 14:
            return False, out, True
        if n == 5:
            self.lessons.append(f"- attempts around: {sig}")
            where = self.rollback()
            self.fails[sig] = 5  # keep counting toward give-up
            return False, ROLLBACK.format(n=n, where=where, lessons="\n".join(self.lessons[-5:])), False
        if n >= 3:
            return False, STEP_BACK.format(n=n, out=out), False
        return False, (f"{TAG} Checks FAILED - you are not done. Fix the cause (not the symptom), then run check:\n{out}"), False

    # ---------------------------------------------------------- fresh-eyes review
    def critic(self, task, ui=False):
        diff = self._git("diff", self.base or "HEAD", "--", ".", ":(exclude)Cargo.lock", ":(exclude)*.lock",
                         ":(exclude).ai", ":(exclude)package-lock.json").stdout if self.git else ""
        if not diff.strip():
            return True, ""
        diff = diff[:60000]
        sys_prompt = ("You are a strict senior reviewer. You did not write this code. Find real problems: the task "
                      "not fully done, bugs, unhandled errors/edge cases, missing tests, placeholder content, "
                      "inconsistency with the existing code. Ignore nitpicks.\nRespond:\n## Problems\n- file:line - "
                      "problem - fix\n(or 'None')\nFinal line exactly: VERDICT: PASS or VERDICT: FAIL")
        user = f"TASK:\n{task}\n\nDIFF:\n```diff\n{diff}\n```"
        if ui:
            brief = design.load(self.root) or "(no brief)"
            sys_prompt = design.UI_CRITIC + "\n\nAlso check correctness as a senior engineer."
            user = f"DESIGN BRIEF:\n{brief}\n\n{design.UI_RULES}\n\n" + user
        review = self.llm.simple(sys_prompt, user, temperature=0.3)
        v = re.findall(r"VERDICT:\s*(PASS|FAIL)", review, re.I)
        passed = bool(v) and v[-1].upper() == "PASS"
        return passed, review

    def plain_summary(self, out):
        """For the user when we give up - no jargon."""
        first = next((l.strip() for l in out.splitlines() if l.strip() and not l.rstrip().endswith(("FAILED:", "FAILED")) and "WINDMILL" not in l), "")
        return ("I couldn't get this working after several different approaches, so I stopped instead of "
                "making a mess. Your project is back at the last working version.\n"
                f"The problem it kept hitting: {first[:200]}\n"
                "You can: try again with more detail about what you want, ask me to try a simpler version, "
                "or run  windmill best \"<task>\"  to have it attempt several independent solutions.")
