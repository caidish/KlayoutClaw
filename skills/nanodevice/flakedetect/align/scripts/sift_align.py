#!/usr/bin/env python
"""Same-substrate SIFT+RANSAC alignment.

Aligns two images taken from the same substrate (e.g., bottom_part
and full_stack on SiO2) using SIFT feature matching. Falls back to
ECC translation if too few SIFT matches.

Exit codes:
    0 — Alignment successful (>=20 inliers)
    1 — Error
    2 — Too few matches (<20 inliers) — use Chamfer pipeline instead

Usage:
    conda run -n base python sift_align.py \
        --source <image> --target <image> \
        --pixel-size <um/px> --output-dir <path>
"""

import argparse
import json
import math
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
from core import mask_centroid


def align_sift(ref_image, mov_image, n_features=2000, ratio_thresh=0.7):
    """Align two same-substrate images via SIFT feature matching + RANSAC.

    Returns:
        (warp_matrix, n_inliers, scale, rotation_deg, good_matches,
         kp_ref, kp_mov)
    """
    if ref_image.ndim == 3:
        gray_ref = cv2.cvtColor(ref_image, cv2.COLOR_BGR2GRAY)
    else:
        gray_ref = ref_image

    if mov_image.ndim == 3:
        gray_mov = cv2.cvtColor(mov_image, cv2.COLOR_BGR2GRAY)
    else:
        gray_mov = mov_image

    sift = cv2.SIFT_create(nfeatures=n_features)
    kp_ref, des_ref = sift.detectAndCompute(gray_ref, None)
    kp_mov, des_mov = sift.detectAndCompute(gray_mov, None)

    if des_ref is None or des_mov is None or len(kp_ref) < 4 or len(kp_mov) < 4:
        return None, 0, 1.0, 0.0, [], kp_ref, kp_mov

    bf = cv2.BFMatcher(cv2.NORM_L2)
    raw_matches = bf.knnMatch(des_ref, des_mov, k=2)

    good = []
    for pair in raw_matches:
        if len(pair) == 2:
            m, n = pair
            if m.distance < ratio_thresh * n.distance:
                good.append(m)

    if len(good) < 10:
        return None, len(good), 1.0, 0.0, good, kp_ref, kp_mov

    pts_ref = np.float32([kp_ref[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    pts_mov = np.float32([kp_mov[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)

    warp_matrix, inliers = cv2.estimateAffinePartial2D(
        pts_ref, pts_mov, method=cv2.RANSAC, ransacReprojThreshold=5.0
    )

    if warp_matrix is None:
        return None, 0, 1.0, 0.0, good, kp_ref, kp_mov

    n_inliers = int(inliers.sum()) if inliers is not None else 0

    a, b = warp_matrix[0, 0], warp_matrix[0, 1]
    scale = math.sqrt(a * a + b * b)
    rotation_deg = math.degrees(math.atan2(warp_matrix[1, 0], warp_matrix[0, 0]))

    return warp_matrix, n_inliers, scale, rotation_deg, good, kp_ref, kp_mov


def align_ecc(ref_image, mov_image):
    """ECC translation alignment fallback."""
    if ref_image.ndim == 3:
        gray_ref = cv2.cvtColor(ref_image, cv2.COLOR_BGR2GRAY)
    else:
        gray_ref = ref_image.copy()

    if mov_image.ndim == 3:
        gray_mov = cv2.cvtColor(mov_image, cv2.COLOR_BGR2GRAY)
    else:
        gray_mov = mov_image.copy()

    eq_ref = cv2.equalizeHist(gray_ref)
    eq_mov = cv2.equalizeHist(gray_mov)

    warp_matrix = np.eye(2, 3, dtype=np.float32)
    criteria = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 5000, 1e-8)

    try:
        cc, warp_matrix = cv2.findTransformECC(
            eq_ref, eq_mov, warp_matrix, cv2.MOTION_TRANSLATION, criteria
        )
    except cv2.error:
        cc = 0.0
        warp_matrix = np.eye(2, 3, dtype=np.float32)

    return warp_matrix, float(cc)


def draw_matches(ref_image, kp_ref, mov_image, kp_mov, good_matches, n_inliers):
    """Draw matched keypoints between images."""
    img = cv2.drawMatches(
        ref_image, kp_ref, mov_image, kp_mov,
        good_matches[:50], None,
        flags=cv2.DrawMatchesFlags_NOT_DRAW_SINGLE_POINTS
    )
    cv2.putText(img, f"matches={len(good_matches)} inliers={n_inliers}",
                (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
    return img


def main():
    parser = argparse.ArgumentParser(description="SIFT+RANSAC same-substrate alignment")
    parser.add_argument("--source", required=True, help="Source image (e.g., bottom_part)")
    parser.add_argument("--target", required=True, help="Target image (e.g., full_stack_raw)")
    parser.add_argument("--pixel-size", type=float, required=True, help="um/px")
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    source_img = cv2.imread(args.source)
    target_img = cv2.imread(args.target)
    if source_img is None:
        print(f"ERROR: Cannot read source: {args.source}", file=sys.stderr)
        sys.exit(1)
    if target_img is None:
        print(f"ERROR: Cannot read target: {args.target}", file=sys.stderr)
        sys.exit(1)

    os.makedirs(args.output_dir, exist_ok=True)

    # Run SIFT alignment (target=ref, source=mov per warp convention)
    warp, n_inliers, scale, rot_deg, good, kp_ref, kp_mov = align_sift(
        target_img, source_img
    )

    # Fallback to ECC if SIFT failed
    if warp is None and len(good) < 10:
        print("SIFT failed, trying ECC translation fallback...")
        warp, cc = align_ecc(target_img, source_img)
        n_inliers = 0
        scale = 1.0
        rot_deg = 0.0

    # Determine quality
    if n_inliers >= 50:
        quality = "good"
    elif n_inliers >= 20:
        quality = "warning"
    else:
        quality = "insufficient"

    print(f"SIFT: {n_inliers} inliers, scale={scale:.4f}, rot={rot_deg:.2f}°")
    print(f"Quality: {quality}")

    # Draw diagnostic
    if good and kp_ref and kp_mov:
        diag = draw_matches(target_img, kp_ref, source_img, kp_mov, good, n_inliers)
        cv2.imwrite(os.path.join(args.output_dir, "01_sift_matches.png"), diag)

    # Generate overlay image
    if warp is not None:
        h, w = target_img.shape[:2]
        M_inv = cv2.invertAffineTransform(warp)
        warped = cv2.warpAffine(source_img, M_inv, (w, h))

        # Mask where warped content exists
        mask = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY) > 5

        # Desaturated target as base
        gray_target = cv2.cvtColor(target_img, cv2.COLOR_BGR2GRAY)
        base = cv2.merge([gray_target, gray_target, gray_target])

        # Tint warped image magenta (boost R+B, suppress G)
        tinted = warped.astype(np.float32)
        tinted[:, :, 0] *= 1.3   # B
        tinted[:, :, 1] *= 0.5   # G suppressed
        tinted[:, :, 2] *= 1.3   # R
        tinted = np.clip(tinted, 0, 255).astype(np.uint8)

        # Desaturated target + 40% tinted warped
        overlay = base.copy()
        overlay[mask] = cv2.addWeighted(base, 0.6, tinted, 0.4, 0)[mask]

        cv2.putText(overlay,
                    f"SIFT overlay: magenta=warped_source ({n_inliers} inliers)",
                    (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
        cv2.imwrite(os.path.join(args.output_dir, "01_sift_overlay.png"), overlay)

    # Save warp matrix
    if warp is not None:
        warp_path = os.path.join(args.output_dir, "warp_sift_bottom.npy")
        np.save(warp_path, warp.astype(np.float64))

    # Update alignment_report.json
    report_path = os.path.join(args.output_dir, "alignment_report.json")
    if os.path.exists(report_path):
        with open(report_path) as f:
            report = json.load(f)
    else:
        report = {}

    if "alignments" not in report:
        report["alignments"] = {}

    report["alignments"]["bottom"] = {
        "method": "sift",
        "quality": quality,
        "warp_file": "warp_sift_bottom.npy",
        "n_inliers": n_inliers,
        "scale": round(scale, 4),
        "rotation_deg": round(rot_deg, 2),
    }

    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)

    # Exit code based on quality
    if quality == "insufficient":
        print("Too few inliers — use Chamfer pipeline instead.")
        sys.exit(2)

    print(f"Saved: warp_sift_bottom.npy, 01_sift_matches.png, 01_sift_overlay.png")


if __name__ == "__main__":
    main()
