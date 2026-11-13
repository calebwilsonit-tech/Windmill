use task::*;
#[test]
fn example() {
    assert_eq!(parse_csv("a,b\n1,2\n").unwrap(), vec![vec!["a", "b"], vec!["1", "2"]]);
}
