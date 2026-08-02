"""Unit tests for graphite combine transforms."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import cv2
import numpy as np

COMBINE = (
    Path(__file__).resolve().parents[2]
    / "skills"
    / "nanodevice_flakedetect_combine"
    / "scripts"
)

spec = importlib.util.spec_from_file_location("combine_transform", COMBINE / "transform.py")
transform = importlib.util.module_from_spec(spec)
sys.modules["combine_transform"] = transform
spec.loader.exec_module(transform)


def test_graphite_transform_preserves_multiple_mask_components(tmp_path):
    """Catches graphite combine falling back to single largest contour output."""
    detect_dir = tmp_path / "detect"
    detect_dir.mkdir()

    graphite_mask = np.zeros((80, 80), dtype=np.uint8)
    graphite_mask[5:35, 10:30] = 255
    graphite_mask[40:72, 45:70] = 255
    cv2.imwrite(str(detect_dir / "graphite_mask.png"), graphite_mask)

    single_component_contour = np.array(
        [[10, 5], [29, 5], [29, 34], [10, 34]], dtype=np.float64
    )
    np.save(str(detect_dir / "graphite_contour.npy"), single_component_contour)

    detections = {
        "materials": {
            "graphite": {
                "mask_file": "graphite_mask.png",
                "contour_file": "graphite_contour.npy",
            }
        }
    }

    masks, counts = transform.build_masks(
        detections,
        str(detect_dir),
        np.eye(2, 3, dtype=np.float32),
        None,
        None,
        (80, 80),
        min_area=1,
    )
    contours = transform.extract_contours(masks, min_area_px=1)

    assert counts["graphite"]["input_pixels"] == 1400
    assert len(contours["graphite"]) == 2
