#!/usr/bin/env python
"""Tests for nanodevice:gdsalign scripts."""
import json
import os
import subprocess
import sys
import tempfile

import numpy as np

SCRIPT_DIR = os.path.join(
    os.path.dirname(__file__), "..", "skills", "nanodevice", "gdsalign", "scripts"
)
GDS_PATH = "/Volumes/RandomData/Stacks/Template.gds"


def run_script(name, args):
    """Run a gdsalign script and return (returncode, stdout, stderr)."""
    cmd = [sys.executable, os.path.join(SCRIPT_DIR, name)] + args
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    return r.returncode, r.stdout, r.stderr


class TestExtractMarkers:
    def test_extracts_4_pairs(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, out, err = run_script("extract_markers.py", [
                "--gds", GDS_PATH, "--output-dir", tmp
            ])
            assert rc == 0, f"Script failed: {err}"
            with open(os.path.join(tmp, "gds_markers.json")) as f:
                data = json.load(f)
            assert len(data["pairs"]) == 4

    def test_pair_centers_match_known_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, _, _ = run_script("extract_markers.py", [
                "--gds", GDS_PATH, "--output-dir", tmp
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
            run_script("extract_markers.py", [
                "--gds", GDS_PATH, "--output-dir", tmp
            ])
            with open(os.path.join(tmp, "gds_markers.json")) as f:
                data = json.load(f)
            for pair in data["pairs"]:
                assert len(pair["markers"]) == 2
                for m in pair["markers"]:
                    assert "bbox" in m
                    assert len(m["bbox"]) == 2


ML08_IMAGE = "/Volumes/RandomData/Stacks/ML08/full_stack_raw.jpg"
ML08_PIXEL_SIZE = "0.087"


class TestDetectMarkers:
    def _run_extract_first(self, tmp):
        rc, _, err = run_script("extract_markers.py", [
            "--gds", GDS_PATH, "--output-dir", tmp
        ])
        assert rc == 0, f"extract_markers failed: {err}"
        return os.path.join(tmp, "gds_markers.json")

    def test_detects_markers_in_ml08(self):
        with tempfile.TemporaryDirectory() as tmp:
            gds_markers = self._run_extract_first(tmp)
            rc, out, err = run_script("detect_markers.py", [
                "--image", ML08_IMAGE,
                "--pixel-size", ML08_PIXEL_SIZE,
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
            run_script("detect_markers.py", [
                "--image", ML08_IMAGE,
                "--pixel-size", ML08_PIXEL_SIZE,
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
            run_script("detect_markers.py", [
                "--image", ML08_IMAGE,
                "--pixel-size", ML08_PIXEL_SIZE,
                "--gds-markers", gds_markers,
                "--output-dir", tmp,
            ])
            assert os.path.exists(os.path.join(tmp, "01_template.png"))
            assert os.path.exists(os.path.join(tmp, "03_detections.png"))


class TestAlignGds:
    def _run_pipeline_to_detect(self, tmp):
        run_script("extract_markers.py", [
            "--gds", GDS_PATH, "--output-dir", tmp
        ])
        run_script("detect_markers.py", [
            "--image", ML08_IMAGE,
            "--pixel-size", ML08_PIXEL_SIZE,
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
            rc, out, err = run_script("align_gds.py", [
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
            run_script("align_gds.py", [
                "--gds-markers", gds_m,
                "--image-markers", img_m,
                "--output-dir", tmp,
            ])
            M = np.load(os.path.join(tmp, "gds_warp.npy"))
            assert M.shape == (2, 3)

    def test_scale_near_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            gds_m, img_m = self._run_pipeline_to_detect(tmp)
            run_script("align_gds.py", [
                "--gds-markers", gds_m,
                "--image-markers", img_m,
                "--output-dir", tmp,
            ])
            with open(os.path.join(tmp, "gds_alignment_report.json")) as f:
                report = json.load(f)
            scale = report["transform"]["scale"]
            assert 0.8 < scale < 1.5, f"Scale {scale} outside expected range"

    def test_residual_below_threshold(self):
        with tempfile.TemporaryDirectory() as tmp:
            gds_m, img_m = self._run_pipeline_to_detect(tmp)
            run_script("align_gds.py", [
                "--gds-markers", gds_m,
                "--image-markers", img_m,
                "--output-dir", tmp,
            ])
            with open(os.path.join(tmp, "gds_alignment_report.json")) as f:
                report = json.load(f)
            assert report["quality"]["mean_residual_um"] < 2.0


ML08_TRACES = "/Volumes/RandomData/Stacks/ML08/output/combine/traces.json"


class TestCommitGds:
    def _run_full_pipeline(self, tmp):
        run_script("extract_markers.py", ["--gds", GDS_PATH, "--output-dir", tmp])
        run_script("detect_markers.py", [
            "--image", ML08_IMAGE, "--pixel-size", ML08_PIXEL_SIZE,
            "--gds-markers", os.path.join(tmp, "gds_markers.json"),
            "--output-dir", tmp,
        ])
        run_script("align_gds.py", [
            "--gds-markers", os.path.join(tmp, "gds_markers.json"),
            "--image-markers", os.path.join(tmp, "image_markers.json"),
            "--output-dir", tmp,
        ])
        return tmp

    def test_warp_only_produces_warped_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._run_full_pipeline(tmp)
            rc, out, err = run_script("commit_gds.py", [
                "--warp", os.path.join(tmp, "gds_warp.npy"),
                "--traces", ML08_TRACES,
                "--image", ML08_IMAGE,
                "--pixel-size", ML08_PIXEL_SIZE,
                "--gds", GDS_PATH,
                "--output-dir", tmp,
                "--warp-only",
            ])
            assert rc == 0, f"Script failed: {err}"
            assert os.path.exists(os.path.join(tmp, "full_stack_gds.png"))
            assert os.path.exists(os.path.join(tmp, "traces_gds.json"))

    def test_transformed_contours_in_gds_coords(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._run_full_pipeline(tmp)
            run_script("commit_gds.py", [
                "--warp", os.path.join(tmp, "gds_warp.npy"),
                "--traces", ML08_TRACES,
                "--image", ML08_IMAGE,
                "--pixel-size", ML08_PIXEL_SIZE,
                "--gds", GDS_PATH,
                "--output-dir", tmp,
                "--warp-only",
            ])
            with open(os.path.join(tmp, "traces_gds.json")) as f:
                data = json.load(f)
            for mat_name, mat_data in data["materials"].items():
                for trace in mat_data:
                    for pt in trace["contour_gds"]:
                        assert -2000 < pt[0] < 4000, f"{mat_name} x={pt[0]} out of range"
                        assert -2000 < pt[1] < 3000, f"{mat_name} y={pt[1]} out of range"
