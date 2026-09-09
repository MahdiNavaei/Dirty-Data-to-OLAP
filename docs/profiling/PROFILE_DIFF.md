# Profile Diff

`diff_column_profiles` compares project-owned column profiles by stable source,
table and column identity. It reports primitive, null-marker, distinct-ratio,
length and numeric changes without comparing raw values.

`COMPARABLE` means the identity and observation/configuration context match.
`LIMITED_COMPARABILITY` is used when source boundedness or profile configuration
differs. `INCOMPATIBLE` is used for identity mismatch. A diff is diagnostic
evidence, not a quality decision and does not infer business meaning.
