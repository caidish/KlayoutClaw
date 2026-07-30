from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import cv2
import numpy as np


SCRIPT_DIR = Path(__file__).resolve().parent
SKILLS_ROOT = SCRIPT_DIR.parents[1]
REPO_ROOT = SKILLS_ROOT.parent.parent if SKILLS_ROOT.parent.name == ".codex" else SKILLS_ROOT.parent
BASE_DETECT = SKILLS_ROOT / "nanodevice_flakedetect_detect" / "scripts"
REPO_SAM_ROOT = REPO_ROOT / "tools" / "sam2-main"
LEGACY_SAM_ROOT = Path("D:/Users/liyiz/desktop_backup/shixi/sam2-main")
DEFAULT_SAM_CONFIG = "configs/sam2.1/sam2.1_hiera_b+.yaml"
DEFAULT_SAM_CHECKPOINT = "model/sam2.1_hiera_base_plus.pt"


def default_sam_root() -> Path:
    env_root = os.environ.get("SAM2_ROOT")
    if env_root:
        return Path(env_root).expanduser()
    if REPO_SAM_ROOT.exists():
        return REPO_SAM_ROOT
    return LEGACY_SAM_ROOT

MATERIAL_FILES = {
    "graphite": ("graphite_mask.png", "graphite_contour.npy", "graphite_result.json"),
    "graphene": ("graphene_mask.png", "graphene_contour.npy", "graphene_result.json"),
    "bottom_hbn": ("bottom_hbn_mask.png", "bottom_hbn_contour.npy", "bottom_hbn_result.json"),
    "top_hbn": ("top_hbn_mask.png", "top_hbn_contour.npy", "top_hbn_result.json"),
}

MASK_OVERLAY_BGR = np.array([0, 0, 255], dtype=np.float32)
MASK_CONTOUR_BGR = (0, 0, 255)
POSITIVE_POINT_BGR = (0, 255, 0)
NEGATIVE_POINT_BGR = (0, 165, 255)
GRAPHITE_PRIOR_CONTOUR_BGR = (0, 255, 255)
_SAM2_PREDICTOR_CACHE: dict[tuple[str, str, str, str], Any] = {}


def finalize_graphene_mask(graphene_mask: np.ndarray,
                           graphite_prior_mask: np.ndarray) -> np.ndarray:
    """Return graphene-only output; the graphite prior is auxiliary evidence."""
    if graphene_mask.shape != graphite_prior_mask.shape:
        raise ValueError("graphene and graphite-prior masks must have the same shape")
    return np.where(graphene_mask > 0, 255, 0).astype(np.uint8)


def build_parser(material: str) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=f"{material} SAM2 prompt wrapper")
    p.add_argument("--prompt-rank", type=int, default=-1)
    p.add_argument("--n-prompt-candidates", type=int, default=8)
    p.add_argument("--use-sam2", action="store_true")
    p.add_argument("--sam-target-frac", type=float, default=0.22)
    p.add_argument("--manual-prompts-json", type=Path, default=None)
    p.add_argument("--grid-step", type=int, default=80)
    p.add_argument("--sam-root", default=str(default_sam_root()))
    p.add_argument("--sam-config", default=DEFAULT_SAM_CONFIG)
    p.add_argument("--sam-checkpoint", default=DEFAULT_SAM_CHECKPOINT)
    p.add_argument("--graphite-prior-mask", type=Path, default=None)
    p.add_argument("--warp-sift-bottom", type=Path, default=None)
    p.add_argument("--warp-top", type=Path, default=None)
    p.add_argument("--full-stack-image", type=Path, default=None)
    p.add_argument("--require-graphite-prior", action="store_true")
    return p


def run_baseline(material: str, passthrough: list[str]) -> None:
    script = BASE_DETECT / f"{material}.py"
    proc = subprocess.run([sys.executable, str(script), *passthrough])
    if proc.returncode != 0:
        raise SystemExit(proc.returncode)


def _arg_value(args: list[str], name: str) -> str | None:
    prefix = name + "="
    for i, arg in enumerate(args):
        if arg == name and i + 1 < len(args):
            return args[i + 1]
        if arg.startswith(prefix):
            return arg[len(prefix):]
    return None


def _has_flag(args: list[str], name: str) -> bool:
    return name in args


def _with_required_graphene_footprint(passthrough: list[str],
                                      output_dir: Path | None) -> list[str]:
    """Force graphene calls to use the sibling align footprint mask."""
    if output_dir is None:
        raise SystemExit("graphene requires --output-dir so align/footprint_mask.png can be fixed")

    footprint = output_dir.parent / "align" / "footprint_mask.png"
    if not footprint.exists():
        raise SystemExit(f"graphene requires align footprint at {footprint}")

    fixed_args: list[str] = []
    i = 0
    while i < len(passthrough):
        arg = passthrough[i]
        if arg == "--footprint-mask":
            i += 2
            continue
        if arg.startswith("--footprint-mask="):
            i += 1
            continue
        fixed_args.append(arg)
        i += 1

    print(f"INFO: graphene forced --footprint-mask {footprint}")
    return [*fixed_args, "--footprint-mask", str(footprint)]


def _bool_from_report_value(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "yes", "1"}:
            return True
        if lowered in {"false", "no", "0"}:
            return False
    return None


def _align_report_paths(passthrough: list[str], output_dir: Path | None = None) -> list[Path]:
    paths: list[Path] = []

    footprint_arg = _arg_value(passthrough, "--footprint-mask")
    if footprint_arg:
        paths.append(Path(footprint_arg).expanduser().resolve().parent / "alignment_report.json")

    image_arg = _arg_value(passthrough, "--image")
    if image_arg:
        image_path = Path(image_arg).expanduser().resolve()
        paths.append(image_path.parent.parent / "output" / "align" / "alignment_report.json")

    if output_dir is not None:
        out_path = output_dir.expanduser().resolve()
        paths.append(out_path.parent / "align" / "alignment_report.json")

    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path).lower()
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def _read_align_mirror(path: Path) -> bool | None:
    if not path.exists():
        return None
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    top = report.get("alignments", {}).get("top", {})
    value = _bool_from_report_value(top.get("mirror"))
    if value is not None:
        return value

    source = report.get("source", {})
    value = _bool_from_report_value(source.get("mirrored"))
    if value is not None:
        return value

    return _bool_from_report_value(report.get("mirror"))


def _graphene_should_mirror_from_align(
    passthrough: list[str],
    fallback: bool,
    output_dir: Path | None = None,
) -> bool:
    for report_path in _align_report_paths(passthrough, output_dir):
        mirrored = _read_align_mirror(report_path)
        if mirrored is not None:
            return mirrored
    return fallback


def _with_effective_mirror_flag(passthrough: list[str], mirrored: bool) -> list[str]:
    out = [arg for arg in passthrough if arg != "--mirror"]
    if mirrored:
        out.append("--mirror")
    return out


def _load_mask(path: Path) -> np.ndarray:
    mask = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if mask is None:
        raise FileNotFoundError(path)
    return (mask > 0).astype(np.uint8) * 255


def _file_fingerprint(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    try:
        p = path.expanduser().resolve()
        st = p.stat()
    except OSError:
        return {"path": str(path), "missing": True}
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return {
        "path": str(p),
        "size": int(st.st_size),
        "sha256": h.hexdigest(),
    }


def _mask_fingerprint(mask: np.ndarray) -> str:
    h = hashlib.sha256()
    h.update(np.ascontiguousarray(mask > 0).tobytes())
    return h.hexdigest()


def candidate_generation_signature(material: str, args: argparse.Namespace,
                                   image_path: Path, baseline_mask: np.ndarray,
                                   graphene_mirrored: bool) -> dict[str, Any]:
    """Inputs that affect prompt candidates, deliberately excluding prompt_rank.

    A later visual selection should be able to reuse the same candidate masks
    and images instead of deleting/re-running SAM2.
    """
    return {
        "version": 1,
        "material": material,
        "image": _file_fingerprint(image_path),
        "baseline_mask_sha256": _mask_fingerprint(baseline_mask),
        "manual_prompts": _file_fingerprint(args.manual_prompts_json),
        "use_sam2": bool(args.use_sam2),
        "n_prompt_candidates": int(args.n_prompt_candidates),
        "sam_target_frac": float(args.sam_target_frac),
        "grid_step": int(args.grid_step),
        "sam_root": str(Path(args.sam_root).expanduser().resolve()),
        "sam_config": str(args.sam_config),
        "sam_checkpoint": str(args.sam_checkpoint),
        "graphene_mirrored": bool(graphene_mirrored),
    }


def candidate_cache_path(out_dir: Path, material: str) -> Path:
    return out_dir / f"{material}_candidate_masks.npz"


def sidecar_path(out_dir: Path, material: str) -> Path:
    return out_dir / f"{material}_prompt_candidates.json"


def _cache_key(rank: int) -> str:
    return f"rank_{int(rank)}"


def _candidate_images_exist(out_dir: Path, image_names: list[str]) -> bool:
    return bool(image_names) and all((out_dir / name).exists() for name in image_names)


def load_reusable_sidecar(out_dir: Path, material: str,
                          signature: dict[str, Any]) -> dict[str, Any] | None:
    path = sidecar_path(out_dir, material)
    masks_path = candidate_cache_path(out_dir, material)
    if not path.exists() or not masks_path.exists():
        return None
    try:
        sidecar = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    if sidecar.get("generation_signature") != signature:
        return None
    image_names = list(sidecar.get("candidate_images") or [])
    if not _candidate_images_exist(out_dir, image_names):
        return None
    return sidecar


def load_cached_candidate_mask(out_dir: Path, material: str, rank: int) -> np.ndarray | None:
    path = candidate_cache_path(out_dir, material)
    if not path.exists():
        return None
    key = _cache_key(rank)
    try:
        with np.load(str(path)) as data:
            if key not in data:
                return None
            return (data[key] > 0).astype(np.uint8) * 255
    except Exception:
        return None


def save_candidate_mask_cache(out_dir: Path, material: str,
                              masks_by_rank: dict[int, np.ndarray]) -> None:
    if not masks_by_rank:
        return
    arrays = {
        _cache_key(rank): (mask > 0).astype(np.uint8) * 255
        for rank, mask in masks_by_rank.items()
        if mask is not None
    }
    if arrays:
        np.savez_compressed(str(candidate_cache_path(out_dir, material)), **arrays)


def clear_stale_candidate_cache(out_dir: Path, material: str) -> None:
    for stale in out_dir.glob(f"{material}_candidate_*.png"):
        stale.unlink(missing_ok=True)
    (out_dir / f"{material}_candidate_montage.png").unlink(missing_ok=True)
    candidate_cache_path(out_dir, material).unlink(missing_ok=True)


def candidate_score_for_rank(sidecar: dict[str, Any], rank: int) -> float | None:
    for result in sidecar.get("candidate_results") or []:
        if int(result.get("rank", -9999)) == int(rank):
            metrics = result.get("metrics") or {}
            score = metrics.get("score")
            return float(score) if isinstance(score, (int, float)) else None
    return None


def rewrite_selection_from_cache(material: str, out_dir: Path, image: np.ndarray,
                                 sidecar: dict[str, Any], selected_rank: int,
                                 pixel_size: float, display_image: np.ndarray,
                                 grid_step: int,
                                 graphite_prior_mask: np.ndarray | None = None) -> bool:
    selected_mask = load_cached_candidate_mask(out_dir, material, selected_rank)
    if selected_mask is None:
        return False
    update_outputs_from_mask(
        material, out_dir, selected_mask, pixel_size,
        selected_rank=selected_rank,
        selected_score=candidate_score_for_rank(sidecar, selected_rank),
    )
    candidate_images = []
    candidates = sidecar.get("candidates") or []
    for candidate in candidates:
        rank = int(candidate.get("rank", -9999))
        mask = load_cached_candidate_mask(out_dir, material, rank)
        if mask is None:
            continue
        name = f"{material}_candidate_{rank + 1:02d}_on_grid.png"
        cv2.imwrite(
            str(out_dir / name),
            draw_candidate_on_grid(
                display_image, mask, candidate,
                rank == selected_rank, grid_step,
                graphite_prior_mask=graphite_prior_mask,
            ),
        )
        candidate_images.append(name)
    montage = write_candidate_montage(out_dir, material, candidate_images)
    sidecar["selected_rank"] = int(selected_rank)
    sidecar["selected_candidate"] = _candidate_by_rank(candidates, selected_rank)
    sidecar["candidate_images"] = candidate_images
    sidecar["candidate_montage"] = montage
    sidecar["cache_reused_for_selection"] = True
    sidecar_path(out_dir, material).write_text(
        json.dumps(sidecar, indent=2), encoding="utf-8"
    )
    return True


def draw_grid(image: np.ndarray, step: int = 100) -> np.ndarray:
    out = image.copy()
    h, w = out.shape[:2]
    step = max(25, int(step))
    for x in range(0, w, step):
        cv2.line(out, (x, 0), (x, h - 1), (255, 255, 255), 1)
        cv2.putText(out, str(x), (x + 6, 24), cv2.FONT_HERSHEY_SIMPLEX,
                    0.65, (255, 255, 255), 2, cv2.LINE_AA)
        cv2.putText(out, str(x), (x + 6, h - 12), cv2.FONT_HERSHEY_SIMPLEX,
                    0.65, (255, 255, 255), 2, cv2.LINE_AA)
    for y in range(0, h, step):
        cv2.line(out, (0, y), (w - 1, y), (255, 255, 255), 1)
        cv2.putText(out, str(y), (8, y + 24), cv2.FONT_HERSHEY_SIMPLEX,
                    0.65, (255, 255, 255), 2, cv2.LINE_AA)
        cv2.putText(out, str(y), (w - 90, y + 24), cv2.FONT_HERSHEY_SIMPLEX,
                    0.65, (255, 255, 255), 2, cv2.LINE_AA)
    return out


def warp_graphite_mask_to_mirrored_top(graphite_mask: np.ndarray,
                                       warp_sift_bottom: np.ndarray,
                                       warp_top: np.ndarray,
                                       full_shape: tuple[int, int],
                                       top_shape: tuple[int, int]) -> tuple[np.ndarray, dict[str, Any]]:
    full_h, full_w = int(full_shape[0]), int(full_shape[1])
    top_h, top_w = int(top_shape[0]), int(top_shape[1])
    bottom_to_full = cv2.invertAffineTransform(
        np.asarray(warp_sift_bottom, dtype=np.float32)
    )
    full_to_top = cv2.invertAffineTransform(
        np.asarray(warp_top, dtype=np.float32)
    )
    binary = (graphite_mask > 0).astype(np.uint8) * 255
    full_mask = cv2.warpAffine(
        binary,
        bottom_to_full,
        (full_w, full_h),
        flags=cv2.INTER_NEAREST,
        borderValue=0,
    )
    top_mask = cv2.warpAffine(
        full_mask,
        full_to_top,
        (top_w, top_h),
        flags=cv2.INTER_NEAREST,
        borderValue=0,
    )
    top_mask = (top_mask > 0).astype(np.uint8) * 255
    report = {
        "input_pixels": int((binary > 0).sum()),
        "full_pixels": int((full_mask > 0).sum()),
        "output_pixels": int((top_mask > 0).sum()),
        "bottom_to_full": bottom_to_full.tolist(),
        "full_to_mirrored_top": full_to_top.tolist(),
        "full_shape": [full_h, full_w],
        "top_shape": [top_h, top_w],
    }
    return top_mask, report


def draw_graphite_prior_on_grid(grid_image: np.ndarray,
                                prior_mask: np.ndarray) -> np.ndarray:
    out = grid_image.copy()
    mask = (prior_mask > 0).astype(np.uint8) * 255
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(out, cnts, -1, GRAPHITE_PRIOR_CONTOUR_BGR, 2)
    return out


def write_graphite_prior_files(output_dir: Path,
                               clean_grid: np.ndarray,
                               top_shape: tuple[int, int],
                               graphite_mask_path: Path,
                               warp_sift_bottom_path: Path,
                               warp_top_path: Path,
                               full_stack_image_path: Path,
                               grid_step: int) -> dict[str, Any]:
    graphite_mask = _load_mask(graphite_mask_path)
    full_image = cv2.imread(str(full_stack_image_path), cv2.IMREAD_COLOR)
    if full_image is None:
        raise FileNotFoundError(full_stack_image_path)
    warp_sift_bottom = np.load(str(warp_sift_bottom_path))
    warp_top = np.load(str(warp_top_path))
    prior_mask, report = warp_graphite_mask_to_mirrored_top(
        graphite_mask,
        warp_sift_bottom,
        warp_top,
        full_shape=full_image.shape[:2],
        top_shape=top_shape,
    )
    report.update({
        "graphite_mask_path": str(graphite_mask_path),
        "warp_sift_bottom_path": str(warp_sift_bottom_path),
        "warp_top_path": str(warp_top_path),
        "full_stack_image_path": str(full_stack_image_path),
        "prior_mask_file": "graphite_on_top_mask.png",
        "prior_grid_file": f"graphene_source_grid_{grid_step}px_graphite_prior.png",
        "style": "yellow_contour",
        "visual_only": True,
    })
    output_dir.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(output_dir / "graphite_on_top_mask.png"), prior_mask)
    cv2.imwrite(
        str(output_dir / f"graphene_source_grid_{grid_step}px_graphite_prior.png"),
        draw_graphite_prior_on_grid(clean_grid, prior_mask),
    )
    (output_dir / "graphite_on_top_prior_report.json").write_text(
        json.dumps(report, indent=2),
        encoding="utf-8",
    )
    return report


def _graphite_prior_paths(args: argparse.Namespace) -> list[Path | None]:
    return [
        args.graphite_prior_mask,
        args.warp_sift_bottom,
        args.warp_top,
        args.full_stack_image,
    ]


def _infer_full_stack_image(image_arg: str | None) -> Path | None:
    if image_arg is None:
        return None
    image_path = Path(image_arg)
    candidate = image_path.parent / "full_stack_raw.jpg"
    return candidate if candidate.exists() else None


def _autofill_graphene_graphite_prior(args: argparse.Namespace,
                                      out_dir: Path,
                                      image_arg: str | None) -> None:
    if any(path is not None for path in _graphite_prior_paths(args)):
        return
    graphite_mask = out_dir / "graphite_mask.png"
    warp_sift_bottom = out_dir.parent / "align" / "warp_sift_bottom.npy"
    warp_top = out_dir.parent / "align" / "warp_top.npy"
    full_stack_image = _infer_full_stack_image(image_arg)
    required = [graphite_mask, warp_sift_bottom, warp_top, full_stack_image]
    if not all(path is not None and path.exists() for path in required):
        return
    args.graphite_prior_mask = graphite_mask
    args.warp_sift_bottom = warp_sift_bottom
    args.warp_top = warp_top
    args.full_stack_image = full_stack_image
    print(
        "INFO: graphene graphite prior auto-filled from "
        f"{graphite_mask}, {warp_sift_bottom}, {warp_top}, {full_stack_image}"
    )


def _write_required_graphite_prior(args: argparse.Namespace,
                                   out_dir: Path,
                                   clean_grid: np.ndarray,
                                   top_shape: tuple[int, int]) -> np.ndarray | None:
    paths = _graphite_prior_paths(args)
    if any(path is not None for path in paths):
        missing = [
            name for name, path in zip(
                [
                    "--graphite-prior-mask",
                    "--warp-sift-bottom",
                    "--warp-top",
                    "--full-stack-image",
                ],
                paths,
            )
            if path is None
        ]
        if missing:
            raise SystemExit(
                "graphite prior requested but missing " + ", ".join(missing)
            )
        write_graphite_prior_files(
            output_dir=out_dir,
            clean_grid=clean_grid,
            top_shape=top_shape,
            graphite_mask_path=args.graphite_prior_mask,
            warp_sift_bottom_path=args.warp_sift_bottom,
            warp_top_path=args.warp_top,
            full_stack_image_path=args.full_stack_image,
            grid_step=args.grid_step,
        )
        return _load_mask(out_dir / "graphite_on_top_mask.png")
    if args.require_graphite_prior:
        raise SystemExit(
            "graphite prior is required; provide --graphite-prior-mask, "
            "--warp-sift-bottom, --warp-top, and --full-stack-image"
        )
    return None


def load_manual_prompt_candidates(path: Path, n: int) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data.get("candidates", []) if isinstance(data, dict) else data
    if not isinstance(rows, list):
        raise ValueError("--manual-prompts-json must be a list or contain a candidates list")
    candidates = []
    for i, row in enumerate(rows[:max(1, n)]):
        pos = row.get("positive_points", [])
        neg = row.get("negative_points", [])
        if not pos:
            raise ValueError(f"manual candidate {i} has no positive_points")
        candidates.append({
            "rank": int(row.get("rank", i)),
            "source": row.get("source", "manual_grid"),
            "positive_points": [[int(x), int(y)] for x, y in pos],
            "negative_points": [[int(x), int(y)] for x, y in neg],
        })
    if not candidates:
        raise ValueError("--manual-prompts-json did not contain any candidates")
    return candidates


def _largest_contour(mask: np.ndarray) -> np.ndarray | None:
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        return None
    return max(cnts, key=cv2.contourArea)


def _point_from_percentile(mask: np.ndarray, pct: float) -> tuple[int, int]:
    dt = cv2.distanceTransform((mask > 0).astype(np.uint8), cv2.DIST_L2, 5)
    ys, xs = np.where(mask > 0)
    if ys.size == 0:
        h, w = mask.shape[:2]
        return w // 2, h // 2
    vals = dt[ys, xs]
    keep = vals >= np.percentile(vals, pct)
    if keep.any():
        xs2 = xs[keep]
        ys2 = ys[keep]
        cx = float(xs.mean())
        cy = float(ys.mean())
        idx = int(np.argmin((xs2 - cx) ** 2 + (ys2 - cy) ** 2))
        return int(xs2[idx]), int(ys2[idx])
    max_val = vals.max()
    max_keep = vals >= max_val
    cx = float(xs.mean())
    cy = float(ys.mean())
    idx = int(np.argmin((xs[max_keep] - cx) ** 2 + (ys[max_keep] - cy) ** 2))
    xs = xs[max_keep]
    ys = ys[max_keep]
    return int(xs[idx]), int(ys[idx])


def _point_in_mask_near_anchor(mask: np.ndarray, ax: float, ay: float) -> tuple[int, int]:
    ys, xs = np.where(mask > 0)
    if ys.size == 0:
        h, w = mask.shape[:2]
        return w // 2, h // 2
    x0, x1 = int(xs.min()), int(xs.max())
    y0, y1 = int(ys.min()), int(ys.max())
    tx = x0 + ax * max(1, x1 - x0)
    ty = y0 + ay * max(1, y1 - y0)
    dt = cv2.distanceTransform((mask > 0).astype(np.uint8), cv2.DIST_L2, 5)
    vals = dt[ys, xs]
    if vals.max() > 1:
        interior = vals >= max(1.0, vals.max() * 0.45)
        if interior.any():
            xs = xs[interior]
            ys = ys[interior]
    idx = int(np.argmin((xs - tx) ** 2 + (ys - ty) ** 2))
    return int(xs[idx]), int(ys[idx])


def _negative_points(mask: np.ndarray, count: int, margin_px: int) -> list[tuple[int, int]]:
    h, w = mask.shape[:2]
    cnt = _largest_contour(mask)
    if cnt is None:
        return [(10, 10), (w - 10, 10), (10, h - 10), (w - 10, h - 10)][:count]
    x, y, bw, bh = cv2.boundingRect(cnt)
    candidates = [
        (x - margin_px, y + bh // 2),
        (x + bw + margin_px, y + bh // 2),
        (x + bw // 2, y - margin_px),
        (x + bw // 2, y + bh + margin_px),
        (x - margin_px, y - margin_px),
        (x + bw + margin_px, y - margin_px),
        (x - margin_px, y + bh + margin_px),
        (x + bw + margin_px, y + bh + margin_px),
    ]
    out = []
    for px, py in candidates:
        px = min(max(int(px), 0), w - 1)
        py = min(max(int(py), 0), h - 1)
        if mask[py, px] == 0:
            out.append((px, py))
        if len(out) >= count:
            return out
    for pt in [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)]:
        if pt not in out:
            out.append(pt)
        if len(out) >= count:
            break
    return out


def make_prompt_candidates(mask: np.ndarray, pixel_size: float, n: int) -> list[dict[str, Any]]:
    margin_px = max(8, int(round(2.0 / max(pixel_size, 1e-6))))
    neg_pool = _negative_points(mask, 4, margin_px)
    percentiles = [99, 97, 95, 93, 90, 87, 84, 80]
    candidates = []
    for rank in range(n):
        pos = [_point_from_percentile(mask, percentiles[rank % len(percentiles)])]
        if rank >= 4:
            pos.append(_point_from_percentile(mask, max(60, percentiles[rank % len(percentiles)] - 20)))
        neg_count = 1 + (rank % min(4, max(1, len(neg_pool))))
        candidates.append({
            "rank": rank,
            "positive_points": [[int(x), int(y)] for x, y in pos],
            "negative_points": [[int(x), int(y)] for x, y in neg_pool[:neg_count]],
        })
    return candidates


def draw_candidate(image: np.ndarray, mask: np.ndarray, candidate: dict[str, Any], selected: bool) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    out = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    out = cv2.addWeighted(image, 0.45, out, 0.55, 0)
    overlay = out.copy()
    overlay[mask > 0] = (overlay[mask > 0] * 0.55 + MASK_OVERLAY_BGR * 0.45).astype(np.uint8)
    out = cv2.addWeighted(overlay, 0.65, out, 0.35, 0)
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(out, cnts, -1, MASK_CONTOUR_BGR, 2)
    for x, y in candidate["positive_points"]:
        cv2.circle(out, (int(x), int(y)), 8, POSITIVE_POINT_BGR, -1)
        cv2.circle(out, (int(x), int(y)), 9, (255, 255, 255), 2)
    for x, y in candidate["negative_points"]:
        cv2.circle(out, (int(x), int(y)), 8, NEGATIVE_POINT_BGR, -1)
        cv2.circle(out, (int(x), int(y)), 9, (255, 255, 255), 2)
    cv2.rectangle(out, (0, 0), (out.shape[1], 50), (20, 20, 20), -1)
    cv2.putText(out, f"candidate #{candidate['rank']}  green=positive orange=negative red=mask",
                (12, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.85, (255, 255, 255), 2, cv2.LINE_AA)
    if selected:
        cv2.rectangle(out, (0, 0), (out.shape[1] - 1, out.shape[0] - 1), (0, 255, 255), 8)
    return out


def draw_candidate_on_grid(image: np.ndarray, mask: np.ndarray | None,
                           candidate: dict[str, Any], selected: bool,
                           step: int = 100,
                           graphite_prior_mask: np.ndarray | None = None) -> np.ndarray:
    out = draw_grid(image, step)
    if mask is not None:
        overlay = out.copy()
        overlay[mask > 0] = (
            overlay[mask > 0] * 0.50 + MASK_OVERLAY_BGR * 0.50
        ).astype(np.uint8)
        out = cv2.addWeighted(overlay, 0.70, out, 0.30, 0)
        cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL,
                                   cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(out, cnts, -1, MASK_CONTOUR_BGR, 2)
    is_baseline = candidate.get("kind") == "baseline"
    label = f"candidate #{candidate['rank']}"
    if is_baseline:
        label += "  baseline/refined"
    else:
        label += "  green=positive yellow=graphite-prior orange=negative red=mask"
    cv2.rectangle(out, (0, 0), (out.shape[1], 50), (20, 20, 20), -1)
    cv2.putText(out, label, (12, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.85,
                (255, 255, 255), 2, cv2.LINE_AA)
    if graphite_prior_mask is not None:
        prior = (graphite_prior_mask > 0).astype(np.uint8) * 255
        cnts, _ = cv2.findContours(prior, cv2.RETR_EXTERNAL,
                                   cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(out, cnts, -1, GRAPHITE_PRIOR_CONTOUR_BGR, 2)
    if not is_baseline:
        for x, y in candidate["positive_points"]:
            cv2.circle(out, (int(x), int(y)), 8, POSITIVE_POINT_BGR, -1)
            cv2.circle(out, (int(x), int(y)), 9, (255, 255, 255), 2)
        for x, y in candidate["negative_points"]:
            cv2.circle(out, (int(x), int(y)), 8, NEGATIVE_POINT_BGR, -1)
            cv2.circle(out, (int(x), int(y)), 9, (255, 255, 255), 2)
    if selected:
        cv2.rectangle(out, (0, 0), (out.shape[1] - 1, out.shape[0] - 1),
                      (0, 255, 255), 8)
    return out


def write_candidate_montage(out_dir: Path, material: str,
                            image_names: list[str]) -> str | None:
    tiles = []
    for name in image_names:
        img = cv2.imread(str(out_dir / name), cv2.IMREAD_COLOR)
        if img is not None:
            tiles.append(img)
    if not tiles:
        return None
    h0, w0 = tiles[0].shape[:2]
    target_w = 520
    target_h = max(1, int(round(h0 * target_w / max(1, w0))))
    resized = [cv2.resize(tile, (target_w, target_h),
                          interpolation=cv2.INTER_AREA) for tile in tiles]
    blank = np.zeros_like(resized[0])
    while len(resized) < 9:
        resized.append(blank.copy())
    rows = []
    for i in range(0, 9, 3):
        rows.append(np.hstack(resized[i:i + 3]))
    montage = np.vstack(rows)
    out_name = f"{material}_candidate_montage.png"
    cv2.imwrite(str(out_dir / out_name), montage)
    return out_name


def _candidate_by_rank(candidates: list[dict[str, Any]], rank: int) -> dict[str, Any] | None:
    for candidate in candidates:
        if int(candidate.get("rank", -9999)) == int(rank):
            return candidate
    return None


def draw_prompt_candidate(image: np.ndarray, candidate: dict[str, Any], selected: bool) -> np.ndarray:
    out = image.copy()
    for x, y in candidate["positive_points"]:
        cv2.circle(out, (int(x), int(y)), 8, (0, 255, 0), -1)
        cv2.circle(out, (int(x), int(y)), 9, (255, 255, 255), 2)
    for x, y in candidate["negative_points"]:
        cv2.circle(out, (int(x), int(y)), 8, (0, 0, 255), -1)
        cv2.circle(out, (int(x), int(y)), 9, (255, 255, 255), 2)
    cv2.rectangle(out, (0, 0), (out.shape[1], 50), (20, 20, 20), -1)
    cv2.putText(out, f"candidate #{candidate['rank']}  green=positive red=negative",
                (12, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.85, (255, 255, 255), 2, cv2.LINE_AA)
    if selected:
        cv2.rectangle(out, (0, 0), (out.shape[1] - 1, out.shape[0] - 1), (0, 255, 255), 8)
    return out


def _contour_from_mask(mask: np.ndarray) -> np.ndarray:
    cnt = _largest_contour(mask)
    if cnt is None:
        return np.zeros((0, 2), dtype=np.float64)
    return cnt.reshape(-1, 2).astype(np.float64)


def _try_sam2(image: np.ndarray, candidate: dict[str, Any], args: argparse.Namespace) -> tuple[np.ndarray | None, dict[str, Any]]:
    info: dict[str, Any] = {"attempted": True}
    sam_root = Path(args.sam_root)
    checkpoint = sam_root / args.sam_checkpoint
    if not sam_root.exists() or not checkpoint.exists():
        info["status"] = "missing_sam_root_or_checkpoint"
        return None, info
    import operator  # noqa: F401
    sam_root_str = str(sam_root)
    if sam_root_str not in sys.path:
        sys.path.append(sam_root_str)
    try:
        import torch
        from sam2.build_sam import build_sam2
        from sam2.sam2_image_predictor import SAM2ImagePredictor
    except Exception as exc:
        info["status"] = "import_failed"
        info["error"] = repr(exc)
        return None, info
    try:
        device = "cuda" if torch.cuda.is_available() else "cpu"
        allow_cpu = os.environ.get("KLAYOUTCLAW_ALLOW_SAM2_CPU", "").lower() in {"1", "true", "yes"}
        if device != "cuda" and not allow_cpu:
            raise SystemExit(
                "SAM2 CUDA is required but torch.cuda.is_available() is false. "
                "Do not clear CUDA_VISIBLE_DEVICES in Docker; set "
                "KLAYOUTCLAW_ALLOW_SAM2_CPU=1 only for an explicit CPU debug run."
            )
        key = (
            str(sam_root.resolve()),
            str(args.sam_config),
            str(checkpoint.resolve()),
            device,
        )
        predictor = _SAM2_PREDICTOR_CACHE.get(key)
        if predictor is None:
            model = build_sam2(args.sam_config, str(checkpoint), device=device)
            predictor = SAM2ImagePredictor(model)
            _SAM2_PREDICTOR_CACHE[key] = predictor
        predictor.set_image(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
        pts = np.array(candidate["positive_points"] + candidate["negative_points"], dtype=np.float32)
        labels = np.array([1] * len(candidate["positive_points"]) + [0] * len(candidate["negative_points"]), dtype=np.int32)
        masks, scores, _ = predictor.predict(point_coords=pts, point_labels=labels, multimask_output=False)
        info["status"] = "ok"
        info["score"] = float(scores[0]) if len(scores) else None
        info["device"] = device
        return (masks[0] > 0).astype(np.uint8) * 255, info
    except Exception as exc:
        info["status"] = "predict_failed"
        info["error"] = repr(exc)
        return None, info


def _main_flake_mask(image: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    mask = (gray > 20).astype(np.uint8) * 255
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
    if n <= 1:
        return np.ones(gray.shape, dtype=np.uint8) * 255
    idx = int(np.argmax(stats[1:, cv2.CC_STAT_AREA]) + 1)
    main = (labels == idx).astype(np.uint8) * 255
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
    return cv2.morphologyEx(main, cv2.MORPH_CLOSE, k)


def make_graphene_point_candidates(image: np.ndarray, n: int, baseline_mask: np.ndarray | None = None) -> tuple[list[dict[str, Any]], np.ndarray]:
    flake = _main_flake_mask(image)
    use_baseline = baseline_mask is not None and (baseline_mask > 0).any()
    seed_mask = baseline_mask if use_baseline else flake
    baseline_area = int((baseline_mask > 0).sum()) if baseline_mask is not None else 0
    flake_area = max(1, int((flake > 0).sum()))
    baseline_slots = 4 if baseline_area >= max(500, int(0.03 * flake_area)) else 2
    cnt = _largest_contour(flake)
    if cnt is None:
        h, w = flake.shape[:2]
        cnt = np.array([[[0, 0]], [[w - 1, 0]], [[w - 1, h - 1]], [[0, h - 1]]], dtype=np.int32)
    x, y, w, h = cv2.boundingRect(cnt)
    h_img, w_img = flake.shape[:2]
    anchors = [(0.52, 0.42), (0.56, 0.50), (0.48, 0.36), (0.62, 0.58),
               (0.44, 0.54), (0.54, 0.68), (0.42, 0.28), (0.70, 0.40)]
    baseline_anchors = [(0.50, 0.50), (0.35, 0.50), (0.65, 0.50), (0.50, 0.35)]
    neg_base = [(x + 0.18 * w, y + 0.18 * h), (x + 0.78 * w, y + 0.76 * h),
                (x + 0.50 * w, y + 0.88 * h), (max(0, x - 30), y + 0.50 * h)]
    candidates = []
    for rank in range(n):
        if use_baseline and rank < baseline_slots:
            px, py = _point_in_mask_near_anchor(seed_mask, *baseline_anchors[rank])
        else:
            ax, ay = anchors[rank % len(anchors)]
            px = int(np.clip(x + ax * w, 0, w_img - 1))
            py = int(np.clip(y + ay * h, 0, h_img - 1))
            if flake[py, px] == 0:
                dist = cv2.distanceTransform((flake > 0).astype(np.uint8), cv2.DIST_L2, 5)
                yy, xx = np.unravel_index(int(np.argmax(dist)), dist.shape)
                px, py = int(xx), int(yy)
        pos = [[px, py]]
        if rank >= 4:
            pos.append([int(np.clip(x + 0.50 * w, 0, w_img - 1)), int(np.clip(y + 0.50 * h, 0, h_img - 1))])
        neg = []
        for nx, ny in neg_base[:1 + (rank % 4)]:
            qx = int(np.clip(nx, 0, w_img - 1))
            qy = int(np.clip(ny, 0, h_img - 1))
            if abs(qx - px) + abs(qy - py) > 80:
                neg.append([qx, qy])
        candidates.append({"rank": rank, "positive_points": pos, "negative_points": neg})
    return candidates, flake


def _mask_score(mask: np.ndarray, flake: np.ndarray, target_frac: float) -> dict[str, float]:
    area = float((mask > 0).sum())
    flake_area = max(1.0, float((flake > 0).sum()))
    inside = float(((mask > 0) & (flake > 0)).sum())
    inside_frac = inside / max(1.0, area)
    area_frac = area / flake_area
    cnt = _largest_contour(mask)
    solidity = 0.0
    aspect = 1.0
    if cnt is not None and len(cnt) >= 3:
        hull = cv2.convexHull(cnt)
        solidity = float(cv2.contourArea(cnt)) / max(1.0, float(cv2.contourArea(hull)))
        (_, _), (rw, rh), _ = cv2.minAreaRect(cnt)
        aspect = float(max(rw, rh) / max(min(rw, rh), 1e-6))
    target_frac = float(np.clip(target_frac, 0.05, 1.0))
    size_score = float(np.exp(-((area_frac - target_frac) / 0.30) ** 2))
    shape_score = float(np.exp(-max(0.0, aspect - 2.5) / 3.0))
    strip_penalty = 0.35 if aspect > 6.0 else 0.65 if aspect > 4.0 else 1.0
    score = (0.45 * size_score + 0.25 * inside_frac + 0.20 * min(1.0, solidity) + 0.10 * shape_score) * strip_penalty
    if area_frac < 0.01 or area_frac > 1.05:
        score *= 0.25
    return {
        "area_px": area,
        "area_frac_of_flake": area_frac,
        "inside_frac": inside_frac,
        "solidity": solidity,
        "aspect": aspect,
        "shape_score": shape_score,
        "strip_penalty": strip_penalty,
        "score": score,
    }


def run_graphene_sam_candidates(args: argparse.Namespace, out_dir: Path, image: np.ndarray,
                                baseline_mask: np.ndarray | None = None,
                                generation_signature: dict[str, Any] | None = None,
                                graphite_prior_mask: np.ndarray | None = None) -> tuple[np.ndarray | None, dict[str, Any]]:
    if args.manual_prompts_json is not None:
        candidates = load_manual_prompt_candidates(args.manual_prompts_json, max(8, args.n_prompt_candidates))
        flake = _main_flake_mask(image)
    else:
        candidates, flake = make_graphene_point_candidates(image, max(8, args.n_prompt_candidates), baseline_mask=baseline_mask)
    baseline_candidate = None
    if baseline_mask is not None and (baseline_mask > 0).any():
        bx, by = _point_from_percentile(baseline_mask, 95)
        baseline_candidate = {
            "rank": 8,
            "kind": "baseline",
            "positive_points": [[int(bx), int(by)]],
            "negative_points": [],
        }
        candidates = candidates[:8] + [baseline_candidate]
    results = []
    candidate_ranks = {int(candidate["rank"]) for candidate in candidates}
    if args.prompt_rank >= 0:
        if args.prompt_rank not in candidate_ranks:
            raise SystemExit(
                f"--prompt-rank {args.prompt_rank} is not available; "
                f"available ranks: {sorted(candidate_ranks)}"
            )
        selected_rank = args.prompt_rank
    else:
        selected_rank = -1
    selected_mask = None
    best_score = -1.0
    draw_entries = []
    masks_by_rank: dict[int, np.ndarray] = {}
    for candidate in candidates[:8]:
        sam_mask, sam_info = _try_sam2(image, candidate, args)
        if sam_mask is None:
            result = {"rank": candidate["rank"], "sam2": sam_info, "metrics": None}
            draw_mask = flake
        else:
            metrics = _mask_score(sam_mask, flake, args.sam_target_frac)
            result = {"rank": candidate["rank"], "sam2": sam_info, "metrics": metrics}
            draw_mask = sam_mask
            if args.prompt_rank >= 0 and candidate["rank"] == selected_rank:
                selected_mask = sam_mask
            if args.prompt_rank < 0 and metrics["score"] > best_score:
                best_score = metrics["score"]
                selected_rank = candidate["rank"]
                selected_mask = sam_mask
            masks_by_rank[int(candidate["rank"])] = sam_mask
        results.append(result)
        draw_entries.append((f"graphene_candidate_{candidate['rank'] + 1:02d}_on_grid.png",
                             draw_mask, candidate))
    if baseline_candidate is not None:
        metrics = _mask_score(baseline_mask, flake, args.sam_target_frac)
        results.append({
            "rank": 8,
            "kind": "baseline",
            "sam2": {"attempted": False, "status": "baseline"},
            "metrics": metrics,
        })
        if args.prompt_rank == 8:
            selected_mask = baseline_mask
        masks_by_rank[8] = baseline_mask
        draw_entries.append(("graphene_candidate_09_on_grid.png",
                             baseline_mask, baseline_candidate))
    if selected_mask is None and results:
        scored = [(r["metrics"]["score"], r["rank"]) for r in results if r["metrics"] is not None]
        if scored:
            selected_rank = max(scored)[1]
            if selected_rank == 8 and baseline_mask is not None:
                selected_mask = baseline_mask
            else:
                selected_candidate = _candidate_by_rank(candidates, selected_rank)
                if selected_candidate is not None:
                    selected_mask, _ = _try_sam2(image, selected_candidate, args)
    for name, draw_mask, candidate in draw_entries:
        cv2.imwrite(str(out_dir / name),
                    draw_candidate_on_grid(image, draw_mask, candidate,
                                           candidate["rank"] == selected_rank,
                                           args.grid_step,
                                           graphite_prior_mask=graphite_prior_mask))
    candidate_images = [name for name, _, _ in draw_entries]
    montage = write_candidate_montage(out_dir, "graphene", candidate_images)
    save_candidate_mask_cache(out_dir, "graphene", masks_by_rank)
    selected_candidate = _candidate_by_rank(candidates[:9], selected_rank)
    sidecar = {
        "material": "graphene",
        "selected_rank": selected_rank,
        "selected_candidate": selected_candidate,
        "candidates": candidates[:9],
        "candidate_results": results,
        "candidate_images": candidate_images,
        "candidate_montage": montage,
        "source_grid": f"graphene_source_grid_{args.grid_step}px.png",
        "graphite_prior_grid": f"graphene_source_grid_{args.grid_step}px_graphite_prior.png" if graphite_prior_mask is not None else None,
        "graphite_prior_on_candidates": graphite_prior_mask is not None,
        "manual_prompts_json": str(args.manual_prompts_json) if args.manual_prompts_json is not None else None,
        "sam2": {"attempted": True, "status": "multi_candidate"},
        "generation_signature": generation_signature,
    }
    return selected_mask, sidecar


def update_outputs_from_mask(material: str, out_dir: Path, mask: np.ndarray,
                             pixel_size: float,
                             selected_rank: int | None = None,
                             selected_score: float | None = None) -> None:
    mask_name, contour_name, result_name = MATERIAL_FILES[material]
    cv2.imwrite(str(out_dir / mask_name), mask)
    np.save(str(out_dir / contour_name), _contour_from_mask(mask))
    result_path = out_dir / result_name
    data = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else {}
    area_px = int((mask > 0).sum())
    data["area_px"] = area_px
    data["area_um2"] = round(area_px * pixel_size * pixel_size, 3)
    data["contour_file"] = contour_name
    data["mask_file"] = mask_name
    data["sam_prompt_refined"] = True
    if selected_rank is not None:
        data["selected_rank"] = int(selected_rank)
        selected = data.get("selected")
        if not isinstance(selected, dict):
            selected = {}
        selected["rank"] = int(selected_rank)
        if selected_score is not None:
            selected["score"] = float(selected_score)
        data["selected"] = selected
    result_path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def run_prompt_wrapper(material: str) -> int:
    parser = build_parser(material)
    args, passthrough = parser.parse_known_args()
    out_arg = _arg_value(passthrough, "--output-dir")
    out_dir = Path(out_arg) if out_arg is not None else None
    graphene_mirrored = False
    if material == "graphene":
        passthrough = _with_required_graphene_footprint(passthrough, out_dir)
        graphene_mirrored = _graphene_should_mirror_from_align(
            passthrough,
            fallback=_has_flag(passthrough, "--mirror"),
            output_dir=out_dir,
        )
        passthrough = _with_effective_mirror_flag(passthrough, graphene_mirrored)
    run_baseline(material, passthrough)

    if material == "top_hbn":
        if out_arg is not None:
            out_dir = Path(out_arg)
            for stale in out_dir.glob("top_hbn_candidate_*.png"):
                stale.unlink(missing_ok=True)
            (out_dir / "top_hbn_prompt_candidates.json").unlink(missing_ok=True)
            (out_dir / "top_hbn_candidate_masks.npz").unlink(missing_ok=True)
        print("OK: top_hbn baseline footprint copied; SAM candidates disabled")
        return 0
    if material == "bottom_hbn":
        if out_arg is not None:
            out_dir = Path(out_arg)
            keep_png = {"bottom_hbn_mask.png", "bottom_hbn_mask_bp.png"}
            for stale in out_dir.glob("bottom_hbn*.png"):
                if stale.name not in keep_png:
                    stale.unlink(missing_ok=True)
            for stale in out_dir.glob("01_host_mask_bp*.png"):
                stale.unlink(missing_ok=True)
            (out_dir / "bottom_hbn_prompt_candidates.json").unlink(missing_ok=True)
            (out_dir / "bottom_hbn_candidate_masks.npz").unlink(missing_ok=True)
        print("OK: bottom_hbn baseline masks copied; SAM candidates disabled")
        return 0

    image_arg = _arg_value(passthrough, "--target-image") if material == "bottom_hbn" else _arg_value(passthrough, "--image")
    pixel_arg = _arg_value(passthrough, "--pixel-size")
    if out_arg is None or image_arg is None or pixel_arg is None:
        raise SystemExit("missing --output-dir, --image/--target-image, or --pixel-size")
    out_dir = Path(out_arg)
    image = cv2.imread(str(image_arg), cv2.IMREAD_COLOR)
    if image is None:
        raise SystemExit(f"could not read image: {image_arg}")
    pixel_size = float(pixel_arg)
    mask_name, _, _ = MATERIAL_FILES[material]
    mask = _load_mask(out_dir / mask_name)
    display_image = cv2.flip(image, 1) if material == "graphene" and graphene_mirrored else image
    clean_grid = draw_grid(display_image, args.grid_step)
    cv2.imwrite(str(out_dir / f"{material}_source_grid_{args.grid_step}px.png"),
                clean_grid)
    graphite_prior_mask = None
    if material == "graphene":
        _autofill_graphene_graphite_prior(args, out_dir, image_arg)
        graphite_prior_mask = _write_required_graphite_prior(
            args,
            out_dir,
            clean_grid,
            display_image.shape[:2],
        )
    generation_signature = candidate_generation_signature(
        material, args, Path(image_arg), mask, graphene_mirrored
    )
    reusable_sidecar = load_reusable_sidecar(out_dir, material, generation_signature)
    if reusable_sidecar is not None:
        selected_rank = args.prompt_rank if args.prompt_rank >= 0 else int(reusable_sidecar.get("selected_rank", 8))
        if rewrite_selection_from_cache(
            material, out_dir, image, reusable_sidecar, selected_rank,
            pixel_size, display_image, args.grid_step,
            graphite_prior_mask=graphite_prior_mask,
        ):
            print(f"OK: {material} candidate cache reused; selected rank {selected_rank}")
            return 0
    elif args.manual_prompts_json is not None or args.use_sam2:
        clear_stale_candidate_cache(out_dir, material)

    if material == "graphite" and args.manual_prompts_json is None:
        sidecar = {
            "material": material,
            "selected_rank": 8,
            "selected_candidate": {
                "rank": 8,
                "kind": "baseline",
                "positive_points": [],
                "negative_points": [],
            },
            "candidates": [],
            "candidate_results": [],
            "candidate_images": [],
            "candidate_montage": None,
            "source_grid": f"{material}_source_grid_{args.grid_step}px.png",
            "manual_prompts_json": None,
            "sam2": {
                "attempted": bool(args.use_sam2),
                "status": "manual_grid_prompts_required",
            },
            "generation_signature": generation_signature,
            "note": "graphite SAM prompts must be supplied with --manual-prompts-json after inspecting the source grid",
        }
        (out_dir / f"{material}_prompt_candidates.json").write_text(json.dumps(sidecar, indent=2), encoding="utf-8")
        update_outputs_from_mask(material, out_dir, mask, pixel_size,
                                 selected_rank=8,
                                 selected_score=None)
        print(f"OK: graphite source grid written; manual grid prompts required before SAM candidates")
        return 0

    if material == "graphene" and args.use_sam2:
        sam_image = display_image
        baseline_mask = mask
        sam_mask, sidecar = run_graphene_sam_candidates(
            args, out_dir, sam_image, baseline_mask=baseline_mask,
            generation_signature=generation_signature,
            graphite_prior_mask=graphite_prior_mask,
        )
        sidecar["source_grid_mirrored"] = graphene_mirrored
        if sam_mask is not None:
            mask = sam_mask
            if graphite_prior_mask is not None:
                mask = finalize_graphene_mask(mask, graphite_prior_mask)
                sidecar["graphite_prior_auxiliary_only"] = True
            selected_result = next(
                (r for r in sidecar.get("candidate_results", [])
                 if r.get("rank") == sidecar.get("selected_rank")),
                {},
            )
            metrics = selected_result.get("metrics") or {}
            update_outputs_from_mask(
                material, out_dir, mask, pixel_size,
                selected_rank=sidecar.get("selected_rank"),
                selected_score=metrics.get("score"),
            )
        (out_dir / "graphene_prompt_candidates.json").write_text(json.dumps(sidecar, indent=2), encoding="utf-8")
        print(f"OK: graphene SAM2 candidates written; selected rank {sidecar['selected_rank']}")
        return 0

    if args.manual_prompts_json is not None:
        candidates = load_manual_prompt_candidates(args.manual_prompts_json, max(1, args.n_prompt_candidates))
    else:
        candidates = make_prompt_candidates(mask, pixel_size, max(1, args.n_prompt_candidates))
    candidates = candidates[:8]
    bx, by = _point_from_percentile(mask, 95)
    baseline_candidate = {
        "rank": 8,
        "kind": "baseline",
        "positive_points": [[int(bx), int(by)]],
        "negative_points": [],
    }
    all_candidates = candidates + [baseline_candidate]
    candidate_ranks = {int(c["rank"]) for c in all_candidates}
    if args.prompt_rank >= 0:
        if args.prompt_rank not in candidate_ranks:
            raise SystemExit(
                f"--prompt-rank {args.prompt_rank} is not available; "
                f"available ranks: {sorted(candidate_ranks)}"
            )
        selected_rank = args.prompt_rank
    else:
        selected_rank = -1
    candidate_results = []
    selected_mask = mask if selected_rank == 8 else None
    best_score = -1.0
    sam_info: dict[str, Any] = {"attempted": bool(args.use_sam2),
                                "status": "not_used"}
    draw_entries = []
    masks_by_rank: dict[int, np.ndarray] = {}

    for candidate in candidates:
        draw_mask = None
        result: dict[str, Any]
        if args.use_sam2:
            sam_mask, this_sam_info = _try_sam2(image, candidate, args)
            sam_info = this_sam_info
            if sam_mask is not None:
                metrics = _mask_score(sam_mask, mask, args.sam_target_frac)
                draw_mask = sam_mask
                result = {
                    "rank": candidate["rank"],
                    "sam2": this_sam_info,
                    "metrics": metrics,
                }
                if args.prompt_rank >= 0 and candidate["rank"] == selected_rank:
                    selected_mask = sam_mask
                if args.prompt_rank < 0 and metrics["score"] > best_score:
                    best_score = metrics["score"]
                    selected_rank = candidate["rank"]
                    selected_mask = sam_mask
                masks_by_rank[int(candidate["rank"])] = sam_mask
            else:
                result = {
                    "rank": candidate["rank"],
                    "sam2": this_sam_info,
                    "metrics": None,
                }
        else:
            result = {
                "rank": candidate["rank"],
                "sam2": {"attempted": False, "status": "not_used"},
                "metrics": None,
            }
        candidate_results.append(result)
        draw_entries.append((f"{material}_candidate_{candidate['rank'] + 1:02d}_on_grid.png",
                             draw_mask, candidate))

    baseline_metrics = _mask_score(mask, mask, args.sam_target_frac)
    candidate_results.append({
        "rank": 8,
        "kind": "baseline",
        "sam2": {"attempted": False, "status": "baseline_refined"},
        "metrics": baseline_metrics,
    })
    if args.prompt_rank < 0 and selected_mask is None:
        selected_rank = 8
        selected_mask = mask
    if args.prompt_rank == 8:
        selected_mask = mask
    draw_entries.append((f"{material}_candidate_09_on_grid.png",
                         mask, baseline_candidate))
    masks_by_rank[8] = mask

    if selected_mask is not None:
        mask = selected_mask
        if material == "graphene" and graphite_prior_mask is not None:
            mask = finalize_graphene_mask(mask, graphite_prior_mask)
        selected_result = next(
            (r for r in candidate_results if r.get("rank") == selected_rank),
            {},
        )
        metrics = selected_result.get("metrics") or {}
        update_outputs_from_mask(material, out_dir, mask, pixel_size,
                                 selected_rank=selected_rank,
                                 selected_score=metrics.get("score"))
    for name, draw_mask, candidate in draw_entries:
        cv2.imwrite(str(out_dir / name),
                    draw_candidate_on_grid(display_image, draw_mask, candidate,
                                           candidate["rank"] == selected_rank,
                                           args.grid_step))

    candidate_images = [name for name, _, _ in draw_entries]
    montage = write_candidate_montage(out_dir, material, candidate_images)
    save_candidate_mask_cache(out_dir, material, masks_by_rank)
    selected_candidate = _candidate_by_rank(all_candidates, selected_rank)
    sidecar = {
        "material": material,
        "selected_rank": selected_rank,
        "selected_candidate": selected_candidate,
        "candidates": all_candidates,
        "candidate_results": candidate_results,
        "candidate_images": candidate_images,
        "candidate_montage": montage,
        "source_grid": f"{material}_source_grid_{args.grid_step}px.png",
        "manual_prompts_json": str(args.manual_prompts_json) if args.manual_prompts_json is not None else None,
        "sam2": sam_info,
        "generation_signature": generation_signature,
    }
    if material == "graphene":
        sidecar["source_grid_mirrored"] = graphene_mirrored
    (out_dir / f"{material}_prompt_candidates.json").write_text(json.dumps(sidecar, indent=2), encoding="utf-8")
    print(f"OK: {material} prompt candidates written; selected rank {selected_rank}; sam2={sam_info.get('status', 'not_used')}")
    return 0
