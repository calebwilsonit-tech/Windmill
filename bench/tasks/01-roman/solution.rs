#[derive(Debug, PartialEq, Eq)]
pub enum RomanError { OutOfRange, Invalid }
const T: [(u32, &str); 13] = [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"), (50, "L"),
    (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")];
pub fn to_roman(mut n: u32) -> Result<String, RomanError> {
    if !(1..=3999).contains(&n) { return Err(RomanError::OutOfRange); }
    let mut s = String::new();
    for (v, r) in T { while n >= v { s.push_str(r); n -= v; } }
    Ok(s)
}
pub fn from_roman(s: &str) -> Result<u32, RomanError> {
    let (mut rest, mut n) = (s, 0u32);
    for (v, r) in T { while rest.starts_with(r) { rest = &rest[r.len()..]; n += v; } }
    if !rest.is_empty() || n == 0 || n > 3999 { return Err(RomanError::Invalid); }
    if to_roman(n).as_deref() == Ok(s) { Ok(n) } else { Err(RomanError::Invalid) }
}
