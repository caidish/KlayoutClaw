#!/usr/bin/env python3
"""Enhance microscope-image local contrast in LAB L without altering geometry."""

from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np


def enhance_lab_l(
    image: np.ndarray,
    clip_limit: float = 2.0,
    tile_grid: tuple[int, int] = (8, 8),
) -> np.ndarray:
    """Apply CLAHE to LAB L while retaining the original LAB a/b channels."""
    if not isinstance(image, np.ndarray) or image.ndim != 3 or image.shape[2] != 3:
        raise ValueError("image must be an HxWx3 BGR array")
    if image.dtype != np.uint8:
        raise ValueError("image must use uint8 pixels")
    if clip_limit <= 0:
        raise ValueError("clip_limit must be positive")
    if (
        len(tile_grid) != 2
        or int(tile_grid[0]) <= 0
        or int(tile_grid[1]) <= 0
    ):
        raise ValueError("tile_grid values must be positive")

    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    l_chan, a_chan, b_chan = cv2.split(lab)
    clahe = cv2.createCLAHE(
        clipLimit=float(clip_limit),
        tileGridSize=(int(tile_grid[0]), int(tile_grid[1])),
    )
    enhanced_l = clahe.apply(l_chan)
    enhanced_lab = cv2.merge((enhanced_l, a_chan, b_chan))
    return cv2.cvtColor(enhanced_lab, cv2.COLOR_LAB2BGR)


def make_diagnostic(original: np.ndarray, enhanced: np.ndarray) -> np.ndarray:
    """Return a labeled, side-by-side BGR diagnostic image."""
    if original.shape != enhanced.shape:
        raise ValueError("diagnostic images must have identical shapes")
    left = original.copy()
    right = enhanced.copy()
    for canvas, label in ((left, "ORIGINAL"), (right, "CLAHE LAB-L")):
        cv2.rectangle(canvas, (0, 0), (260, 42), (0, 0, 0), -1)
        cv2.putText(
            canvas,
            label,
            (12, 30),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )
    return np.hstack((left, right))


def _write_image(path: Path, image: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(path), image):
        raise OSError(f"could not write image: {path}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--diagnostic", type=Path)
    parser.add_argument("--clip-limit", type=float, default=2.0)
    parser.add_argument("--tile-grid", type=int, nargs=2, default=(8, 8))
    args = parser.parse_args()

    image = cv2.imread(str(args.input), cv2.IMREAD_COLOR)
    if image is None:
        raise SystemExit(f"could not read input image: {args.input}")
    enhanced = enhance_lab_l(
        image,
        clip_limit=args.clip_limit,
        tile_grid=(args.tile_grid[0], args.tile_grid[1]),
    )
    _write_image(args.output, enhanced)
    if args.diagnostic is not None:
        _write_image(args.diagnostic, make_diagnostic(image, enhanced))
    print(
        f"enhanced={args.output} shape={enhanced.shape[1]}x{enhanced.shape[0]} "
        f"clip_limit={args.clip_limit} tile_grid={tuple(args.tile_grid)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
