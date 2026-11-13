# Semantic versions and ranges (hard)
`Version::parse(s) -> Result<Version, SemverError>`: `MAJOR.MINOR.PATCH`, optional `-PRERELEASE`
(dot-separated identifiers of `[0-9A-Za-z-]`, none empty) and optional `+BUILD` (same charset, ignored for
ordering and equality). Numbers: no leading zeros (except `0` itself); numeric prerelease identifiers likewise.
Invalid -> `Err(SemverError::Invalid)`.
Ordering (`impl Ord`): by major, minor, patch; a version with a prerelease is LOWER than the same version
without; prerelease identifiers compare left to right: numeric ones numerically, alphanumeric ones by ASCII,
numeric < alphanumeric, and if all equal the longer list is greater. `PartialEq` must agree with this
(build metadata ignored).
`matches(req: &str, v: &Version) -> Result<bool, SemverError>`: `req` is comparators separated by `,`
(all must hold), each one of `=X.Y.Z`, `>X.Y.Z`, `>=`, `<`, `<=`, `^X.Y.Z`, `~X.Y.Z` (spaces allowed around
them). Caret: `^1.2.3` = `>=1.2.3, <2.0.0`; `^0.2.3` = `>=0.2.3, <0.3.0`; `^0.0.3` = `>=0.0.3, <0.0.4`.
Tilde: `~1.2.3` = `>=1.2.3, <1.3.0`. A version WITH a prerelease only matches a range if some comparator in
it has a prerelease on the same MAJOR.MINOR.PATCH. Bad requirement -> `Err(SemverError::Invalid)`.
