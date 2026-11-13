#!/usr/bin/env python3
"""Disciplined build loop for a local model:

  1. PLAN      - architect pass sees the whole-repo map, writes small numbered steps
  2. STEPS     - each step runs in a FRESH session that may ONLY do that step;
                 it must pass the full check (fmt, clippy, tests, slop guard) before
                 the next step starts. Failing steps are retried with the errors.
  3. REVIEW    - independent hostile reviewer with a requirement-by-requirement
                 completeness checklist; anything PARTIAL/MISSING = FAIL
  4. FIX       - fresh session addresses the review; loop back to 3
  5. MUTANTS   - (optional, cargo-mutants) proves the tests really test the new code

  python pipeline.py "Add CSV export to the reports page"
  python pipeline.py --task-file task.md
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import ai_corpus  # noqa: E402
import ai_index  # noqa: E402
import ai_query  # noqa: E402
from common import (DIFF_EXCLUDE, check_server, chat, diff_since, die, git, is_dirty,  # noqa: E402
                    journal, load_project_cfg, log, repo_map, repo_root, run_aider, run_dir, run_shell,
                    EVENTS, emit)

PLAN_SYSTEM = """You are a principal engineer planning work for a junior engineer who will
execute ONE step at a time with no memory of other steps. Do not write the full code.

Output markdown in EXACTLY this format:

## Goal
One paragraph.

## Requirements
- R1: a single, testable requirement taken from the task
- R2: ...
(List EVERY requirement implied by the task, including error handling and edge cases.)

## Steps
### Step 1: <short title>
Files: path/one.rs, path/two.rs
Do: precise instructions - which functions/types to add or change, signatures, behaviour.
Tests: the exact test cases to add in this step.
Done when: observable condition (e.g. "cargo test passes including new test parse_rejects_empty").

### Step 2: ...

Rules for steps:
- 2 to 8 steps. Each step is small, compiles on its own, and leaves all tests passing.
- Order steps so later steps build on earlier ones (types/data first, then logic, then wiring).
- Each step lists only the files it touches. Use real names from the repo map.
- Every requirement R1..Rn must be covered by some step."""

STEP_TEMPLATE = """You are executing STEP {i} OF {n} of the attached PLAN.md.
Steps 1..{prev} are already complete and committed. Do NOT redo them and do NOT start
steps after {i} - another session will do those.

{step}

Hard rules:
- Implement this step COMPLETELY. No todo!(), unimplemented!(), TODO comments,
  placeholder comments, stubs, or "simplified" versions. If it's in this step, finish it.
- Write the tests listed for this step. Tests must assert real behaviour.
- Only edit the files this step needs.
- You are done only when the test command passes (it also runs clippy and a guard that
  rejects unfinished code).
{extra}"""

REVIEW_SYSTEM = """You are a strict principal-engineer code reviewer. You did not write this code
and you are looking for reasons to reject it.

Respond in markdown with exactly these sections:

## Requirements checklist
For EVERY requirement in the plan (R1..Rn) AND anything else the task asks for:
- R1: DONE | PARTIAL | MISSING - evidence (file:function, test name)

## Issues
- [high|medium|low] file:line - problem - concrete fix
Look for: bugs, wrong logic, unhandled errors/edge cases, missing or weak tests,
code that only handles the happy path, security problems, violations of PROJECT RULES,
dead code, duplication. Ignore pure style nitpicks. Write "None" if there are none.

Final line, exactly one of:
VERDICT: PASS
VERDICT: FAIL

Rules: any PARTIAL or MISSING requirement, or any high/medium issue => VERDICT: FAIL."""

STEP_RE = re.compile(r"^###\s*Step\s+(\d+)\s*:?(.*?)$(.*?)(?=^###\s*Step\s+\d+|^##\s|\Z)", re.M | re.S)


def parse_steps(plan):
    steps = [(int(m.group(1)), m.group(2).strip(), m.group(0).strip()) for m in STEP_RE.finditer(plan)]
    return steps


def step_files(step_text, root):
    m = re.search(r"^Files:\s*(.+)$", step_text, re.M)
    if not m:
        return []
    files = [f.strip(" `") for f in re.split(r"[,\s]+", m.group(1)) if f.strip(" `")]
    return [root / f for f in files if (root / f).is_file()]


def references(root, cfg, text, files=(), budget=None):
    """Exact dependency APIs + library examples for this text, within a token budget."""
    try:
        return ai_corpus.reference_pack(root, text, files, budget=budget or cfg.get("reference_budget", 1800),
                                        cats=cfg.get("corpus_categories") or None,
                                        include_copyleft=cfg.get("include_copyleft", False))
    except Exception as e:  # noqa: BLE001 - references are a bonus, never block the run
        log(f"(reference search skipped: {e})")
        return ""


def step_context(root, files, step_text):
    """Token-cheap context for one step: signatures of everything the step's files depend on.
    If the step names no existing files, outline the most related files instead."""
    g = ai_index.build(root, quiet=True)
    rels = [str(f.relative_to(root)).replace("\\", "/") for f in files]
    if rels:
        return ai_query.context_pack(g, rels)
    out = ["# Context: files most related to this step (outlines only)", ""]
    for rel, _ in ai_query.related(g, step_text, k=4):
        out.append(ai_index.file_block(rel, g["files"][rel], 0))
    return "\n".join(out) + "\n"


def record(root, base, status, task, steps, extra):
    changed = git("diff", "--name-only", base, "--", ".", *DIFF_EXCLUDE, cwd=root, check=False).split()
    head = git("rev-parse", "--short", "HEAD", cwd=root, check=False)
    lines = [f"steps: " + "; ".join(f"{i}. {t}" for i, (_, t, _) in enumerate(steps, 1))[:300],
             f"files: {', '.join(changed[:15]) or 'none'}{' …' if len(changed) > 15 else ''}",
             f"commits: {base[:7]}..{head}"] + extra
    journal(root, status, task, lines)


def run_mutants(root, base, out, rnd):
    """Mutation-test only the changed code. Returns list of surviving mutants."""
    patch = out / f"mutants-{rnd}.diff"
    patch.write_text(git("diff", base, "--", ".", *DIFF_EXCLUDE, cwd=root, check=False) + "\n", encoding="utf-8")
    log(f"MUTANTS round {rnd} - mutating changed code to see if tests catch it (slow)")
    mdir = out / f"mutants-{rnd}"
    subprocess.run(["cargo", "mutants", "--in-diff", str(patch), "--output", str(mdir),
                    "--no-shuffle", "--colors", "never"], cwd=root)
    missed = mdir / "mutants.out" / "missed.txt"
    return [l for l in missed.read_text(encoding="utf-8").splitlines() if l.strip()] if missed.exists() else []


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("task", nargs="?", help="what you want built")
    ap.add_argument("--task-file", help="read the task from a file instead")
    ap.add_argument("--rounds", type=int, help="max review/fix rounds")
    ap.add_argument("--plan-file", help="use this plan instead of generating one")
    ap.add_argument("--no-mutants", action="store_true", help="skip mutation testing")
    args = ap.parse_args()

    task = Path(args.task_file).read_text(encoding="utf-8") if args.task_file else args.task
    if not task:
        die("Give a task, e.g.  python pipeline.py \"add input validation to signup\"")

    root = repo_root()
    cfg = load_project_cfg(root)
    rounds = args.rounds or cfg["max_rounds"]
    retries = cfg.get("step_retries", 3)
    test_cmd, lint_cmd = cfg["test_cmd"], cfg["lint_cmd"]
    use_mutants = (cfg.get("mutation_testing", True) and not args.no_mutants
                   and (root / "Cargo.toml").exists() and shutil.which("cargo-mutants"))
    check_server()
    if is_dirty(root):
        die("Working tree has uncommitted changes. Commit or stash them first.")

    out = run_dir(root, "pipeline")
    base = git("rev-parse", "HEAD", cwd=root)
    os.environ["AI_KIT_BASE"] = base  # slop guard checks everything added since here
    rules_file = root / "AGENTS.md"
    rules = rules_file.read_text(encoding="utf-8") if rules_file.exists() else "(none)"
    (out / "TASK.md").write_text(task, encoding="utf-8")
    log(f"Run folder: {out}\nBase commit: {base[:10]}  (undo all: git reset --hard {base[:10]})")
    if (root / "Cargo.lock").exists():
        try:
            ai_corpus.cmd_deps(root, quiet=True)
        except Exception as e:  # noqa: BLE001 - dependency references are a bonus; never block the build
            log(f"(dependency index skipped: {e})")

    # ---------------------------------------------------------------- 1. PLAN
    plan_path = out / "PLAN.md"
    if args.plan_file:
        plan_path.write_text(Path(args.plan_file).read_text(encoding="utf-8"), encoding="utf-8")
    else:
        log("1 PLAN - building repo map and planning (can take a while)")
        plan = chat([
            {"role": "system", "content": PLAN_SYSTEM},
            {"role": "user", "content": f"PROJECT RULES:\n{rules}\n\nREPO MAP:\n{repo_map(root)}\n\n"
                                        f"{references(root, cfg, task, budget=1200)}\nTASK:\n{task}"},
        ])
        steps = parse_steps(plan)
        if not steps:  # one retry with a sharper instruction
            plan = chat([
                {"role": "system", "content": PLAN_SYSTEM},
                {"role": "user", "content": f"REPO MAP:\n{repo_map(root)}\n\nTASK:\n{task}\n\n"
                                            "Your previous answer did not use the required '### Step N: title' format. Use it exactly."},
            ])
        plan_path.write_text(plan, encoding="utf-8")
    plan = plan_path.read_text(encoding="utf-8")
    if EVENTS:
        emit(type="text", text="**The plan**\n\n" + plan + "\n")
    else:
        print(plan)
    steps = parse_steps(plan)
    if not steps:
        log("Plan has no parsable steps - falling back to a single implementation step.")
        steps = [(1, "Implement the whole plan", "Implement everything in PLAN.md.")]

    # ---------------------------------------------------------------- 2. STEPS
    n = len(steps)
    for idx, (_, title, text) in enumerate(steps, 1):
        feedback = ""
        for attempt in range(1, retries + 1):
            log(f"2 STEP {idx}/{n}: {title}  (attempt {attempt}/{retries})")
            msg = STEP_TEMPLATE.format(i=idx, n=n, prev=idx - 1 if idx > 1 else "none",
                                       step=text, extra=feedback)
            files = step_files(text, root)
            ctx_path = out / f"step{idx}-context.md"
            ctx_path.write_text(step_context(root, files, text) + "\n" + references(root, cfg, f"{title}\n{text}", files),
                                encoding="utf-8")
            run_aider(root, f"TASK (overall):\n{task}\n\n{msg}", out, f"step{idx}-try{attempt}",
                      test_cmd, lint_cmd, read=[plan_path, ctx_path], edit=files)
            ok, check_out = run_shell(test_cmd, root)
            if ok:
                log(f"Step {idx} complete and verified.")
                break
            feedback = (f"\nA previous attempt at this step left the project failing the check. "
                        f"Fix this first:\n```\n{check_out[-4000:]}\n```")
        else:
            log(f"Step {idx} still failing after {retries} attempts. Stopping here so broken code "
                f"isn't built on.\nSee {out}. Partial work: git diff {base[:10]}")
            record(root, base, "STOPPED", task, steps, [f"stuck on step {idx} ({title}) after {retries} tries",
                                                        f"last error: {check_out.strip().splitlines()[0][:120] if check_out.strip() else '?'}"])
            sys.exit(2)

    # ---------------------------------------------------------------- 3-5. REVIEW / FIX / MUTANTS
    mutants_clean = not use_mutants
    for rnd in range(1, rounds + 1):
        tests_ok, test_out = run_shell(test_cmd, root)
        lint_ok, lint_out = run_shell(lint_cmd, root)
        diff = diff_since(base, root)

        log(f"3 REVIEW round {rnd}/{rounds}  (check: {'PASS' if tests_ok else 'FAIL'})")
        system = REVIEW_SYSTEM + (f"\n\nAlso check:\n{cfg['review_extra']}" if cfg.get("review_extra") else "")
        review = chat([
            {"role": "system", "content": system},
            {"role": "user", "content":
                f"TASK:\n{task}\n\nPLAN:\n{plan}\n\nPROJECT RULES:\n{rules}\n\n"
                f"CHECK RESULT ({'PASS' if tests_ok else 'FAIL'}):\n{test_out}\n\n"
                f"LINT RESULT ({'PASS' if lint_ok else 'FAIL'}):\n{lint_out}\n\n"
                f"DIFF:\n```diff\n{diff}\n```"},
        ], temperature=0.3)
        review_path = out / f"REVIEW-{rnd}.md"
        review_path.write_text(review, encoding="utf-8")
        if EVENTS:
            emit(type="text", text="**Reviewer**\n\n" + review + "\n")
        else:
            print(review)
        verdicts = re.findall(r"VERDICT:\s*(PASS|FAIL)", review, re.I)
        incomplete = re.search(r"^\s*[-*]?\s*\**R\d+\**\s*:\s*\**(PARTIAL|MISSING)", review, re.M | re.I)
        approved = bool(verdicts) and verdicts[-1].upper() == "PASS" and not incomplete

        missed = []
        if approved and tests_ok and lint_ok and use_mutants:
            missed = run_mutants(root, base, out, rnd)
            mutants_clean = not missed
            if missed:
                log(f"{len(missed)} mutant(s) survived - the tests don't really check that code.")

        if approved and tests_ok and lint_ok and mutants_clean:
            record(root, base, "DONE", task, steps, [f"review rounds: {rnd}" + (", mutation testing clean" if use_mutants else "")])
            log(f"DONE after {rnd} review round(s): all steps verified, reviewer approved every requirement"
                f"{', tests caught every mutant' if use_mutants else ''}.\n"
                f"Review changes: git diff {base[:10]}\nUndo all:       git reset --hard {base[:10]}")
            return

        log(f"4 FIX round {rnd}")
        problems = []
        if not tests_ok:
            problems.append(f"The check command fails:\n```\n{test_out[-4000:]}\n```")
        if not lint_ok:
            problems.append(f"The lint command fails:\n```\n{lint_out[-2000:]}\n```")
        if missed:
            problems.append("Mutation testing changed the code below and NO test failed, so the tests do not "
                            "actually verify this behaviour. Add or strengthen tests so each of these changes "
                            "would make a test fail (do not change the production code unless it's wrong):\n"
                            + "\n".join(missed[:40]))
            read = [plan_path]
        else:
            problems.append("Fix every PARTIAL/MISSING requirement and every high/medium issue in the attached "
                            "review. Finish incomplete work completely. Don't touch unrelated code. Don't weaken tests.")
            read = [plan_path, review_path]
        run_aider(root, f"TASK:\n{task}\n\n" + "\n\n".join(problems), out, f"fix-{rnd}",
                  test_cmd, lint_cmd, read=read)

    record(root, base, "STOPPED", task, steps, [f"no full approval after {rounds} review rounds - see REVIEW files in {out.name}"])
    log(f"Stopped after {rounds} rounds without full approval. Latest review in {out}.\n"
        f"Changes so far: git diff {base[:10]}")
    sys.exit(2)


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:  # noqa: BLE001 - show and keep the real error instead of a silent crash
        import traceback
        tb = traceback.format_exc()
        log = Path.home() / ".windmill" / "logs" / "build-crash.log"
        try:
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text(tb, encoding="utf-8")
        except OSError:
            pass
        print("\nWindmill's build step crashed. The error was:\n" + tb[-3000:] + f"\n(saved to {log})", flush=True)
        sys.exit(3)
