use task::*;
fn e(s: &str) -> f64 { eval(s).unwrap_or_else(|err| panic!("{s}: {err:?}")) }
#[test]
fn precedence_and_assoc() {
    assert_eq!(e("2^3^2"), 512.0);
    assert_eq!(e("-2^2"), -4.0);
    assert_eq!(e("(-2)^2"), 4.0);
    assert_eq!(e("2*-3"), -6.0);
    assert_eq!(e("--3"), 3.0);
    assert_eq!(e("10-4-3"), 3.0);
    assert_eq!(e("100/10/5"), 2.0);
    assert_eq!(e(" ( 1 + 2 ) * ( 3 + 4 ) "), 21.0);
    assert_eq!(e(".5+1.25"), 1.75);
    assert_eq!(e("2^-1"), 0.5);
    assert_eq!(e("((((7))))"), 7.0);
}
#[test]
fn errors() {
    for s in ["", "1 +", "(1", "1)", "2 3", "1..2", "abc", "*1", "()", "1 + * 2"] {
        assert_eq!(eval(s), Err(CalcError::Invalid), "{s:?}");
    }
    assert_eq!(eval("5/(2-2)"), Err(CalcError::DivisionByZero));
}
