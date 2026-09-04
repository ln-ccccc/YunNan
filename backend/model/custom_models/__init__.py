from mmseg.registry import MODELS

# Avoid duplicate registration when the mmseg source tree already ships
# DINOv3Backbone/DINOv3SwinEncoder under mmseg.models.backbones.
try:
    if "DINOv3SwinEncoder" not in MODELS.module_dict:
        from .dinov3swin import DINOv3SwinEncoder  # noqa: F401
except KeyError as exc:
    if "already registered" not in str(exc):
        raise
