# Preview pose, outfit, and prompt

Open [`pose-outfit-preview.json`](pose-outfit-preview.json) by dropping the file
onto the ComfyUI canvas, or use **Workflow → Open**.

The workflow has four nodes:

1. **Pose & Outfit Reference** selects local references and composes the text.
2. **Pose reference** previews the `pose_image` output.
3. **Outfit reference** previews the `outfit_image` output.
4. **Positive prompt** displays `positive_prompt` with the built-in **Preview Any** node.

Install this node and [your own permitted reference library](../docs/library.md)
first, then choose a pose and outfit and click **Run**. The published example starts
with **None** for both references so it imports without requiring specific third-party
IDs. Selecting no references produces black 1×1 placeholders and the subject/extra
text only.

No model, GPU, API key, or other custom node pack is required. The preview workflow
was exercised on ComfyUI 0.24.0 with frontend 1.45.15, using local reference images.
If your ComfyUI does not offer **Preview Any**, update ComfyUI or connect the string
output to an equivalent text preview node.

To use this in a generation workflow, connect either image to your preferred
image-conditioning nodes. Connect `positive_prompt` to a text encoder. The pose
reference is a drawing, not a precomputed OpenPose skeleton; use the appropriate
preprocessor if your conditioning model requires one.
