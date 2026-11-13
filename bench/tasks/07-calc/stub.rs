/// Calculator errors.
#[derive(Debug, PartialEq, Eq)]
pub enum CalcError {
    /// Division by zero.
    DivisionByZero,
    /// Malformed expression.
    Invalid,
}

/// Evaluates an arithmetic expression.
pub fn eval(expr: &str) -> Result<f64, CalcError> {
    todo!()
}
