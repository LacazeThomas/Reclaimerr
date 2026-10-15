# Release follow-ups

Small additions deferred from the 0.5.7 release. Estimates include focused verification.

- [ ] **Rule search and filters (45–90 minutes):** search names and descriptions; filter by enabled status and movie version, series, season, or episode scope. Keep the existing outcome filter.
- [ ] **Total matched size in rule preview (1–3 hours):** calculate across every matching result, independent of pagination. Label it matched media size, since it is not guaranteed reclaimed space; avoid double-counting shared physical files.
- [ ] **Episode runtime condition (1–2 hours):** expose the stored episode runtime through the rule engine, editor, generated reference, and validation. Use consistent units with movie duration and leave unavailable runtime unknown.
