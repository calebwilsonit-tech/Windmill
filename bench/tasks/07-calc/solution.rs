#[derive(Debug, PartialEq, Eq)]
pub enum CalcError { DivisionByZero, Invalid }
struct P { c: Vec<char>, i: usize }
impl P {
    fn ws(&mut self) { while self.i < self.c.len() && self.c[self.i].is_whitespace() { self.i += 1; } }
    fn peek(&mut self) -> Option<char> { self.ws(); self.c.get(self.i).copied() }
    fn expr(&mut self) -> Result<f64, CalcError> {
        let mut v = self.term()?;
        while let Some(op) = self.peek() { if op == '+' || op == '-' { self.i += 1; let r = self.term()?; v = if op == '+' { v + r } else { v - r }; } else { break; } }
        Ok(v)
    }
    fn term(&mut self) -> Result<f64, CalcError> {
        let mut v = self.unary()?;
        while let Some(op) = self.peek() {
            if op == '*' || op == '/' { self.i += 1; let r = self.unary()?; if op == '/' { if r == 0.0 { return Err(CalcError::DivisionByZero); } v /= r; } else { v *= r; } } else { break; }
        }
        Ok(v)
    }
    fn unary(&mut self) -> Result<f64, CalcError> {
        if self.peek() == Some('-') { self.i += 1; return Ok(-self.unary()?); }
        self.power()
    }
    fn power(&mut self) -> Result<f64, CalcError> {
        let b = self.atom()?;
        if self.peek() == Some('^') { self.i += 1; let e = self.unary()?; return Ok(b.powf(e)); }
        Ok(b)
    }
    fn atom(&mut self) -> Result<f64, CalcError> {
        match self.peek() {
            Some('(') => { self.i += 1; let v = self.expr()?; if self.peek() != Some(')') { return Err(CalcError::Invalid); } self.i += 1; Ok(v) }
            Some(ch) if ch.is_ascii_digit() || ch == '.' => {
                let s = self.i;
                while self.i < self.c.len() && (self.c[self.i].is_ascii_digit() || self.c[self.i] == '.') { self.i += 1; }
                self.c[s..self.i].iter().collect::<String>().parse().map_err(|_| CalcError::Invalid)
            }
            _ => Err(CalcError::Invalid),
        }
    }
}
pub fn eval(expr: &str) -> Result<f64, CalcError> {
    let mut p = P { c: expr.chars().collect(), i: 0 };
    let v = p.expr()?;
    if p.peek().is_some() { return Err(CalcError::Invalid); }
    Ok(v)
}
