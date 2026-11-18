# Generate mannequin references with SpicyAPI

## Browser interface

Type `sh ` (including the space) in Terminal, drag **`start_pose_studio.sh`**
into the window, then press Return. Using `sh` also works for downloaded copies
that have lost executable permission. The launcher finds the project beside
itself or at `~/code/ComfyUI_PoseGenerator`; `POSE_STUDIO_DIR` can override this.
It opens Pose Studio at `http://127.0.0.1:8765`, regardless of Terminal's current
directory. Keep that Terminal window open while generating.
The existing project environment already has its dependencies. For a separate
environment, install `requirements-studio.txt`; ComfyUI and torch are not needed.

1. Choose an image folder, or click **Use staged v2 folder**. Folder upload
   copies PNG, JPEG, and WebP files locally; caption files are ignored. The limits
   are 500 images, 1 GiB per folder, and 10 MiB / 8 megapixels per image.
2. Upload a style reference or use a `spicy://` reference. Upload a local style
   image when an older remote reference has expired.
3. Select images in the gallery. **Clear**, then check one image for a pilot.
   The default preset is Mannequin v2 with source-first image order. Prompts are
   editable in the browser; the original prompt files are not changed.
4. Set the per-image and total spending caps, then click **Generate selected
   images**. Actual API quotes must fit both caps. Images and style are sent to
   SpicyAPI at this point; the API key remains on the local server.
5. Results appear as each image finishes. Hover over a finished image and move
   left/right to wipe between its original photo and generated drawing. Moving
   off the image restores the drawing. Click for a larger wipe comparison,
   a touch/keyboard reveal slider, and a PNG download. Outputs and the ComfyUI-compatible catalog are
   saved in a separate `assets/gallery-studio/<run-id>/` for every new run.

**Pause after current** finishes the accepted image, then stops the queue.
Closing the browser does not stop generation. Restarting the server never starts
paid jobs automatically: choose a saved run and **Resume run** to recover it.
Resuming preserves the original settings, spending cap, and job IDs, and skips
completed outputs. It does not regenerate failed API jobs. **Use settings for
new run** copies a saved run's source and settings into a new, editable run with
a separate budget; selected images will be generated again.

**Regenerate** replaces that image in the same gallery and enlarged comparison,
using the same source, style, prompt, image order, and resolution. The previous
image stays visible until a verified replacement is ready. Its original output
path and catalog entry then use the new result; older versions are backed up
under the batch's `history/` directory. Other images and edited catalog labels
are preserved. Retries do not create additional entries in Saved runs.

Each retry is a new paid generation with a separate cap equal to the per-image
cap shown on the button. Run totals include retry charges and their authorized
caps. Click **Regenerate** on several images in succession: each is queued in
click order, with its position shown on the thumbnail. The server processes one
at a time, replacing each result in the same batch. Closing the browser does not
stop the queue; keep the server running. A failed attempt keeps its previous
image and lets the remaining queued images continue. Repeated clicks on the same
pending image do not add duplicate paid attempts.

**Pause after current** pauses the whole queue after the accepted image finishes.
Queued work is saved across server restarts and stays paused until **Resume
queue** is clicked. Interrupted retries recover their saved job through
**Resume retry**. The server also adopts completed retries from the older
interface on startup, without making API requests.

The server binds only to loopback, rejects cross-origin writes, and does not
expose `.env`, API credentials, or raw provider receipts to the browser. Browser
uploads are saved in the ignored `.cache/pose-studio/` directory. Existing CLI
batches remain separate; the Saved runs selector lists runs created by this UI.

To launch manually, or choose a different port:

```sh
.venv/bin/python scripts/pose_studio.py --open --port 8765
```

## Command-line interface

`scripts/generate_pose_references.py` sends a drawing-style reference as Image 1
and each source photo as Image 2 to `bytedance/seedream-5.5/edit`. It uses
`scripts/prompts/pose-mannequin.txt`, which preserves the visible pose and crop,
removes identity and scene details, and requests white mannequin line art.
Occluded anatomy remains an approximation that needs visual review.

Install `requests` and `Pillow` in the Python environment running the script.
The project development environment already includes both. The key is read from
`SPICY_API_KEY` or `SAI_API_TOKEN`, first from the environment and then from the
project `.env`. It is never printed or stored in batch receipts.

## One-image pilot

From the repository root:

```sh
.venv/bin/python scripts/generate_pose_references.py \
  --source /absolute/path/to/source-images \
  --style /absolute/path/to/mannequin-style.png \
  --pattern specific-source.jpeg \
  --limit 1
```

`--style` also accepts an existing `spicy://f/fil_...` uploaded file or
`spicy://a/ast_...` saved asset. An uploaded file normally expires after one day;
a local style image can be uploaded again automatically. A saved asset remains
usable until deleted. Keep the same style argument when resuming a destination.

The default output directory is `assets/gallery-mannequin`, which Git already ignores.
The default is one image, 2k PNG, an opaque white background, and the supported
aspect ratio closest to the source. For 880×1184 sources, that is 3:4, a slight
approximation of the original ratio. Source bytes are never resized or overwritten.

## Full batch and cost limits

Use `--limit 0` to select all images. Omit `--pattern` to include every PNG, JPEG,
and WebP recursively. Add `--dry-run` to inventory the selection without any API
requests, uploads, or file writes.

The script obtains an exact quote for each request, then submits the unchanged
request with that quote and its expected cost. Defaults are a maximum of
USD 0.051 per image and USD 1.50 cumulatively across the destination's recorded
requests. At that per-image ceiling, 28 images total at most USD 1.428. These are
configured spending limits, not a claim about the current account price. A higher
quote stops the run before creation; it is never accepted automatically.

Use `--max-per-image` and `--max-total` to set other limits. The cumulative cap
conservatively counts every submitted or uncertain request, including previous
runs and failures, without assuming a refund. Processing is sequential.

## Uploads and recovery

Local images use the documented upload-ticket → PUT → commit flow. Only the
committed URI enters `image_urls`. The API bearer key is never sent to upload or
download storage. Uploads are cached by content hash until their expiry.

`batch.json` preserves the exact request, quote, idempotency key, task ID, hashes,
and billing status. Run the same command after an interruption: completed outputs
are verified and skipped; accepted tasks are polled and downloaded again if
needed. A succeeded task is not considered downloadable while assets are pending.
Stopping this script does not cancel an accepted remote task.

Do not delete `batch.json` to retry a failed download. If a create response was
lost, the same request and idempotency key are reused only within 23 hours of the
first attempt. Beyond that, the script stops: recover the task ID from SpicyAPI's
logs before doing anything that could create a duplicate. Failed or unavailable
outputs are never regenerated automatically. A changed prompt, style, source
content, model settings, or API key requires a different destination.

## Change the prompt or output settings

Pass a text file with `--prompt /absolute/path/to/pose-mannequin-v2.txt` and use
a new folder with `--destination /absolute/path/to/assets/gallery-mannequin-v2`.
The folder is created automatically. Each destination has its own batch state
and cumulative spending cap; a new destination does not skip images completed
in another destination. Keep `--limit 1` for a new-version pilot, then use the
same destination and prompt with `--limit 0` to complete that version.

To resume a previous version, use its original prompt text, style, source,
resolution, and API key. Changes to `--limit`, `--pattern`, or the spending caps
do not change the generation configuration. A mismatch error names the differing
fields without printing prompt contents or key fingerprints.

### Source-first prompt variant

`scripts/prompts/pose-mannequin-v2.txt` treats Image 1 as the photo to edit
and Image 2 as linework guidance only. Use it with `--image-order source-first`
in a new destination. It emphasizes the original crop, visible joint locations,
and a single figure, addressing failures where the style mannequin replaces or
is overlaid on the photographed pose. This is a prompt experiment, not a guarantee
of pose fidelity. Review a failed-source pilot before running the whole dataset.

```sh
.venv/bin/python scripts/generate_pose_references.py \
  --source /absolute/path/to/source-images \
  --style spicy://f/fil_your_style_reference \
  --prompt scripts/prompts/pose-mannequin-v2.txt \
  --image-order source-first \
  --destination assets/gallery-mannequin-prompt-v2 \
  --pattern specific-source.jpeg \
  --limit 1 --max-total 0.051
```

Without `--image-order`, the existing style-first order remains unchanged.
Always match the image numbers in the prompt to the chosen order. Resuming a
saved job reuses its exact original input order and prompt.

`scripts/prompts/pose-mannequin-v3.txt` keeps the same source-first layout
and explicitly removes bedding silhouettes and foreground object contours. It
also prohibits converting those outlines into invented limbs or body volumes.
Use a new destination such as `assets/gallery-mannequin-prompt-v3` for this variant.

## Review and use

Each completed PNG is saved under `img/`. `catalog.json` contains completed,
verified outputs with blank pose prompts and an `Unreviewed` category. Source
caption files are not sent to the model or copied into the catalog because they
contain identity, outfit, and scene information.

Review pose fidelity, hands, limb overlaps, framing, and unwanted shading. Fill
in the catalog's `label`, `prompt`, `category`, and `angle` for accepted references;
these edits survive a resumed run. To browse this library in ComfyUI, set
`POSE_GENERATOR_ASSETS` to its absolute directory before starting ComfyUI.

## API references

- [Seedream 5.5 edit parameters](https://spicyapi.ai/models/seedream-5-5/edit/api)
- [Media uploads and inline Base64 limits](https://docs.spicyapi.ai/docs/media)
- [Exact-input quotes](https://docs.spicyapi.ai/docs/quotes-and-compatibility)
- [Idempotency and the 24-hour replay window](https://docs.spicyapi.ai/docs/idempotency)

## Curating a Studio folder

Open the folder under **Saved runs**. After its generation queue finishes,
**Delete** moves a drawing to the folder's `trash/` and removes its catalog
entry. **Undo last delete** restores the most recently deleted drawing. Source
photos and generation receipts stay in place.

**Rename folder** changes the saved-run name and its Finder shortcut under
`assets/gallery-studio/by-name/`. **Add images** accepts finished mannequin
PNGs for direct import or source photos for generation. The photo option shows
the additional spending ceiling before submission and uses this folder's image
generation settings. Imported drawings can be captioned and deleted.

Under **Folder metadata**, choose Grok 4.7 (project `GROK_API_KEY` or
`XAI_API_KEY`) or SpicyAPI (the existing generation key). Each folder starts
with an editable **Caption prompt** for short, neutral descriptions of the
visible pose. **Save prompt** saves the wording without sending an image.
**Test one** and **Tag remaining** also save the prompt used for that pass.
Changing the prompt or model makes existing drawings eligible for a new pass.
Each drawing keeps its current caption until a replacement succeeds. **Test one** captions
the first remaining drawing. **Tag remaining** processes the rest in folder
order. Each request sends one generated drawing, resized locally, and is billed
by the selected provider. Keep the Studio server running while it works.

Click **Caption** on a finished thumbnail or in its detail view to caption
that specific drawing, even if it already has a caption. It uses the selected
vision model and folder prompt. A successful single-image request replaces
the image caption.

Click **Caption** on several thumbnails to queue them in click order. The
server processes one caption at a time per folder. Queued requests keep their
prompt and provider, and failures appear on the individual cards. After a
Studio restart, use **Resume captions** for requests that had not started.
An interrupted in-flight request is marked for review before a paid retry.

In detail view, edit **Image caption**, select or clear tags in each row,
and click **Save changes**. Manual edits are local and appear in the grid,
filters, and catalog immediately. Folder-wide passes preserve manual captions.
Both captioning actions preserve manually edited tag rows for that drawing.
**Delete** is available in either view, with **Undo last delete** in the folder.

Successful captions appear on each card and in the folder's `catalog.json`.
The PNG, its download name, and the catalog ID follow the existing pose naming
convention. Each folder reserves pose numbers so separately generated folders
can be combined. Posture and figure-count tags such as `standing`, `sitting`,
`solo`, and `pair` are searchable in the ComfyUI picker. Drawings the model
cannot classify are marked for review. A later pass skips processed drawings
and picks up changed regenerations.

**Edit global tags** manages the shared vocabulary. Add or remove values in the
camera position, character stance, character count, and gesture rows, or create
a new row. Saving a vocabulary change updates the available filters and makes
drawings eligible for another caption pass. Each catalog entry stores the
selected tags by row in `tag_groups` and as a flat `tags` list. In Studio,
selecting `sitting` and `kneeling` in one row matches either stance. Selecting
`pair` in the count row narrows those results to two-person poses.

## Depth maps on a ComfyUI server

Set `POSE_DEPTH_COMFY_URL` in the project's local `.env` to the base URL of
your ComfyUI server. Install a native DA3 checkpoint, such as
`depth_anything_3_mono_large.safetensors`, in its `models/geometry_estimation/`
directory. Studio checks the server's installed models before uploading an image.

**Test one depth map** sends one finished mannequin drawing to that server.
Inspect **Depth PNG** in the drawing's detail view. **Generate remaining depth maps**
processes each missing map in the folder. Studio uses DA3's mono mode and
saves grayscale PNGs under `depth/`, keyed by the drawing's SHA-256. A caption
edit reuses the map. A changed drawing becomes eligible for a new map.

In the ComfyUI reference node, choose **Saved depth** under `pose_image_mode`
to send the map through `pose_image`. **Drawing** is the default. The node reads
the selected gallery from the machine running that node. For a separate
installation, copy the gallery, including its catalog, images and depth folder.

[Official ComfyUI DA3 checkpoints](https://huggingface.co/Comfy-Org/Depth-Anything-3/tree/main/geometry_estimation)
