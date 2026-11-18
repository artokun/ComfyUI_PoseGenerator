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

## Add your own outfits

Outfits live in a **folder gallery**: any folder directly under `assets/` with a
`catalog.json`. The pack ships one, `assets/Outfits/` (71 outfits); its first entry
(`preppy_O001`) is the template to copy. Put new outfits there, or in your own
folder such as `assets/My Outfits/`. A folder that only holds outfits does not
appear in the **Pose gallery** picker; its outfits show up in **Browse outfits**
and the `outfit` dropdown. Restart ComfyUI (or reload the page) after adding rows.

### Naming

Name each outfit `{style}_O{NNN}`: the lowercase style, `_O`, and a three-digit
number, for example `preppy_O001`, `streetwear_O004`. This mirrors the mannequins
(`full_single_P001_front`). Use the same name for the image file, the thumbnail
and the catalog `id`: `img/preppy_O001.webp`, `thumbs/preppy_O001.webp`.

### Catalog row

```json
{
 "id": "preppy_O001",
 "label": "Sage cropped cardigan, cream lace cami, plaid pleated mini skirt, ruffle socks, platform loafers",
 "prompt": "wearing a sage-green cropped knit cardigan over a cream lace camisole, a sage and cream plaid pleated mini skirt, white ruffled ankle socks and cream platform loafers",
 "category": "Preppy",
 "image": "img/preppy_O001.webp",
 "thumbnail": "thumbs/preppy_O001.webp",
 "tags": ["ankle socks", "autumn", "camisole", "cardigan", "cream", "knit", "lace", "loafers", "plaid", "sage green", "skirt", "spring", "white"],
 "tag_groups": {
  "Top": ["cardigan", "camisole"],
  "Bottom": ["skirt"],
  "Legs": ["ankle socks"],
  "Feet": ["loafers"],
  "Colors": ["sage green", "cream", "white"],
  "Patterns & fabrics": ["plaid", "lace", "knit"],
  "Season": ["spring", "autumn"]
 }
}
```

- `label`: the caption in **Browse outfits**; list garments top to bottom.
- `prompt`: what the node writes into `{outfit}`; start with "wearing".
- `category`: one style, which fills the category filter: Casual, Preppy,
  Streetwear, Formal, Business, Party, Sporty, Loungewear, Swimwear, Boho,
  Y2K, Gothic, Cottagecore.
- `tag_groups`: one row per kind of tag, each row a list. **Browse outfits** turns
  every row into filter chips; chips in one row match any, chips in different rows
  must all match ("skirt" + "sage green"). `tags` repeats every tag flat, for search.

Use these rows and lowercase words so filters stay consistent across outfits:

| Row | What goes in it | Example tags |
|---|---|---|
| Head | headwear and hair accessories | hat, cap, beret, beanie, headband, hair bow, hair clip |
| Outerwear | layers worn over the top | blazer, jacket, leather jacket, denim jacket, coat, trench coat, puffer, vest |
| Top | upper-body garments | t-shirt, blouse, shirt, camisole, tank top, crop top, sweater, cardigan, hoodie, corset |
| Bottom | lower-body garments | skirt, mini skirt, jeans, trousers, shorts, leggings, cargo pants |
| Full body | one piece covering top and bottom | dress, mini dress, maxi dress, slip dress, jumpsuit, romper, overalls |
| Underwear | visible underwear or lingerie | bra, bralette, slip, garter belt |
| Legs | anything worn on the leg, any length | tights, stockings, thigh-highs, knee-high socks, ankle socks, leg warmers |
| Feet | footwear only | sneakers, loafers, boots, ankle boots, heels, sandals, flats |
| Accessories | carried or worn extras | bag, belt, scarf, gloves, jewelry, sunglasses |
| Colors | main colours, most visible first | black, white, cream, beige, brown, grey, navy, blue, sage green, green, red, pink, purple, yellow, orange |
| Patterns & fabrics | prints and materials | plaid, stripes, floral, polka dot, denim, leather, knit, lace, satin, linen |
| Season | when it would be worn | spring, summer, autumn, winter |

Socks always go in **Legs**, whatever their length; **Feet** is only footwear.
Leave out a row an outfit does not use.

### Rendering an outfit

Outfits are ghost-mannequin renders: clothes floating in their worn shape with no
body, so they never fight the pose. `scripts/prompts/outfit-ghost-mannequin.txt`
is the prompt; replace `{outfit}` with a description of the garments. Name the
footwear: with a style reference, an outfit without shoes tends to borrow the
reference's. Check there is no skin between garments before adding an outfit.
Save the image as WebP (quality about 90) and a thumbnail about 512 px on the
long edge.

The bundled set was made with these scripts (paid, optional; the node never
calls them):

```sh
# 1. Describe outfits in scripts/outfits/outfit_specs.json (id, label, prompt, category, tags, tag_groups).
# 2. Render with gpt-image-2 through the Batch API (half price, usually minutes):
OPENAI_API_KEY=... python scripts/generate_outfits.py --batch
OPENAI_API_KEY=... python scripts/generate_outfits.py --collect
# 3. Any refusals: render those ids with Seedream 5.5 instead.
SPICY_API_KEY=... python scripts/generate_outfits_seedream.py --only <ids>
# 4. Build the gallery (WebP, thumbnails, catalog).
python scripts/build_outfit_gallery.py --dest "assets/My Outfits"
```

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
