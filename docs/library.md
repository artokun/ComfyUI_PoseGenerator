# Local reference libraries

Use images and descriptions you have permission to use. The original pose and
outfit sites prohibit bulk downloading and redistribution of their images; the
gallery download experiment and its derived catalog are not distributed with this
integration. See [the original notices and credits](../THIRD_PARTY.md).

## Set up a library

Create a folder containing your images and `catalog.json`. Put it at
`assets/gallery` beside the node's Python files, or set `POSE_GENERATOR_ASSETS`
to its absolute path before starting ComfyUI.

```json
{
  "schema_version": 1,
  "pose": [{
    "id": "standing-front",
    "label": "Standing with a hand on the hip",
    "prompt": "standing with a hand on the hip, front view",
    "category": "Standing",
    "angle": "front view",
    "image": "img/standing-front.webp",
    "thumbnail": "thumbs/standing-front.webp"
  }],
  "outfit": [{
    "id": "blue-jacket",
    "label": "Blue jacket",
    "prompt": "blue jacket",
    "category": "Casual",
    "image": "img/blue-jacket.webp",
    "thumbnail": "thumbs/blue-jacket.webp"
  }]
}
```

Replace these example filenames with your own files. `image` and `thumbnail`
must resolve to PNG, JPEG, or WebP files inside the library. They may point to the
same file for a small collection. IDs must be unique within their reference type.

The node adds “wearing” to an outfit description if necessary. Use your own
English descriptions in `prompt`; the browser displays `label`. Reopening the
browser refreshes its catalog. Reload ComfyUI's node definitions after adding
entries if you use the native ID dropdowns.

An explicit `POSE_GENERATOR_ASSETS` takes precedence. Otherwise the node uses
`assets/gallery-webp` when its completed catalog is present, then `assets/gallery`.
An empty library still lets you compose text with no references selected.

With the standard asset location, the node's **Pose gallery** dropdown includes
named Pose Studio folders and other catalogs directly under `assets/`. Studio
retry folders are omitted. The original PNG and optimized WebP libraries share
one entry. An explicitly configured external library can be browsed independently.

Choose a gallery on the node to narrow its pose IDs, or switch galleries inside
**Browse poses**. Tag filters use the rows saved with each drawing. Selecting
two tags in one row matches either one. Selections in different rows must all
match. Legacy catalogs can still use their category and camera angle filters.

## Generate your own mannequin references

The optional [SpicyAPI batch helper](pose-generation.md) converts your source
photos into mannequin line art using a separate style reference. It supports
one-image pilots, exact-price caps, resumable tasks, and a catalog for review.
This is an explicit paid development tool; the ComfyUI node never calls it.

## Optional WebP optimization with Tinify

For a collection you have permission to upload and transform, the source checkout
includes a resumable optimizer.
The optimizer expects a prepared collection with `catalog.json`, a `source.json`
manifest containing `revision` and `files` (each with a `path`), a `LICENSE` notice,
and a `data/` directory. A plain catalog alone is not enough for this helper.

Put your Tinify key in an untracked `.env`:

```text
TINIFY_API_KEY=your_key_here
```

```sh
python -m pip install requests Pillow
python scripts/optimize_assets.py --source /absolute/path/to/library --destination /absolute/path/to/library-webp --jobs 12
```

This explicit step sends images and thumbnails from the library's `img/` and
`thumbs/` folders to Tinify. It preserves dimensions and keeps the originals.
Results are WebP; an already smaller source WebP is retained. Conversion can
consume an additional compression credit, so review your Tinify account allowance.

Successful uploads and verified results are recorded so interrupted runs resume.
Authentication or quota failures stop the batch. The helper never logs the key.
Only after all files succeed does it write the new catalog. Configure the resulting
folder as your library, or use the default `assets/gallery-webp` destination.

The optimizer is a development helper, excluded from the registry archive. Tinify
is optional and is never called by the node during workflow execution.
