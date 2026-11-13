//! The grader grading itself: a tiny task where we know the right answer.
use std::fs;
use windmill_testkit::grader::{grade, selftest};

fn task(dir: &std::path::Path) {
    fs::create_dir_all(dir).unwrap();
    fs::write(dir.join("stub.rs"), "pub fn double(x: i32) -> i32 { todo!() }\n").unwrap();
    fs::write(dir.join("solution.rs"), "pub fn double(x: i32) -> i32 { x * 2 }\n").unwrap();
    fs::write(dir.join("visible.rs"), "#[test]\nfn v() { assert_eq!(task::double(1), 2); }\n").unwrap();
    fs::write(dir.join("hidden.rs"), "#[test]\nfn a() { assert_eq!(task::double(-3), -6); }\n#[test]\nfn b() { assert_eq!(task::double(0), 0); }\n").unwrap();
}

#[test]
fn grades_and_selftests() {
    let tmp = std::env::temp_dir().join(format!("wm-grader-{}", std::process::id()));
    let t = tmp.join("task");
    task(&t);
    assert_eq!(selftest(&t, &tmp.join("st")).unwrap(), Ok(()));
    let wrong = grade(&t, "pub fn double(x: i32) -> i32 { x + 2 }\n", &tmp.join("wrong")).unwrap();
    assert_eq!((wrong.passed, wrong.total, wrong.solved()), (0, 2, false));
    let broken = grade(&t, "pub fn double(x: i32) -> i32 { x * }\n", &tmp.join("broken")).unwrap();
    assert!(broken.compile_error);
    let _ = fs::remove_dir_all(&tmp);
}
