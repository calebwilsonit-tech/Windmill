use std::cmp::Ordering;
#[derive(Debug, PartialEq, Eq)]
pub enum SemverError { Invalid }
#[derive(Debug, Clone)]
pub struct Version { pub major: u64, pub minor: u64, pub patch: u64, pub pre: Vec<String>, pub build: Vec<String> }
fn num(s: &str) -> Result<u64, SemverError> {
    if s.is_empty() || !s.bytes().all(|b| b.is_ascii_digit()) || (s.len() > 1 && s.starts_with('0')) { return Err(SemverError::Invalid); }
    s.parse().map_err(|_| SemverError::Invalid)
}
fn idents(s: &str, pre: bool) -> Result<Vec<String>, SemverError> {
    let v: Vec<String> = s.split('.').map(String::from).collect();
    for i in &v {
        if i.is_empty() || !i.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-') { return Err(SemverError::Invalid); }
        if pre && i.bytes().all(|b| b.is_ascii_digit()) && i.len() > 1 && i.starts_with('0') { return Err(SemverError::Invalid); }
    }
    Ok(v)
}
impl Version {
    pub fn parse(s: &str) -> Result<Version, SemverError> {
        let (rest, build) = match s.split_once('+') { Some((a, b)) => (a, idents(b, false)?), None => (s, vec![]) };
        let (core, pre) = match rest.split_once('-') { Some((a, b)) => (a, idents(b, true)?), None => (rest, vec![]) };
        let p: Vec<&str> = core.split('.').collect();
        if p.len() != 3 { return Err(SemverError::Invalid); }
        Ok(Version { major: num(p[0])?, minor: num(p[1])?, patch: num(p[2])?, pre, build })
    }
}
impl PartialEq for Version { fn eq(&self, o: &Self) -> bool { self.cmp(o) == Ordering::Equal } }
impl Eq for Version {}
impl PartialOrd for Version { fn partial_cmp(&self, o: &Self) -> Option<Ordering> { Some(self.cmp(o)) } }
impl Ord for Version {
    fn cmp(&self, o: &Self) -> Ordering {
        (self.major, self.minor, self.patch).cmp(&(o.major, o.minor, o.patch)).then_with(|| {
            match (self.pre.is_empty(), o.pre.is_empty()) {
                (true, true) => Ordering::Equal, (true, false) => Ordering::Greater, (false, true) => Ordering::Less,
                _ => {
                    for (a, b) in self.pre.iter().zip(&o.pre) {
                        let c = match (a.parse::<u64>(), b.parse::<u64>()) {
                            (Ok(x), Ok(y)) => x.cmp(&y), (Ok(_), Err(_)) => Ordering::Less, (Err(_), Ok(_)) => Ordering::Greater,
                            _ => a.cmp(b),
                        };
                        if c != Ordering::Equal { return c; }
                    }
                    self.pre.len().cmp(&o.pre.len())
                }
            }
        })
    }
}
fn v3(a: u64, b: u64, c: u64) -> Version { Version { major: a, minor: b, patch: c, pre: vec![], build: vec![] } }
pub fn matches(req: &str, v: &Version) -> Result<bool, SemverError> {
    let mut cmps: Vec<(&str, Version)> = Vec::new();
    for part in req.split(',') {
        let p = part.trim();
        let (op, rest) = ["<=", ">=", "<", ">", "=", "^", "~"].iter().find_map(|o| p.strip_prefix(o).map(|r| (*o, r.trim()))).ok_or(SemverError::Invalid)?;
        if rest.starts_with(['=', '>', '<', '^', '~']) { return Err(SemverError::Invalid); }
        let b = Version::parse(rest)?;
        match op {
            "^" => {
                let hi = if b.major > 0 { v3(b.major + 1, 0, 0) } else if b.minor > 0 { v3(0, b.minor + 1, 0) } else { v3(0, 0, b.patch + 1) };
                cmps.push((">=", b)); cmps.push(("<", hi));
            }
            "~" => { let hi = v3(b.major, b.minor + 1, 0); cmps.push((">=", b)); cmps.push(("<", hi)); }
            _ => cmps.push((op, b)),
        }
    }
    if !v.pre.is_empty() && !cmps.iter().any(|(_, c)| !c.pre.is_empty() && (c.major, c.minor, c.patch) == (v.major, v.minor, v.patch)) {
        return Ok(false);
    }
    Ok(cmps.iter().all(|(op, c)| match *op { "=" => v == c, ">" => v > c, ">=" => v >= c, "<" => v < c, "<=" => v <= c, _ => false }))
}
