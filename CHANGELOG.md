# Changelog

## 0.1.1

- Choose a pose gallery on the node: named Pose Studio folders and other catalogs under `assets/` appear in a **Pose gallery** dropdown, and **Browse poses** can switch galleries.
- Filter thumbnails by saved tags as well as category, camera angle, and search.
- Add a **pose_image_mode** input: output the pose drawing, or a saved depth map for that pose.
- Stop listing `torch` as a dependency. ComfyUI already provides it, and installing it from the node pack could replace a working CUDA build.

## 0.1.0

- Add a single Pose & Outfit Reference node with two image outputs and three text outputs.
- Browse local thumbnails by search, category, and camera angle.
- Support English descriptions, source labels, and literal prompt templates in local catalogs.
- Add a resumable Tinify WebP optimization helper for permitted local images.
- Include a model-free preview workflow, registry artwork, and matching GitHub social card.
- Credit the original gallery creators and preserve their notices; withhold gallery redistribution and bulk setup pending permission.
- Run shared tests, packaging checks, Bandit, and known registry YARA patterns before upload.
