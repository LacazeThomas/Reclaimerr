# Community Rules

Rules shared by Reclaimerr users. Each one is a file you can import directly.

Every rule here is checked by the test suite against the current rule engine, so it imports without errors. It is still someone else's rule: read the conditions and use **Preview Matches** before enabling it.

## Using a rule

1. Download the rule's `.json` file from the table below.
2. Open **Rules → Import** and choose the file.
3. Open the imported rule. Add library or user selections if you want them, and check the action.
4. Use **Preview Matches**, then enable the rule.

Shared rules arrive disabled, with automatic deletion and Arr tagging off. Without a library condition a rule covers every library of its media type.

## Rules

| Rule | Scope | Outcome | Description | Author |
| --- | --- | --- | --- | --- |
| [Low-rated unwatched movies](rules/low-rated-unwatched-movies.json) | Movie version | Cleanup | Unwatched movies older than 90 days rated below 5.5 on TMDB, with at least 100 votes | @jessielw |
| [Unplayed specials](rules/unplayed-specials.json) | Season | Cleanup | Season 0 with no recorded playback, added more than 180 days ago | @jessielw |
| [Protect recent requests](rules/protect-recent-requests.json) | Movie version | Protect | Movies with an active Seerr request from the last 30 days | @jessielw |

## Sharing a rule

1. Build and test the rule in Reclaimerr, then export it from the **Rules** page.
2. Fork [the repository](https://github.com/jessielw/Reclaimerr) and add the export as `docs/community-rules/rules/<short-name>.json`. Use lowercase words and dashes for the name.
3. Edit the file so it meets the checklist below.
4. Add a row for the rule to the table above, with your GitHub handle as the author.
5. Run `uv run pytest tests/test_community_rules.py`, then open a pull request against the `dev` branch.

The test enforces this checklist:

- The file holds one rule, and `"enabled"` is `false`.
- `description` says what the rule matches and why.
- No install-specific values: `library.id`, `arr.movie_ids`, `arr.series_ids`, Seerr requester IDs, usernames, or the action's `radarr_service_config_ids`, `sonarr_service_config_ids`, and `quality_profile_id`.
- `auto_delete_enabled` is off, so nobody's media is deleted without review.
- The rule imports without errors.

The file format is documented on [Rule Format](../reference/rule-format.md).
