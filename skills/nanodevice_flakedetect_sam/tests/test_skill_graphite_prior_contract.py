from __future__ import annotations

from pathlib import Path
import unittest


SKILL_PATH = Path(__file__).resolve().parents[1] / "SKILL.md"
REFERENCES_DIR = Path(__file__).resolve().parents[1] / "references"


def _words(path: Path) -> str:
    return " ".join(path.read_text(encoding="utf-8").lower().split())


class GraphitePriorPromptContractTests(unittest.TestCase):
    def test_skill_routes_actions_to_required_references(self) -> None:
        skill = _words(SKILL_PATH)

        self.assertIn("before taking an action, read the matching reference first", skill)
        self.assertIn("create or rewrite `graphite_manual_prompts.json`", skill)
        self.assertIn("references/graphite-prompts.md", skill)
        self.assertIn("choose or freeze a graphene prompt rank", skill)
        self.assertIn("references/visual-review.md", skill)
        self.assertIn("continue to `detections.json`, combine, gdsalign", skill)

    def test_each_graphene_prompt_candidate_requires_graphite_overlap_positive(self) -> None:
        graphene = _words(REFERENCES_DIR / "graphene-prompts.md")

        self.assertIn("graphite_on_top_mask.png", graphene)
        self.assertIn("at least one valid graphene positive in the overlap region", graphene)
        self.assertIn("never accept zero-overlap graphene", graphene)

    def test_graphite_reference_requires_centerline_points_and_distant_negatives(self) -> None:
        graphite = _words(REFERENCES_DIR / "graphite-prompts.md")

        self.assertIn("positive points on the optical centerline", graphite)
        self.assertIn("not on the strip edge", graphite)
        self.assertIn("two to four well-placed centerline positives", graphite)
        self.assertIn("far enough away", graphite)
        self.assertIn("cannot fall inside the full dark-band envelope", graphite)


if __name__ == "__main__":
    unittest.main()
