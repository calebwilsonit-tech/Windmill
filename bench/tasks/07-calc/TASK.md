# Expression calculator (hard)
`eval(expr: &str) -> Result<f64, CalcError>` for: numbers (`12`, `3.5`, `.5`), `+ - * /`, `^` (power,
**right**-associative: `2^3^2` = 512), unary minus (`-2^2` = -4, `2*-3` = -6, `--3` = 3), parentheses,
and any whitespace. Precedence: `^` > unary minus > `* /` > `+ -`; `* / + -` are left-associative.
Errors: division by zero -> `CalcError::DivisionByZero`; anything malformed (empty, `1 +`, `(1`, `2 3`,
unknown characters, `1..2`) -> `CalcError::Invalid`.
