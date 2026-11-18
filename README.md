# Pose & Outfit Reference for ComfyUI

![Pose & Outfit Reference for ComfyUI](assets/banner.png)

Pick a pose. Pick an outfit. Send the reference images and a ready-to-edit
positive prompt into your ComfyUI workflow.

## Credit where it belongs

**This is a ComfyUI adaptation of an existing gallery project. I did not create
the original gallery/tool or its reference assets, and I do not claim them as my
work.** My contribution is this small, independent ComfyUI integration.

Huge thanks to **[ギア / @giaaibusi](https://x.com/giaaibusi)** for the original
[pose](https://ai-pose-gallery.pages.dev/) and
[outfit](https://ai-costume-gallery.pages.dev/) galleries, and to
**[Anhao](https://github.com/anhao)** for the
[Chinese gallery project](https://github.com/anhao/ai-pose-gallery-zh), its
localization, organization, and generous work making these references easier to
explore. Their work is the reason this adaptation exists. Please visit the
original projects and give their creators the recognition and support they deserve.

The aim here is simply to make this wonderful reference workflow convenient
inside ComfyUI, with English descriptions and editable prompt text. This is an
independent adaptation, with no claim of endorsement or affiliation. If a creator
or rights holder would prefer this adaptation or any referenced material removed,
I will gladly take it down. [Full credits and image terms](THIRD_PARTY.md).

## What it does

- Switch pose galleries on the node and filter thumbnails by saved tags, category, camera angle, or search.
- Choose pose and outfit references independently.
- Compose a positive prompt with a simple text template.
- Override either description without changing its reference image.
- Preview images and prompt text directly on the canvas.
- Run locally without a model or an API key.

The node provides reference images; connect them to the image-conditioning nodes
you already use. It does not generate a finished image by itself.

<!-- Screenshot insertion point: docs/screenshots/workflow-overview.png.
     Captures stay local pending permission for the depicted gallery images.
     See docs/screenshots/README.md for captions and insertion snippets. -->

## Install

Clone this repository into `ComfyUI/custom_nodes/ComfyUI_PoseGenerator`. Using
ComfyUI's Python environment, install the requirements and restart ComfyUI:

```sh
python -m pip install -r requirements.txt
```

Add **Pose & Outfit Reference** from **image → reference**.

**Bring references you have permission to use.** Put your images and a
`catalog.json` in `assets/gallery`, or point `POSE_GENERATOR_ASSETS` to your own
library folder. [Library setup and catalog example](docs/library.md).

The public source and registry package do **not** bundle the original gallery
images. The original pose and outfit sites currently prohibit bulk downloading
and redistribution, including modified copies. Gallery distribution and automated
setup remain on hold pending the rights holder's permission. The local research
catalog contained 1,439 poses and 1,407 outfits; these are not included downloads.

## Use

1. Click **Browse poses** or **Browse outfits**, choose a thumbnail, then click
   **Use pose** or **Use outfit**.
2. Enter a **subject**, optional **extra** details, and any description overrides.
3. Click **Preview positive prompt** to inspect the composed text.
4. Connect the image outputs to your reference-image workflow and
   `positive_prompt` to **CLIP Text Encode (Prompt)** or your preferred encoder.
   Convert the encoder's text widget to an input if needed.

| Output | Type | Use |
| --- | --- | --- |
| `pose_image` | `IMAGE` | Selected pose at its source dimensions |
| `outfit_image` | `IMAGE` | Selected outfit at its source dimensions |
| `positive_prompt` | `STRING` | Composed text for your positive prompt |
| `pose_text` | `STRING` | Pose description on its own |
| `outfit_text` | `STRING` | Outfit description on its own |

Choose **None** or **Clear selection** to omit a reference from the prompt.
An omitted image output is a black 1×1 placeholder; leave it unconnected.

![Pose and outfit workflow with two image previews and a composed prompt](docs/screenshots/workflow-overview.png)

![Pose browser with search, camera-angle filtering, and English descriptions](docs/screenshots/pose-gallery.png)

![Outfit browser with thumbnails and selected reference details](docs/screenshots/outfit-gallery.png)

![Preview of the composed positive prompt](docs/screenshots/prompt-preview.png)

## Prompt templates

```text
{subject}, {pose}, {outfit}, {extra}
```

For example, with your own descriptions:

```text
a traveler, standing with one hand on the hip, front view, wearing a blue jacket, soft studio light
```

Supported fields: `{subject}`, `{pose}`, `{outfit}`, `{extra}`, `{pose_name}`,
`{outfit_name}`, and `{angle}`. Outfit text already begins with “wearing”.
Use `pose_override` and `outfit_override` to refine the text.

Prompt weights such as `({pose}:1.2)` and line breaks work. Use `{{` and `}}` for
literal braces. Templates perform literal substitution; they do not execute code.

## Example workflow

[Download the four-node preview workflow](examples/pose-outfit-preview.json),
then drop it onto ComfyUI. Select references from your own installed library and
click **Run** to see both images and the composed text. No checkpoint, GPU, or
additional node packs are required. [Example instructions](examples/README.md).

## Documentation

- [Local libraries and optional WebP optimization](docs/library.md)
- [Registry publishing and GitHub social preview setup](docs/releasing.md)
- [Development and pre-upload checks](docs/development.md)
- [Credits, source terms, and removal requests](THIRD_PARTY.md)

The integration software is [MIT licensed](LICENSE). That license grants no
rights to the original gallery images, metadata, or derived descriptions.
