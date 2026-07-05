"""bottom_hbn.py phase-3 unit tests."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

DETECT = Path(__file__).resolve().parents[2] / "skills" / "nanodevice_flakedetect_detect" / "scripts"


def _src() -> str:
    return (DETECT / "bottom_hbn.py").read_text(encoding="utf-8")


def test_docstring_does_not_claim_host_equals_hbn():
    src = _src()
    forbidden = [
        "host IS the bottom_hBN",
        "host is the bottom_hbn",
        "host = bottom_hbn",
    ]
    for f in forbidden:
        assert f.lower() not in src.lower(), (
            f"bottom_hbn.py docstring still claims '{f}'"
        )


def test_has_hbn_classification_step():
    src = _src()
    tree = ast.parse(src)
    func_names = {n.name for n in ast.walk(tree)
                  if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert any("classify" in n.lower() or "hbn_mask" in n.lower()
               or "extract_hbn" in n.lower()
               for n in func_names), (
        "bottom_hbn.py must define an hBN classification/extraction step "
        "distinct from compute_host"
    )


def test_dilation_uses_fixed_gt_convention():
    src = _src()
    assert "BOTTOM_HBN_DILATION_UM" in src, (
        "bottom_hbn.py must expose the fixed bottom hBN dilation radius as "
        "a named constant"
    )
    tree = ast.parse(src)
    values = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign):
            for target in n.targets:
                if (isinstance(target, ast.Name)
                        and target.id == "BOTTOM_HBN_DILATION_UM"
                        and isinstance(n.value, ast.Constant)):
                    values.append(n.value.value)
    assert values == [1.5], (
        "bottom_hbn.py must use the current fixed GT-convention dilation "
        f"of 1.5 um, got {values}"
    )


def test_low_confidence_threshold_relative_to_host_area():
    src = _src()
    # the historic 500 um^2 floor must be gone in any comparison context
    for line in src.splitlines():
        if "500" in line and ("<" in line or ">" in line):
            assert "host" in line.lower() or "frac" in line.lower(), (
                f"bottom_hbn.py 500-um² literal still in comparison: {line!r}"
            )
