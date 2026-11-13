pub fn merge(v: Vec<(i64, i64)>) -> Vec<(i64, i64)> {
    let mut v: Vec<(i64, i64)> = v.into_iter().map(|(a, b)| if a > b { (b, a) } else { (a, b) }).collect();
    v.sort();
    let mut out: Vec<(i64, i64)> = Vec::new();
    for (a, b) in v {
        if let Some(last) = out.last_mut() {
            if (a as i128) <= last.1 as i128 + 1 { last.1 = last.1.max(b); continue; }
        }
        out.push((a, b));
    }
    out
}
