"""Literal prompt substitution; values are never evaluated or re-expanded."""

import re
from string import Formatter

FIELDS = {"edit", "subject", "pose", "outfit", "location", "extra", "pose_name", "outfit_name", "angle"}
DEFAULT_TEMPLATE = "{edit} {subject}, {pose}, {outfit}, {location}, {extra}"
DEFAULT_PICTURE_TAG = "<Picture {n}>"
EDIT_TARGETS = ("Pose", "Subject")
REFERENCE_TEXT = ("Image and description", "Image only")
LOCATIONS = ("From the subject photo", "From the location image", "Plain studio", "Custom")


def validate_picture_tag(tag):
    tag = (tag or "").strip()
    if tag and "{n}" not in tag:
        raise ValueError("picture_tag needs {n} for the picture number, for example <Picture {n}> or image {n}. Leave it empty to turn picture references off.")
    return tag


def picture_order(has_subject, has_pose, has_outfit, edit_target="Subject", has_location=False):
    """The pictures that exist, in the order a model numbers them. The edit target comes first."""
    order = ["pose", "subject", "outfit", "location"] if edit_target == "Pose" else ["subject", "pose", "outfit", "location"]
    present = {"subject": has_subject, "pose": has_pose, "outfit": has_outfit, "location": has_location}
    return [kind for kind in order if present[kind]]


def compose_values(subject, pose_text, outfit_text, extra, *, order=(), picture_tag="", depth=False,
                   describe=True, location="Custom", pose_name="", outfit_name="", angle=""):
    """Template values. With a picture tag, {subject}, {pose} and {outfit} point at their pictures
    by number and {edit} opens an edit instruction for picture 1."""
    values = {"edit": "", "location": "", "subject": subject, "pose": pose_text, "outfit": outfit_text, "extra": extra,
              "pose_name": pose_name, "outfit_name": outfit_name, "angle": angle}
    tag = validate_picture_tag(picture_tag)
    if not tag or not order:
        return values
    tags = {kind: tag.replace("{n}", str(index)) for index, kind in enumerate(order, 1)}
    guide = "depth map" if depth else "mannequin drawing"
    if "subject" in tags:
        who = subject.strip() or "the person"
        if tags["subject"] not in who:
            who = f"{who} from {tags['subject']}"
        values["subject"] = who + ", keeping their face, hair and body"
    if "pose" in tags:
        if order[0] == "pose":
            phrase = (f"in exactly the same pose, framing and camera angle as the {guide} in {tags['pose']} "
                      "(a real human body with natural skin, no mannequin joints)")
        else:
            phrase = f"in the exact pose of the {guide} in {tags['pose']} (copy only the pose, not its look)"
        values["pose"] = phrase + (f": {pose_text.strip()}" if describe and pose_text.strip() else "")
    if "outfit" in tags:
        phrase = f"wearing the outfit shown in {tags['outfit']} (only the clothes, not its background)"
        described = re.sub(r"^wearing\s+", "", outfit_text.strip(), flags=re.I)
        values["outfit"] = phrase + (f": {described}" if describe and described else "")
    elif "subject" in tags and not outfit_text.strip():
        # Nothing chosen to wear: without this a pose edit keeps the bare mannequin body.
        values["outfit"] = f"wearing the same clothes as in {tags['subject']}"
    if location == "From the subject photo" and "subject" in tags and order[0] != "subject":
        # Picture 1 sets the scene unless told otherwise; when the photo is picture 1 it already does.
        values["location"] = f"in the same location, background and lighting as {tags['subject']}"
    elif location == "From the location image" and "location" in tags:
        values["location"] = f"in the same location, background and lighting as {tags['location']}"
    elif location == "Plain studio":
        values["location"] = "on a plain white studio background"
    if order[0] == "subject":
        values["edit"] = f"Edit {tags['subject']}: show"
    elif order[0] == "pose":
        values["edit"] = f"Edit {tags['pose']}: replace the {guide} with a real photo of"
    return values


def with_picture_fields(template, values):
    """Templates written before {edit} and {location} existed still get both: the settings on
    the node decide whether they say anything, not whether the template mentions them."""
    if values.get("edit") and "{edit}" not in template:
        template = "{edit} " + template
    if values.get("location") and "{location}" not in template:
        head, comma, tail = template.rpartition(", {extra}")
        template = head + ", {location}, {extra}" + tail if comma else template + ", {location}"
    return template


def render_prompt(template, values):
    parts = []
    try:
        parsed = list(Formatter().parse(template))
    except ValueError as error:
        raise ValueError("Unbalanced prompt braces. Use {{ and }} for literal braces.") from error
    for literal, field, spec, conversion in parsed:
        parts.append(literal)
        if field is None:
            continue
        if field not in FIELDS or spec or conversion:
            raise ValueError(f"Unsupported prompt field {{{field}}}. Use: {', '.join(sorted(FIELDS))}.")
        parts.append(str(values.get(field, "")).strip())
    text = "".join(parts)
    # Empty selections should not leave repeated commas in the default template.
    text = re.sub(r"(?:,[ \t]*){2,}", ", ", text)
    return text.strip(" ,\t\n")
