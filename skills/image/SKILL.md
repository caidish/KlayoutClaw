---
name: klayoutclaw:image
description: Load a reference image (microscope photo, optical image, SEM) into KLayout as a background overlay for design alignment. Use this skill whenever the user wants to load an image, add a background image, overlay a microscope photo, import a reference picture, align to a photo, or trace over an image. Also trigger when the user says "load this image", "use this photo as reference", "overlay the microscope image", or needs to position/scale/remove background images.
---

# KLayout Image Overlay

Load reference images (JPG, PNG, BMP) into KLayout as background overlays for aligning device geometry to real microscope photos.

## Prerequisites

- KLayout running with KlayoutClaw plugin (v0.5+)
- A layout must be open (use `create_layout` first if needed)

## Scripts

### add_image.py — Load an image as background overlay

```bash
python scripts/add_image.py <filepath> [--pixel-size 0.1] [--scale-bar <um> <pixels>] [--x 0] [--y 0] [--center]
```

- `filepath` — Path to image file (JPG, PNG, BMP)
- `--pixel-size` — Microns per pixel (default: 1.0)
- `--scale-bar` — Derive pixel size from a scale bar: `<length_um> <length_pixels>`. Takes priority over `--pixel-size`.
- `--x`, `--y` — Position offset in microns (default: 0, 0)
- `--center` — Center the image at the given position (default: image corner at position)

Example — set pixel size directly:
```bash
python scripts/add_image.py ~/photos/graphene.jpg --pixel-size 0.1
```

Example — derive pixel size from a 20 um scale bar that spans 153 pixels:
```bash
python scripts/add_image.py ~/photos/graphene.jpg --scale-bar 20 153 --center
# Output: Scale bar: 20.0 um / 153.0 px = 0.1307 um/px
```

Example — load and center at a specific position:
```bash
python scripts/add_image.py ~/photos/flake.png --pixel-size 0.05 --x 100 --y 50 --center
```

### list_images.py — List all background images in the view

```bash
python scripts/list_images.py
```

Prints a table of all loaded images with their ID, filename, position, and visibility.

### remove_image.py — Remove a background image

```bash
python scripts/remove_image.py <image_id | all>
```

- `image_id` — Numeric ID of the image to remove (from `list_images.py`)
- `all` — Remove all background images

## Workflow

1. Load a microscope image as reference background
2. Use geometry scripts to draw device features (contacts, gates, mesa) on top
3. Adjust pixel-size to match the image's physical scale (check the scale bar)
4. Use `list_images.py` to see loaded images, `remove_image.py` to clean up

## Estimating pixel-size

If the image has a scale bar of length `S` microns spanning `P` pixels:
```
pixel-size = S / P
```

For example, a 20 um scale bar that spans 200 pixels gives `pixel-size = 0.1`.
