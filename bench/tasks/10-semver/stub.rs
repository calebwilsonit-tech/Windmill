use std::cmp::Ordering;

/// Semver errors.
#[derive(Debug, PartialEq, Eq)]
pub enum SemverError {
    /// Not a valid version or requirement.
    Invalid,
}

/// A semantic version.
#[derive(Debug, Clone)]
pub struct Version {
    /// Major.
    pub major: u64,
    /// Minor.
    pub minor: u64,
    /// Patch.
    pub patch: u64,
    /// Prerelease identifiers (empty if none).
    pub pre: Vec<String>,
    /// Build metadata (ignored for ordering).
    pub build: Vec<String>,
}

impl Version {
    /// Parses a version string.
    pub fn parse(s: &str) -> Result<Version, SemverError> {
        todo!()
    }
}

impl PartialEq for Version {
    fn eq(&self, other: &Self) -> bool {
        todo!()
    }
}
impl Eq for Version {}
impl PartialOrd for Version {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}
impl Ord for Version {
    fn cmp(&self, other: &Self) -> Ordering {
        todo!()
    }
}

/// Whether `v` satisfies the requirement `req`.
pub fn matches(req: &str, v: &Version) -> Result<bool, SemverError> {
    todo!()
}
