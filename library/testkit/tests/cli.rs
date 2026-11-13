//! CLI tests (assert_cmd): run the real binary the way a user would; check stdout, stderr and exit code.
use assert_cmd::Command;
use predicates::prelude::*;

#[test]
fn prints_report() {
    Command::cargo_bin("inv").unwrap()
        .args(["add", "sword", "3", "add", "shield", "1", "remove", "sword", "1"])
        .assert().success().stdout("shield x1\nsword x2\n");
}

#[test]
fn errors_go_to_stderr_with_exit_code() {
    Command::cargo_bin("inv").unwrap()
        .args(["remove", "sword", "1"])
        .assert().code(1).stderr(predicate::str::contains("NotEnough"));
    Command::cargo_bin("inv").unwrap().args(["add"]).assert().code(2).stderr(predicate::str::contains("usage"));
}
