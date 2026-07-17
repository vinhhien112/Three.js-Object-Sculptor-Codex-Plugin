#!/usr/bin/env python3
"""Create a side-by-side visual acceptance sheet for AI vision review.

The sheet is only evidence packaging. It does not score the images. Codex or
another AI vision reviewer should inspect the generated sheet and write the
score back with append_sculpt_review.py.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Any

from sculpt_contract import (
    VISUAL_EVIDENCE_ARTIFACT_TYPE,
    VISUAL_EVIDENCE_GENERATOR,
    VISUAL_EVIDENCE_MANIFEST_VERSION,
    file_sha256,
    parse_json,
    visual_evidence_manifest_sha256,
)
from extract_reference_pbr import color_distance, rgb_to_hex, sample_corner_background
from sculpt_image_io import (
    load_image_rgba as load_image,
    read_png,  # compatibility re-export for existing script consumers
    write_png_rgb,
)


def composite_over_checker(pixel: tuple[int, int, int, int], x: int, y: int) -> tuple[int, int, int]:
    red, green, blue, alpha = pixel
    background = 238 if ((x // 12 + y // 12) % 2 == 0) else 210
    mix = alpha / 255.0
    return (
        round(red * mix + background * (1 - mix)),
        round(green * mix + background * (1 - mix)),
        round(blue * mix + background * (1 - mix)),
    )


def resize_contain(
    width: int,
    height: int,
    pixels: list[tuple[int, int, int, int]],
    target_w: int,
    target_h: int,
) -> list[tuple[int, int, int]]:
    """Fit the entire image inside a panel without cropping reference evidence."""
    scale = min(target_w / width, target_h / height)
    scaled_w = max(1, round(width * scale))
    scaled_h = max(1, round(height * scale))
    offset_x = (target_w - scaled_w) // 2
    offset_y = (target_h - scaled_h) // 2
    output = [
        (238, 238, 238) if ((x // 12 + y // 12) % 2 == 0) else (210, 210, 210)
        for y in range(target_h)
        for x in range(target_w)
    ]
    for y in range(scaled_h):
        source_y = min(height - 1, int(y / scale))
        for x in range(scaled_w):
            source_x = min(width - 1, int(x / scale))
            target_x = offset_x + x
            target_y = offset_y + y
            output[target_y * target_w + target_x] = composite_over_checker(
                pixels[source_y * width + source_x], target_x, target_y
            )
    return output


def resize_mask_contain(
    width: int,
    height: int,
    mask: list[bool],
    target_w: int,
    target_h: int,
) -> list[bool]:
    """Place a foreground mask in the same contain/no-crop coordinate system."""
    scale = min(target_w / width, target_h / height)
    scaled_w = max(1, round(width * scale))
    scaled_h = max(1, round(height * scale))
    offset_x = (target_w - scaled_w) // 2
    offset_y = (target_h - scaled_h) // 2
    output = [False] * (target_w * target_h)
    for y in range(scaled_h):
        source_y = min(height - 1, int(y / scale))
        for x in range(scaled_w):
            source_x = min(width - 1, int(x / scale))
            output[(offset_y + y) * target_w + offset_x + x] = mask[
                source_y * width + source_x
            ]
    return output


def build_silhouette_mask(
    width: int,
    height: int,
    pixels: list[tuple[int, int, int, int]],
) -> tuple[list[bool], dict, list[str]]:
    """Segment an isolated object without the material extractor's color-preservation fallback."""
    warnings: list[str] = []
    transparent_fraction = sum(alpha < 245 for _, _, _, alpha in pixels) / max(1, len(pixels))
    background, background_noise = sample_corner_background(width, height, pixels)
    threshold = max(10.0, background_noise * 3.0)
    if transparent_fraction > 0.03:
        mask = [alpha > 24 for _, _, _, alpha in pixels]
        source = "alpha"
    else:
        mask = [
            alpha > 16 and color_distance((red, green, blue), background) > threshold
            for red, green, blue, alpha in pixels
        ]
        source = "corner-background-distance"
    coverage = sum(mask) / max(1, len(mask))
    if coverage < 0.01:
        warnings.append("silhouette mask is nearly empty; use an alpha image or cleaner background")
    if coverage > 0.95:
        warnings.append("silhouette mask covers almost the full frame; background separation is unreliable")
    return (
        mask,
        {
            "source": source,
            "backgroundColor": rgb_to_hex(background),
            "backgroundNoise": round(background_noise, 3),
            "threshold": round(threshold, 3),
            "transparentPixelFraction": round(transparent_fraction, 4),
            "foregroundCoverage": round(coverage, 4),
        },
        warnings,
    )


def _mask_stats(mask: list[bool], width: int, height: int) -> dict:
    points = [(index % width, index // width) for index, keep in enumerate(mask) if keep]
    if not points:
        return {
            "coverage": 0.0,
            "bbox": [0.0, 0.0, 0.0, 0.0],
            "centroid": [0.5, 0.5],
            "aspectRatio": 0.0,
        }
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    x0, x1 = min(xs), max(xs) + 1
    y0, y1 = min(ys), max(ys) + 1
    bbox_w = max(1, x1 - x0)
    bbox_h = max(1, y1 - y0)
    return {
        "coverage": round(len(points) / max(1, width * height), 5),
        "bbox": [
            round(x0 / width, 5),
            round(y0 / height, 5),
            round(bbox_w / width, 5),
            round(bbox_h / height, 5),
        ],
        "centroid": [
            round((sum(xs) / len(xs) + 0.5) / width, 5),
            round((sum(ys) / len(ys) + 0.5) / height, 5),
        ],
        "aspectRatio": round(bbox_w / bbox_h, 5),
    }


def _contour_points(mask: list[bool], width: int, height: int, limit: int = 1200) -> list[tuple[int, int]]:
    points: list[tuple[int, int]] = []
    for y in range(height):
        for x in range(width):
            if not mask[y * width + x]:
                continue
            if (
                x == 0
                or y == 0
                or x == width - 1
                or y == height - 1
                or not mask[y * width + x - 1]
                or not mask[y * width + x + 1]
                or not mask[(y - 1) * width + x]
                or not mask[(y + 1) * width + x]
            ):
                points.append((x, y))
    if len(points) <= limit:
        return points
    step = len(points) / limit
    return [points[min(len(points) - 1, int(index * step))] for index in range(limit)]


def _mean_nearest_distance(
    source: list[tuple[int, int]],
    target: list[tuple[int, int]],
) -> float:
    if not source or not target:
        return 1.0
    total = 0.0
    for source_x, source_y in source:
        nearest_squared = min(
            (source_x - target_x) ** 2 + (source_y - target_y) ** 2
            for target_x, target_y in target
        )
        total += math.sqrt(nearest_squared)
    return total / len(source)


def silhouette_diagnostics(
    reference: tuple[int, int, list[tuple[int, int, int, int]]],
    render: tuple[int, int, list[tuple[int, int, int, int]]],
    size: int = 256,
) -> tuple[dict, list[bool], list[bool]]:
    """Return deterministic alignment hints; these metrics never approve a pass."""
    ref_w, ref_h, ref_pixels = reference
    ren_w, ren_h, ren_pixels = render
    ref_mask, ref_mask_info, ref_warnings = build_silhouette_mask(ref_w, ref_h, ref_pixels)
    ren_mask, ren_mask_info, ren_warnings = build_silhouette_mask(ren_w, ren_h, ren_pixels)
    normalized_ref = resize_mask_contain(ref_w, ref_h, ref_mask, size, size)
    normalized_render = resize_mask_contain(ren_w, ren_h, ren_mask, size, size)
    intersection = sum(
        first and second for first, second in zip(normalized_ref, normalized_render)
    )
    union = sum(first or second for first, second in zip(normalized_ref, normalized_render))
    iou = intersection / union if union else 1.0
    ref_stats = _mask_stats(normalized_ref, size, size)
    render_stats = _mask_stats(normalized_render, size, size)
    centroid_delta = math.dist(ref_stats["centroid"], render_stats["centroid"]) / math.sqrt(2)
    ref_aspect = float(ref_stats["aspectRatio"])
    render_aspect = float(render_stats["aspectRatio"])
    aspect_delta = (
        abs(ref_aspect - render_aspect) / max(ref_aspect, render_aspect)
        if ref_aspect > 0 and render_aspect > 0
        else 1.0
    )
    ref_contour = _contour_points(normalized_ref, size, size)
    render_contour = _contour_points(normalized_render, size, size)
    contour_distance = (
        _mean_nearest_distance(ref_contour, render_contour)
        + _mean_nearest_distance(render_contour, ref_contour)
    ) * 0.5 / math.hypot(size, size)
    ref_bbox = ref_stats["bbox"]
    render_bbox = render_stats["bbox"]
    ref_area = float(ref_bbox[2]) * float(ref_bbox[3])
    render_area = float(render_bbox[2]) * float(render_bbox[3])
    uniform_scale = math.sqrt(ref_area / render_area) if ref_area > 0 and render_area > 0 else 1.0
    appearance = appearance_diagnostics(
        (ref_w, ref_h, ref_pixels, ref_mask),
        (ren_w, ren_h, ren_pixels, ren_mask),
    )
    return (
        {
            "diagnosticOnly": True,
            "acceptanceAuthority": False,
            "maskResolution": size,
            "silhouetteIou": round(iou, 5),
            "centroidDelta": round(centroid_delta, 5),
            "aspectRatioDelta": round(aspect_delta, 5),
            "normalizedContourDistance": round(contour_distance, 5),
            "appearance": appearance,
            "reference": ref_stats,
            "render": render_stats,
            "alignmentHints": {
                "translateX": round(float(ref_stats["centroid"][0]) - float(render_stats["centroid"][0]), 5),
                "translateY": round(float(ref_stats["centroid"][1]) - float(render_stats["centroid"][1]), 5),
                "uniformScale": round(uniform_scale, 5),
                "aspectCorrection": round(ref_aspect / render_aspect, 5) if render_aspect > 0 else 1.0,
                "applyOrder": ["camera-framing", "root-transform", "geometry"],
            },
            "maskDiagnostics": {
                "reference": ref_mask_info,
                "render": ren_mask_info,
                "warnings": list(dict.fromkeys(ref_warnings + ren_warnings)),
            },
        },
        normalized_ref,
        normalized_render,
    )


def _foreground_appearance_stats(
    width: int,
    height: int,
    pixels: list[tuple[int, int, int, int]],
    mask: list[bool],
) -> dict[str, Any]:
    stride = max(1, max(width, height) // 512)
    capped_gradient = 0.0
    edges = 0
    pairs = 0
    count = 0
    highlight_count = 0
    highlight_energy = 0.0
    color_sum = [0.0, 0.0, 0.0]
    histogram = [0] * 24

    def luminance(pixel: tuple[int, int, int, int]) -> float:
        return (pixel[0] * 0.2126 + pixel[1] * 0.7152 + pixel[2] * 0.0722) / 255.0

    for y in range(0, max(0, height - stride), stride):
        for x in range(0, max(0, width - stride), stride):
            index = y * width + x
            if not mask[index]:
                continue
            pixel = pixels[index]
            count += 1
            for channel, value in enumerate(pixel[:3]):
                color_sum[channel] += value / 255.0
                histogram[channel * 8 + min(7, value // 32)] += 1
            value = luminance(pixel)
            if value > 0.78:
                highlight_count += 1
            highlight_energy += max(0.0, value - 0.72)
            for neighbor in (index + stride, index + stride * width):
                if not mask[neighbor]:
                    continue
                delta = abs(value - luminance(pixels[neighbor]))
                capped_gradient += min(delta, 0.12)
                edges += int(delta > 0.06)
                pairs += 1
    divisor = max(1, count)
    histogram_total = max(1, sum(histogram))
    return {
        "detailEnergy": capped_gradient / max(1, pairs),
        "edgeDensity": edges / max(1, pairs),
        "highlightCoverage": highlight_count / divisor,
        "highlightEnergy": highlight_energy / divisor,
        "meanColor": [value / divisor for value in color_sum],
        "histogram": [value / histogram_total for value in histogram],
        "sampleCount": count,
    }


def appearance_diagnostics(
    reference: tuple[int, int, list[tuple[int, int, int, int]], list[bool]],
    render: tuple[int, int, list[tuple[int, int, int, int]], list[bool]],
) -> dict[str, Any]:
    reference_stats = _foreground_appearance_stats(*reference)
    render_stats = _foreground_appearance_stats(*render)

    def symmetric_ratio(first: float, second: float, quiet_floor: float) -> float:
        if max(first, second) <= quiet_floor:
            return 1.0
        if min(first, second) <= 0:
            return 0.0
        return min(first, second) / max(first, second)

    detail_ratio = symmetric_ratio(
        float(reference_stats["detailEnergy"]),
        float(render_stats["detailEnergy"]),
        0.003,
    )
    edge_ratio = symmetric_ratio(
        float(reference_stats["edgeDensity"]),
        float(render_stats["edgeDensity"]),
        0.005,
    )
    highlight_coverage_ratio = symmetric_ratio(
        float(reference_stats["highlightCoverage"]),
        float(render_stats["highlightCoverage"]),
        0.002,
    )
    highlight_energy_ratio = symmetric_ratio(
        float(reference_stats["highlightEnergy"]),
        float(render_stats["highlightEnergy"]),
        0.001,
    )
    histogram_similarity = sum(
        min(first, second)
        for first, second in zip(reference_stats["histogram"], render_stats["histogram"])
    )
    mean_color_delta = math.dist(
        reference_stats["meanColor"], render_stats["meanColor"]
    ) / math.sqrt(3)
    return {
        "diagnosticOnly": True,
        "guardrailMode": "veto-only",
        "detailEnergyRatio": round(detail_ratio, 5),
        "edgeDensityRatio": round(edge_ratio, 5),
        "highlightCoverageRatio": round(highlight_coverage_ratio, 5),
        "highlightEnergyRatio": round(highlight_energy_ratio, 5),
        "foregroundHistogramIntersection": round(histogram_similarity, 5),
        "foregroundMeanColorDelta": round(mean_color_delta, 5),
        "referenceDetailEnergy": round(float(reference_stats["detailEnergy"]), 6),
        "renderDetailEnergy": round(float(render_stats["detailEnergy"]), 6),
        "referenceEdgeDensity": round(float(reference_stats["edgeDensity"]), 6),
        "renderEdgeDensity": round(float(render_stats["edgeDensity"]), 6),
        "referenceHighlightCoverage": round(float(reference_stats["highlightCoverage"]), 6),
        "renderHighlightCoverage": round(float(render_stats["highlightCoverage"]), 6),
        "referenceHighlightEnergy": round(float(reference_stats["highlightEnergy"]), 6),
        "renderHighlightEnergy": round(float(render_stats["highlightEnergy"]), 6),
        "sampleCounts": {
            "reference": int(reference_stats["sampleCount"]),
            "render": int(render_stats["sampleCount"]),
        },
    }


def write_silhouette_overlay(
    path: Path,
    reference_mask: list[bool],
    render_mask: list[bool],
    size: int,
) -> None:
    pixels: list[tuple[int, int, int]] = []
    for reference, render in zip(reference_mask, render_mask):
        if reference and render:
            pixels.append((240, 240, 236))
        elif reference:
            pixels.append((238, 78, 78))
        elif render:
            pixels.append((51, 190, 210))
        else:
            pixels.append((18, 22, 26))
    write_png_rgb(path, size, size, pixels)


def fill_rect(
    canvas: list[tuple[int, int, int]],
    width: int,
    x0: int,
    y0: int,
    rect_w: int,
    rect_h: int,
    color: tuple[int, int, int],
) -> None:
    height = len(canvas) // width
    for y in range(max(0, y0), min(height, y0 + rect_h)):
        row = y * width
        for x in range(max(0, x0), min(width, x0 + rect_w)):
            canvas[row + x] = color


def blit(
    canvas: list[tuple[int, int, int]],
    width: int,
    image: list[tuple[int, int, int]],
    image_w: int,
    x0: int,
    y0: int,
) -> None:
    image_h = len(image) // image_w
    height = len(canvas) // width
    for y in range(image_h):
        target_y = y0 + y
        if target_y < 0 or target_y >= height:
            continue
        for x in range(image_w):
            target_x = x0 + x
            if 0 <= target_x < width:
                canvas[target_y * width + target_x] = image[y * image_w + x]


def create_sheet(
    reference: Path,
    render: Path,
    out: Path,
    width: int,
    height: int,
    gutter: int,
) -> dict:
    payload = create_sheet_pairs(
        [{"viewId": "primary", "referenceImage": reference, "renderScreenshot": render}],
        out,
        width,
        height,
        gutter,
    )
    payload["referenceImage"] = str(reference.resolve())
    payload["renderScreenshot"] = str(render.resolve())
    payload["manifestSha256"] = visual_evidence_manifest_sha256(payload)
    return payload


def create_sheet_pairs(
    pairs: list[dict],
    out: Path,
    width: int,
    height: int,
    gutter: int,
    diagnostics_dir: Path | None = None,
    render_provenance: dict[str, Any] | None = None,
) -> dict:
    if not pairs:
        raise ValueError("at least one reference/render pair is required")
    panel_w = width
    panel_h = height
    canvas_w = panel_w * 2 + gutter * 3
    header_h = 28
    row_h = panel_h + header_h + gutter
    canvas_h = len(pairs) * row_h + gutter
    canvas = [(246, 242, 236)] * (canvas_w * canvas_h)
    evidence_views: list[dict] = []
    for index, pair in enumerate(pairs):
        reference = Path(pair["referenceImage"]).expanduser().resolve()
        render = Path(pair["renderScreenshot"]).expanduser().resolve()
        ref_w, ref_h, ref_pixels = load_image(reference)
        ren_w, ren_h, ren_pixels = load_image(render)
        diagnostics, reference_mask, render_mask = silhouette_diagnostics(
            (ref_w, ref_h, ref_pixels),
            (ren_w, ren_h, ren_pixels),
        )
        y0 = gutter + index * row_h
        fill_rect(canvas, canvas_w, gutter, y0, panel_w, header_h, (40, 45, 48))
        fill_rect(canvas, canvas_w, gutter * 2 + panel_w, y0, panel_w, header_h, (40, 45, 48))
        fill_rect(canvas, canvas_w, gutter, y0 + header_h, panel_w, panel_h, (230, 230, 230))
        fill_rect(canvas, canvas_w, gutter * 2 + panel_w, y0 + header_h, panel_w, panel_h, (230, 230, 230))
        ref_panel = resize_contain(ref_w, ref_h, ref_pixels, panel_w, panel_h)
        ren_panel = resize_contain(ren_w, ren_h, ren_pixels, panel_w, panel_h)
        blit(canvas, canvas_w, ref_panel, panel_w, gutter, y0 + header_h)
        blit(canvas, canvas_w, ren_panel, panel_w, gutter * 2 + panel_w, y0 + header_h)
        fill_rect(
            canvas,
            canvas_w,
            panel_w + gutter + gutter // 2,
            y0,
            max(2, gutter // 5),
            panel_h + header_h,
            (170, 146, 92),
        )
        provenance = pair.get("referenceProvenance")
        if provenance is None:
            provenance = {
                "origin": "observed",
                "allowedUse": "acceptance",
                "source": "user-supplied-or-original-reference",
            }
        if not isinstance(provenance, dict):
            raise ValueError("referenceProvenance must be an object")
        origin = provenance.get("origin")
        allowed_use = provenance.get("allowedUse")
        if origin not in {"observed", "synthetic-hypothesis"}:
            raise ValueError("referenceProvenance.origin must be observed or synthetic-hypothesis")
        if allowed_use not in {"acceptance", "planning-veto"}:
            raise ValueError("referenceProvenance.allowedUse must be acceptance or planning-veto")
        if origin == "synthetic-hypothesis" and allowed_use != "planning-veto":
            raise ValueError("synthetic-hypothesis references may only use planning-veto")
        evidence_view = {
            "viewId": str(pair.get("viewId") or f"view-{index + 1}"),
            "referenceImage": str(reference),
            "referenceSha256": file_sha256(reference),
            "referenceDimensions": {"width": ref_w, "height": ref_h},
            "referenceProvenance": provenance,
            "renderScreenshot": str(render),
            "renderSha256": file_sha256(render),
            "renderDimensions": {"width": ren_w, "height": ren_h},
            "comparisonImage": str(out.resolve()),
            "fitDiagnostics": diagnostics,
        }
        if diagnostics_dir is not None:
            diagnostics_dir.mkdir(parents=True, exist_ok=True)
            safe_id = re.sub(r"[^a-zA-Z0-9_-]+", "-", evidence_view["viewId"]).strip("-") or f"view-{index + 1}"
            overlay = (diagnostics_dir / f"{safe_id}-silhouette-overlay.png").resolve()
            write_silhouette_overlay(overlay, reference_mask, render_mask, 256)
            evidence_view["diagnosticOverlay"] = str(overlay)
        evidence_views.append(evidence_view)
    write_png_rgb(out, canvas_w, canvas_h, canvas)
    comparison_hash = file_sha256(out)
    for evidence_view in evidence_views:
        evidence_view["comparisonSha256"] = comparison_hash
        evidence_view["comparisonDimensions"] = {"width": canvas_w, "height": canvas_h}
    manifest = {
        "artifactType": VISUAL_EVIDENCE_ARTIFACT_TYPE,
        "manifestVersion": VISUAL_EVIDENCE_MANIFEST_VERSION,
        "generator": VISUAL_EVIDENCE_GENERATOR,
        "comparisonImage": str(out.resolve()),
        "comparisonSha256": comparison_hash,
        "comparisonDimensions": {"width": canvas_w, "height": canvas_h},
        "views": evidence_views,
        "layout": "each row: left=full reference,right=full render",
        "panelWidth": panel_w,
        "panelHeight": panel_h,
        "fitMode": "contain-no-crop",
        "note": "Send this exact contact-sheet hash to AI vision. Diagnostics are veto-only guardrails and never approve a pass by themselves.",
    }
    if render_provenance is not None:
        provenance = dict(render_provenance)
        provenance["renderSha256"] = sorted({
            str(view["renderSha256"])
            for view in evidence_views
            if isinstance(view.get("renderSha256"), str)
        })
        manifest["renderProvenance"] = provenance
    manifest["manifestSha256"] = visual_evidence_manifest_sha256(manifest)
    # Keep the old key in returned JSON for callers that only display the views.
    return {**manifest, "evidenceSet": evidence_views}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--render", type=Path)
    parser.add_argument(
        "--pairs-json",
        help=(
            "JSON array/file of {viewId,referenceImage,renderScreenshot,referenceProvenance?}; "
            "referenceProvenance={origin: observed|synthetic-hypothesis, "
            "allowedUse: acceptance|planning-veto, source: string}; synthetic hypotheses "
            "must use planning-veto"
        ),
    )
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--manifest-out", type=Path, help="Write a hash-bound visual evidence manifest")
    parser.add_argument(
        "--sculpt-manifest",
        type=Path,
        help="For module acceptance, bind the evidence to the current sculpt manifest snapshot.",
    )
    parser.add_argument(
        "--module-id",
        help="Module whose declared runtime implementation produced this render.",
    )
    parser.add_argument(
        "--runtime-receipt",
        type=Path,
        help=(
            "JSON object/array captured from window.__THREEJS_SCULPT_CAPTURE_RUNTIME__; "
            "required for module acceptance"
        ),
    )
    parser.add_argument(
        "--diagnostics-dir",
        type=Path,
        help="Optionally write red/reference, cyan/render silhouette overlay PNGs.",
    )
    parser.add_argument("--panel-width", type=int, default=720)
    parser.add_argument("--panel-height", type=int, default=720)
    parser.add_argument("--gutter", type=int, default=24)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        if bool(args.sculpt_manifest) != bool(args.module_id):
            raise ValueError("--sculpt-manifest and --module-id must be provided together")
        if bool(args.runtime_receipt) != bool(args.sculpt_manifest):
            raise ValueError(
                "--runtime-receipt is required with --sculpt-manifest/--module-id "
                "and cannot be used without them"
            )
        render_provenance = None
        if args.sculpt_manifest and args.module_id:
            from sculpt_manifest import entry_by_id, load_modules, read_object
            from sculpt_module_state import (
                implementation_contract_paths,
                implementation_semantic_hashes,
                module_hash,
            )
            from sculpt_module_contract import module_build_receipt_path

            sculpt_manifest_path = args.sculpt_manifest.expanduser().resolve()
            sculpt_manifest = read_object(sculpt_manifest_path, "sculpt manifest")
            if args.module_id not in entry_by_id(sculpt_manifest):
                raise ValueError(f"unknown module {args.module_id!r}")
            module = load_modules(
                sculpt_manifest_path, sculpt_manifest, [args.module_id]
            )[args.module_id][1]
            payload = module.get("payload") if isinstance(module.get("payload"), dict) else {}
            global_spec = (
                sculpt_manifest.get("globalSpec")
                if isinstance(sculpt_manifest.get("globalSpec"), dict)
                else {}
            )
            declared_view_ids = sorted({
                item.get("id")
                for source in (global_spec.get("viewEvidence", []), payload.get("viewEvidence", []))
                if isinstance(source, list)
                for item in source
                if isinstance(item, dict) and isinstance(item.get("id"), str) and item.get("id")
            })
            implementation_paths = implementation_contract_paths(sculpt_manifest_path, module)
            build_path = module_build_receipt_path(sculpt_manifest_path, args.module_id)
            if not build_path.is_file():
                raise ValueError(
                    "module build receipt is missing; run `sculpt module build` before rendering"
                )
            build_receipt = json.loads(build_path.read_text(encoding="utf-8"))
            if not isinstance(build_receipt, dict):
                raise ValueError("module build receipt must be a JSON object")
            runtime_path = args.runtime_receipt.expanduser().resolve()
            if not runtime_path.is_file():
                raise ValueError(f"runtime receipt is missing: {runtime_path}")
            raw_runtime_receipt = json.loads(runtime_path.read_text(encoding="utf-8"))
            if isinstance(raw_runtime_receipt, list):
                matches = [
                    item
                    for item in raw_runtime_receipt
                    if isinstance(item, dict)
                    and item.get("factoryId") == build_receipt.get("factoryId")
                ]
                if len(matches) != 1:
                    raise ValueError(
                        "runtime receipt array must contain exactly one entry for the current factory"
                    )
                runtime_receipt = matches[0]
            elif isinstance(raw_runtime_receipt, dict):
                runtime_receipt = raw_runtime_receipt
            else:
                raise ValueError("runtime receipt must be a JSON object or array")
            render_provenance = {
                "artifactType": "threejs-sculpt-render-provenance",
                "version": 2,
                "moduleId": args.module_id,
                "moduleHash": module_hash(
                    sculpt_manifest_path, sculpt_manifest, args.module_id
                ),
                "declaredViewIds": declared_view_ids,
                "implementationFiles": {
                    str(path): file_sha256(path) for path in implementation_paths
                },
                "implementationSemanticFiles": implementation_semantic_hashes(
                    implementation_paths
                ),
                "buildReceiptPath": str(build_path),
                "buildReceiptSha256": file_sha256(build_path),
                "buildReceipt": build_receipt,
                "runtimeReceiptPath": str(runtime_path),
                "runtimeReceiptSha256": file_sha256(runtime_path),
                "runtimeReceipt": runtime_receipt,
            }
        if args.pairs_json:
            candidate = Path(args.pairs_json).expanduser()
            raw = candidate.read_text(encoding="utf-8") if candidate.is_file() else args.pairs_json
            pairs = parse_json(raw, "--pairs-json")
            if not isinstance(pairs, list) or not all(isinstance(item, dict) for item in pairs):
                raise ValueError("--pairs-json must contain an array of objects")
        elif args.reference and args.render:
            pairs = [
                {
                    "viewId": "primary",
                    "referenceImage": str(args.reference),
                    "renderScreenshot": str(args.render),
                }
            ]
        else:
            raise ValueError("provide --reference and --render, or provide --pairs-json")
        payload = create_sheet_pairs(
            pairs,
            args.out.expanduser().resolve(),
            max(128, args.panel_width),
            max(128, args.panel_height),
            max(6, args.gutter),
            args.diagnostics_dir.expanduser().resolve() if args.diagnostics_dir else None,
            render_provenance,
        )
        if args.manifest_out:
            manifest = args.manifest_out.expanduser().resolve()
            manifest.parent.mkdir(parents=True, exist_ok=True)
            manifest_payload = {
                key: value for key, value in payload.items() if key != "evidenceSet"
            }
            manifest.write_text(
                json.dumps(manifest_payload, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(payload, indent=2, ensure_ascii=False) if args.json else payload["comparisonImage"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
