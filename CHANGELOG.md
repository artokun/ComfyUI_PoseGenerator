# Changelog

## 0.1.3

- Ship two pose galleries made with Pose Studio from our own photos: **Instagram Poses** (28 drawings, 24 with saved depth maps) and **OF poses** (24 drawings). They appear in the **Pose gallery** dropdown with no setup. Drawings are WebP with thumbnails; depth maps sit beside the drawings.
- Folder galleries can carry **outfits**, not just poses. Ship `assets/Outfits/` with one ghost-mannequin outfit rendered with MiniMax H3 Image (`preppy_O001`), which is also the template for adding your own.
- **Browse outfits** filters by grouped tags (Tops, Bottoms, Colors, Season, …) the same way Browse poses does.
- Document the outfit naming convention (`{style}_O{NNN}`), catalog fields, tag vocabulary and the ghost-mannequin prompt (`scripts/prompts/outfit-ghost-mannequin.txt`) in docs/library.md.
- The **Pose gallery** picker no longer offers an empty "Original gallery" when it isn't installed, or folders that only hold outfits.
- Add `scripts/bundle_gallery.py`, which turns a finished Pose Studio run into a shippable folder gallery (drawings, depth maps and reviewed catalog rows only; no source photos, comparisons, history or batch receipts).
- The release check allowlists those two galleries (WebP and JSON only) and still refuses the original gallery.

## 0.1.2

- **Saved depth** now also reads depth maps stored beside the drawing (`img/depth/<drawing name>`), the layout the original gallery uses. Before, only Pose Studio's `depth/` folder worked, so every original-gallery pose failed in Saved depth mode.

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
