#!/usr/bin/env python3
"""Derive aligned monochrome ink art from a color mother image.

Adapted from ``handdraw-story-video/scripts/make_lineart.py``.
Copyright (c) 2026 Handdraw Story Video contributors, MIT License.
See ``THIRD_PARTY_NOTICES.md``.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np


SUPPORTED_OUTPUT_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


def read_color_image(path: str | Path) -> np.ndarray:
    """Read an image from a Unicode-safe path as BGR pixels."""
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"找不到彩色母图：{source}")
    source_bytes = np.fromfile(source, dtype=np.uint8)
    image = cv2.imdecode(source_bytes, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"无法读取彩色母图：{source}")
    return image


def derive_lineart(image_bgr: np.ndarray) -> np.ndarray:
    """Return a 3-channel grayscale line image with the same pixel geometry."""
    if image_bgr.ndim != 3 or image_bgr.shape[2] != 3:
        raise ValueError("彩色母图必须是三通道 BGR 图像")

    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
    hsv = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2HSV)
    saturation = hsv[:, :, 1].astype(np.float32)

    # Neutral dark strokes are likely ink. Saturated pencil colors are
    # suppressed without dilation so fine internal lines stay separated.
    darkness = np.clip((218.0 - gray) / 185.0, 0.0, 1.0)
    neutrality = 1.0 - np.clip((saturation - 22.0) / 92.0, 0.0, 1.0)
    ink_strength = darkness * (neutrality**1.35)

    # Preserve genuinely black marks despite JPEG or color fringing.
    extreme_black = np.clip((72.0 - gray) / 42.0, 0.0, 1.0)
    ink_strength = np.maximum(ink_strength, extreme_black * 0.92)
    ink_strength = np.clip(ink_strength, 0.0, 1.0) ** 0.72
    ink_strength = cv2.GaussianBlur(ink_strength, (0, 0), 0.28)

    # Keep a whisper of paper texture while producing clean video contours.
    paper = np.clip(252.0 + (gray - 242.0) * 0.06, 248.0, 255.0)
    lineart = paper * (1.0 - ink_strength) + 12.0 * ink_strength
    lineart = np.clip(lineart, 0, 255).astype(np.uint8)
    return cv2.cvtColor(lineart, cv2.COLOR_GRAY2BGR)


def extract_lineart(input_path: str | Path, output_path: str | Path) -> Path:
    """Create an aligned line image atomically and return its final path."""
    source = Path(input_path)
    output = Path(output_path)
    if source.resolve() == output.resolve():
        raise ValueError("彩色母图和线稿图不能使用同一个路径")
    extension = output.suffix.lower() or ".png"
    if extension not in SUPPORTED_OUTPUT_EXTENSIONS:
        raise ValueError(f"不支持的线稿格式：{extension}")

    color = read_color_image(source)
    lineart = derive_lineart(color)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f"{output.stem}.partial{extension}")
    temporary.unlink(missing_ok=True)
    try:
        encoded_ok, encoded = cv2.imencode(extension, lineart)
        if not encoded_ok:
            raise RuntimeError(f"无法编码线稿图：{output}")
        encoded.tofile(temporary)
        decoded = read_color_image(temporary)
        if decoded.shape[:2] != color.shape[:2]:
            raise RuntimeError("线稿与彩色母图尺寸不一致")
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return output


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="approved color mother image")
    parser.add_argument("output", type=Path, help="output PNG/JPEG/WebP line image")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    print(extract_lineart(args.input, args.output).resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
