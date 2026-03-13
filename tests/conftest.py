#!/usr/bin/env python
"""Shared fixtures and helpers for nanodevice test suites."""
import json
import os
import subprocess
import sys

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ML08_DIR = os.path.join(PROJECT_ROOT, "tests_resources", "ml08")
PIXEL_SIZE = "0.087"

BOTTOM_PART = os.path.join(ML08_DIR, "bottom_part.jpg")
TOP_PART = os.path.join(ML08_DIR, "top_part.jpg")
FULL_STACK_RAW = os.path.join(ML08_DIR, "full_stack_raw.jpg")
FULL_STACK_LUT = os.path.join(ML08_DIR, "full_stack_w_LUT.jpg")
TEMPLATE_GDS = os.path.join(ML08_DIR, "Template.gds")

FLAKEDETECT_DIR = os.path.join(
    PROJECT_ROOT, "skills", "nanodevice", "flakedetect"
)
GDSALIGN_DIR = os.path.join(
    PROJECT_ROOT, "skills", "nanodevice", "gdsalign", "scripts"
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run_flakedetect_script(stage, script_name, args, timeout=300):
    """Run a flakedetect sub-skill script.

    Args:
        stage: Sub-skill stage — one of "align", "detect", "combine",
               or "scripts" (for core.py imports via -c).
        script_name: Python script filename (e.g. "sift_align.py").
        args: List of CLI argument strings.
        timeout: Max seconds before killing the process.

    Returns:
        Tuple (returncode, stdout, stderr).
    """
    script_path = os.path.join(FLAKEDETECT_DIR, stage, "scripts", script_name)
    cmd = [sys.executable, script_path] + args
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stdout, r.stderr


def run_gdsalign_script(script_name, args, timeout=120):
    """Run a gdsalign script.

    Args:
        script_name: Python script filename (e.g. "extract_markers.py").
        args: List of CLI argument strings.
        timeout: Max seconds before killing the process.

    Returns:
        Tuple (returncode, stdout, stderr).
    """
    script_path = os.path.join(GDSALIGN_DIR, script_name)
    cmd = [sys.executable, script_path] + args
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stdout, r.stderr


def build_detections_json(detect_dir, align_dir, pixel_size="0.087"):
    """Assemble detections.json from individual *_result.json files.

    This mirrors what the orchestrator agent does between detect and
    combine stages.

    Args:
        detect_dir: Directory containing *_result.json, *_mask.png, *_contour.npy.
        align_dir: Directory containing footprint files (for top_hBN source).
        pixel_size: Pixel size string.

    Returns:
        Path to the written detections.json file.
    """
    materials = {}
    configs = [
        ("graphite",   "graphite_result.json",   "bottom_part", False),
        ("graphene",   "graphene_result.json",    "top_part",    True),
        ("bottom_hBN", "bottom_hbn_result.json",  "full_stack",  False),
        ("top_hBN",    "top_hbn_result.json",     "full_stack",  False),
    ]
    for mat_name, result_file, coord_sys, mirrored in configs:
        result_path = os.path.join(detect_dir, result_file)
        with open(result_path) as f:
            result = json.load(f)

        if mat_name == "bottom_hBN":
            prefix = "bottom_hbn"
        elif mat_name == "top_hBN":
            prefix = "top_hbn"
        else:
            prefix = mat_name.lower()

        materials[mat_name] = {
            "mask_file": f"{prefix}_mask.png",
            "contour_file": f"{prefix}_contour.npy",
            "area_px": result["area_px"],
            "area_um2": result["area_um2"],
            "coordinate_system": coord_sys,
            "mirrored": mirrored,
        }

    detections = {
        "pixel_size_um": float(pixel_size),
        "materials": materials,
    }

    out_path = os.path.join(detect_dir, "detections.json")
    with open(out_path, "w") as f:
        json.dump(detections, f, indent=2)
    return out_path
