# Runtime And Contract

## How To Read This File

Read this file before running any `nanodevice_flakedetect_sam` script,
configuring SAM2, interpreting candidate file names, or running on
Windows/PowerShell.

Use this file as the setup and command reference. It does not contain the visual
selection rules. After reading it:

- If selecting graphite/backgate, read `manual-prompt-workflow.md`, then
  `visual-review-failure-modes.md`.
- If selecting graphene, read `manual-prompt-workflow.md`,
  `graphene-overlap-prompts.md`, then `visual-review-failure-modes.md`.
- If running only bottom hBN or top hBN wrappers, this file is enough unless
  graphite/graphene candidate selection is also part of the task.

## SAM2 Lookup

By default the scripts look for SAM2 in this order:

1. `SAM2_ROOT` environment variable, if set.
2. Repository-local `tools/sam2-main`.
3. Legacy local path `D:/Users/liyiz/desktop_backup/shixi/sam2-main`.

Expected SAM2 files:

- config: `configs/sam2.1/sam2.1_hiera_b+.yaml`
- checkpoint: `model/sam2.1_hiera_base_plus.pt`

For GitHub sharing, put the SAM2 source tree at `tools/sam2-main`. The
checkpoint path expected by the wrappers is
`tools/sam2-main/model/sam2.1_hiera_base_plus.pt`. The checkpoint is larger than
GitHub's normal 100 MB file limit, so commit it only through Git LFS or
distribute it separately and instruct users to place it at that path.

## Candidate Outputs

Graphite and graphene write:

- `<material>_candidate_01_on_grid.png` ... `<material>_candidate_09_on_grid.png`
- `<material>_candidate_montage.png`
- `<material>_prompt_candidates.json`

Candidate images must show the raw/source image with a coordinate grid, prompt
points, and the SAM-produced mask overlay. The grid is mandatory because the
agent uses it to correct prompt locations.

Overlay conventions:

- Mask overlay: red.
- Positive prompt points: green.
- Negative prompt points: orange.
- Graphene graphite-on-top prior: thin yellow contour labelled `graphite prior`.

The graphite prior is visual-only. It must not constrain SAM, clip
`graphene_mask.png`, or change downstream combine output.

Candidate 9 is the original baseline/refined mask, not a SAM run. For graphene,
candidate 9 must be the exact `graphene_mask.png` selected by the baseline
detector from `00_graphene_candidates.png`, including any explicit
`--cluster-id` choice.

## Windows PowerShell Guard

On Windows/PowerShell, always set UTF-8 Python I/O before running these scripts:

```powershell
$env:PYTHONIOENCODING='utf-8'
```

This is a runtime guard, not an algorithm choice. Some detectors print area
units such as `um^2`; the default GBK console encoding can crash on those
characters. Treat that crash as an encoding/runtime issue, not a detection,
alignment, SAM, or scoring failure.

## Command Skeletons

Use `instrMCPdev` unless the user explicitly asks for another environment.

```powershell
conda run -n instrMCPdev python skills\nanodevice_flakedetect_sam\scripts\graphite.py `
  --image <bottom_part.jpg> --pixel-size <um/px> --output-dir <detect_dir>

conda run -n instrMCPdev python skills\nanodevice_flakedetect_sam\scripts\graphene.py `
  --image <top_part.jpg> --pixel-size <um/px> --mirror --output-dir <detect_dir>

conda run -n instrMCPdev python skills\nanodevice_flakedetect_sam\scripts\bottom_hbn.py `
  --image <bottom_part.jpg> --warp-matrix <align/warp_sift_bottom.npy> `
  --target-image <full_stack_raw.jpg> --pixel-size <um/px> --output-dir <detect_dir>

conda run -n instrMCPdev python skills\nanodevice_flakedetect_sam\scripts\top_hbn.py `
  --footprint-mask <align/footprint_mask.png> --footprint-contour <align/footprint_contour.npy> `
  --image <full_stack_raw.jpg> --pixel-size <um/px> --output-dir <detect_dir>
```

Top hBN remains the align footprint. The SAM wrapper exists only for interface
compatibility and must not create top-hBN prompt candidates.
