# Weighted grid path (hard)
`shortest_path(grid: &[&str]) -> Option<u32>`: rows of equal length. `S` start, `E` end, `#` wall,
`.` open, digits `1`-`9` = terrain with that cost. Move up/down/left/right. Entering a cell costs:
`.`/`S`/`E` = 1, a digit = its value; you never pay for the start cell. Return the minimum total cost
from S to E, or `None` if unreachable, if S or E is missing, or if rows have different lengths.
Must handle a 300x300 grid quickly (well under a second in release, a few seconds in debug).
