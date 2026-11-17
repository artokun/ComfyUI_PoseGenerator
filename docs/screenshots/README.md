# Screenshot set

Captured from the running ComfyUI node. The README intentionally leaves the
screenshots unembedded so they can be added later.

These captures depict upstream gallery images and are kept **local and excluded
from Git and registry packaging** while permission is unresolved. Replace them
with captures using your own permitted images, or obtain the rights holder's
permission first. [Source notices](../../THIRD_PARTY.md).

| File | Suggested caption |
| --- | --- |
| `workflow-overview.png` | One node supplies two reference images and an editable positive prompt. |
| `reference-node.png` | Pose and outfit selections, prompt controls, and thumbnail previews. |
| `pose-gallery.png` | Find a pose by description, category, and camera angle. |
| `outfit-gallery.png` | Browse outfit references and inspect their prompt text before selecting. |
| `prompt-preview.png` | Preview the exact positive prompt before running the workflow. |

After permission, these snippets can be inserted into the root README:

```markdown
![Pose and outfit workflow with two image previews and a composed prompt](docs/screenshots/workflow-overview.png)

![Pose browser with search, camera-angle filtering, and English descriptions](docs/screenshots/pose-gallery.png)

![Outfit browser with thumbnails and selected reference details](docs/screenshots/outfit-gallery.png)

![Preview of the composed positive prompt](docs/screenshots/prompt-preview.png)
```
