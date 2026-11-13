use task::*;
#[test]
fn prefers_cheap_detour() {
    let g = ["S9E", ".#.", "..."];
    assert_eq!(shortest_path(&g), Some(6)); // around the 9: down,down,right,right,up,up
    let h = ["S2E"];
    assert_eq!(shortest_path(&h), Some(3));
}
#[test]
fn invalid_inputs() {
    assert_eq!(shortest_path(&["S.."]), None);
    assert_eq!(shortest_path(&["..E"]), None);
    assert_eq!(shortest_path(&["S..", "E"]), None);
    assert_eq!(shortest_path(&[]), None);
    assert_eq!(shortest_path(&["SE"]), Some(1));
}
#[test]
fn big_grid() {
    let mut rows: Vec<String> = (0..300).map(|r| (0..300).map(|c| if (r * 7 + c * 3) % 11 == 0 && r % 5 != 0 { '#' } else if (r + c) % 4 == 0 { '5' } else { '.' }).collect()).collect();
    rows[0].replace_range(0..1, "S");
    rows[299].replace_range(299..300, "E");
    let refs: Vec<&str> = rows.iter().map(|s| s.as_str()).collect();
    let t = std::time::Instant::now();
    let d = shortest_path(&refs).expect("reachable");
    assert!(d >= 598 && d < 2000, "{d}");
    assert!(t.elapsed().as_secs_f64() < 10.0);
}
