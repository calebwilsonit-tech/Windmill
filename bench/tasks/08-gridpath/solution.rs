use std::cmp::Reverse;
use std::collections::BinaryHeap;
pub fn shortest_path(grid: &[&str]) -> Option<u32> {
    let g: Vec<Vec<char>> = grid.iter().map(|r| r.chars().collect()).collect();
    let w = g.first()?.len();
    if g.iter().any(|r| r.len() != w) { return None; }
    let find = |ch| g.iter().enumerate().find_map(|(r, row)| row.iter().position(|&c| c == ch).map(|c| (r, c)));
    let (s, e) = (find('S')?, find('E')?);
    let mut dist = vec![vec![u32::MAX; w]; g.len()];
    let mut h = BinaryHeap::new();
    dist[s.0][s.1] = 0;
    h.push(Reverse((0u32, s.0, s.1)));
    while let Some(Reverse((d, r, c))) = h.pop() {
        if (r, c) == e { return Some(d); }
        if d > dist[r][c] { continue; }
        for (dr, dc) in [(0i32, 1i32), (1, 0), (0, -1), (-1, 0)] {
            let (nr, nc) = (r as i32 + dr, c as i32 + dc);
            if nr < 0 || nc < 0 || nr as usize >= g.len() || nc as usize >= w { continue; }
            let (nr, nc) = (nr as usize, nc as usize);
            let cost = match g[nr][nc] { '#' => continue, ch @ '1'..='9' => ch as u32 - '0' as u32, _ => 1 };
            if d + cost < dist[nr][nc] { dist[nr][nc] = d + cost; h.push(Reverse((d + cost, nr, nc))); }
        }
    }
    None
}
