use task::*;
#[test]
fn examples() {
    assert_eq!(to_roman(1994).unwrap(), "MCMXCIV");
    assert_eq!(from_roman("XLII"), Ok(42));
    assert_eq!(to_roman(0), Err(RomanError::OutOfRange));
}
