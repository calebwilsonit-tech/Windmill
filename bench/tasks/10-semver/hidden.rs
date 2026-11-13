use task::*;
fn v(s: &str) -> Version { Version::parse(s).unwrap_or_else(|_| panic!("{s}")) }
#[test]
fn spec_precedence_chain() {
    let chain = ["1.0.0-alpha", "1.0.0-alpha.1", "1.0.0-alpha.beta", "1.0.0-beta", "1.0.0-beta.2", "1.0.0-beta.11",
                 "1.0.0-rc.1", "1.0.0", "1.0.1", "1.1.0", "2.0.0"];
    for w in chain.windows(2) { assert!(v(w[0]) < v(w[1]), "{} < {}", w[0], w[1]); }
    assert_eq!(v("1.0.0+build.5"), v("1.0.0+other"));
    assert_eq!(v("1.2.3-x.7+b").pre, vec!["x", "7"]);
}
#[test]
fn invalid_versions() {
    for s in ["", "1", "1.2", "1.2.3.4", "01.2.3", "1.02.3", "1.2.3-", "1.2.3-a..b", "1.2.3-01", "a.b.c", "1.2.3+", "1.2.3-a_b", " 1.2.3"] {
        assert_eq!(Version::parse(s).err(), Some(SemverError::Invalid), "{s:?}");
    }
    assert!(Version::parse("1.2.3-0a").is_ok());
}
#[test]
fn ranges() {
    assert_eq!(matches("^0.2.3", &v("0.2.9")), Ok(true));
    assert_eq!(matches("^0.2.3", &v("0.3.0")), Ok(false));
    assert_eq!(matches("^0.0.3", &v("0.0.4")), Ok(false));
    assert_eq!(matches("~1.2.3", &v("1.2.99")), Ok(true));
    assert_eq!(matches("~1.2.3", &v("1.3.0")), Ok(false));
    assert_eq!(matches(">=1.0.0, <2.0.0", &v("1.5.0")), Ok(true));
    assert_eq!(matches(" >1.0.0 , <=1.0.5 ", &v("1.0.5")), Ok(true));
    assert_eq!(matches("=1.2.3", &v("1.2.3+meta")), Ok(true));
    assert_eq!(matches("<1.0.0", &v("1.0.0")), Ok(false));
}
#[test]
fn prerelease_rules() {
    assert_eq!(matches("^1.2.3", &v("1.3.0-alpha")), Ok(false));
    assert_eq!(matches(">=1.2.3-alpha", &v("1.2.3-beta")), Ok(true));
    assert_eq!(matches(">=1.2.3-alpha", &v("1.2.4-beta")), Ok(false));
    assert_eq!(matches(">=1.2.3-alpha", &v("1.2.4")), Ok(true));
}
#[test]
fn bad_requirements() {
    for r in ["", "^", ">=1.2", "1.2.3", "~>1.0.0", "^1.2.3,", "=>1.0.0"] {
        assert_eq!(matches(r, &v("1.2.3")), Err(SemverError::Invalid), "{r:?}");
    }
}
