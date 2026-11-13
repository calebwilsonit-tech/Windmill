#[derive(Debug, PartialEq, Eq)]
pub enum CsvError { UnterminatedQuote }
pub fn parse_csv(input: &str) -> Result<Vec<Vec<String>>, CsvError> {
    let mut rows = Vec::new();
    if input.is_empty() { return Ok(rows); }
    let c: Vec<char> = input.chars().collect();
    let (mut i, mut row, mut f) = (0, Vec::new(), String::new());
    let mut quoted_field = false;
    while i < c.len() {
        let ch = c[i];
        if f.is_empty() && !quoted_field && ch == '"' {
            quoted_field = true; i += 1;
            loop {
                if i >= c.len() { return Err(CsvError::UnterminatedQuote); }
                if c[i] == '"' { if i + 1 < c.len() && c[i + 1] == '"' { f.push('"'); i += 2; continue; } i += 1; break; }
                f.push(c[i]); i += 1;
            }
            continue;
        }
        match ch {
            ',' => { row.push(std::mem::take(&mut f)); quoted_field = false; i += 1; }
            '\r' if i + 1 < c.len() && c[i + 1] == '\n' => { row.push(std::mem::take(&mut f)); rows.push(std::mem::take(&mut row)); quoted_field = false; i += 2; }
            '\n' => { row.push(std::mem::take(&mut f)); rows.push(std::mem::take(&mut row)); quoted_field = false; i += 1; }
            _ => { f.push(ch); i += 1; }
        }
    }
    let ends_with_break = input.ends_with('\n');
    if !ends_with_break { row.push(f); rows.push(row); }
    Ok(rows)
}
