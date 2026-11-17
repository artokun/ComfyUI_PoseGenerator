"""Literal prompt substitution; values are never evaluated or re-expanded."""

import re
from string import Formatter

FIELDS = {"subject", "pose", "outfit", "extra", "pose_name", "outfit_name", "angle"}
DEFAULT_TEMPLATE = "{subject}, {pose}, {outfit}, {extra}"


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
