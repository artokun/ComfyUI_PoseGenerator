# Changelog

## Unreleased

- **Location picture.** Connect a picture of a place to the new optional `location_image` input and the result is set there: its setting, background and lighting, not any people in it. It is the last numbered picture and comes out on the new `picture_4` output (or an earlier one when the subject, pose or outfit is missing). It takes over from "From the subject photo"; **Plain studio** ignores it, and with **Custom** the picture is passed on and you describe the scene in `extra`. Tested on MiniMax H3 Image.
- Pose Studio gets an **Outfits** page (`/outfits`) that makes outfit references in queued runs, the way the Poses page makes drawings:
  - **Pictures**: choose a folder, pick files, or drop or paste images onto the page.
    - **A person**: posed in an A-pose or I-pose on white, then removed, keeping the clothes as a ghost mannequin. Posing can be skipped.
    - **Flat lay or collage** (for example a Pinterest outfit board): the separate pieces are assembled into one worn outfit in a single step. Brand names, logos and text are removed, and items that are not worn (perfume, cosmetics, phones) are left out.
  - **Descriptions**: one outfit per line becomes a ghost-mannequin outfit.
  - Each run uses OpenAI gpt-image-2 or Seedream 5.5 on SpicyAPI. Results are 1152 × 1536 (3:4).
  - Runs queue one after another, can be paused after the current outfit and resumed, and never restart paid work by themselves after the server stops. A failed outfit does not stop the run and can be retried; the pose or the outfit of any item can be regenerated.
  - SpicyAPI requests are quoted first and held to a per-image and a total cap; an accepted job is picked up again after a restart, not submitted twice.
  - **Caption outfits** asks a vision model (Grok or Nemotron VL) for each outfit's label, prompt, category and body-zone tags, all editable.
  - **Save outfits to gallery** writes them to a folder gallery under `assets/` with `{category}_O001` IDs. Move that folder into `assets/shipped/` to ship it.
  - Development tool only: the ComfyUI node never calls it.

## 0.1.5

- **`location` and the edit opener now work with any prompt template.** A template without `{location}` or `{edit}`, which includes every workflow saved before 0.1.4, ignored the location setting and left out the "Edit <Picture 1>:" opener. The node now adds both itself when the template leaves them out: the opener at the start and the location before `{extra}`. To leave the location out, set `location` to **Custom**.

## 0.1.4

- **Subject photo.** Connect a photo of a person to the new optional `subject_image` input and the prompt refers to that person by picture number.
- **Numbered pictures.** New outputs `picture_1`, `picture_2`, `picture_3` hold the subject, pose and outfit that exist, in the order the prompt numbers them. Wire them to your model's image 1, 2, 3. An unselected reference leaves its slot empty instead of sending a black square. `width` and `height` report the size of `picture_1`.
- **Picture-aware prompt.** `picture_tag` sets how the prompt names a picture (`<Picture {n}>` for MiniMax H3, `image {n}` for Qwen Edit, Kontext or Seedream, empty for a plain text prompt). `{subject}`, `{pose}` and `{outfit}` then point at their pictures, and new `{edit}` and `{location}` fields open the edit instruction and set the scene.
- **`edit_target`**: which picture the model edits. **Pose** (default) keeps the drawing's pose, framing and camera angle. **Subject** keeps the photo's scene.
- **`location`**: with the pose as the edited picture, take the place and lighting **from the subject photo** (default), use a **plain studio**, or describe it yourself in `extra`.
- **`megapixels`** resizes `picture_1` (sides are multiples of 32), and **`reference_text`** chooses whether the pose and outfit descriptions are added alongside the pictures.
- Workflows saved with earlier versions load unchanged: new inputs and outputs are appended, and old workflows keep the plain text prompt.
- Add a MiniMax H3 Image example: `examples/minimax-h3-image-edit.json` (drop on the canvas) and `examples/minimax-h3-image-edit-api.json` (API format). Tested on MiniMax H3 Image.

## 0.1.3

- Ship two pose galleries made with Pose Studio from our own photos: **Instagram Poses** (28 drawings) and **OF poses** (22 drawings), every drawing with a saved depth map. They appear in the **Pose gallery** dropdown with no setup. Drawings are WebP with thumbnails; depth maps sit beside the drawings.
- Folder galleries can carry **outfits**, not just poses. Ship `assets/Outfits/` with **91 original ghost-mannequin outfits** across 15 categories: Instagram (20 trend looks: coquette, balletcore, clean girl, Parisian, tenniscore, brunch, cozy and more), Casual, Preppy, Streetwear, Formal, Business, Party, Sporty, Loungewear, Boho, Y2K, Gothic, Cottagecore, Lingerie and Swimwear. Each has a label, a `wearing …` prompt and body-zone tags. The descriptions are our own, rendered with OpenAI gpt-image-2: `preppy_O001` from text alone as the style baseline, every other outfit with it as the presentation reference.
- Add the outfit tooling: `scripts/outfits/outfit_specs.json` (the descriptions and tags), `scripts/generate_outfits.py` (gpt-image-2, per request or `--batch` / `--collect` through the OpenAI Batch API at half price), `scripts/generate_outfits_seedream.py` (Seedream 5.5 on SpicyAPI, for outfits OpenAI refuses) and `scripts/build_outfit_gallery.py` (renders to WebP gallery).
- **Browse outfits** filters by grouped tags the same way Browse poses does. Outfit rows follow the body: Head, Outerwear, Top, Bottom, Full body, Underwear, Legs, Feet, Accessories, plus Colors, Patterns & fabrics and Season.
- Document the outfit naming convention (`{style}_O{NNN}`), catalog fields, tag vocabulary and the ghost-mannequin prompt (`scripts/prompts/outfit-ghost-mannequin.txt`) in docs/library.md.
- The **Pose gallery** picker no longer offers an empty "Original gallery" when it isn't installed, or folders that only hold outfits.
- Add `scripts/bundle_gallery.py`, which turns a finished Pose Studio run into a shippable folder gallery (drawings, depth maps and reviewed catalog rows only; no source photos, comparisons, history or batch receipts).
- `scripts/bundle_gallery.py` also holds back drawings whose category is `Unreviewed` (flagged for pose review).
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
