"""A single picker node with two independent image outputs."""

import hashlib

import numpy as np
import torch
from PIL import Image, ImageOps

from .library import NONE, catalog_stamp, choices, image_path, selected
from .prompts import DEFAULT_TEMPLATE, render_prompt


def load_reference(entry):
    if entry is None:
        return torch.zeros((1, 1, 1, 3), dtype=torch.float32)
    path = image_path(entry["image"])
    if not path.is_file():
        raise ValueError(f"Missing reference image: {entry['id']}. Reinstall the gallery assets.")
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source)
        if image.mode == "RGBA" or "transparency" in image.info:
            rgba = image.convert("RGBA")
            background = Image.new("RGBA", rgba.size, "white")
            image = Image.alpha_composite(background, rgba)
        array = np.array(image.convert("RGB"), dtype=np.float32) / 255.0
    return torch.from_numpy(array).unsqueeze(0)


class PoseOutfitReference:
    CATEGORY = "image/reference"
    FUNCTION = "generate"
    RETURN_TYPES = ("IMAGE", "IMAGE", "STRING", "STRING", "STRING")
    RETURN_NAMES = ("pose_image", "outfit_image", "positive_prompt", "pose_text", "outfit_text")
    DESCRIPTION = "Choose pose and outfit references, then compose a prompt with {subject}, {pose}, {outfit}, and {extra}. None returns an empty 1×1 image on that output."
    SEARCH_ALIASES = ["pose generator", "outfit picker", "reference gallery"]

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "pose": (choices("pose"), {"default": NONE}),
            "outfit": (choices("outfit"), {"default": NONE}),
            "subject": ("STRING", {"default": "a person", "multiline": False}),
            "prompt_template": ("STRING", {"default": DEFAULT_TEMPLATE, "multiline": True, "dynamicPrompts": False}),
            "extra": ("STRING", {"default": "", "multiline": True, "dynamicPrompts": False}),
        }, "optional": {
            "pose_override": ("STRING", {"default": "", "multiline": True, "dynamicPrompts": False}),
            "outfit_override": ("STRING", {"default": "", "multiline": True, "dynamicPrompts": False}),
        }}

    @classmethod
    def VALIDATE_INPUTS(cls, pose, outfit, prompt_template):
        try:
            selected("pose", pose)
            selected("outfit", outfit)
            render_prompt(prompt_template, {})
        except ValueError as error:
            return str(error)
        return True

    @classmethod
    def IS_CHANGED(cls, pose, outfit, **kwargs):
        digest = hashlib.sha256(repr(catalog_stamp()).encode())
        for kind, identity in (("pose", pose), ("outfit", outfit)):
            entry = selected(kind, identity)
            if entry is not None:
                with image_path(entry["image"]).open("rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
        return digest.hexdigest()

    def generate(self, pose, outfit, subject, prompt_template, extra, pose_override="", outfit_override=""):
        pose_entry = selected("pose", pose)
        outfit_entry = selected("outfit", outfit)
        pose_text = pose_override.strip() or (pose_entry["prompt"] if pose_entry else "")
        outfit_text = outfit_override.strip() or (outfit_entry["prompt"] if outfit_entry else "")
        values = {
            "subject": subject, "pose": pose_text, "outfit": outfit_text, "extra": extra,
            "pose_name": pose_entry["label"] if pose_entry else "",
            "outfit_name": outfit_entry["label"] if outfit_entry else "",
            "angle": pose_entry.get("angle", "") if pose_entry else "",
        }
        prompt = render_prompt(prompt_template, values)
        return (load_reference(pose_entry), load_reference(outfit_entry), prompt, pose_text, outfit_text)
