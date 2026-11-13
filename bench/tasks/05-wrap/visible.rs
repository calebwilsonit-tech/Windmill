use task::*;
#[test]
fn example() {
    assert_eq!(wrap("the quick brown fox", 10), vec!["the quick", "brown fox"]);
}
