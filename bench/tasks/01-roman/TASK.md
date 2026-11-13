# Roman numerals (easy)
Implement in `src/lib.rs`:
- `to_roman(n: u32) -> Result<String, RomanError>`: standard uppercase numerals for 1..=3999
  (subtractive forms IV, IX, XL, XC, CD, CM). 0 or > 3999 -> `Err(RomanError::OutOfRange)`.
- `from_roman(s: &str) -> Result<u32, RomanError>`: parse **canonical** uppercase numerals only.
  Anything that isn't exactly what `to_roman` would produce (empty, lowercase, invalid letters,
  "IIII", "VV", "IC", "MMMM"...) -> `Err(RomanError::Invalid)`.
