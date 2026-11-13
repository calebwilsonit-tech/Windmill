/// CSV errors.
#[derive(Debug, PartialEq, Eq)]
pub enum CsvError {
    /// A quoted field was never closed.
    UnterminatedQuote,
}

/// Parses CSV text into records of fields.
pub fn parse_csv(input: &str) -> Result<Vec<Vec<String>>, CsvError> {
    todo!()
}
