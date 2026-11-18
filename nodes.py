"""A single picker node with two independent image outputs."""

import hashlib
from pathlib import PurePosixPath

import numpy as np
import torch
from PIL import Image, ImageOps

from .library import NONE, catalog_stamp, choices, image_path, selected
from .prompts import (DEFAULT_PICTURE_TAG, DEFAULT_TEMPLATE, EDIT_TARGETS, REFERENCE_TEXT, compose_values,
                      picture_order, render_prompt, validate_picture_tag)


def _depth_candidates(entry, digest):
    """Where a pose's saved depth map can live, in the order to try them.

    Pose Studio writes depth/<sha256 of the drawing>.png at the gallery root.
    The original gallery keeps one per drawing beside it, under the same name:
    img/depth/<drawing file name>.
    """
    image = PurePosixPath(entry["image"])
    return (f"depth/{digest}.png", str(image.parent / "depth" / image.name))


def reference_path(entry, depth=False):
    root = entry.get("_root")
    path = image_path(entry["image"], root)
    if depth and path.is_file():
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        for candidate in _depth_candidates(entry, digest):
            try:
                found = image_path(candidate, root)
            except ValueError:  # outside the gallery or not an image: not a depth map
                continue
            if found.is_file():
                return found
        raise ValueError("Generate a depth map for this pose in Pose Studio or select Drawing.")
    return path


def load_reference(entry, depth=False):
    if entry is None:
        return torch.zeros((1, 1, 1, 3), dtype=torch.float32)
    path = reference_path(entry, depth)
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


def fit_megapixels(image, megapixels, multiple=32):
    """Resize to about `megapixels` of area, keeping the aspect ratio, with both sides a multiple of 32."""
    height, width = image.shape[1], image.shape[2]
    if megapixels <= 0:
        return image
    scale = (megapixels * 1_000_000 / (width * height)) ** 0.5
    new_width = max(multiple, round(width * scale / multiple) * multiple)
    new_height = max(multiple, round(height * scale / multiple) * multiple)
    if (new_width, new_height) == (width, height):
        return image
    resized = torch.nn.functional.interpolate(image.movedim(-1, 1), size=(new_height, new_width), mode="bicubic", antialias=True)
    return resized.movedim(1, -1).clamp(0, 1)


class PoseOutfitReference:
    CATEGORY = "image/reference"
    FUNCTION = "generate"
    # New outputs are appended: saved workflows link outputs by position.
    RETURN_TYPES = ("IMAGE", "IMAGE", "STRING", "STRING", "STRING", "IMAGE", "IMAGE", "IMAGE", "INT", "INT")
    RETURN_NAMES = ("pose_image", "outfit_image", "positive_prompt", "pose_text", "outfit_text",
                    "picture_1", "picture_2", "picture_3", "width", "height")
    OUTPUT_TOOLTIPS = (
        "The pose drawing or depth map. None gives an empty 1×1 image.",
        "The outfit image. None gives an empty 1×1 image.",
        "The composed prompt. With picture_tag set it refers to the pictures by number.",
        "The pose description.", "The outfit description.",
        "The picture to edit. Wire picture_1, picture_2, picture_3 to the model's image 1, 2, 3 in that order.",
        "The second picture, or nothing if there is none. Wire it straight into the model.",
        "The third picture, or nothing if there is none. Wire it straight into the model.",
        "Width of picture_1 (0 without pictures).", "Height of picture_1 (0 without pictures).",
    )
    DESCRIPTION = ("Choose pose and outfit references, optionally connect a photo of the subject, and compose a prompt with "
                   "{edit}, {subject}, {pose}, {outfit}, and {extra}. picture_1..3 are the subject, pose and outfit that exist, "
                   "numbered the way the prompt refers to them.")
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
            "pose_image_mode": (["Drawing", "Saved depth"], {"default": "Drawing"}),
            # New widgets go last: saved workflows store widget values by position.
            "picture_tag": ("STRING", {"default": DEFAULT_PICTURE_TAG, "multiline": False,
                            "tooltip": "How the prompt names a picture; {n} is its number. MiniMax H3: <Picture {n}>. Qwen Edit, Kontext, Seedream: image {n}. Empty: plain text prompt with no picture references."}),
            "edit_target": (list(EDIT_TARGETS), {"default": "Pose",
                            "tooltip": "Which picture is picture_1, the one the model edits. Pose keeps the drawing's pose and framing; Subject keeps the photo's scene."}),
            "megapixels": ("FLOAT", {"default": 4.0, "min": 0.0, "max": 32.0, "step": 0.5,
                           "tooltip": "Resize picture_1 to about this area (sides are multiples of 32). 0 leaves it as it is."}),
            "reference_text": (list(REFERENCE_TEXT), {"default": REFERENCE_TEXT[0],
                               "tooltip": "With picture references on, also add the pose and outfit descriptions, or point at the pictures only."}),
            "subject_image": ("IMAGE", {"tooltip": "Optional photo of the subject. The prompt then refers to this person by picture number."}),
        }}

    @classmethod
    def VALIDATE_INPUTS(cls, pose, outfit, prompt_template, picture_tag=""):
        try:
            selected("pose", pose)
            selected("outfit", outfit)
            render_prompt(prompt_template, {})
            if isinstance(picture_tag, str):
                validate_picture_tag(picture_tag)
        except ValueError as error:
            return str(error)
        return True

    @classmethod
    def IS_CHANGED(cls, pose, outfit, **kwargs):
        digest = hashlib.sha256()
        for kind, identity in (("pose", pose), ("outfit", outfit)):
            entry = selected(kind, identity)
            if entry is not None:
                root = entry["_root"]
                digest.update(repr((identity, catalog_stamp(root))).encode())
                path = reference_path(entry, kind == "pose" and kwargs.get("pose_image_mode") == "Saved depth")
                with path.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
        return digest.hexdigest()

    def generate(self, pose, outfit, subject, prompt_template, extra, pose_override="", outfit_override="",
                 pose_image_mode="Drawing", picture_tag="", edit_target="Subject", megapixels=0.0,
                 reference_text="Image and description", subject_image=None):
        pose_entry = selected("pose", pose)
        outfit_entry = selected("outfit", outfit)
        depth = pose_image_mode == "Saved depth"
        pose_text = pose_override.strip() or (pose_entry["prompt"] if pose_entry else "")
        outfit_text = outfit_override.strip() or (outfit_entry["prompt"] if outfit_entry else "")
        pose_image, outfit_image = load_reference(pose_entry, depth), load_reference(outfit_entry)
        available = {"subject": subject_image[:1] if subject_image is not None else None,
                     "pose": pose_image if pose_entry else None, "outfit": outfit_image if outfit_entry else None}
        order = picture_order(available["subject"] is not None, pose_entry is not None, outfit_entry is not None, edit_target)
        values = compose_values(
            subject, pose_text, outfit_text, extra, order=order, picture_tag=picture_tag, depth=depth,
            describe=reference_text != "Image only",
            pose_name=pose_entry["label"] if pose_entry else "",
            outfit_name=outfit_entry["label"] if outfit_entry else "",
            angle=pose_entry.get("angle", "") if pose_entry else "",
        )
        prompt = render_prompt(prompt_template, values)
        pictures = [available[kind] for kind in order] + [None] * (3 - len(order))
        width = height = 0
        if pictures[0] is not None:
            pictures[0] = fit_megapixels(pictures[0], megapixels)
            height, width = pictures[0].shape[1], pictures[0].shape[2]
        return (pose_image, outfit_image, prompt, pose_text, outfit_text, *pictures, width, height)
