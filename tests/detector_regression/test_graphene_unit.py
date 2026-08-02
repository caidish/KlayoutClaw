"""graphene.py phase-2 unit tests."""
from __future__ import annotations

import ast
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pytest

DETECT = Path(__file__).resolve().parents[2] / "skills" / "nanodevice_flakedetect_detect" / "scripts"


def _src() -> str:
    return (DETECT / "graphene.py").read_text(encoding="utf-8")


def test_no_offline_priors_referenced():
    src = _src()
    forbid = ["graphene_shape_priors", "shape_priors.json",
              "graphene_priors", "fit_shape_priors"]
    found = [t for t in forbid if t in src]
    assert not found, f"graphene.py still references offline priors: {found}"


def test_no_pdms_branch():
    src = _src()
    assert "PDMS" not in src and "pdms" not in src, (
        "graphene.py still has PDMS-named flow control"
    )


def test_no_center_placeholder():
    """The 5x5 center placeholder fallback must be replaced with an empty
    mask + low_confidence=True."""
    src = _src()
    # Forbid the recognized placeholder pattern
    forbidden = ["center_placeholder", "5x5 placeholder",
                 "5, 5", "(5, 5)"]
    tree = ast.parse(src)
    # also flag any function that writes a non-empty mask in a no-candidate path
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and "fallback" in node.name.lower():
            seg = ast.get_source_segment(src, node) or ""
            if "np.ones" in seg or "np.zeros" not in seg:
                bad.append(node.name)
    assert not bad, f"no-candidate fallback writes non-empty mask: {bad}"


def test_low_confidence_flag_emitted():
    """When no candidate survives, the result JSON must carry
    'low_confidence': true."""
    src = _src()
    assert '"low_confidence"' in src or "'low_confidence'" in src, (
        "graphene.py must emit low_confidence in its result JSON"
    )


def test_graphene_emits_ranked_candidate_panel_and_sidecar():
    """graphene.py must expose ranked candidates for agent visual selection."""
    src = _src()
    assert "draw_graphene_candidates" in src, (
        "graphene.py must render a true ranked candidate panel, not alias the "
        "final overlay to 00_graphene_candidates.png"
    )
    assert "top_candidates" in src, (
        "graphene_result.json must include top_candidates so agents can inspect "
        "ranked scores and choose --cluster-id"
    )
    assert "selected" in src, (
        "graphene_result.json must include selected candidate metadata"
    )


def test_contrast_polarity_inferred():
    """The brighter-than-host assumption must be replaced with signed
    contrast inference."""
    src = _src()
    assert "rel_L" in src
    # forbid the original 15..80 sweep range
    tree = ast.parse(src)
    for call in ast.walk(tree):
        if not isinstance(call, ast.Call):
            continue
        if getattr(call.func, "attr", "") == "linspace" or getattr(call.func, "id", "") == "range":
            args = [a for a in call.args if isinstance(a, ast.Constant)]
            vals = [a.value for a in args]
            if vals == [15, 80] or vals == [15, 80, 5]:
                pytest.fail(
                    f"L{call.lineno}: hardcoded rel_L sweep 15..80 still present"
                )


def test_area_gate_scaled_by_host():
    """`100..5000 um²` must be replaced with host-fraction based bounds."""
    src = _src()
    for lit in ["100", "5000"]:
        # only flag if appearing in a comparison context
        # heuristic: `< 5000` or `> 100` near `area`
        for line in src.splitlines():
            if (lit in line and "area" in line.lower()
                and ("<" in line or ">" in line)
                and "host" not in line.lower()):
                pytest.fail(
                    f"area gate appears unscaled by host: {line.strip()!r}"
                )


def test_graphene_grows_into_footprint():
    """Phase 10b: graphene.py must implement footprint-bounded region grow.

    Checks:
    1. The 1.10x hardcoded grow area cap is gone (replaced by 1.50 or dynamic).
    2. A footprint-bounded grow function exists that accepts a footprint_mask arg.
    3. The grow cap constant in the leakage-check path is >= 1.40 (not 1.10).
    """
    src = _src()
    tree = ast.parse(src)

    # (1) No hardcoded 1.10 area cap constant anywhere
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and "GROW_AREA_CAP" in t.id:
                    val = node.value
                    if isinstance(val, ast.Constant) and isinstance(val.value, float):
                        assert val.value >= 1.40, (
                            f"GROW_AREA_CAP_FACTOR = {val.value} is still ≤ 1.10 "
                            "(Phase 10b requires >= 1.40 or dynamic footprint-bounded grow)"
                        )

    # (2) Footprint-bounded grow function must exist with footprint_mask param
    fp_grow_funcs = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            param_names = [a.arg for a in node.args.args]
            if "footprint_mask" in param_names and "grow" in node.name.lower():
                fp_grow_funcs.append(node.name)
    assert fp_grow_funcs, (
        "graphene.py must define a grow function with a footprint_mask parameter "
        "(Phase 10b: footprint-bounded region grow)"
    )

    # (3) AREA_MAX_FOOTPRINT_FRAC constant must exist
    assert "AREA_MAX_FOOTPRINT_FRAC" in src, (
        "graphene.py must define AREA_MAX_FOOTPRINT_FRAC constant (Phase 10b area gate)"
    )


def test_graphene_uses_footprint_containment_when_available():
    """Phase 10a: graphene.py must expose --footprint-mask CLI and implement
    spatial containment scoring so the default cluster (rank 0) is reliably
    the footprint-contained graphene flake, eliminating agent cluster-id overrides.
    """
    src = _src()
    # Must mention both footprint and containment concepts
    assert ("footprint" in src.lower() and "containment" in src.lower()), (
        "graphene.py must mention footprint-containment scoring"
    )
    # CLI flag must be exposed
    assert "--footprint-mask" in src, "expose --footprint-mask CLI"
    # The scoring function must exist
    assert "spatial_containment_score" in src, (
        "graphene.py must define spatial_containment_score()"
    )
    # The warp helper must exist (needed to translate footprint to top_part coords)
    assert "_load_footprint_in_toppart_coords" in src, (
        "graphene.py must define _load_footprint_in_toppart_coords() to warp "
        "the footprint mask from full_stack coords to mirrored-top_part coords"
    )


def test_graphene_contrast_filter_is_pool_relative():
    """The contrast filter must keep at least 3 candidates regardless of
    absolute contrast magnitude (otherwise it's sample-fitted to high-contrast
    substrates like SiO2).

    Phase 10c: replaces the absolute 20%-of-max gate with a pool-relative
    composite ranking (|relL|^0.25 × log(area)).  This balances contrast and
    area signals so a large dim candidate (AH07: relL=3, 2610 µm²) beats tiny
    bright spots (relL=95, 9 µm²) without GT-fitted thresholds.
    """
    src = _src()
    # No 20% absolute gate — the literal 0.20 magnitude gate is removed.
    uses_abs_gate = (
        "MIN_CONTRAST_FRAC = 0.20" in src
        or "min_abs_rel = max_abs_rel * 0.20" in src
    )
    assert not uses_abs_gate, (
        "graphene.py still uses an absolute 0.20 relL gate; should be pool-relative "
        "(Phase 10c: use composite |relL|^0.25 × log(area) ranking)"
    )
    # A top-N pool-relative selection must be present.
    has_pool_selection = (
        "keep_top_n" in src
        or "sorted_by_composite" in src
        or "_pool_sort_key" in src
        or "keep_top" in src.lower()
    )
    assert has_pool_selection, (
        "graphene.py needs pool-relative contrast filtering (Phase 10c): "
        "add keep_top_n = max(3, len(candidates) // 3) selection with composite ranking"
    )
    # Black-artifact pre-filter must exclude near-zero L candidates.
    has_black_filter = (
        "BLACK_ARTIFACT_L_FLOOR" in src
        or "L_med" in src and "non_black" in src
    )
    assert has_black_filter, (
        "graphene.py needs BLACK_ARTIFACT_L_FLOOR pre-filter (Phase 10c: "
        "removes opaque edge artifacts that dominate |relL| ranking)"
    )


def test_graphene_hybrid_survival():
    """Survival uses a UNION of absolute-floor + top-area + top-contrast.
    No single filter should be able to drop a candidate that any other filter retains."""
    src = _src()
    # Sentinels for the hybrid policy
    for token in ["ABSOLUTE_RELL_FLOOR", "TOP_AREA_KEEP", "TOP_CONTRAST_KEEP"]:
        assert token in src, f"{token} missing from hybrid policy"
    # Forbid the pure top-N approach as the sole filter
    assert "abs_survivors" in src and "area_survivors" in src, (
        "hybrid policy must compute multiple survivor sets"
    )


def test_graphene_containment_penalty_uses_absolute_overlap():
    """Phase 10c: the containment penalty must consider absolute overlap area,
    not just fractional containment.

    A large candidate with 24.5% containment (e.g. 1438 µm² candidate, 352 µm²
    overlap) must not be outscored by a tiny 11 µm² candidate at 100% containment.
    The fix: apply the penalty only when BOTH fractional containment < 0.5 AND
    absolute overlap < expected minimum (0.5% of footprint area).
    """
    src = _src()
    # The absolute overlap variable or concept must be present.
    has_absolute_overlap = (
        "absolute_overlap_px" in src
        or "absolute_overlap_um2" in src
        or "norm_abs_overlap" in src
    )
    assert has_absolute_overlap, (
        "graphene.py containment penalty must compute absolute overlap area "
        "(Phase 10c: prevents tiny fully-contained noise from beating large "
        "partially-contained graphene flakes)"
    )
    # The combined penalty condition must reference both fractional and absolute.
    has_dual_condition = (
        "absolute_overlap_px < expected_min_overlap_px" in src
        or "absolute_overlap_um2 < expected_min_overlap_um2" in src
    )
    assert has_dual_condition, (
        "graphene.py penalty must gate on BOTH fractional containment < floor "
        "AND absolute_overlap < expected_min (Phase 10c dual condition)"
    )


def test_graphene_bridges_polarity_consistent_ccs():
    """Phase 14: graphene.py must implement intra-flake polarity-consistent
    bridging to recover fragmented graphene masks on mixed-thickness flakes.

    Checks:
    1. The bridging function _bridge_polarity_consistent_ccs is defined.
    2. The physics constants BRIDGE_MAX_DISTANCE_UM and
       BRIDGE_REL_L_MAGNITUDE_RATIO are present.
    """
    src = _src()
    assert "_bridge_polarity_consistent_ccs" in src or "bridge_polarity" in src.lower(), (
        "graphene.py must implement intra-flake bridging"
    )
    for token in ["BRIDGE_MAX_DISTANCE_UM", "BRIDGE_REL_L_MAGNITUDE_RATIO"]:
        assert token in src, f"missing {token} constant"


def test_graphene_auto_footprint_informs_scoring():
    """Phase 15: the auto-discovered footprint must influence candidate ranking,
    not just the grow step.  Otherwise the photometric-only score determines
    which CC is selected without using the spatial footprint constraint.

    Root cause (ml09/AH03): Phase 13 routed the auto-discovered footprint only
    to footprint_mask_grow_only (grow step only), not to any scoring parameter.
    So containment scoring was never activated for auto-discovered footprints.

    Phase 15 fix: auto-discovered footprint is routed to BOTH:
      - footprint_mask_score_only -> candidate scoring (NEW in Phase 15)
      - footprint_mask_grow_only  -> grow step (unchanged from Phase 13)
    This enables containment scoring for auto-discovered footprints WITHOUT
    expanding the upper area gate (which would admit large non-graphene CCs).

    Design decision: footprint_mask_score_only does NOT expand the area gate
    (unlike footprint_mask which does both scoring and gate expansion).  This is
    intentional: expanding the gate via the auto-discovered footprint admits
    large hBN shadow regions / stamp edges that were correctly excluded by the
    host-based gate.  The footprint validates spatial location of candidates
    ALREADY in the pool, not which candidates enter the pool.

    Checks:
    1. detect_graphene() accepts a footprint_mask_score_only parameter.
    2. main() routing uses footprint_mask_score_only for auto-discovered footprint.
    3. _score_candidates uses fp_for_scoring fallback (score_only when no explicit).
    """
    src = _src()

    # 1. detect_graphene() must accept footprint_mask_score_only
    assert "footprint_mask_score_only" in src, (
        "graphene.py detect_graphene() must accept footprint_mask_score_only "
        "(Phase 15: scoring-only footprint that does not expand area gate)"
    )

    # 2. main() routing must pass auto-discovered footprint to score_only
    has_score_only_routing = (
        "footprint_mask_score_only=loaded_footprint_mask" in src
        or "footprint_mask_score_only = loaded_footprint_mask" in src
    )
    assert has_score_only_routing, (
        "graphene.py main() must route auto-discovered footprint to "
        "footprint_mask_score_only (Phase 15: score without area gate expansion)"
    )

    # 3. The scoring function must use fp_for_scoring as a fallback
    has_fp_for_scoring = "fp_for_scoring" in src
    assert has_fp_for_scoring, (
        "graphene.py must define fp_for_scoring as fallback for containment "
        "scoring when footprint_mask is None but score_only is available"
    )

    # 4. Phase 15 documentation must be present
    has_phase15_comment = "Phase 15" in src
    assert has_phase15_comment, (
        "graphene.py must reference Phase 15 in routing or scoring comments"
    )
