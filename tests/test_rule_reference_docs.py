from __future__ import annotations

import re
import unittest

from scripts.gen_rule_reference import OUTPUT, render


def _normalize(text: str) -> list[str]:
    """Drop the table padding prettier adds so only content is compared."""
    lines = []
    for line in text.splitlines():
        line = re.sub(r"-{3,}", "---", line)
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            lines.append(line)
    return lines


class RuleReferenceDocsTests(unittest.TestCase):
    def test_committed_page_matches_rule_engine(self) -> None:
        self.assertEqual(
            _normalize(OUTPUT.read_text(encoding="utf-8")),
            _normalize(render()),
            "docs/reference/rule-fields.md is stale. Run "
            "`uv run python -m scripts.gen_rule_reference` and "
            "`npm run format:docs`.",
        )


if __name__ == "__main__":
    unittest.main()
