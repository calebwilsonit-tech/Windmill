use task::*;
#[test]
fn touching_and_unsorted() {
    assert_eq!(merge(vec![(4, 6), (1, 3)]), vec![(1, 6)]);
    assert_eq!(merge(vec![(10, 8), (1, 1), (3, 3)]), vec![(1, 1), (3, 3), (8, 10)]);
    assert_eq!(merge(vec![(1, 10), (2, 3), (4, 5)]), vec![(1, 10)]);
    assert_eq!(merge(vec![(5, 5), (5, 5)]), vec![(5, 5)]);
}
#[test]
fn extremes() {
    assert_eq!(merge(vec![(i64::MAX - 1, i64::MAX), (i64::MIN, i64::MIN + 1)]),
               vec![(i64::MIN, i64::MIN + 1), (i64::MAX - 1, i64::MAX)]);
    assert_eq!(merge(vec![(i64::MIN, 0), (1, i64::MAX)]), vec![(i64::MIN, i64::MAX)]);
}
#[test]
fn many() {
    let v: Vec<(i64, i64)> = (0..1000).rev().map(|i| (i * 3, i * 3 + 1)).collect();
    assert_eq!(merge(v).len(), 1000);
    let w: Vec<(i64, i64)> = (0..1000).map(|i| (i * 2, i * 2 + 1)).collect();
    assert_eq!(merge(w), vec![(0, 1999)]);
}
