use task::*;
fn v(s: &str) -> Version { Version::parse(s).unwrap() }
#[test]
fn example() {
    assert!(v("1.0.0-alpha") < v("1.0.0"));
    assert_eq!(matches("^1.2.3", &v("1.9.0")), Ok(true));
}
