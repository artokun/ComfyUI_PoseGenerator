# Changelog

## 0.1.3

- Ship two pose galleries made with Pose Studio from our own photos: **Instagram Poses** (28 drawings, 24 with saved depth maps) and **OF poses** (24 drawings). They appear in the **Pose gallery** dropdown with no setup. Drawings are WebP with thumbnails; depth maps sit beside the drawings.
- Folder galleries can carry **outfits**, not just poses. Ship `assets/Outfits/` with **71 original ghost-mannequin outfits** across 14 categories: Casual, Preppy, Streetwear, Formal, Business, Party, Sporty, Loungewear, Boho, Y2K, Gothic, Cottagecore, Lingerie and Swimwear. Each has a label, a `wearing …` prompt and body-zone tags. The descriptions are our own, rendered with OpenAI gpt-image-2: `preppy_O001` from text alone as the style baseline, every other outfit with it as the presentation reference.
- Add the outfit tooling: `scripts/outfits/outfit_specs.json` (the descriptions and tags), `scripts/generate_outfits.py` (gpt-image-2, per request or `--batch` / `--collect` through the OpenAI Batch API at half price), `scripts/generate_outfits_seedream.py` (Seedream 5.5 on SpicyAPI, for outfits OpenAI refuses) and `scripts/build_outfit_gallery.py` (renders to WebP gallery).
- **Browse outfits** filters by grouped tags the same way Browse poses does. Outfit rows follow the body: Head, Outerwear, Top, Bottom, Full body, Underwear, Legs, Feet, Accessories, plus Colors, Patterns & fabrics and Season.
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
