use task::*;
fn p(s: &str) -> Vec<Vec<String>> { parse_csv(s).unwrap() }
#[test]
fn quoting() {
    assert_eq!(p("\"a,b\",\"say \"\"hi\"\"\"\n"), vec![vec!["a,b", "say \"hi\""]]);
    assert_eq!(p("\"line1\nline2\",x"), vec![vec!["line1\nline2", "x"]]);
    assert_eq!(p("ab\"c,d"), vec![vec!["ab\"c", "d"]]);
    assert_eq!(p("\"\",x"), vec![vec!["", "x"]]);
}
#[test]
fn line_endings_and_empties() {
    assert_eq!(p("a,b\r\nc,d\r\n"), vec![vec!["a", "b"], vec!["c", "d"]]);
    assert_eq!(p(""), Vec::<Vec<String>>::new());
    assert_eq!(p("a\n\nb"), vec![vec!["a"], vec![""], vec!["b"]]);
    assert_eq!(p("a,,b,"), vec![vec!["a", "", "b", ""]]);
    assert_eq!(p(" a , b "), vec![vec![" a ", " b "]]);
}
#[test]
fn errors() {
    assert_eq!(parse_csv("\"abc"), Err(CsvError::UnterminatedQuote));
    assert_eq!(parse_csv("x,\"a\nb"), Err(CsvError::UnterminatedQuote));
}
