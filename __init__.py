"""ComfyUI entry point for Pose & Outfit Reference."""

from .nodes import PoseOutfitReference
from .routes import register_routes

NODE_CLASS_MAPPINGS = {"PoseOutfitReference": PoseOutfitReference}
NODE_DISPLAY_NAME_MAPPINGS = {"PoseOutfitReference": "Pose & Outfit Reference"}
WEB_DIRECTORY = "./web"

register_routes()

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
