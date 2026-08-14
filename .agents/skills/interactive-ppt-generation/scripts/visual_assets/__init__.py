"""Built-in visual asset retrieval subsystem for Interactive PPT Generation."""

from .models import SearchCandidate, SlotRequirement
from .pipeline import VisualAssetPipeline

__all__ = ["SearchCandidate", "SlotRequirement", "VisualAssetPipeline"]
