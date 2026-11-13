/// Errors for roman numeral conversion.
#[derive(Debug, PartialEq, Eq)]
pub enum RomanError {
    /// Number not in 1..=3999.
    OutOfRange,
    /// Not a canonical roman numeral.
    Invalid,
}

/// Converts 1..=3999 to a roman numeral.
pub fn to_roman(n: u32) -> Result<String, RomanError> {
    todo!()
}

/// Parses a canonical roman numeral.
pub fn from_roman(s: &str) -> Result<u32, RomanError> {
    todo!()
}
