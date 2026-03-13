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
    SLOW_TEST_TIMEOUT, run_flakedetect_script, build_detections_json,
)


# ---------------------------------------------------------------------------
# Module-level shared pipeline — every slow stage runs exactly once
# ---------------------------------------------------------------------------

class _SharedPipeline:
    """Lazy, incremental pipeline runner shared across all slow test classes.

    Each stage runs at most once.  Dependency order is enforced by the
    ``ensure(stage)`` method, which recursively ensures prerequisites.
    """

    _PREREQS = {
        "source_contour": [],
        "footprint": ["source_contour"],
        "sweep": ["footprint"],
        "refine": ["sweep"],
        "sift_align": [],
        "graphite": [],
        "graphene": [],
        "bottom_hbn": ["footprint"],
        "top_hbn": ["footprint"],
        "detections": ["graphite", "graphene", "bottom_hbn", "top_hbn"],
        "ecc_register": [],
        "transform": ["sift_align", "refine", "detections"],
        "overlay": ["transform"],
    }

    def __init__(self):
        self._dir = None
        self._completed = set()

    @property
    def dir(self):
        if self._dir is None:
            self._dir = tempfile.mkdtemp(prefix="test_flakedetect_shared_")
        return self._dir

    def ensure(self, stage):
        """Ensure *stage* and all its prerequisites have been run."""
        if stage in self._completed:
            return self.dir
        for dep in self._PREREQS.get(stage, []):
            self.ensure(dep)
        getattr(self, f"_run_{stage}")()
        self._completed.add(stage)
        return self.dir

    # -- stage runners -------------------------------------------------------

    def _run_source_contour(self):
        rc, _, err = run_flakedetect_script("align", "source_contour.py", [
            "--image", TOP_PART, "--mirror", "--output-dir", self.dir,
        ])
        assert rc == 0, f"source_contour.py failed: {err}"

    def _run_footprint(self):
        rc, _, err = run_flakedetect_script("align", "footprint.py", [
            "--source", TOP_PART, "--target", FULL_STACK_RAW, "--mirror",
            "--source-contour", os.path.join(self.dir, "source_contour.npy"),
            "--source-mask", os.path.join(self.dir, "source_mask.png"),
            "--pixel-size", PIXEL_SIZE, "--output-dir", self.dir,
        ])
        assert rc == 0, f"footprint.py failed: {err}"

    def _run_sweep(self):
        rc, _, err = run_flakedetect_script("align", "sweep.py", [
            "--source-contour", os.path.join(self.dir, "source_contour.npy"),
            "--source-mask", os.path.join(self.dir, "source_mask.png"),
            "--footprint-contour", os.path.join(self.dir, "footprint_contour.npy"),
            "--footprint-mask", os.path.join(self.dir, "footprint_mask.png"),
            "--target-image", FULL_STACK_RAW,
            "--pixel-size", PIXEL_SIZE, "--output-dir", self.dir,
        ])
        assert rc == 0, f"sweep.py failed: {err}"

    def _run_refine(self):
        rc, _, err = run_flakedetect_script("align", "refine.py", [
            "--source-contour", os.path.join(self.dir, "source_contour.npy"),
            "--source-mask", os.path.join(self.dir, "source_mask.png"),
            "--footprint-contour", os.path.join(self.dir, "footprint_contour.npy"),
            "--footprint-mask", os.path.join(self.dir, "footprint_mask.png"),
            "--target-image", FULL_STACK_RAW,
            "--rot-hint", "108.8",
            "--pixel-size", PIXEL_SIZE, "--output-dir", self.dir,
        ], timeout=SLOW_TEST_TIMEOUT)
        assert rc == 0, f"refine.py failed: {err}"

    def _run_sift_align(self):
        rc, _, err = run_flakedetect_script("align", "sift_align.py", [
            "--source", BOTTOM_PART, "--target", FULL_STACK_RAW,
            "--pixel-size", PIXEL_SIZE, "--output-dir", self.dir,
        ])
        assert rc == 0, f"sift_align.py failed: {err}"

    def _run_graphite(self):
        rc, _, err = run_flakedetect_script("detect", "graphite.py", [
            "--image", BOTTOM_PART, "--pixel-size", PIXEL_SIZE,
            "--output-dir", self.dir,
        ])
        assert rc == 0, f"graphite.py failed: {err}"

    def _run_graphene(self):
        rc, _, err = run_flakedetect_script("detect", "graphene.py", [
            "--image", TOP_PART, "--pixel-size", PIXEL_SIZE,
            "--mirror", "--output-dir", self.dir,
        ])
        assert rc == 0, f"graphene.py failed: {err}"

    def _run_bottom_hbn(self):
        rc, _, err = run_flakedetect_script("detect", "bottom_hbn.py", [
            "--image", FULL_STACK_RAW,
            "--footprint-mask", os.path.join(self.dir, "footprint_mask.png"),
            "--pixel-size", PIXEL_SIZE, "--output-dir", self.dir,
        ])
        assert rc == 0, f"bottom_hbn.py failed: {err}"

    def _run_top_hbn(self):
        rc, _, err = run_flakedetect_script("detect", "top_hbn.py", [
            "--footprint-mask", os.path.join(self.dir, "footprint_mask.png"),
            "--image", FULL_STACK_RAW,
            "--pixel-size", PIXEL_SIZE, "--output-dir", self.dir,
        ])
        assert rc == 0, f"top_hbn.py failed: {err}"

    def _run_detections(self):
        build_detections_json(self.dir, self.dir, PIXEL_SIZE)

    def _run_ecc_register(self):
        rc, _, err = run_flakedetect_script("combine", "ecc_register.py", [
            "--raw", FULL_STACK_RAW, "--lut", FULL_STACK_LUT,
            "--output-dir", self.dir,
        ])
        assert rc == 0, f"ecc_register.py failed: {err}"

    def _run_transform(self):
        rc, _, err = run_flakedetect_script("combine", "transform.py", [
            "--detections", os.path.join(self.dir, "detections.json"),
            "--align-dir", self.dir,
            "--image", FULL_STACK_RAW,
            "--pixel-size", PIXEL_SIZE, "--output-dir", self.dir,
        ])
        assert rc == 0, f"transform.py failed: {err}"

    def _run_overlay(self):
        rc, _, err = run_flakedetect_script("combine", "overlay.py", [
            "--traces", os.path.join(self.dir, "traces.json"),
            "--raw", FULL_STACK_RAW,
            "--output-dir", self.dir,
        ])
        assert rc == 0, f"overlay.py failed: {err}"


_pipeline = _SharedPipeline()


# =========================================================================
# Fast tests — independent temp dirs, no @pytest.mark.slow
# =========================================================================

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


class TestGraphite:
    """Tests for detect/scripts/graphite.py — graphite strip detection."""

    def _run(self, tmp):
        rc, out, err = run_flakedetect_script("detect", "graphite.py", [
            "--image", BOTTOM_PART,
            "--pixel-size", PIXEL_SIZE,
            "--output-dir", tmp,
        ])
        assert rc == 0, f"graphite.py failed: {err}"
        return tmp

    def test_detects_graphite(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._run(tmp)
            assert os.path.exists(os.path.join(tmp, "graphite_mask.png"))
            assert os.path.exists(os.path.join(tmp, "graphite_contour.npy"))
            assert os.path.exists(os.path.join(tmp, "graphite_result.json"))

    def test_area_reasonable(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._run(tmp)
            with open(os.path.join(tmp, "graphite_result.json")) as f:
                result = json.load(f)
            assert result["area_um2"] > 0


class TestGraphene:
    """Tests for detect/scripts/graphene.py — graphene detection."""

    def _run(self, tmp):
        rc, out, err = run_flakedetect_script("detect", "graphene.py", [
            "--image", TOP_PART,
            "--pixel-size", PIXEL_SIZE,
            "--mirror",
            "--output-dir", tmp,
        ])
        assert rc == 0, f"graphene.py failed: {err}"
        return tmp

    def test_detects_graphene(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._run(tmp)
            assert os.path.exists(os.path.join(tmp, "graphene_mask.png"))
            assert os.path.exists(os.path.join(tmp, "graphene_contour.npy"))
            assert os.path.exists(os.path.join(tmp, "graphene_result.json"))

    def test_area_positive(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._run(tmp)
            with open(os.path.join(tmp, "graphene_result.json")) as f:
                result = json.load(f)
            assert result["area_um2"] > 0


class TestEccRegister:
    """Tests for combine/scripts/ecc_register.py — raw↔LUT registration."""

    def test_produces_combine_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, out, err = run_flakedetect_script("combine", "ecc_register.py", [
                "--raw", FULL_STACK_RAW,
                "--lut", FULL_STACK_LUT,
                "--output-dir", tmp,
            ])
            assert rc == 0, f"ecc_register.py failed: {err}"
            with open(os.path.join(tmp, "combine_report.json")) as f:
                report = json.load(f)
            assert "raw2lut" in report
            assert "dx" in report["raw2lut"]
            assert "dy" in report["raw2lut"]
            assert "ecc_correlation" in report["raw2lut"]

    def test_correlation_positive(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, _, _ = run_flakedetect_script("combine", "ecc_register.py", [
                "--raw", FULL_STACK_RAW,
                "--lut", FULL_STACK_LUT,
                "--output-dir", tmp,
            ])
            with open(os.path.join(tmp, "combine_report.json")) as f:
                report = json.load(f)
            assert report["raw2lut"]["ecc_correlation"] > 0


# =========================================================================
# Slow tests — all share _pipeline singleton to avoid redundant runs
# =========================================================================

@pytest.mark.slow
class TestFootprint:
    """Tests for align/scripts/footprint.py — target footprint via K-means + GrabCut."""

    def test_produces_footprint(self):
        d = _pipeline.ensure("footprint")
        assert os.path.exists(os.path.join(d, "footprint_mask.png"))
        assert os.path.exists(os.path.join(d, "footprint_contour.npy"))

    def test_footprint_contour_closed(self):
        d = _pipeline.ensure("footprint")
        contour = np.load(os.path.join(d, "footprint_contour.npy"))
        pts = contour.reshape(-1, 2)
        dist = np.linalg.norm(pts[0] - pts[-1])
        assert dist < 5.0, f"Contour not closed: gap = {dist:.1f} px"


@pytest.mark.slow
class TestSweep:
    """Tests for align/scripts/sweep.py — coarse rotation search."""

    def test_produces_candidates(self):
        d = _pipeline.ensure("sweep")
        candidates = glob.glob(os.path.join(d, "candidate_*.png"))
        assert len(candidates) >= 8, f"Only {len(candidates)} candidates"

    def test_sweep_candidates_in_report(self):
        d = _pipeline.ensure("sweep")
        with open(os.path.join(d, "alignment_report.json")) as f:
            report = json.load(f)
        assert "sweep_candidates" in report
        assert len(report["sweep_candidates"]) >= 8


@pytest.mark.slow
class TestRefine:
    """Tests for align/scripts/refine.py — fine alignment optimization."""

    def test_produces_warp_top(self):
        d = _pipeline.ensure("refine")
        M = np.load(os.path.join(d, "warp_top.npy"))
        assert M.shape == (2, 3)

    def test_meets_acceptance_thresholds(self):
        d = _pipeline.ensure("refine")
        with open(os.path.join(d, "alignment_report.json")) as f:
            report = json.load(f)
        top = report["alignments"]["top"]
        assert top["iou"] > 0.5, f"IoU {top['iou']:.3f} too low"
        assert top["fwd_chamfer_um"] < 4.0, \
            f"Chamfer {top['fwd_chamfer_um']:.2f} um too high"


@pytest.mark.slow
class TestBottomHbn:
    """Tests for detect/scripts/bottom_hbn.py — bottom hBN detection."""

    def test_detects_bottom_hbn(self):
        d = _pipeline.ensure("bottom_hbn")
        assert os.path.exists(os.path.join(d, "bottom_hbn_mask.png"))
        assert os.path.exists(os.path.join(d, "bottom_hbn_contour.npy"))
        assert os.path.exists(os.path.join(d, "bottom_hbn_result.json"))


@pytest.mark.slow
class TestTopHbn:
    """Tests for detect/scripts/top_hbn.py — top hBN = footprint copy."""

    def test_copies_footprint(self):
        d = _pipeline.ensure("top_hbn")
        fp_mask = cv2.imread(
            os.path.join(d, "footprint_mask.png"), cv2.IMREAD_GRAYSCALE
        )
        top_mask = cv2.imread(
            os.path.join(d, "top_hbn_mask.png"), cv2.IMREAD_GRAYSCALE
        )
        np.testing.assert_array_equal(fp_mask, top_mask)


@pytest.mark.slow
class TestTransformAndOverlay:
    """Tests for combine/scripts/transform.py + overlay.py.

    Runs the full pipeline (align + detect + transform + overlay) through
    the shared _pipeline singleton.
    """

    # --- Transform assertions ---

    def test_produces_traces_json(self):
        d = _pipeline.ensure("overlay")
        with open(os.path.join(d, "traces.json")) as f:
            traces = json.load(f)
        expected = {"top_hBN", "graphene", "bottom_hBN", "graphite"}
        assert set(traces["materials"].keys()) == expected

    def test_contours_have_px_and_um(self):
        d = _pipeline.ensure("overlay")
        with open(os.path.join(d, "traces.json")) as f:
            traces = json.load(f)
        for mat_name, mat_traces in traces["materials"].items():
            for trace in mat_traces:
                assert "contour_px" in trace, f"{mat_name} missing contour_px"
                assert "contour_um" in trace, f"{mat_name} missing contour_um"
                assert len(trace["contour_px"]) > 0

    def test_layer_map_correct(self):
        d = _pipeline.ensure("overlay")
        with open(os.path.join(d, "traces.json")) as f:
            traces = json.load(f)
        lm = traces["layer_map"]
        assert lm["top_hBN"] == "10/0"
        assert lm["graphene"] == "11/0"
        assert lm["bottom_hBN"] == "12/0"
        assert lm["graphite"] == "13/0"

    # --- Overlay assertions ---

    def test_produces_overlay_images(self):
        d = _pipeline.ensure("overlay")
        assert os.path.exists(os.path.join(d, "overlay_raw.png"))

    def test_produces_mask_composite(self):
        d = _pipeline.ensure("overlay")
        assert os.path.exists(os.path.join(d, "mask_composite.png"))
