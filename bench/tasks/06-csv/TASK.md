# CSV parser (medium)
`parse_csv(input: &str) -> Result<Vec<Vec<String>>, CsvError>` (RFC 4180 style):
- Fields separated by `,`, records by `\n` or `\r\n`. A trailing line break does not create an extra record.
  Empty input -> no records. An empty line in the middle is a record with one empty field.
- A field starting with `"` is quoted: it may contain commas, line breaks and `""` (an escaped quote).
  Missing closing quote -> `Err(CsvError::UnterminatedQuote)`.
- A `"` inside an unquoted field is just a normal character. Whitespace is kept as-is.
