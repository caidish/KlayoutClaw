#!/usr/bin/env python
"""Tests for nanodevice:flakedetect pipeline scripts."""
import glob
import json
import os
import sys
import tempfile

import cv2
import numpy as np
import pytest

# Add core.py to import path
sys.path.insert(0, os.path.join(
    os.path.dirname(__file__), "..", "skills", "nanodevice", "flakedetect", "scripts"
))
from core import (
    morph_clean, make_warp, warp_contour, invert_warp, LAYER_MAP, STACK_ORDER,
)

from conftest import (
    BOTTOM_PART, TOP_PART, FULL_STACK_RAW, FULL_STACK_LUT, PIXEL_SIZE,
    run_flakedetect_script, build_detections_json,
)


class TestCore:
    """Unit tests for core.py shared utilities."""

    def test_morph_clean_roundtrip(self):
        """morph_clean on a solid rectangle preserves shape."""
        mask = np.zeros((200, 200), dtype=np.uint8)
        mask[50:150, 50:150] = 255
        cleaned = morph_clean(mask, close_k=5, open_k=5)
        original_area = (mask > 0).sum()
        cleaned_area = (cleaned > 0).sum()
        assert abs(cleaned_area - original_area) / original_area < 0.05

    def test_make_warp_identity(self):
        """make_warp with no rotation/translation/scale gives identity."""
        M = make_warp(100, 100, 100, 100, 0.0, 1.0)
        expected = np.array([[1, 0, 0], [0, 1, 0]], dtype=np.float64)
        np.testing.assert_allclose(M, expected, atol=1e-10)

    def test_warp_contour_translation(self):
        """warp_contour with pure translation shifts all points."""
        contour = np.array([[10, 20], [30, 40], [50, 60]], dtype=np.float64)
        M = np.array([[1, 0, 5], [0, 1, -3]], dtype=np.float64)
        warped = warp_contour(contour, M)
        expected = np.array([[[15, 17]], [[35, 37]], [[55, 57]]], dtype=np.float64)
        np.testing.assert_allclose(warped, expected, atol=1e-10)

    def test_invert_warp_roundtrip(self):
        """invert_warp(M) composed with M gives identity."""
        M = make_warp(50, 50, 80, 90, 0.5, 1.2)
        M_inv = invert_warp(M)
        full = np.vstack([M, [0, 0, 1]])
        full_inv = np.vstack([M_inv, [0, 0, 1]])
        product = full_inv @ full
        np.testing.assert_allclose(product, np.eye(3), atol=1e-10)

    def test_layer_map_keys(self):
        """LAYER_MAP has all 4 expected material keys."""
        expected_keys = {"top_hBN", "graphene", "bottom_hBN", "graphite"}
        assert set(LAYER_MAP.keys()) == expected_keys
        assert LAYER_MAP["top_hBN"] == "10/0"
        assert LAYER_MAP["graphene"] == "11/0"
        assert LAYER_MAP["bottom_hBN"] == "12/0"
        assert LAYER_MAP["graphite"] == "13/0"


class TestSiftAlign:
    """Tests for align/scripts/sift_align.py — SIFT feature matching."""

    def _run(self, tmp):
        rc, out, err = run_flakedetect_script("align", "sift_align.py", [
            "--source", BOTTOM_PART,
            "--target", FULL_STACK_RAW,
            "--pixel-size", PIXEL_SIZE,
            "--output-dir", tmp,
        ])
        assert rc == 0, f"sift_align.py failed: {err}"
        return tmp

    def test_produces_warp_matrix(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._run(tmp)
            M = np.load(os.path.join(tmp, "warp_sift_bottom.npy"))
            assert M.shape == (2, 3)
            assert M.dtype == np.float64

    def test_sufficient_inliers(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._run(tmp)
            with open(os.path.join(tmp, "alignment_report.json")) as f:
                report = json.load(f)
            n = report["alignments"]["bottom"]["n_inliers"]
            assert n >= 20, f"Only {n} inliers"

    def test_scale_near_unity(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._run(tmp)
            with open(os.path.join(tmp, "alignment_report.json")) as f:
                report = json.load(f)
            scale = report["alignments"]["bottom"]["scale"]
            assert 0.9 < scale < 1.1, f"Scale {scale} outside range"


class TestSourceContour:
    """Tests for align/scripts/source_contour.py — flake contour extraction."""

    def _run(self, tmp):
        rc, out, err = run_flakedetect_script("align", "source_contour.py", [
            "--image", TOP_PART,
            "--mirror",
            "--output-dir", tmp,
        ])
        assert rc == 0, f"source_contour.py failed: {err}"
        return tmp

    def test_extracts_contour(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._run(tmp)
            contour = np.load(os.path.join(tmp, "source_contour.npy"))
            assert contour.ndim == 2
            assert contour.shape[1] == 2
            assert contour.shape[0] > 10

    def test_produces_mask(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._run(tmp)
            mask = cv2.imread(
                os.path.join(tmp, "source_mask.png"), cv2.IMREAD_GRAYSCALE
            )
            assert mask is not None
            unique = set(np.unique(mask))
            assert unique <= {0, 255}
