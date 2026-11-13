#!/usr/bin/env python3
"""BEST-OF-N: make N independent attempts at a task, each in its own git worktree,
run the tests on every attempt, and keep the best one. Trades time for quality.

  python best_of_n.py "Fix the race condition in the job queue" -n 6
  python best_of_n.py --task-file task.md --apply
"""
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import (check_server, die, git, is_dirty, load_project_cfg, log, repo_root,  # noqa: E402
                    run_aider, run_dir, run_shell)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("task", nargs="?")
    ap.add_argument("--task-file")
    ap.add_argument("-n", type=int, help="number of attempts (default from ai-kit.json, else 4)")
    ap.add_argument("--apply", action="store_true", help="apply the winning patch to your working tree")
    ap.add_argument("--keep", action="store_true", help="keep all attempt worktrees for inspection")
    args = ap.parse_args()

    task = Path(args.task_file).read_text(encoding="utf-8") if args.task_file else args.task
    if not task:
        die("Give a task.")
    root = repo_root()
    cfg = load_project_cfg(root)
    n = args.n or cfg["best_of_n"]
    test_cmd, lint_cmd = cfg["test_cmd"], cfg["lint_cmd"]
    if not test_cmd:
        die("Best-of-N needs a test_cmd in ai-kit.json - tests are how the winner is picked.")
    check_server()

    if (root / "Cargo.toml").exists() and "CARGO_TARGET_DIR" not in os.environ:
        # Share one target/ dir across attempts so dependencies compile only once
        os.environ["CARGO_TARGET_DIR"] = str(root / "target")

    out = run_dir(root, "bestofn")
    base = git("rev-parse", "HEAD", cwd=root)
    os.environ["AI_KIT_BASE"] = base  # slop guard checks everything added since here
    os.environ["AI_KIT_NO_INDEX"] = "1"  # don't run index hooks inside throwaway worktrees
    log(f"{n} attempts, base {base[:10]}, run folder {out}")

    results = []
    for i in range(1, n + 1):
        wt = out / f"attempt-{i}"
        git("worktree", "add", "--detach", str(wt), base, cwd=root)
        log(f"Attempt {i}/{n}")
        run_aider(wt, f"{task}\n\nImplement this COMPLETELY - no todo!(), stubs, placeholder comments "
                      f"or simplified versions. Write tests that assert real behaviour, including error "
                      f"cases. You are done only when the test command passes.", out, f"attempt-{i}",
                  test_cmd, lint_cmd)
        # Capture the patch BEFORE our test run so its build artifacts aren't included
        git("add", "-A", cwd=wt)  # include new files in the patch
        patch = git("diff", "--cached", "--binary", base, cwd=wt, check=False)
        tests_ok, _ = run_shell(test_cmd, wt)
        lint_ok, _ = run_shell(lint_cmd, wt)
        (out / f"attempt-{i}.patch").write_text(patch + "\n", encoding="utf-8")
        size = sum(1 for line in patch.splitlines()
                   if line[:1] in "+-" and not line.startswith(("+++", "---")))
        results.append({"i": i, "tests": tests_ok, "lint": lint_ok, "lines": size, "wt": wt})
        log(f"Attempt {i}: tests {'PASS' if tests_ok else 'FAIL'}, lint {'PASS' if lint_ok else 'FAIL'}, {size} lines changed")

    # Rank: tests pass > lint pass > non-empty > smallest change
    ranked = sorted(results, key=lambda r: (not r["tests"], not r["lint"], r["lines"] == 0, r["lines"]))
    print("\n  #  tests  lint  lines")
    for r in ranked:
        print(f"  {r['i']}  {'PASS' if r['tests'] else 'fail':5}  {'PASS' if r['lint'] else 'fail':4}  {r['lines']}")
    best = ranked[0]
    winner = out / f"attempt-{best['i']}.patch"

    for r in results:
        if not args.keep:
            git("worktree", "remove", "--force", str(r["wt"]), cwd=root, check=False)
    if not args.keep:
        git("worktree", "prune", cwd=root, check=False)

    if not best["tests"] or best["lines"] == 0:
        log(f"No attempt passed the tests. Patches are in {out} if you want to look.")
        sys.exit(2)

    shutil.copy(winner, out / "WINNER.patch")
    log(f"Winner: attempt {best['i']} -> {out / 'WINNER.patch'}")
    if args.apply:
        r = subprocess.run(["git", "apply", "--index", str(winner)], cwd=root)
        if r.returncode == 0:
            log("Applied and staged in your repo. Review with `git diff --cached`, then commit.")
        else:
            die("git apply failed (did your repo change during the run?). Apply manually:  git apply " + str(winner))
    else:
        log(f"Apply it with:  git apply {winner}")


if __name__ == "__main__":
    main()
