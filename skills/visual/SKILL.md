---
name: klayoutclaw:visual
description: Capture the current KLayout layout as a PNG image for visual inspection. Use this skill whenever the user wants to see, view, screenshot, preview, or visually inspect the current layout. Also trigger when the user asks "what does it look like", "show me the layout", "take a screenshot", "capture the design", or needs a visual check of their GDS geometry.
---

# KLayout Visual Capture

Save the current KLayout layout to a temporary GDS file, convert it to PNG, and return both file paths for visual inspection.

## Prerequisites

- KLayout running with KlayoutClaw plugin (v0.3+)
- A layout with geometry must be open
- Python packages: `gdstk`, `matplotlib` (in the conda environment)

## Script

### capture.py — Capture layout as PNG

```bash
python scripts/capture.py [--output path.png] [--gds path.gds] [--dpi 200]
```

- `--output` — PNG output path (default: `/tmp/klayoutclaw_capture.png`)
- `--gds` — GDS output path (default: `/tmp/klayoutclaw_capture.gds`)
- `--dpi` — Image resolution (default: 200)

Returns the paths to both the GDS and PNG files, printed to stdout.

Example:
```bash
python scripts/capture.py
# Output:
# GDS saved: /tmp/klayoutclaw_capture.gds
# PNG saved: /tmp/klayoutclaw_capture.png
```

## How It Works

1. Calls `save_layout` via MCP to write the current layout to a temp GDS file
2. Runs `tools/gds_to_image.py` from the KlayoutClaw repo to convert GDS to PNG
3. Prints both file paths

The GDS-to-PNG conversion uses `gdstk` to parse the GDS and `matplotlib` to render all layers with distinct colors and a legend.

## After Capture

Use the Read tool on the PNG path to view the image directly in the conversation. This gives immediate visual feedback on layout geometry.
