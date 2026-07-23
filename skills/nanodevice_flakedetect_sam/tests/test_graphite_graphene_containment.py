from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import unittest


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "sam_common.py"
SPEC = importlib.util.spec_from_file_location("sam_common", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
sam_common = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sam_common)


class GraphiteGrapheneSeparationTests(unittest.TestCase):
    def test_graphite_prior_does_not_change_graphene_output(self) -> None:
        graphene = np.zeros((5, 6), dtype=np.uint8)
        graphene[1:4, 1:3] = 255
        graphite_prior = np.zeros_like(graphene)
        graphite_prior[0:2, 4:6] = 255

        finalized = sam_common.finalize_graphene_mask(graphene, graphite_prior)

        self.assertTrue(np.array_equal(finalized, graphene))
        self.assertFalse(np.any(finalized[graphite_prior > 0]))

    def test_returns_binary_uint8_without_mutating_input(self) -> None:
        graphene = np.array([[0, 1], [7, 255]], dtype=np.uint8)
        original = graphene.copy()
        prior = np.full_like(graphene, 255)

        finalized = sam_common.finalize_graphene_mask(graphene, prior)

        self.assertTrue(np.array_equal(graphene, original))
        self.assertEqual(finalized.dtype, np.uint8)
        self.assertTrue(np.array_equal(
            finalized, np.array([[0, 255], [255, 255]], dtype=np.uint8)
        ))

    def test_rejects_mismatched_mask_shapes(self) -> None:
        with self.assertRaisesRegex(ValueError, "same shape"):
            sam_common.finalize_graphene_mask(
                np.zeros((4, 4), dtype=np.uint8),
                np.zeros((3, 4), dtype=np.uint8),
            )


if __name__ == "__main__":
    unittest.main()
