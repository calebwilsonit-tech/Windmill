use task::*;
#[test]
fn whitespace_and_edges() {
    assert_eq!(wrap("  a   b\n\nc  ", 3), vec!["a b", "c"]);
    assert_eq!(wrap("", 5), Vec::<String>::new());
    assert_eq!(wrap("   ", 5), Vec::<String>::new());
    assert_eq!(wrap("abc", 0), Vec::<String>::new());
    assert_eq!(wrap("exact fit", 9), vec!["exact fit"]);
}
#[test]
fn long_words() {
    assert_eq!(wrap("abcdefgh", 3), vec!["abc", "def", "gh"]);
    assert_eq!(wrap("hi abcdefg yo", 4), vec!["hi", "abcd", "efg", "yo"]);
    assert_eq!(wrap("abcdefg x", 5), vec!["abcde", "fg x"]);
}
#[test]
fn unicode_counts_chars() {
    assert_eq!(wrap("héllo wörld ñandú", 11), vec!["héllo wörld", "ñandú"]);
    assert_eq!(wrap("日本語テキスト", 3), vec!["日本語", "テキス", "ト"]);
    for line in wrap(&"lorem ipsum dolor sit amet ".repeat(50), 17) {
        assert!(line.chars().count() <= 17 && !line.starts_with(' ') && !line.ends_with(' '));
    }
}
