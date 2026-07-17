#!/usr/bin/env python3
"""Inspect and gate the current adaptive sculpt pass."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Any

from sculpt_contract import (
    check_pass as contract_check_pass,
    component_type,
    load_spec_file,
    pass_order,  # compatibility re-export for existing script consumers
    pipeline_status,
    sync_pipeline,
    write_spec_atomic,
)
from sculpt_geometry import (
    VALID_PRIMITIVES,
    validate_geometry_component,
    validate_repetition_systems,
)


ATTACHMENT_ROLES = {
    "appendage", "branch", "limb", "arm", "leg", "handle", "connector",
    "tube", "cable", "horn", "wing", "tail", "root", "fork", "rib",
    "support", "hinge", "socket", "pipe",
}
ATTACHMENT_PRIMITIVES = {"cylinder", "cone", "capsule", "tube", "curve-sweep"}
SPECIAL_PRIMITIVE_PROFILES = {
    "fiber-system": "fiber",
    "volume-field": "volume",
}


def has_non_empty(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip()) and value.strip().lower() not in {"none", "unassessed", "n/a"}
    if isinstance(value, list):
        return any(has_non_empty(item) for item in value)
    if isinstance(value, dict):
        return any(has_non_empty(item) for item in value.values())
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return math.isfinite(float(value)) and abs(float(value)) > 0
    return False


def has_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def is_vector3(value: Any) -> bool:
    return isinstance(value, list) and len(value) == 3 and all(has_number(item) for item in value)


def layer_number(value: Any, keys: tuple[str, ...]) -> float:
    if has_number(value):
        return float(value)
    if isinstance(value, dict):
        for key in keys:
            if has_number(value.get(key)):
                return float(value[key])
    return 0.0


def component_requires_attachment(component: dict[str, Any]) -> bool:
    if component_type(component) == "assembly" or not component.get("parent"):
        return False
    tokens = set(
        re.findall(
            r"[a-z0-9]+",
            " ".join(
                str(component.get(field) or "").lower()
                for field in ("name", "id", "role")
            ),
        )
    )
    primitive = str(component.get("primitive") or "").lower()
    return bool(tokens & ATTACHMENT_ROLES) or primitive in ATTACHMENT_PRIMITIVES


def attachment_complete(component: dict[str, Any]) -> bool:
    attachment = component.get("attachment")
    if not isinstance(attachment, dict):
        return False
    return all(
        (
            is_vector3(attachment.get("localStart")),
            is_vector3(attachment.get("localEnd")),
            has_non_empty(attachment.get("parentSocket") or attachment.get("parentId")),
            has_non_empty(attachment.get("contactType")),
            layer_number(attachment.get("embedDepth"), ("base", "amount", "value")) > 0
            or layer_number(attachment.get("overlap"), ("base", "amount", "value")) > 0,
            has_number(attachment.get("gapTolerance")),
        )
    )


def attachment_gaps(spec: dict[str, Any]) -> list[str]:
    gaps: list[str] = []
    for component in spec.get("componentTree", []):
        if not isinstance(component, dict) or not component_requires_attachment(component):
            continue
        if not attachment_complete(component):
            component_id = str(component.get("id") or component.get("name") or "(unnamed)")
            gaps.append(
                f"component {component_id!r} needs parent socket, endpoints, contact, overlap, and gap tolerance"
            )
    return gaps


def _intentional_uniform_surface(value: dict[str, Any]) -> bool:
    text = " ".join(
        str(value.get(field) or "")
        for field in ("surfaceIntent", "samplingNotes", "shaderNotes", "notes")
    ).lower()
    return any(
        phrase in text
        for phrase in (
            "intentionally smooth",
            "intentional smooth",
            "intentionally uniform",
            "intentional uniform",
            "flat graphic color",
        )
    )


def _hero_material_ids(spec: dict[str, Any], materials: list[dict[str, Any]]) -> set[str]:
    hero_ids: set[str] = set()
    for component in spec.get("componentTree", []):
        if not isinstance(component, dict) or component_type(component) == "assembly":
            continue
        importance = component.get("importance", 1.0)
        if not has_number(importance) or float(importance) < 0.5:
            continue
        material_layers = (
            component.get("materialLayers")
            if isinstance(component.get("materialLayers"), list)
            else []
        )
        for value in [component.get("material"), *material_layers]:
            if isinstance(value, str) and value.strip():
                hero_ids.add(value)
        for feature in component.get("localFeatures", []) if isinstance(component.get("localFeatures"), list) else []:
            if isinstance(feature, dict) and isinstance(feature.get("material"), str):
                hero_ids.add(feature["material"])
    if hero_ids:
        return hero_ids
    return {
        str(item["id"])
        for item in materials
        if isinstance(item.get("id"), str) and item.get("qualityTier") != "utility"
    }


def material_gaps(spec: dict[str, Any]) -> list[str]:
    materials = [item for item in spec.get("materials", []) if isinstance(item, dict)]
    if not materials:
        return ["materials array is empty"]
    materials_by_id = {
        item.get("id"): item
        for item in materials
        if isinstance(item.get("id"), str)
    }
    gaps: list[str] = []
    contract = spec.get("qualityContract")
    minimums = contract.get("minimumSpecDepth") if isinstance(contract, dict) else {}
    minimum_materials = minimums.get("materialLayers") if isinstance(minimums, dict) else None
    if isinstance(minimum_materials, int) and len(materials) < minimum_materials:
        gaps.append(
            f"materialLayers is below the selected complexity depth ({len(materials)} < {minimum_materials})"
        )
    hero_ids = _hero_material_ids(spec, materials)
    for material in materials:
        material_id = str(material.get("id") or "(unnamed)")
        if material.get("qualityTier") == "utility" or material_id not in hero_ids:
            continue
        intentional_uniform = _intentional_uniform_surface(material)
        variation = material.get("colorVariation")
        albedo = material.get("albedo")
        palette_values = (
            variation.get("palette", []) if isinstance(variation, dict) else []
        )
        secondary = albedo.get("secondary", []) if isinstance(albedo, dict) else []
        palette = len([item for item in [*palette_values, *secondary] if has_non_empty(item)]) >= 2
        response = (
            layer_number(material.get("roughness"), ("variation",)) > 0
            or layer_number(material.get("normal"), ("strength", "amplitude")) > 0
            or layer_number(material.get("bump"), ("amplitude", "strength")) > 0
            or layer_number(material.get("displacement"), ("amplitude", "strength")) > 0
        )
        locality = (
            layer_number(material.get("ambientOcclusion"), ("cavityStrength", "strength")) > 0
            or isinstance(material.get("referencePbr"), dict)
        )
        if not palette and not intentional_uniform:
            gaps.append(
                f"hero material {material_id!r} needs a multi-color reference palette or an explicit intentional-uniform rule"
            )
        if not response and not intentional_uniform:
            gaps.append(
                f"hero material {material_id!r} needs executable roughness variation or normal/bump/displacement response"
            )
        if not locality and not intentional_uniform:
            gaps.append(
                f"hero material {material_id!r} needs executable AO/reference-PBR locality or an explicit intentional-smooth rule"
            )

    lookdev = spec.get("lookDevTargets")
    quality_first = isinstance(lookdev, dict) and lookdev.get("qualityPriority") == "reference-fidelity"
    if quality_first and has_non_empty(spec.get("sourceImage")):
        for material in materials:
            if material.get("qualityTier") == "utility":
                continue
            reference = material.get("referencePbr")
            maps = reference.get("maps") if isinstance(reference, dict) else None
            has_browser_urls = isinstance(maps, dict) and all(
                isinstance(maps.get(channel), dict)
                and has_non_empty(maps[channel].get("url"))
                for channel in ("albedo", "roughness", "height", "normal", "ao")
            )
            if (
                not isinstance(reference, dict)
                or reference.get("usable") is not True
                or reference.get("materialCropConfirmed") is not True
                or not has_browser_urls
            ):
                material_id = str(material.get("id") or "(unnamed)")
                gaps.append(
                    f"material {material_id!r} needs confirmed material-crop PBR maps with browser URLs, or use balanced quality"
                )
    for component in spec.get("componentTree", []):
        if not isinstance(component, dict):
            continue
        primitive = component.get("primitive")
        expected = SPECIAL_PRIMITIVE_PROFILES.get(primitive)
        if expected is None:
            continue
        material_id = component.get("material")
        material = materials_by_id.get(material_id)
        if not isinstance(material, dict):
            continue
        actual = material.get("materialProfile", "standard")
        if actual != expected:
            component_id = str(component.get("id") or "(unnamed)")
            gaps.append(
                f"material {material_id!r} used by {primitive} component {component_id!r} "
                f"needs materialProfile {expected!r}"
            )
    return gaps


def surface_gaps(spec: dict[str, Any]) -> list[str]:
    components = [
        item
        for item in spec.get("componentTree", [])
        if isinstance(item, dict) and component_type(item) != "assembly"
    ]
    gaps: list[str] = []
    for item in components:
        importance = item.get("importance", 1.0)
        if has_number(importance) and float(importance) < 0.75:
            continue
        detail = item.get("surfaceDetail")
        meaningful = isinstance(detail, dict) and any(
            layer_number(detail.get(field), ("base", "amount", "value")) > 0
            for field in ("macroRoughness", "microRoughness", "bumpAmplitude")
        )
        if meaningful or (isinstance(detail, dict) and _intentional_uniform_surface(detail)):
            continue
        component_id = str(item.get("id") or item.get("name") or "(unnamed)")
        gaps.append(
            f"important component {component_id!r} needs numeric executable surfaceDetail or an explicit intentionally-smooth rule"
        )
    return gaps


def lighting_gaps(spec: dict[str, Any]) -> list[str]:
    lighting = spec.get("lightingFromPhoto", [])
    if not isinstance(lighting, list):
        return ["lightingFromPhoto must describe the review lighting"]
    text = " ".join(str(item).lower() for item in lighting)
    groups = {
        "key light": ("key", "main light", "sun"),
        "fill or environment light": ("fill", "ambient", "environment", "hdr", "hemisphere"),
        "tone/exposure": ("tone", "exposure", "aces", "filmic"),
        "contact shadow": ("contact shadow", "ground shadow", "ambient occlusion", "ao"),
    }
    return [f"lightingFromPhoto is missing {label}" for label, words in groups.items() if not any(word in text for word in words)]


def interaction_gaps(spec: dict[str, Any]) -> list[str]:
    readiness = spec.get("actionReadiness")
    if not isinstance(readiness, dict) or readiness.get("enabled") is not True:
        return ["actionReadiness.enabled must be true for an interaction pass"]
    components = [
        item
        for item in spec.get("componentTree", [])
        if isinstance(item, dict) and component_type(item) != "assembly"
    ]
    missing = [
        str(item.get("id") or "(unnamed)")
        for item in components
        if item.get("level") in {"macro", "meso"}
        and not isinstance(item.get("actionProfile"), dict)
    ]
    if missing:
        return ["macro/meso components missing actionProfile: " + ", ".join(missing)]
    return []


def pre_spec_gaps(spec: dict[str, Any]) -> list[str]:
    assessment = spec.get("preSpecAssessment")
    if not isinstance(assessment, dict):
        return ["preSpecAssessment is required before blockout"]
    object_class = assessment.get("objectClass")
    gaps: list[str] = []
    if not isinstance(object_class, dict):
        gaps.append("preSpecAssessment.objectClass is required")
    else:
        if not has_non_empty(object_class.get("primaryType")):
            gaps.append("identify the primary object type from the reference")
        for field in ("formLanguage", "structureKind", "materialFamilies"):
            if not has_non_empty(object_class.get(field)):
                gaps.append(f"fill preSpecAssessment.objectClass.{field} from visual inspection")
    silhouette = spec.get("silhouette")
    if not isinstance(silhouette, dict) or not has_non_empty(
        [silhouette.get("boundingShape"), silhouette.get("aspectRatios"), silhouette.get("dominantCurves")]
    ):
        gaps.append("record the observed silhouette shape/proportions before blockout")
    return gaps


def spec_depth_gaps(spec: dict[str, Any], include_micro: bool) -> list[str]:
    contract = spec.get("qualityContract")
    minimums = contract.get("minimumSpecDepth") if isinstance(contract, dict) else None
    if not isinstance(minimums, dict):
        return ["qualityContract.minimumSpecDepth is required"]
    components = [
        item
        for item in spec.get("componentTree", [])
        if isinstance(item, dict) and component_type(item) != "assembly"
    ]
    actual = {
        "macroComponents": sum(item.get("level") == "macro" for item in components),
        "mesoComponents": sum(item.get("level") == "meso" for item in components),
        "microFeatureGroups": sum(
            len(item.get("localFeatures", []))
            for item in components
            if isinstance(item.get("localFeatures"), list)
        ),
    }
    fields = ("macroComponents", "mesoComponents")
    if include_micro:
        fields += ("microFeatureGroups",)
    return [
        f"{field} is below the selected complexity depth ({actual[field]} < {minimums[field]})"
        for field in fields
        if isinstance(minimums.get(field), int) and actual[field] < minimums[field]
    ]


def pass_specific_evidence(pass_id: str) -> list[str]:
    if pass_id in {"structure", "form", "structural-pass", "form-refinement"}:
        return ["child joints have explicit attachment contracts and no visible floating roots"]
    if pass_id in {"lookdev", "material-pass", "surface-pass", "lighting-pass"}:
        return ["palette, material response, local detail, lighting, and contact shadow are reviewable"]
    if pass_id in {"interaction", "interaction-pass"}:
        return ["runtime checks cover load, transforms, and the requested interaction"]
    if pass_id in {"optimization", "optimization-pass"}:
        return ["measured FPS, draw calls, triangles, device, and performance capture"]
    return []


def pass_specific_gaps(spec: dict[str, Any], pass_id: str) -> list[str]:
    gaps: list[str] = []
    if pass_id == "blockout":
        gaps.extend(pre_spec_gaps(spec))
    if pass_id in {"structure", "form", "structural-pass", "form-refinement"}:
        gaps.extend(attachment_gaps(spec))
        gaps.extend(
            spec_depth_gaps(
                spec,
                include_micro=pass_id in {"form", "form-refinement"},
            )
        )
    if pass_id in {"lookdev", "material-pass"}:
        gaps.extend(material_gaps(spec))
    if pass_id in {"lookdev", "surface-pass"}:
        gaps.extend(surface_gaps(spec))
    if pass_id in {"lookdev", "lighting-pass"}:
        gaps.extend(lighting_gaps(spec))
    if pass_id in {"interaction", "interaction-pass"}:
        gaps.extend(interaction_gaps(spec))
    return list(dict.fromkeys(gaps))


def check_pass(
    spec: dict[str, Any],
    requested_pass: str,
    *,
    _geometry_prevalidated: bool = False,
) -> tuple[bool, str, dict[str, Any]]:
    allowed, message, status = contract_check_pass(spec, requested_pass)
    if not allowed:
        return allowed, message, status
    if not _geometry_prevalidated:
        capability_errors = geometry_capability_report(spec)["errors"]
        if capability_errors:
            return (
                False,
                f"pass {requested_pass!r} has unsupported geometry: {'; '.join(capability_errors)}",
                status,
            )
    gaps = pass_specific_gaps(spec, requested_pass)
    if gaps:
        return False, f"pass {requested_pass!r} needs spec refinement: {'; '.join(gaps)}", status
    return True, message, status


def geometry_capability_report(spec: dict[str, Any]) -> dict[str, Any]:
    """Summarize whether the declared hierarchy has real registered emitters."""
    components = [item for item in spec.get("componentTree", []) if isinstance(item, dict)]
    component_lookup = {
        str(item["id"]): item
        for item in components
        if isinstance(item.get("id"), str) and item["id"].strip()
    }
    repetition_systems = spec.get("repetitionSystems", [])
    errors = validate_repetition_systems(repetition_systems)
    for component in components:
        errors.extend(
            validate_geometry_component(
                component,
                repetition_systems,
                component_lookup,
            )
        )
    errors = list(dict.fromkeys(errors))
    return {
        "canGenerate": not errors,
        "parts": sum(component_type(item) == "part" for item in components),
        "assemblies": sum(component_type(item) == "assembly" for item in components),
        "repetitionSystems": len(repetition_systems) if isinstance(repetition_systems, list) else 0,
        "supportedPrimitives": sorted(VALID_PRIMITIVES),
        "errors": errors,
    }


def status_payload(spec: dict[str, Any]) -> dict[str, Any]:
    status = pipeline_status(spec)
    capabilities = geometry_capability_report(spec)
    current_gaps = (
        []
        if status["currentPass"] == "complete"
        else pass_specific_gaps(spec, str(status["currentPass"]))
    )
    current_gaps.extend(f"geometry: {error}" for error in capabilities["errors"])
    return {
        "targetName": spec.get("targetName"),
        **status,
        "geometryCapabilities": capabilities,
        "currentPassGaps": list(dict.fromkeys(current_gaps)),
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("status", "sync"):
        child = subparsers.add_parser(command)
        child.add_argument("spec", type=Path)
    check = subparsers.add_parser("check")
    check.add_argument("spec", type=Path)
    check.add_argument("--pass-id", required=True)
    args = parser.parse_args(argv)
    path = args.spec.expanduser().resolve()
    try:
        from sculpt_modules import (
            is_module_manifest,
            load_document,
            module_status,
            read_raw_spec,
            save_document,
        )

        raw_spec = read_raw_spec(path)
        if is_module_manifest(raw_spec):
            modular_status = module_status(path, raw_spec)
            if args.command in {"status", "sync"}:
                if modular_status["assemblyReady"]:
                    document = load_document(path, allow_missing=False)
                    if args.command == "sync":
                        sync_pipeline(document.resolved)
                        save_document(document)
                    modular_status["passWorkflow"] = status_payload(document.resolved)
                print(json.dumps(modular_status, indent=2, ensure_ascii=False))
                return 0 if not modular_status["errors"] else 1
            if not modular_status["assemblyReady"]:
                print(
                    json.dumps(
                        {
                            "allowed": False,
                            "message": (
                                "pass workflow is locked until every required module is accepted; "
                                f"current module is {modular_status.get('currentModule')!r}"
                            ),
                            **modular_status,
                        },
                        indent=2,
                        ensure_ascii=False,
                    )
                )
                return 1
        spec = load_spec_file(path)
        if args.command == "status":
            print(json.dumps(status_payload(spec), indent=2, ensure_ascii=False))
            return 0
        if args.command == "sync":
            sync_pipeline(spec)
            write_spec_atomic(path, spec)
            print(json.dumps(status_payload(spec), indent=2, ensure_ascii=False))
            return 0
        allowed, message, status = check_pass(spec, args.pass_id)
        print(json.dumps({"allowed": allowed, "message": message, **status}, indent=2, ensure_ascii=False))
        return 0 if allowed else 1
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
