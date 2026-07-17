#!/usr/bin/env python3
"""Extract reference-derived PBR map evidence from an object image.

This is not photogrammetry and it does not claim exact inverse rendering from a
single image. It extracts pixel evidence that is useful for procedural PBR:
albedo palette, de-lit albedo, roughness estimate, height, normal, and AO maps.
An unconfirmed crop never patches a spec. Low extraction suitability also blocks
patching unless ``--allow-low-confidence`` is explicit. The legacy ``confidence``
field is retained for schema compatibility.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from sculpt_contract import write_spec_atomic
from sculpt_image_io import (
    load_image_rgba_limited as load_image,
    png_dimensions,
    write_png_rgb,
)
from sculpt_modules import load_document, save_document


def slugify(value: str) -> str:
    parts: list[str] = []
    last_dash = False
    for char in value.strip().lower():
        if char.isalnum():
            parts.append(char)
            last_dash = False
        elif not last_dash:
            parts.append("-")
            last_dash = True
    return "".join(parts).strip("-") or "material"


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def clamp01(value: float) -> float:
    return clamp(value, 0.0, 1.0)


def srgb_luma(rgb: tuple[int, int, int]) -> float:
    red, green, blue = rgb
    return (0.2126 * red + 0.7152 * green + 0.0722 * blue) / 255.0


def color_distance(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    return math.sqrt(sum((a[index] - b[index]) ** 2 for index in range(3)))


def saturation(rgb: tuple[int, int, int]) -> float:
    high = max(rgb)
    low = min(rgb)
    return 0.0 if high <= 0 else (high - low) / high


def percentile(values: list[float], fraction: float, fallback: float = 0.0) -> float:
    if not values:
        return fallback
    ordered = sorted(values)
    index = int(round(clamp01(fraction) * (len(ordered) - 1)))
    return ordered[index]


def median_color(samples: list[tuple[int, int, int]]) -> tuple[int, int, int]:
    if not samples:
        return (255, 255, 255)
    return tuple(
        int(percentile([float(sample[channel]) for sample in samples], 0.5))
        for channel in range(3)
    )  # type: ignore[return-value]


def sample_corner_background(
    width: int,
    height: int,
    pixels: list[tuple[int, int, int, int]],
) -> tuple[tuple[int, int, int], float]:
    radius = max(3, min(width, height) // 40)
    samples: list[tuple[int, int, int]] = []
    corner_ranges = [
        (0, radius, 0, radius),
        (width - radius, width, 0, radius),
        (0, radius, height - radius, height),
        (width - radius, width, height - radius, height),
    ]
    for x0, x1, y0, y1 in corner_ranges:
        for y in range(max(0, y0), min(height, y1)):
            for x in range(max(0, x0), min(width, x1)):
                red, green, blue, alpha = pixels[y * width + x]
                if alpha > 16:
                    samples.append((red, green, blue))
    background = median_color(samples)
    noise = percentile([color_distance(sample, background) for sample in samples], 0.75, 0.0)
    return background, noise


def build_foreground_mask(
    width: int,
    height: int,
    pixels: list[tuple[int, int, int, int]],
) -> tuple[list[bool], dict[str, Any], list[str]]:
    warnings: list[str] = []
    alpha_values = [pixel[3] for pixel in pixels]
    transparent_fraction = sum(1 for alpha in alpha_values if alpha < 245) / max(1, len(alpha_values))
    background, background_noise = sample_corner_background(width, height, pixels)
    threshold = max(24.0, background_noise * 2.4)
    mask: list[bool] = []
    if transparent_fraction > 0.03:
        for red, green, blue, alpha in pixels:
            mask.append(alpha > 24)
    else:
        for red, green, blue, alpha in pixels:
            rgb = (red, green, blue)
            distance = color_distance(rgb, background)
            sat = saturation(rgb)
            luma = srgb_luma(rgb)
            mask.append(alpha > 16 and (distance > threshold or (sat > 0.16 and luma < 0.94)))
    coverage = sum(1 for value in mask if value) / max(1, len(mask))
    if coverage < 0.035:
        warnings.append("foreground mask is tiny; material extraction is likely unreliable")
        mask = [pixel[3] > 16 for pixel in pixels]
        coverage = sum(1 for value in mask if value) / max(1, len(mask))
    if coverage > 0.9:
        warnings.append("image is not clearly isolated from background; using most pixels as material evidence")
    return (
        mask,
        {
            "backgroundColor": rgb_to_hex(background),
            "backgroundNoise": round(background_noise, 3),
            "transparentPixelFraction": round(transparent_fraction, 4),
            "foregroundCoverage": round(coverage, 4),
        },
        warnings,
    )


def mask_bbox(width: int, height: int, mask: list[bool]) -> tuple[int, int, int, int]:
    xs: list[int] = []
    ys: list[int] = []
    for index, value in enumerate(mask):
        if value:
            ys.append(index // width)
            xs.append(index % width)
    if not xs or not ys:
        return (0, 0, width, height)
    padding = max(2, min(width, height) // 80)
    x0 = max(0, min(xs) - padding)
    y0 = max(0, min(ys) - padding)
    x1 = min(width, max(xs) + padding + 1)
    y1 = min(height, max(ys) + padding + 1)
    return (x0, y0, max(1, x1 - x0), max(1, y1 - y0))


def resample_crop(
    width: int,
    height: int,
    pixels: list[tuple[int, int, int, int]],
    mask: list[bool],
    bbox: tuple[int, int, int, int],
    size: int,
) -> tuple[list[tuple[int, int, int]], list[bool]]:
    x0, y0, crop_w, crop_h = bbox
    sampled_pixels: list[tuple[int, int, int]] = []
    sampled_mask: list[bool] = []
    for y in range(size):
        source_y = y0 + (y + 0.5) * crop_h / size
        sy = min(height - 1, max(0, int(source_y)))
        for x in range(size):
            source_x = x0 + (x + 0.5) * crop_w / size
            sx = min(width - 1, max(0, int(source_x)))
            index = sy * width + sx
            red, green, blue, alpha = pixels[index]
            sampled_pixels.append((red, green, blue))
            sampled_mask.append(mask[index] and alpha > 16)
    return sampled_pixels, sampled_mask


def representative_samples(
    pixels: list[tuple[int, int, int]],
    mask: list[bool],
    limit: int = 7000,
) -> list[tuple[int, int, int]]:
    candidates = [pixel for pixel, keep in zip(pixels, mask) if keep]
    if not candidates:
        candidates = pixels
    if len(candidates) <= limit:
        return candidates
    step = max(1, len(candidates) // limit)
    return candidates[::step][:limit]


def kmeans_palette(samples: list[tuple[int, int, int]], k: int = 5) -> list[str]:
    if not samples:
        return ["#8A7A5F"]
    ordered = sorted(samples, key=lambda rgb: (srgb_luma(rgb), rgb[0], rgb[1], rgb[2]))
    centers = [
        ordered[int((index + 0.5) * (len(ordered) - 1) / k)]
        for index in range(k)
    ]
    for _ in range(8):
        groups: list[list[tuple[int, int, int]]] = [[] for _ in centers]
        for sample in samples:
            nearest = min(range(len(centers)), key=lambda idx: color_distance(sample, centers[idx]))
            groups[nearest].append(sample)
        new_centers: list[tuple[int, int, int]] = []
        for group, center in zip(groups, centers):
            if not group:
                new_centers.append(center)
                continue
            new_centers.append(
                tuple(int(round(sum(sample[channel] for sample in group) / len(group))) for channel in range(3))
            )  # type: ignore[arg-type]
        centers = new_centers
    counts = Counter(
        min(range(len(centers)), key=lambda idx: color_distance(sample, centers[idx]))
        for sample in samples
    )
    palette = [rgb_to_hex(centers[index]) for index, _ in counts.most_common()]
    return palette[:k]


def rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02X}{:02X}{:02X}".format(*rgb)


def blur_scalar(values: list[float], size: int, radius: int) -> list[float]:
    if radius <= 0:
        return values[:]
    horizontal = [0.0] * (size * size)
    for y in range(size):
        row_offset = y * size
        running = 0.0
        count = 0
        for x in range(-radius, size + radius):
            if 0 <= x < size:
                running += values[row_offset + x]
                count += 1
            remove = x - radius * 2 - 1
            if 0 <= remove < size:
                running -= values[row_offset + remove]
                count -= 1
            write_x = x - radius
            if 0 <= write_x < size:
                horizontal[row_offset + write_x] = running / max(1, count)
    vertical = [0.0] * (size * size)
    for x in range(size):
        running = 0.0
        count = 0
        for y in range(-radius, size + radius):
            if 0 <= y < size:
                running += horizontal[y * size + x]
                count += 1
            remove = y - radius * 2 - 1
            if 0 <= remove < size:
                running -= horizontal[remove * size + x]
                count -= 1
            write_y = y - radius
            if 0 <= write_y < size:
                vertical[write_y * size + x] = running / max(1, count)
    return vertical


def make_tileable_rgb(payload: bytes, size: int, blend_fraction: float = 0.08) -> bytes:
    """Blend opposite border pairs so repeated offline maps do not expose hard seams."""
    if len(payload) != size * size * 3:
        raise ValueError("tileable RGB payload has the wrong size")
    output = bytearray(payload)
    blend = max(1, min(size // 4, round(size * blend_fraction)))

    def blend_pair(first: int, second: int, weight: float) -> None:
        for channel in range(3):
            average = (output[first + channel] + output[second + channel]) * 0.5
            output[first + channel] = round(output[first + channel] * (1 - weight) + average * weight)
            output[second + channel] = round(output[second + channel] * (1 - weight) + average * weight)

    for offset in range(blend):
        weight = 1 - offset / blend
        for y in range(size):
            left = (y * size + offset) * 3
            right = (y * size + size - 1 - offset) * 3
            blend_pair(left, right, weight)
        for x in range(size):
            top = (offset * size + x) * 3
            bottom = ((size - 1 - offset) * size + x) * 3
            blend_pair(top, bottom, weight)
    return bytes(output)


def make_maps(
    pixels: list[tuple[int, int, int]],
    mask: list[bool],
    size: int,
    palette: list[str],
) -> tuple[dict[str, bytes], dict[str, Any]]:
    masked_lumas = [srgb_luma(pixel) for pixel, keep in zip(pixels, mask) if keep]
    fallback_luma = percentile(masked_lumas, 0.5, 0.55)
    fallback_color = hex_to_rgb(palette[0] if palette else "#8A7A5F")
    lumas = [srgb_luma(pixel) if keep else fallback_luma for pixel, keep in zip(pixels, mask)]
    broad_radius = max(8, min(64, size // 24))
    meso_radius = max(2, min(20, size // 128))
    broad_frequency = blur_scalar(lumas, size, broad_radius)
    meso_frequency = blur_scalar(lumas, size, meso_radius)
    p05 = percentile(masked_lumas, 0.05, 0.2)
    p95 = percentile(masked_lumas, 0.95, 0.8)
    value_range = max(0.08, p95 - p05)
    micro_detail = [
        clamp((luma - meso + value_range * 0.5) / value_range, 0.0, 1.0)
        for luma, meso in zip(lumas, meso_frequency)
    ]
    meso_detail = [
        clamp((meso - broad + value_range * 0.5) / value_range, 0.0, 1.0)
        for meso, broad in zip(meso_frequency, broad_frequency)
    ]
    combined_height = [
        clamp(micro * 0.68 + meso * 0.32, 0.0, 1.0)
        for micro, meso in zip(micro_detail, meso_detail)
    ]
    height = blur_scalar(combined_height, size, max(1, size // 256))
    gradient_values: list[float] = []
    for y in range(size):
        for x in range(size):
            left = height[y * size + max(0, x - 1)]
            right = height[y * size + min(size - 1, x + 1)]
            up = height[max(0, y - 1) * size + x]
            down = height[min(size - 1, y + 1) * size + x]
            gradient_values.append(math.sqrt((right - left) ** 2 + (down - up) ** 2))
    grad_p90 = percentile(gradient_values, 0.9, 0.0)
    normal_strength = clamp(10.0 + grad_p90 * 75.0, 10.0, 38.0)
    albedo = bytearray()
    roughness = bytearray()
    height_map = bytearray()
    normal = bytearray()
    ao = bytearray()
    roughness_values: list[float] = []
    for index, ((red, green, blue), keep) in enumerate(zip(pixels, mask)):
        luma = lumas[index]
        shade = clamp(broad_frequency[index], 0.08, 1.0)
        scale = clamp((fallback_luma / shade) ** 0.42, 0.72, 1.35)
        if keep:
            out_r = clamp(red * scale, 0, 255)
            out_g = clamp(green * scale, 0, 255)
            out_b = clamp(blue * scale, 0, 255)
        else:
            out_r, out_g, out_b = fallback_color
        albedo.extend((round(out_r), round(out_g), round(out_b)))
        h = height[index]
        local_gradient = gradient_values[index]
        bright_highlight = max(0.0, luma - p95) / max(0.02, 1.0 - p95)
        rough = clamp01(0.68 + min(0.22, local_gradient * 2.6) + (0.5 - h) * 0.12 - bright_highlight * 0.22)
        roughness_values.append(rough)
        rough_byte = round(rough * 255)
        roughness.extend((rough_byte, rough_byte, rough_byte))
        height_byte = round(h * 255)
        height_map.extend((height_byte, height_byte, height_byte))
    for y in range(size):
        for x in range(size):
            index = y * size + x
            left = height[y * size + max(0, x - 1)]
            right = height[y * size + min(size - 1, x + 1)]
            up = height[max(0, y - 1) * size + x]
            down = height[min(size - 1, y + 1) * size + x]
            dx = (right - left) * normal_strength
            dy = (down - up) * normal_strength
            inv_len = 1.0 / math.sqrt(dx * dx + dy * dy + 1.0)
            nx = -dx * inv_len
            ny = -dy * inv_len
            nz = inv_len
            normal.extend(
                (
                    round((nx * 0.5 + 0.5) * 255),
                    round((ny * 0.5 + 0.5) * 255),
                    round((nz * 0.5 + 0.5) * 255),
                )
            )
            neighbors = (
                left
                + right
                + up
                + down
            ) * 0.25
            cavity = max(0.0, neighbors - height[index])
            ao_value = clamp01(1.0 - cavity * 8.0 - max(0.0, 0.35 - height[index]) * 0.16)
            ao_byte = round(ao_value * 255)
            ao.extend((ao_byte, ao_byte, ao_byte))
    return (
        {
            "albedo": bytes(albedo),
            "roughness": bytes(roughness),
            "height": bytes(height_map),
            "normal": bytes(normal),
            "ao": bytes(ao),
        },
        {
            "valueRange": round(value_range, 4),
            "heightP90Gradient": round(grad_p90, 5),
            "roughnessBase": round(percentile(roughness_values, 0.5, 0.72), 3),
            "roughnessVariation": round(max(0.05, percentile(roughness_values, 0.85, 0.82) - percentile(roughness_values, 0.15, 0.62)), 3),
            "normalStrength": round(normal_strength / 64.0, 3),
            "broadBlurRadius": broad_radius,
            "mesoBlurRadius": meso_radius,
            "tileEdgeBlendFraction": 0.08,
        },
    )


def explicit_mask_from_image(
    path: Path,
    width: int,
    height: int,
    working_dimension: int,
) -> tuple[list[bool], list[str]]:
    mask_width, mask_height, pixels, warnings = load_image(path, working_dimension)
    if (mask_width, mask_height) != (width, height):
        raise ValueError(
            f"explicit mask dimensions {mask_width}x{mask_height} do not match working image "
            f"{width}x{height}"
        )
    has_transparency = any(alpha < 250 for _, _, _, alpha in pixels)
    if has_transparency:
        mask = [alpha >= 128 for _, _, _, alpha in pixels]
    else:
        mask = [srgb_luma((red, green, blue)) >= 0.5 for red, green, blue, _ in pixels]
    coverage = sum(mask) / max(1, len(mask))
    if coverage <= 0.01 or coverage >= 0.99:
        warnings.append("explicit material mask coverage is extreme; verify white/opaque=material")
    return mask, warnings


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    if len(value) == 4 and value.startswith("#"):
        return tuple(int(char * 2, 16) for char in value[1:])  # type: ignore[return-value]
    if len(value) == 7 and value.startswith("#"):
        return (int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16))
    return (138, 122, 95)


def surface_bands_from_stats(stats: dict[str, Any]) -> list[dict[str, Any]]:
    value_range = float(stats.get("valueRange", 0.4))
    detail = float(stats.get("heightP90Gradient", 0.02))
    return [
        {
            "id": "macro",
            "frequency": 2.0,
            "amplitude": round(clamp(0.28 + value_range * 0.35, 0.22, 0.52), 3),
            "role": "reference-derived broad albedo and height breakup",
        },
        {
            "id": "meso",
            "frequency": 14.0,
            "amplitude": round(clamp(0.15 + detail * 4.2, 0.12, 0.35), 3),
            "role": "reference-derived cracks, ridges, pores, grain, or leaf clusters",
        },
        {
            "id": "micro",
            "frequency": 72.0,
            "amplitude": round(clamp(0.055 + detail * 2.4, 0.045, 0.14), 3),
            "role": "reference-derived micro highlight breakup under grazing light",
        },
    ]


def estimate_confidence(
    width: int,
    height: int,
    mask_diagnostics: dict[str, Any],
    stats: dict[str, Any],
    warnings: list[str],
    single_image: bool,
) -> tuple[float, list[str]]:
    confidence_notes: list[str] = []
    min_dim = min(width, height)
    resolution_score = clamp(min_dim / 1024.0, 0.35, 1.0)
    coverage = float(mask_diagnostics.get("foregroundCoverage", 1.0))
    if 0.08 <= coverage <= 0.82:
        mask_score = 1.0
    elif 0.035 <= coverage < 0.08:
        mask_score = 0.55
        confidence_notes.append("foreground mask is very small")
    elif coverage > 0.9:
        mask_score = 0.68
        confidence_notes.append("object/background separation is weak")
    else:
        mask_score = 0.78
    value_range = float(stats.get("valueRange", 0.0))
    dynamic_score = clamp(value_range / 0.48, 0.35, 1.0)
    detail_score = clamp(float(stats.get("heightP90Gradient", 0.0)) * 52.0, 0.35, 1.0)
    warning_penalty = min(0.16, len(warnings) * 0.035)
    single_image_cap = 0.86 if single_image else 0.93
    confidence = (
        0.44
        + resolution_score * 0.14
        + mask_score * 0.14
        + dynamic_score * 0.12
        + detail_score * 0.16
        - warning_penalty
    )
    confidence = min(single_image_cap, clamp01(confidence))
    if single_image:
        confidence_notes.append("single-image inverse rendering cannot prove true physical PBR; confidence is capped")
    if dynamic_score < 0.5:
        confidence_notes.append("low value range weakens height/roughness inference")
    if detail_score < 0.5:
        confidence_notes.append("low high-frequency detail weakens normal/roughness inference")
    return round(confidence, 3), confidence_notes


def map_url(url_prefix: str, filename: str) -> str:
    return url_prefix.rstrip("/") + "/" + filename


def material_patch(
    material_id: str,
    image: Path,
    out_dir: Path,
    url_prefix: str,
    size: int,
    threshold: float,
    confidence: float,
    verdict: str,
    palette: list[str],
    map_stats: dict[str, Any],
    diagnostics: dict[str, Any],
    warnings: list[str],
) -> dict[str, Any]:
    prefix = slugify(material_id)
    maps: dict[str, dict[str, Any]] = {
        channel: {
            "path": str((out_dir / f"{prefix}_{channel}.png").resolve()),
            "channel": channel,
            "source": "reference-pixel-extraction",
            "tileSafe": True,
        }
        for channel in ("albedo", "roughness", "height", "normal", "ao")
    }
    if url_prefix:
        for channel, entry in maps.items():
            entry["url"] = map_url(url_prefix, f"{prefix}_{channel}.png")
    usable = confidence >= threshold
    return {
        "referencePbr": {
            "version": "1.0",
            "sourceImage": str(image.resolve()),
            "extractor": "extract_reference_pbr.py",
            "method": "single-image pixel evidence with broad/meso/micro de-lighting and tile-edge blending; not photogrammetry",
            "usable": usable,
            "verdict": verdict,
            "confidence": confidence,
            "extractionSuitability": confidence,
            "targetThreshold": threshold,
            "hardLimit": "A single image cannot uniquely recover true albedo/roughness/normal/AO; maps are reference-derived estimates.",
            "maps": maps,
            "diagnostics": diagnostics,
            "warnings": warnings,
        },
        "textureResolution": size,
        "albedo": {
            "dominant": palette[0],
            "secondary": palette[1:4],
            "samplingNotes": "Reference-derived from foreground pixels; de-lit to reduce baked shadows/highlights.",
            "map": maps["albedo"],
        },
        "colorVariation": {
            "palette": palette,
            "pattern": "reference-derived pixel palette",
            "amplitude": round(clamp(float(map_stats.get("valueRange", 0.4)) * 0.42, 0.08, 0.35), 3),
            "heightCorrelation": 0.42,
        },
        "roughness": {
            "base": map_stats["roughnessBase"],
            "variation": map_stats["roughnessVariation"],
            "map": maps["roughness"],
            "localResponse": "reference-derived roughness estimate; cavities and textured zones trend rougher, bright highlights trend smoother",
        },
        "normal": {
            "pattern": "reference-derived height-gradient normal map",
            "strength": map_stats["normalStrength"],
            "map": maps["normal"],
            "heightSource": maps["height"],
            "space": "tangent",
        },
        "bump": {
            "pattern": "reference-derived height field",
            "amplitude": round(clamp(float(map_stats.get("heightP90Gradient", 0.02)) * 0.45, 0.01, 0.08), 3),
            "map": maps["height"],
        },
        "ambientOcclusion": {
            "cavityStrength": 0.38,
            "contactShadowBias": 0.35,
            "map": maps["ao"],
            "notes": "Reference-derived cavity estimate from local height minima; verify against grazing-light screenshot.",
        },
        "surfaceFrequencyBands": surface_bands_from_stats(map_stats),
        "localOverrides": [
            {
                "id": "reference-pbr-pixel-evidence",
                "type": "material-map-evidence",
                "evidenceRefs": ["full-object"],
                "channels": ["albedo", "roughness", "height", "normal", "ambient-occlusion"],
                "notes": "Use generated maps as material evidence, then refine after browser screenshot comparison.",
            }
        ],
        "shaderNotes": [
            "Reference-derived maps are estimates from image pixels; verify with neutral, grazing, and reference-matched renders.",
            "Do not treat baked image shadows as final albedo; rerun extraction with a tighter material crop if highlights/shadows pollute the maps.",
            "Offline map borders are blended for repeat safety; inspect for visible repetition at the target texel density.",
        ],
    }


def merge_material_patch(spec: dict[str, Any], material_id: str, patch: dict[str, Any]) -> None:
    materials = spec.get("materials")
    if not isinstance(materials, list):
        raise ValueError("spec.materials must be an array")
    material = next(
        (item for item in materials if isinstance(item, dict) and item.get("id") == material_id),
        None,
    )
    if material is None:
        raise ValueError(f"could not find material {material_id!r} in spec")
    for key, value in patch.items():
        if key == "localOverrides" and isinstance(material.get(key), list) and isinstance(value, list):
            material[key].extend(value)
        elif key == "shaderNotes" and isinstance(material.get(key), list) and isinstance(value, list):
            material[key].extend(value)
        else:
            material[key] = value
    history = spec.setdefault("pbrExtractionHistory", [])
    if isinstance(history, list):
        history.append(
            {
                "materialId": material_id,
                "confidence": patch["referencePbr"]["confidence"],
                "extractionSuitability": patch["referencePbr"]["extractionSuitability"],
                "verdict": patch["referencePbr"]["verdict"],
                "usable": patch["referencePbr"]["usable"],
                "maps": patch["referencePbr"]["maps"],
            }
        )


def extract(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any]]:
    image = args.image.expanduser().resolve()
    if not image.exists():
        raise ValueError(f"{image} does not exist")
    size = int(2 ** round(math.log2(args.size)))
    size = max(256, min(2048, size))
    out_dir = args.out_dir.expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    original_dimensions = png_dimensions(image)
    working_dimension = max(size, 1024) if size < 2048 else 2048
    width, height, source_pixels, load_warnings = load_image(image, working_dimension)
    mask, mask_diag, mask_warnings = build_foreground_mask(width, height, source_pixels)
    explicit_mask_path = getattr(args, "mask", None)
    if explicit_mask_path:
        resolved_mask = explicit_mask_path.expanduser().resolve()
        if not resolved_mask.exists():
            raise ValueError(f"{resolved_mask} does not exist")
        mask, explicit_warnings = explicit_mask_from_image(
            resolved_mask,
            width,
            height,
            working_dimension,
        )
        mask_warnings.extend(explicit_warnings)
        mask_diag["source"] = "explicit-mask"
        mask_diag["path"] = str(resolved_mask)
        mask_diag["foregroundCoverage"] = round(sum(mask) / max(1, len(mask)), 4)
    bbox = mask_bbox(width, height, mask)
    sampled_pixels, sampled_mask = resample_crop(width, height, source_pixels, mask, bbox, size)
    samples = representative_samples(sampled_pixels, sampled_mask)
    palette = kmeans_palette(samples, max(2, min(6, args.palette_size)))
    maps, map_stats = make_maps(sampled_pixels, sampled_mask, size, palette)
    maps = {
        channel: make_tileable_rgb(payload, size)
        for channel, payload in maps.items()
    }
    for channel, payload in maps.items():
        write_png_rgb(out_dir / f"{slugify(args.material_id)}_{channel}.png", size, size, payload)
    warnings = load_warnings + mask_warnings
    diagnostics = {
        "sourceWidth": original_dimensions[0] if original_dimensions else width,
        "sourceHeight": original_dimensions[1] if original_dimensions else height,
        "workingWidth": width,
        "workingHeight": height,
        "mapSize": size,
        "cropBBoxPixels": {
            "x": bbox[0],
            "y": bbox[1],
            "width": bbox[2],
            "height": bbox[3],
        },
        "mask": mask_diag,
        "mapStats": map_stats,
        "palette": palette,
    }
    confidence, confidence_notes = estimate_confidence(
        original_dimensions[0] if original_dimensions else width,
        original_dimensions[1] if original_dimensions else height,
        mask_diag,
        map_stats,
        warnings,
        single_image=True,
    )
    if args.multi_view_reference:
        confidence_notes.append(
            "--multi-view-reference is deprecated: one supplied image cannot prove multi-view material evidence"
        )
    if not args.material_crop_confirmed:
        confidence_notes.append(
            "input was not confirmed as a material crop; maps are diagnostic only and cannot unlock lookdev"
        )
    warnings.extend(confidence_notes)
    threshold = clamp01(args.target_threshold)
    verdict = "pass" if confidence >= threshold else ("conditional" if confidence >= threshold - 0.12 else "reject")
    if not args.material_crop_confirmed and verdict == "pass":
        verdict = "conditional"
    patch = material_patch(
        args.material_id,
        image,
        out_dir,
        args.url_prefix,
        size,
        threshold,
        confidence,
        verdict,
        palette,
        map_stats,
        diagnostics,
        warnings,
    )
    patch["referencePbr"]["materialCropConfirmed"] = args.material_crop_confirmed
    patch["referencePbr"]["usable"] = bool(
        confidence >= threshold and args.material_crop_confirmed
    )
    report = {
        "ok": confidence >= threshold and args.material_crop_confirmed,
        "usable": patch["referencePbr"]["usable"],
        "verdict": verdict,
        "confidence": confidence,
        "extractionSuitability": confidence,
        "targetThreshold": threshold,
        "materialId": args.material_id,
        "sourceImage": str(image),
        "outDir": str(out_dir),
        "palette": palette,
        "maps": patch["referencePbr"]["maps"],
        "diagnostics": diagnostics,
        "warnings": warnings,
        "materialCropConfirmed": args.material_crop_confirmed,
        "limitation": "extractionSuitability measures whether these pixels are useful for inferred maps; it is not physical PBR accuracy and still needs render review",
    }
    return report, patch


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--material-id", default="base")
    parser.add_argument(
        "--mask",
        type=Path,
        help="Optional black/white or alpha mask aligned with the material crop; white/opaque pixels are used.",
    )
    parser.add_argument("--size", type=int, default=1024)
    parser.add_argument("--palette-size", type=int, default=5)
    parser.add_argument("--target-threshold", type=float, default=0.7)
    parser.add_argument("--url-prefix", default="")
    parser.add_argument("--spec", type=Path, help="Optional ObjectSculptSpec JSON to patch")
    parser.add_argument("--in-place", action="store_true", help="Patch --spec in place when confidence passes")
    parser.add_argument("--out-spec", type=Path, help="Write patched spec to this path")
    parser.add_argument("--report", type=Path, help="Write extraction report JSON")
    parser.add_argument("--allow-low-confidence", action="store_true", help="Patch/write even when confidence is below threshold")
    parser.add_argument(
        "--material-crop-confirmed",
        action="store_true",
        help="Confirm that the input is a crop dominated by one material, not a full UI/demo screenshot.",
    )
    parser.add_argument(
        "--multi-view-reference",
        action="store_true",
        help="Deprecated compatibility flag; it no longer raises the score from one image.",
    )
    args = parser.parse_args(argv)

    try:
        if args.spec and not args.in_place and not args.out_spec:
            raise ValueError("when --spec is used, choose --in-place or --out-spec")
        if args.in_place and args.out_spec:
            raise ValueError("choose only one of --in-place or --out-spec")
        report, patch = extract(args)
        if args.report:
            write_spec_atomic(args.report.expanduser().resolve(), report)
        if args.spec:
            if not args.material_crop_confirmed:
                raise ValueError(
                    "spec patching requires --material-crop-confirmed; crop one material region first"
                )
            if not args.url_prefix:
                raise ValueError(
                    "spec patching requires --url-prefix so generated browser asset URLs are explicit"
                )
            if not report["ok"] and not args.allow_low_confidence:
                raise ValueError(
                    f"PBR extraction confidence {report['confidence']} is below target "
                    f"{report['targetThreshold']}; spec was not patched"
                )
            spec_path = args.spec.expanduser().resolve()
            document = load_document(spec_path)
            spec = document.resolved
            merge_material_patch(spec, args.material_id, patch)
            output = spec_path if args.in_place else (args.out_spec.expanduser().resolve() if args.out_spec else None)
            if output:
                save_document(document, output)
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0 if report["ok"] or args.allow_low_confidence else 1
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
