use task::*;
#[test]
fn examples() {
    assert_eq!(merge(vec![(1, 3), (2, 6), (8, 10)]), vec![(1, 6), (8, 10)]);
    assert_eq!(merge(vec![]), vec![]);
}
