"""Backbone-only torch.hub entrypoint for the production inference image."""

from dinov3.hub.backbones import Weights
from dinov3.hub.backbones import dinov3_vitl16 as _dinov3_vitl16


def dinov3_vitl16(*, weights=Weights.LVD1689M, **kwargs):
    """Construct the architecture without loading the separately excluded weights."""
    kwargs.pop("pretrained", None)
    return _dinov3_vitl16(weights=weights, pretrained=False, **kwargs)


dependencies = ["torch", "numpy"]

__all__ = ("dinov3_vitl16",)
