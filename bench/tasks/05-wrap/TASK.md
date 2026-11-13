# Word wrap (medium)
`wrap(text: &str, width: usize) -> Vec<String>`: split `text` into words on any whitespace (runs of
whitespace and newlines count as one separator), then greedily pack words into lines of at most `width`
characters (count `char`s, not bytes), one space between words, no leading/trailing spaces.
A word longer than `width` is broken into `width`-sized pieces (each piece on its own line, the last piece
may continue with following words). Empty/whitespace-only text or width 0 -> empty Vec.
