#!/usr/bin/env python
"""Tests for nanodevice:gdsalign pipeline scripts.

All tests use local fixtures in tests_resources/ml08/.
"""
import json
import os
import tempfile

import numpy as np
import pytest

from conftest import (
    FULL_STACK_RAW, PIXEL_SIZE, TEMPLATE_GDS, run_gdsalign_script,
)


class TestExtractMarkers:
    """Tests for extract_markers.py — GDS L5/0 marker pair extraction."""

    def test_extracts_4_pairs(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, out, err = run_gdsalign_script("extract_markers.py", [
                "--gds", TEMPLATE_GDS, "--output-dir", tmp,
            ])
            assert rc == 0, f"Script failed: {err}"
            with open(os.path.join(tmp, "gds_markers.json")) as f:
                data = json.load(f)
            assert len(data["pairs"]) == 4

    def test_pair_centers_match_known_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_gdsalign_script("extract_markers.py", [
                "--gds", TEMPLATE_GDS, "--output-dir", tmp,
            ])
            with open(os.path.join(tmp, "gds_markers.json")) as f:
                data = json.load(f)
            centers = {p["label"]: p["center_um"] for p in data["pairs"]}
            assert abs(centers["NE"][0] - 812.5) < 1.0
            assert abs(centers["NE"][1] - 812.5) < 1.0
            assert abs(centers["SW"][0] - 737.5) < 1.0
            assert abs(centers["SW"][1] - 737.5) < 1.0
            assert abs(centers["NW"][0] - 737.5) < 1.0
            assert abs(centers["NW"][1] - 812.5) < 1.0
            assert abs(centers["SE"][0] - 812.5) < 1.0
            assert abs(centers["SE"][1] - 737.5) < 1.0

    def test_each_pair_has_2_markers_with_bbox(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_gdsalign_script("extract_markers.py", [
                "--gds", TEMPLATE_GDS, "--output-dir", tmp,
            ])
            with open(os.path.join(tmp, "gds_markers.json")) as f:
                data = json.load(f)
            for pair in data["pairs"]:
                assert len(pair["markers"]) == 2
                for m in pair["markers"]:
                    assert "bbox" in m
                    assert len(m["bbox"]) == 2


class TestDetectMarkers:
    """Tests for detect_markers.py — template matching in microscope image."""

    def _run_extract_first(self, tmp):
        rc, _, err = run_gdsalign_script("extract_markers.py", [
            "--gds", TEMPLATE_GDS, "--output-dir", tmp,
        ])
        assert rc == 0, f"extract_markers failed: {err}"
        return os.path.join(tmp, "gds_markers.json")

    def test_detects_markers_in_ml08(self):
        with tempfile.TemporaryDirectory() as tmp:
            gds_markers = self._run_extract_first(tmp)
            rc, out, err = run_gdsalign_script("detect_markers.py", [
                "--image", FULL_STACK_RAW,
                "--pixel-size", PIXEL_SIZE,
                "--gds-markers", gds_markers,
                "--output-dir", tmp,
            ])
            assert rc == 0, f"Script failed: {err}"
            with open(os.path.join(tmp, "image_markers.json")) as f:
                data = json.load(f)
            assert data["status"] == "complete"
            assert len(data["detections"]) >= 3

    def test_detections_have_required_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            gds_markers = self._run_extract_first(tmp)
            run_gdsalign_script("detect_markers.py", [
                "--image", FULL_STACK_RAW,
                "--pixel-size", PIXEL_SIZE,
                "--gds-markers", gds_markers,
                "--output-dir", tmp,
            ])
            with open(os.path.join(tmp, "image_markers.json")) as f:
                data = json.load(f)
            for det in data["detections"]:
                assert "center_px" in det
                assert "center_um" in det
                assert "score" in det
                assert "rotation_deg" in det

    def test_produces_diagnostic_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            gds_markers = self._run_extract_first(tmp)
            run_gdsalign_script("detect_markers.py", [
                "--image", FULL_STACK_RAW,
                "--pixel-size", PIXEL_SIZE,
                "--gds-markers", gds_markers,
                "--output-dir", tmp,
            ])
            assert os.path.exists(os.path.join(tmp, "01_template.png"))
            assert os.path.exists(os.path.join(tmp, "03_detections.png"))


class TestAlignGds:
    """Tests for align_gds.py — marker correspondence + similarity transform."""

    def _run_pipeline_to_detect(self, tmp):
        run_gdsalign_script("extract_markers.py", [
            "--gds", TEMPLATE_GDS, "--output-dir", tmp,
        ])
        run_gdsalign_script("detect_markers.py", [
            "--image", FULL_STACK_RAW,
            "--pixel-size", PIXEL_SIZE,
            "--gds-markers", os.path.join(tmp, "gds_markers.json"),
            "--output-dir", tmp,
        ])
        return (
            os.path.join(tmp, "gds_markers.json"),
            os.path.join(tmp, "image_markers.json"),
        )

    def test_computes_transform(self):
        with tempfile.TemporaryDirectory() as tmp:
            gds_m, img_m = self._run_pipeline_to_detect(tmp)
            rc, out, err = run_gdsalign_script("align_gds.py", [
                "--gds-markers", gds_m,
                "--image-markers", img_m,
                "--output-dir", tmp,
            ])
            assert rc == 0, f"Script failed: {err}"
            with open(os.path.join(tmp, "gds_alignment_report.json")) as f:
                report = json.load(f)
            assert report["status"] == "complete"
            assert report["quality"]["inliers"] >= 3

    def test_warp_matrix_is_2x3(self):
        with tempfile.TemporaryDirectory() as tmp:
            gds_m, img_m = self._run_pipeline_to_detect(tmp)
            run_gdsalign_script("align_gds.py", [
                "--gds-markers", gds_m,
                "--image-markers", img_m,
                "--output-dir", tmp,
            ])
            M = np.load(os.path.join(tmp, "gds_warp.npy"))
            assert M.shape == (2, 3)

    def test_scale_near_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            gds_m, img_m = self._run_pipeline_to_detect(tmp)
            run_gdsalign_script("align_gds.py", [
                "--gds-markers", gds_m,
                "--image-markers", img_m,
                "--output-dir", tmp,
            ])
            with open(os.path.join(tmp, "gds_alignment_report.json")) as f:
                report = json.load(f)
            scale = report["transform"]["scale"]
            assert 0.8 < scale < 1.5, f"Scale {scale} outside range"

    def test_residual_below_threshold(self):
        with tempfile.TemporaryDirectory() as tmp:
            gds_m, img_m = self._run_pipeline_to_detect(tmp)
            run_gdsalign_script("align_gds.py", [
                "--gds-markers", gds_m,
                "--image-markers", img_m,
                "--output-dir", tmp,
            ])
            with open(os.path.join(tmp, "gds_alignment_report.json")) as f:
                report = json.load(f)
            assert report["quality"]["mean_residual_um"] < 2.0


class TestCommitGds:
    """Tests for commit_gds.py — warp image + contours into GDS coords."""

    def _run_full_pipeline(self, tmp):
        run_gdsalign_script("extract_markers.py", [
            "--gds", TEMPLATE_GDS, "--output-dir", tmp,
        ])
        run_gdsalign_script("detect_markers.py", [
            "--image", FULL_STACK_RAW, "--pixel-size", PIXEL_SIZE,
            "--gds-markers", os.path.join(tmp, "gds_markers.json"),
            "--output-dir", tmp,
        ])
        run_gdsalign_script("align_gds.py", [
            "--gds-markers", os.path.join(tmp, "gds_markers.json"),
            "--image-markers", os.path.join(tmp, "image_markers.json"),
            "--output-dir", tmp,
        ])
        return tmp

    def _make_synthetic_traces(self, tmp):
        """Create a minimal traces.json with a square contour."""
        traces = {
            "image": "full_stack_raw.jpg",
            "pixel_size_um": 0.087,
            "image_size_px": [4096, 3000],
            "image_size_um": [356.352, 261.0],
            "stack": ["top_hBN", "graphene", "bottom_hBN", "graphite"],
            "layer_map": {
                "top_hBN": "10/0",
                "graphene": "11/0",
                "bottom_hBN": "12/0",
                "graphite": "13/0",
            },
            "materials": {
                "graphite": [{
                    "id": 0,
                    "contour_px": [[100, 100], [200, 100], [200, 200], [100, 200]],
                    "contour_um": [[8.7, 8.7], [17.4, 8.7], [17.4, 17.4], [8.7, 17.4]],
                    "area_um2": 75.69,
                    "num_points": 4,
                }],
                "graphene": [],
                "bottom_hBN": [],
                "top_hBN": [],
            },
        }
        path = os.path.join(tmp, "traces.json")
        with open(path, "w") as f:
            json.dump(traces, f)
        return path

    def test_warp_only_produces_warped_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._run_full_pipeline(tmp)
            traces_path = self._make_synthetic_traces(tmp)
            rc, out, err = run_gdsalign_script("commit_gds.py", [
                "--warp", os.path.join(tmp, "gds_warp.npy"),
                "--traces", traces_path,
                "--image", FULL_STACK_RAW,
                "--pixel-size", PIXEL_SIZE,
                "--gds", TEMPLATE_GDS,
                "--output-dir", tmp,
                "--warp-only",
            ])
            assert rc == 0, f"Script failed: {err}"
            assert os.path.exists(os.path.join(tmp, "full_stack_gds.png"))
            assert os.path.exists(os.path.join(tmp, "traces_gds.json"))

    def test_transformed_contours_in_gds_coords(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._run_full_pipeline(tmp)
            traces_path = self._make_synthetic_traces(tmp)
            run_gdsalign_script("commit_gds.py", [
                "--warp", os.path.join(tmp, "gds_warp.npy"),
                "--traces", traces_path,
                "--image", FULL_STACK_RAW,
                "--pixel-size", PIXEL_SIZE,
                "--gds", TEMPLATE_GDS,
                "--output-dir", tmp,
                "--warp-only",
            ])
            with open(os.path.join(tmp, "traces_gds.json")) as f:
                data = json.load(f)
            for mat_name, mat_data in data["materials"].items():
                for trace in mat_data:
                    for pt in trace["contour_gds"]:
                        assert -2000 < pt[0] < 4000, \
                            f"{mat_name} x={pt[0]} out of range"
                        assert -2000 < pt[1] < 3000, \
                            f"{mat_name} y={pt[1]} out of range"
