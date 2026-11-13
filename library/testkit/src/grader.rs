//! Hidden-test grading - how Windmill's benchmark judges code (port of `wm/bench.py`).
//!
//! A *task* folder holds: `TASK.md` (spec), `stub.rs` (signatures), `visible.rs` (examples the coder sees),
//! `hidden.rs` (the real tests, never shown), `solution.rs` (reference). To grade a candidate `lib.rs`:
//! build a throwaway crate, copy in the hidden tests, run `cargo test --test hidden`, count passes.
//!
//! The rule that makes this trustworthy: **[`selftest`] first** - the reference must pass every hidden test
//! and the stub must fail them. Windmill's own self-test caught two broken hidden tests this way.

use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;

/// Result of running hidden tests against one candidate.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Score {
    /// Tests that passed.
    pub passed: u32,
    /// Tests in the hidden file.
    pub total: u32,
    /// The candidate didn't even compile.
    pub compile_error: bool,
}

impl Score {
    /// Fully solved?
    pub fn solved(&self) -> bool {
        !self.compile_error && self.total > 0 && self.passed == self.total
    }
}

/// Counts `#[test]` functions so a compile failure still reports `0/total`, not `0/0`.
pub fn count_tests(src: &str) -> u32 {
    src.matches("#[test]").count() as u32
}

/// Parses every `test result: ... N passed; M failed` line cargo prints.
pub fn parse_cargo_results(output: &str) -> (u32, u32) {
    let (mut passed, mut failed) = (0, 0);
    for line in output.lines().filter(|l| l.contains("test result:")) {
        let num = |key: &str| -> u32 {
            line.split(';')
                .find(|part| part.contains(key))
                .and_then(|part| part.split_whitespace().rev().find_map(|w| w.parse().ok()))
                .unwrap_or(0)
        };
        passed += num("passed");
        failed += num("failed");
    }
    (passed, failed)
}

/// Builds a temporary crate around `lib_src` with the task's tests.
pub fn make_crate(dir: &Path, task: &Path, lib_src: &str) -> std::io::Result<()> {
    fs::create_dir_all(dir.join("src"))?;
    fs::create_dir_all(dir.join("tests"))?;
    fs::write(dir.join("Cargo.toml"), "[package]\nname = \"task\"\nversion = \"0.1.0\"\nedition = \"2021\"\n")?;
    fs::write(dir.join("src/lib.rs"), lib_src)?;
    fs::copy(task.join("visible.rs"), dir.join("tests/visible.rs"))?;
    fs::copy(task.join("hidden.rs"), dir.join("tests/hidden.rs"))?;
    Ok(())
}

/// Grades `lib_src` against the task's hidden tests.
pub fn grade(task: &Path, lib_src: &str, work: &Path) -> std::io::Result<Score> {
    make_crate(work, task, lib_src)?;
    let total = count_tests(&fs::read_to_string(task.join("hidden.rs"))?);
    let out = Command::new("cargo").args(["test", "-q", "--test", "hidden"]).current_dir(work).output()?;
    let text = format!("{}{}", String::from_utf8_lossy(&out.stdout), String::from_utf8_lossy(&out.stderr));
    let (passed, _) = parse_cargo_results(&text);
    let compiled = text.contains("test result:");
    Ok(Score { passed, total, compile_error: !compiled })
}

/// Proves a task is gradeable: the reference solution passes everything, the stub doesn't.
pub fn selftest(task: &Path, scratch: &Path) -> std::io::Result<Result<(), String>> {
    let reference = grade(task, &fs::read_to_string(task.join("solution.rs"))?, &scratch.join("ref"))?;
    let stub = grade(task, &fs::read_to_string(task.join("stub.rs"))?, &scratch.join("stub"))?;
    Ok(match (reference.solved(), stub.solved()) {
        (true, false) => Ok(()),
        (false, _) => Err(format!("reference only passes {}/{} - the hidden tests are wrong", reference.passed, reference.total)),
        (true, true) => Err("the stub passes - the hidden tests don't test anything".to_string()),
    })
}

/// Every task folder under `root` (folders that contain a `hidden.rs`).
pub fn task_dirs(root: &Path) -> Vec<PathBuf> {
    let mut v: Vec<PathBuf> = fs::read_dir(root)
        .map(|rd| rd.filter_map(|e| e.ok().map(|e| e.path())).filter(|p| p.join("hidden.rs").exists()).collect())
        .unwrap_or_default();
    v.sort();
    v
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_multiple_result_lines() {
        let out = "test result: ok. 3 passed; 0 failed; 0 ignored\n...\ntest result: FAILED. 1 passed; 2 failed; 0 ignored";
        assert_eq!(parse_cargo_results(out), (4, 2));
    }

    #[test]
    fn counts_tests_and_solved_rule() {
        assert_eq!(count_tests("#[test]\nfn a() {}\n#[test]\nfn b() {}"), 2);
        assert!(Score { passed: 2, total: 2, compile_error: false }.solved());
        assert!(!Score { passed: 0, total: 0, compile_error: false }.solved());
        assert!(!Score { passed: 2, total: 2, compile_error: true }.solved());
    }
}
