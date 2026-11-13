pub fn wrap(text: &str, width: usize) -> Vec<String> {
    if width == 0 { return vec![]; }
    let mut lines: Vec<String> = Vec::new();
    let mut cur = String::new();
    for w in text.split_whitespace() {
        let chars: Vec<char> = w.chars().collect();
        let mut pieces: Vec<String> = chars.chunks(width).map(|c| c.iter().collect()).collect();
        let last = pieces.pop().unwrap_or_default();
        for p in pieces {
            if !cur.is_empty() { lines.push(std::mem::take(&mut cur)); }
            lines.push(p);
        }
        let need = if cur.is_empty() { last.chars().count() } else { cur.chars().count() + 1 + last.chars().count() };
        if need <= width { if !cur.is_empty() { cur.push(' '); } cur.push_str(&last); }
        else { lines.push(std::mem::take(&mut cur)); cur = last; }
    }
    if !cur.is_empty() { lines.push(cur); }
    lines
}
