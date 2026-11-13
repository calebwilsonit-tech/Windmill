use task::*;
#[test]
fn example() {
    assert_eq!(shortest_path(&["S.E"]), Some(2));
    assert_eq!(shortest_path(&["S#E"]), None);
}
