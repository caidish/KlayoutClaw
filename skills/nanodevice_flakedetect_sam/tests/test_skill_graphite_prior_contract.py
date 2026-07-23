from __future__ import annotations

from pathlib import Path
import unittest


SKILL_PATH = Path(__file__).resolve().parents[1] / "SKILL.md"


class GraphitePriorPromptContractTests(unittest.TestCase):
    def test_each_graphene_prompt_candidate_requires_graphite_overlap_positive(self) -> None:
        skill = " ".join(
            SKILL_PATH.read_text(encoding="utf-8").lower().split()
        )

        self.assertIn("graphite-overlap positive", skill)
        self.assertIn("every graphene prompt candidate", skill)
        self.assertIn("at least one positive point", skill)
        self.assertIn("graphene region that overlaps the yellow graphite contour", skill)


if __name__ == "__main__":
    unittest.main()
