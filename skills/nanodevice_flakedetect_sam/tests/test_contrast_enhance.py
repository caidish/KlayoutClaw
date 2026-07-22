from __future__ import annotations

import importlib.util
from pathlib import Path

import cv2
import numpy as np
import unittest


MODULE_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "contrast_enhance.py"
)
SPEC = importlib.util.spec_from_file_location("contrast_enhance", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
contrast_enhance = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(contrast_enhance)


def _low_contrast_image() -> np.ndarray:
    x = np.linspace(105, 135, 192, dtype=np.float32)
    luma = np.tile(x, (128, 1))
    texture = 3.0 * np.sin(np.arange(128, dtype=np.float32)[:, None] / 7.0)
    gray = np.clip(luma + texture, 0, 255).astype(np.uint8)
    return cv2.merge(
        [
            np.clip(gray + 4, 0, 255).astype(np.uint8),
            gray,
            np.clip(gray - 3, 0, 255).astype(np.uint8),
        ]
    )


class ContrastEnhanceTests(unittest.TestCase):
    def test_preserves_contract_and_increases_local_contrast(self) -> None:
        image = _low_contrast_image()

        enhanced = contrast_enhance.enhance_lab_l(image)

        self.assertEqual(enhanced.shape, image.shape)
        self.assertEqual(enhanced.dtype, np.uint8)
        original_lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
        enhanced_lab = cv2.cvtColor(enhanced, cv2.COLOR_BGR2LAB)
        self.assertGreater(
            float(enhanced_lab[..., 0].std()),
            float(original_lab[..., 0].std()) * 1.10,
        )
        chroma_mae = np.abs(
            enhanced_lab[..., 1:].astype(np.int16)
            - original_lab[..., 1:].astype(np.int16)
        ).mean()
        self.assertLess(float(chroma_mae), 3.0)

    def test_is_deterministic_and_does_not_mutate_input(self) -> None:
        image = _low_contrast_image()
        original = image.copy()

        first = contrast_enhance.enhance_lab_l(image)
        second = contrast_enhance.enhance_lab_l(image)

        self.assertTrue(np.array_equal(image, original))
        self.assertTrue(np.array_equal(first, second))

    def test_rejects_invalid_images(self) -> None:
        cases = [
            (np.zeros((10, 10), dtype=np.uint8), "BGR"),
            (np.zeros((10, 10, 4), dtype=np.uint8), "BGR"),
            (np.zeros((10, 10, 3), dtype=np.float32), "uint8"),
        ]
        for image, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(
                ValueError, message
            ):
                contrast_enhance.enhance_lab_l(image)

    def test_rejects_non_positive_parameters(self) -> None:
        image = _low_contrast_image()
        with self.assertRaisesRegex(ValueError, "clip_limit"):
            contrast_enhance.enhance_lab_l(image, clip_limit=0)
        with self.assertRaisesRegex(ValueError, "tile_grid"):
            contrast_enhance.enhance_lab_l(image, tile_grid=(0, 8))

if __name__ == "__main__":
    unittest.main()
