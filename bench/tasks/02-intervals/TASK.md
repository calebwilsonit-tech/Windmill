# Merge intervals (easy)
`merge(v: Vec<(i64, i64)>) -> Vec<(i64, i64)>`: each pair is an inclusive integer range. If a pair has
start > end, treat it as (end, start). Merge ranges that overlap **or touch** (e.g. (1,3) and (4,6) become
(1,6)), and return them sorted by start. Empty input -> empty output. Must not overflow at i64 extremes.
