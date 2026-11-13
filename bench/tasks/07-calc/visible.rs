use task::*;
#[test]
fn example() {
    assert_eq!(eval("1 + 2 * 3"), Ok(7.0));
    assert_eq!(eval("1/0"), Err(CalcError::DivisionByZero));
}
