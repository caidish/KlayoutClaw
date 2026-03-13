---
name: nanodevice:flakedetect:review
description: Agent review protocol for validating committed flake polygons in KLayout. Use after the commit step to visually inspect polygons against the microscope image, verify contour quality, and decide pass/fail/retry.
---

# nanodevice:flakedetect:review — Visual Validation Protocol

Pure agent workflow using existing skills and MCP tools. No custom scripts needed.

## When to Use

After the commit step has inserted polygons into KLayout. Review validates the actual committed result, not just overlay images.

## Prerequisites

- KLayout running with committed polygons (from the commit step)
- Background microscope image loaded (from the commit step)
- Combine output available: `<out>/combine/overlay_raw.png`, `overlay_lut.png`, `combine_report.json`
- Alignment report available: `<out>/align/alignment_report.json`

## Review Protocol

### Step 1: Screenshot the committed layout

Use MCP `screenshot` to capture the current KLayout viewport with polygons on the background image:

```
mcp__klayoutclaw__screenshot
```

Read the resulting PNG to see all layers overlaid on the microscope photo.

### Step 2: Inspect individual layers

Use the `display` skill to isolate each material layer for focused inspection:

```bash
# Show only top_hBN (layer 10/0) against the background
python skills/display/scripts/show_only.py 10/0

# Screenshot, then inspect

# Show only graphene (layer 11/0)
python skills/display/scripts/show_only.py 11/0

# Show only bottom_hBN (layer 12/0)
python skills/display/scripts/show_only.py 12/0

# Show only graphite (layer 13/0)
python skills/display/scripts/show_only.py 13/0

# Restore all layers
python skills/display/scripts/toggle_layer.py 10/0 11/0 12/0 13/0 --on
```

Take a screenshot after each `show_only` to compare each polygon against the flake features.

### Step 3: Compare with combine overlays

Read the overlay images from the combine step for cross-reference:
- `<out>/combine/overlay_raw.png` — contours on desaturated raw image
- `<out>/combine/overlay_lut.png` — contours on LUT image (if available)
- `<out>/combine/mask_composite.png` — color-coded mask overlay

These show the contours before coordinate transform. The committed KLayout polygons should match.

### Step 4: Structured visual assessment

Answer these specific questions about the KLayout screenshots:

**Q1 — Top hBN boundary:**
"Do the top_hBN polygons (layer 10/0) follow the flake boundary visible in the background image?"
Rate: tight fit / acceptable / poor fit

**Q2 — Graphene containment:**
"Is the graphene polygon (layer 11/0) fully inside the top_hBN polygon (layer 10/0)? Any graphene outside top_hBN indicates a problem."
Rate: fully contained / mostly contained / significant leakage

**Q3 — Graphite alignment:**
"Does the graphite polygon (layer 13/0) align with the dark strip visible in the image?"
Rate: aligned / offset / missing

**Q4 — Bottom hBN coverage:**
"Does the bottom_hBN polygon (layer 12/0) cover the correct underlying hBN region?"
Rate: good / partial / wrong region

**Q5 — Overall:**
"Rate this result: excellent / good / needs work."

### Step 5: Check quantitative metrics

Read the reports for numeric cross-checks:

**From `<out>/align/alignment_report.json`:**
- Forward Chamfer < 3 um → good, > 5 um → investigate
- IoU > 0.7 → good, < 0.5 → poor
- Outside fraction < 0.1 → good, > 0.2 → poor

**From `<out>/combine/combine_report.json`:**
- ECC correlation > 0.9 → raw/LUT registration is reliable
- Transform summary confirms correct warp applied per material

### Step 6: Decision

| Visual Assessment | Metrics | Decision |
|-------------------|---------|----------|
| excellent/good | metrics OK | **PASS** — detection complete |
| good | metrics borderline | **PASS** with note |
| needs work (alignment) | high chamfer / low IoU | **FAIL** → re-run align with different rotation or parameters, then redo combine → commit → review |
| needs work (detection) | — | **FAIL** → re-run specific detect script with adjusted parameters, then redo combine → commit → review |
| needs work (commit) | — | **FAIL** → re-run commit (coordinate transform or layer error) |

### Step 7: Document

Record the decision and reasoning. If FAIL, specify which step to retry and what to change.

## Vision Feedback Principles

1. **Structured prompts, not open-ended.** Specific questions get reliable answers.
2. **Show raw image as context.** The background image provides ground truth.
3. **Budget calls.** Max ~10 vision calls per review cycle.
4. **Vision for coarse judgment, metrics for precision.** Vision distinguishes "completely wrong" from "roughly right." Use numeric metrics for fine distinctions.
5. **Source images as ground truth.** Compare against the original microscope photos when debugging.

## Retry Guidelines

- **Alignment retry**: Try a different rotation from the sweep candidates, or widen the scale range.
- **Detection retry**: Adjust thresholds or use `--cluster-id` overrides. Check source image quality.
- **Max retries**: 2 per stage. If still failing after 2 retries, flag for manual intervention.
