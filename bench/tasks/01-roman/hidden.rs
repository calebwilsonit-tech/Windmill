use task::*;
#[test]
fn to_roman_values() {
    for (n, s) in [(1, "I"), (4, "IV"), (9, "IX"), (14, "XIV"), (40, "XL"), (90, "XC"), (400, "CD"), (900, "CM"),
                   (3999, "MMMCMXCIX"), (2024, "MMXXIV"), (444, "CDXLIV")] {
        assert_eq!(to_roman(n).unwrap(), s, "{n}");
    }
    assert_eq!(to_roman(4000), Err(RomanError::OutOfRange));
}
#[test]
fn roundtrip_all() {
    for n in 1..=3999 {
        assert_eq!(from_roman(&to_roman(n).unwrap()), Ok(n));
    }
}
#[test]
fn rejects_non_canonical() {
    for s in ["", "IIII", "VV", "IC", "IL", "XM", "MMMM", "iv", "ABC", "IIV", "VX", "XXXX", "DD", "CCCC", " X"] {
        assert_eq!(from_roman(s), Err(RomanError::Invalid), "{s:?}");
    }
}
