# Add Pose & Outfit Reference ComfyUI adaptation with original-creator credits

This adds a small ComfyUI adaptation for choosing local pose and outfit references and composing a positive prompt. One node outputs two reference images and three text values, with a searchable thumbnail browser, editable descriptions, and a model-free preview workflow.

## With gratitude to the original creators

**I did not create the original gallery/tool or its reference assets, and I do not claim them as my work.** My contribution is this independent ComfyUI integration.

A huge thank-you to **ギア / [@giaaibusi](https://x.com/giaaibusi)** for the original [pose](https://ai-pose-gallery.pages.dev/) and [outfit](https://ai-costume-gallery.pages.dev/) galleries. The care put into the poses, angles, outfits, and their organization makes these wonderful resources for artists and creators.

And heartfelt thanks to **[Anhao](https://github.com/anhao)** for [ai-pose-gallery-zh](https://github.com/anhao/ai-pose-gallery-zh), the Chinese localization, organization, and engineering that made the original work easier to explore. That project also offers its own ComfyUI integration; this is an independent, focused alternative, with no claim to being the first or official integration.

This exists because I admired that work and wanted a simple way to use a similar reference workflow inside ComfyUI, with English descriptions and editable prompts. Please visit and support the original creators. **If either creator or any rights holder would prefer this adaptation or referenced material taken down, I will gladly do so.**

## Scope and image terms

The README gives prominent credit, and THIRD_PARTY.md preserves the sources' distinct terms with an unmodified copy of the mirror license. The original pose and outfit sites currently forbid bulk downloading and redistribution, including modified images. A mirror's noncommercial notice is not treated as permission from the original creator.

Gallery files, derived source descriptions, bulk-download tooling, and screenshots containing the gallery are excluded from publication while permission is unresolved. The public node accepts user-supplied, permitted images. Registry and GitHub artwork were generated separately without copied gallery images.

## Validation and release

- 30 local tests passed; actual ComfyUI preview execution verified.
- Python/package checks, actual YARA preflight, and Bandit passed.
- CI and registry publishing share the same checks; publishing verifies and uploads only the checked archive.
- Registry icon, banner, GitHub social card, example workflow, and setup guide included.

Publishing still needs the publisher's REGISTRY_ACCESS_TOKEN repository secret. This PR does not publish the node or imply permission/endorsement from the gallery creators.
