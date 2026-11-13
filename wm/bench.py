"""`windmill benchmark`: measure how good the AI really is, on your PC, with numbers.

Each task (bench/tasks/*) has a spec, a stub, a few visible example tests, and HIDDEN tests the model
never sees - scoring uses only the hidden ones, so neither mode can game them.

Modes
  plain     the model answers once in a single chat turn (how you'd use a chatbot): no tools, no retries
  windmill  the full Windmill agent: tools, checks, debugger, fresh-eyes review
  api-plain / api-windmill   the same, but through any OpenAI-compatible API (OpenAI, Gemini, Anthropic,
            OpenRouter...) for a side-by-side with a frontier model.
Results are saved in ~/.windmill/benchmarks/ and shown next to each model in `windmill models`."""
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from . import config, server, ui
from .llm import LLM, LLMError

TASKS = config.KIT / "bench" / "tasks"
RESULTS = config.HOME / "benchmarks"
CARGO_TOML = '[package]\nname = "task"\nversion = "0.1.0"\nedition = "2021"\n\n[dependencies]\n'
PLAIN_SYSTEM = ("You are an expert Rust programmer. Implement the task completely and correctly. Reply with the "
                "complete contents of src/lib.rs in a single ```rust code block and nothing else.")


def tasks(only=None):
    out = sorted(p for p in TASKS.iterdir() if p.is_dir() and (p / "hidden.rs").exists())
    if only:
        want = [o.strip() for o in only.split(",") if o.strip()]
        out = [p for p in out if any(p.name.startswith(w) or w in p.name for w in want)]
    return out


def title(t):
    return (t / "TASK.md").read_text(encoding="utf-8").splitlines()[0].lstrip("# ").strip()


def make_crate(dst, t, lib_src):
    dst.mkdir(parents=True, exist_ok=True)
    (dst / "Cargo.toml").write_text(CARGO_TOML, encoding="utf-8")
    (dst / "src").mkdir(exist_ok=True)
    (dst / "src" / "lib.rs").write_text(lib_src, encoding="utf-8")
    (dst / "tests").mkdir(exist_ok=True)
    shutil.copy(t / "visible.rs", dst / "tests" / "visible.rs")
    shutil.copy(t / "TASK.md", dst / "TASK.md")
    (dst / ".gitignore").write_text("target/\n", encoding="utf-8")
    env = {**os.environ, "GIT_AUTHOR_NAME": "bench", "GIT_AUTHOR_EMAIL": "b@x", "GIT_COMMITTER_NAME": "bench",
           "GIT_COMMITTER_EMAIL": "b@x"}
    for c in (["git", "init", "-q"], ["git", "add", "-A"], ["git", "commit", "-q", "-m", "task"]):
        subprocess.run(c, cwd=dst, capture_output=True, env=env)


def score(dst, t, target_dir):
    """Run the hidden (+visible) tests. Returns dict(passed_all, passed, total)."""
    shutil.copy(t / "hidden.rs", dst / "tests" / "hidden.rs")
    total = (t / "hidden.rs").read_text(encoding="utf-8").count("#[test]")
    env = {**os.environ, "CARGO_TARGET_DIR": str(target_dir), "CARGO_TERM_COLOR": "never", "RUST_BACKTRACE": "0"}
    try:
        r = subprocess.run(["cargo", "test", "-q", "--test", "hidden"], cwd=dst, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", env=env, timeout=600)
        out = r.stdout + r.stderr
    except subprocess.TimeoutExpired:
        return {"passed_all": False, "passed": 0, "total": total, "note": "tests timed out"}
    m = re.findall(r"test result: \w+\. (\d+) passed; (\d+) failed", out)
    passed = sum(int(a) for a, _ in m)
    note = "" if m else "did not compile"
    return {"passed_all": r.returncode == 0 and passed == total, "passed": passed, "total": total, "note": note}


def extract_rust(text):
    blocks = re.findall(r"```(?:rust|rs)?\s*\n(.*?)```", text, re.S)
    return max(blocks, key=len) if blocks else text


def run_plain(llm, t, dst):
    stub = (t / "stub.rs").read_text(encoding="utf-8")
    user = (f"{(t / 'TASK.md').read_text(encoding='utf-8')}\n\nsrc/lib.rs (fill this in, keep the public API):\n"
            f"```rust\n{stub}\n```\n\nExample tests (tests/visible.rs):\n```rust\n"
            f"{(t / 'visible.rs').read_text(encoding='utf-8')}\n```")
    r = llm.chat([{"role": "system", "content": PLAIN_SYSTEM}, {"role": "user", "content": user}],
                 temperature=0.3, max_tokens=12000)
    (dst / "src" / "lib.rs").write_text(extract_rust(r["content"]), encoding="utf-8")


def run_agent(llm, cfg, t, dst, max_steps):
    from .agent import Agent, Printer
    from .sessions import Session
    msg = ((t / "TASK.md").read_text(encoding="utf-8") + "\n\nImplement this in src/lib.rs (the stub is already "
           "there; keep its public API). Example tests are in tests/visible.rs; add your own tests too. "
           "You're done when `check` passes.")
    agent = Agent(dst, cfg, interactive=False, critic=True, max_steps=max_steps, llm=llm,
                  session=Session.new(dst, title=f"[auto] benchmark {t.name}"))
    agent.p = Printer(quiet=True)
    agent.send(msg)
    return agent


def model_label(cfg, api):
    return api["model"] if api else (cfg.get("model_name") or "local model")


def run(modes=("plain", "windmill"), only=None, api=None, max_steps=50):
    cfg = config.load()
    if any(not m.startswith("api") for m in modes) and not server.ensure(cfg):
        return None
    if not shutil.which("cargo"):
        ui.err("The benchmark tasks are Rust - install Rust first (windmill setup).")
        return None
    local = LLM(cfg)
    remote = LLM(cfg, base=api["base"], key=api["key"], model=api["model"]) if api else None
    ts = tasks(only)
    work = Path(tempfile.mkdtemp(prefix="windmill-bench-"))
    target = config.HOME / "bench-target"
    results = {"date": time.strftime("%Y-%m-%d %H:%M"), "model": cfg.get("model_name"),
               "api_model": api["model"] if api else None, "modes": {}}
    ui.rule(f"Benchmark: {len(ts)} tasks x {len(modes)} mode(s)")
    print(ui.dim("  Hidden tests decide the score. Windmill mode is slower on purpose (it checks and fixes its work).\n"))
    for mode in modes:
        llm = remote if mode.startswith("api") else local
        rows = []
        for t in ts:
            dst = work / mode / t.name
            make_crate(dst, t, (t / "stub.rs").read_text(encoding="utf-8"))
            t0 = time.time()
            u0 = dict(llm.usage)
            err = ""
            with ui.Spinner(f"{mode:12} {t.name}"):
                try:
                    if mode.endswith("plain"):
                        run_plain(llm, t, dst)
                    else:
                        run_agent(llm, cfg, t, dst, max_steps)
                except (LLMError, Exception) as e:  # noqa: BLE001 - a crash is a failed task, not a failed benchmark
                    err = str(e)[:200]
                s = score(dst, t, target)
            s.update({"task": t.name, "seconds": round(time.time() - t0),
                      "tokens_out": llm.usage["completion"] - u0["completion"], "error": err})
            rows.append(s)
            mark = ui.green("PASS") if s["passed_all"] else ui.red("fail")
            print(f"  {mode:12} {t.name:16} {mark}  {s['passed']}/{s['total']} hidden tests  "
                  f"{ui.dim(str(s['seconds']) + 's')}  {ui.dim(s['note'] or err)}")
        results["modes"][mode] = rows
    shutil.rmtree(work, ignore_errors=True)
    save_and_report(results)
    return results


def summary(rows):
    solved = sum(r["passed_all"] for r in rows)
    tests = sum(r["passed"] for r in rows), sum(r["total"] for r in rows)
    return solved, len(rows), tests


def save_and_report(res):
    RESULTS.mkdir(parents=True, exist_ok=True)
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", f"{res['date']}_{res['api_model'] or res['model']}")
    (RESULTS / f"{name}.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    ui.rule("Results")
    lines = [f"# Windmill benchmark - {res['date']}", f"Local model: {res['model']}" +
             (f" | API model: {res['api_model']}" if res["api_model"] else ""), "",
             "| Mode | Tasks solved | Hidden tests passed | Time |", "|---|---|---|---|"]
    for mode, rows in res["modes"].items():
        solved, n, (p, t) = summary(rows)
        secs = sum(r["seconds"] for r in rows)
        print(f"  {ui.bold(mode):24} {solved}/{n} tasks fully solved   {p}/{t} hidden tests   {secs // 60}m{secs % 60:02d}s")
        lines.append(f"| {mode} | {solved}/{n} | {p}/{t} | {secs // 60}m{secs % 60:02d}s |")
    if "plain" in res["modes"] and "windmill" in res["modes"]:
        a, b = summary(res["modes"]["plain"])[0], summary(res["modes"]["windmill"])[0]
        print(ui.dim(f"\n  Windmill's checking and fixing turned {a} solved tasks into {b} with the same model."))
    (RESULTS / f"{name}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(ui.dim(f"\n  Saved: {RESULTS / (name + '.md')}"))


def best_scores():
    """{model name: (solved, total)} best Windmill-mode result per model (shown in the models menu)."""
    best = {}
    for f in RESULTS.glob("*.json") if RESULTS.exists() else []:
        try:
            r = json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            continue
        for mode, rows in r["modes"].items():
            if mode != "windmill":
                continue
            s, n, _ = summary(rows)
            key = r["model"]
            if n >= 5 and (key not in best or s / n > best[key][0] / best[key][1]):
                best[key] = (s, n)
    return best


def history():
    files = sorted(RESULTS.glob("*.json")) if RESULTS.exists() else []
    if not files:
        ui.info("No benchmark runs yet:  windmill benchmark")
        return
    ui.rule("Benchmark history")
    for f in files[-20:]:
        r = json.loads(f.read_text(encoding="utf-8"))
        parts = [f"{m} {summary(rows)[0]}/{summary(rows)[1]}" for m, rows in r["modes"].items()]
        print(f"  {r['date']}  {(r['api_model'] or r['model'] or '?')[:38]:38}  " + "   ".join(parts))


def selftest(only=None):
    """Prove the hidden tests are right: reference solutions must pass, stubs must fail."""
    target = config.HOME / "bench-target"
    work = Path(tempfile.mkdtemp(prefix="windmill-selftest-"))
    ok_all = True
    for t in tasks(only):
        good = work / "good" / t.name
        make_crate(good, t, (t / "solution.rs").read_text(encoding="utf-8"))
        s1 = score(good, t, target)
        r = subprocess.run(["cargo", "test", "-q", "--test", "visible"], cwd=good, capture_output=True, text=True,
                           env={**os.environ, "CARGO_TARGET_DIR": str(target)})
        bad = work / "stub" / t.name
        make_crate(bad, t, (t / "stub.rs").read_text(encoding="utf-8"))
        s2 = score(bad, t, target)
        ok = s1["passed_all"] and r.returncode == 0 and not s2["passed_all"]
        ok_all &= ok
        print(f"  {ui.green('ok ') if ok else ui.red('BAD')} {t.name:16} reference {s1['passed']}/{s1['total']}"
              f"{' (visible ok)' if r.returncode == 0 else ' (VISIBLE FAIL)'}   stub {s2['passed']}/{s2['total']} {s2['note']}")
    shutil.rmtree(work, ignore_errors=True)
    return ok_all
