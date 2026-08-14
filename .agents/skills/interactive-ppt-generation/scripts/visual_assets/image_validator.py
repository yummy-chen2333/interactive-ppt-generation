from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageFilter, ImageStat, UnidentifiedImageError

from .models import SlotRequirement


@dataclass(slots=True)
class ValidationResult:
    valid: bool
    reason: str
    details: dict[str, Any]


class ImageValidator:
    def validate(self, path: Path, content_type: str, slot: SlotRequirement) -> ValidationResult:
        try:
            prefix = path.read_bytes()[:512].lstrip().lower()
        except OSError as exc:
            return ValidationResult(False, f"cannot read file: {exc}", {})
        if prefix.startswith((b"<!doctype html", b"<html", b"<head", b"<body")):
            return ValidationResult(False, "HTML masquerading as image", {"content_type": content_type})
        if content_type.startswith("text/html"):
            return ValidationResult(False, "HTML content type", {"content_type": content_type})

        try:
            with Image.open(path) as probe:
                probe.verify()
            with Image.open(path) as image:
                image.load()
                width, height = image.size
                image_format = str(image.format or "").upper()
                mode = image.mode
                if width < slot.min_width or height < slot.min_height:
                    return ValidationResult(
                        False,
                        f"resolution too low: {width}x{height} < {slot.min_width}x{slot.min_height}",
                        {"width": width, "height": height, "format": image_format},
                    )
                grayscale = image.convert("L")
                thumbnail = grayscale.copy()
                thumbnail.thumbnail((512, 512))
                edge = thumbnail.filter(ImageFilter.FIND_EDGES)
                edge_variance = float(ImageStat.Stat(edge).var[0])
                entropy = float(thumbnail.entropy())
                perceptual_hash = self._difference_hash(thumbnail)
                content_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
                ratio = width / max(1, height)
                aspect_score = 100.0
                if slot.desired_aspect_ratio:
                    aspect_score = max(
                        0.0,
                        100.0
                        * (1 - abs(ratio - slot.desired_aspect_ratio) / slot.desired_aspect_ratio),
                    )
                clarity_score = min(100.0, 25.0 + math.sqrt(max(0.0, edge_variance)) * 8 + entropy * 4)
                details = {
                    "width": width,
                    "height": height,
                    "format": image_format,
                    "mode": mode,
                    "content_type": content_type,
                    "content_sha256": content_sha256,
                    "perceptual_hash": perceptual_hash,
                    "edge_variance": round(edge_variance, 3),
                    "entropy": round(entropy, 3),
                    "clarity_score": round(clarity_score, 3),
                    "aspect_score": round(aspect_score, 3),
                }
                return ValidationResult(True, "validated", details)
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            return ValidationResult(False, f"invalid image payload: {exc}", {})

    @staticmethod
    def is_near_duplicate(hash_value: str, prior_hashes: list[str], max_distance: int) -> bool:
        try:
            integer = int(hash_value, 16)
            return any((integer ^ int(previous, 16)).bit_count() <= max_distance for previous in prior_hashes)
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _difference_hash(image: Image.Image) -> str:
        resized = image.resize((9, 8))
        if hasattr(resized, "get_flattened_data"):
            pixels = list(resized.get_flattened_data())
        else:
            pixels = list(resized.getdata())
        bits = []
        for row in range(8):
            offset = row * 9
            bits.extend(pixels[offset + column] > pixels[offset + column + 1] for column in range(8))
        value = 0
        for bit in bits:
            value = (value << 1) | int(bit)
        return f"{value:016x}"
