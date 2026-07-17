#!/usr/bin/env python3
"""Create one concise ObjectSculptSpec with integrated pre-spec planning."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from sculpt_contract import (
    CURRENT_SCHEMA_VERSION,
    build_pass_plan,
    complexity_minimums,
    parse_json,
    sync_pipeline,
    write_spec_atomic,
)
from sculpt_view_hypotheses import make_view_hypothesis_policy


def slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "object"


def make_pre_spec_assessment(
    target_name: str,
    complexity: str = "moderate",
    intended_use: str = "browser-prop",
) -> dict[str, Any]:
    minimums = complexity_minimums(complexity)
    return {
        "objectClass": {
            "primaryType": "unassessed",
            "formLanguage": [],
            "structureKind": [],
            "motionPotential": [],
            "materialFamilies": [],
            "notes": "Fill these fields from the reference before blockout generation.",
        },
        "complexity": {
            "tier": complexity,
            "scores": {
                "silhouetteComplexity": 0,
                "componentCount": 0,
                "hierarchyDepth": 0,
                "repetitionDensity": 0,
                "materialLayerCount": 0,
                "localDetailDensity": 0,
                "occlusionRisk": 0,
                "actionReadinessNeed": 0,
            },
            "estimatedCounts": {
                "macroComponents": minimums["macroLayers"],
                "mesoComponents": minimums["mesoLayers"],
                "microFeatureGroups": minimums["microLayers"],
                "materialLayers": minimums["materials"],
                "repetitionSystems": 0,
            },
            "reasoning": [
                f"{complexity!r} is only the initial estimate for {target_name!r}; revise it after visual inspection."
            ],
        },
        "specDepthDecision": {
            "requiredDepth": complexity,
            "minimumComponentLevels": [
                level
                for level, count in (
                    ("macro", minimums["macroLayers"]),
                    ("meso", minimums["mesoLayers"]),
                    ("micro", minimums["microLayers"]),
                )
                if count > 0
            ],
            "needsRepetitionSystems": complexity in {"complex", "ultra"},
            "needsMaterialLocalOverrides": complexity != "simple",
            "needsMultipleReviewViews": False,
            "needsActionReadyHierarchy": intended_use in {"animated", "playable", "destructible"},
            "rationale": "Use only the depth needed to preserve the visible identity and intended behavior.",
        },
        "specializedRegions": {
            "status": "unassessed",
            "notes": (
                "Inspect for identity-critical faces and hands. Declare each visible region, "
                "or set status to none with a reason before strict validation."
            ),
            "regions": [],
        },
        "unknownsToResolveBeforeImplementation": [],
    }


def make_quality_contract(
    complexity: str = "moderate",
    quality_profile: str = "balanced",
) -> dict[str, Any]:
    minimums = complexity_minimums(complexity)
    return {
        "qualityBar": complexity,
        "qualityProfile": quality_profile,
        "definitionOfDone": [
            "The final render preserves the reference silhouette, proportions, recognizable structure, material response, and required runtime behavior."
        ],
        "minimumSpecDepth": {
            "macroComponents": minimums["macroLayers"],
            "mesoComponents": minimums["mesoLayers"],
            "microFeatureGroups": minimums["microLayers"],
            "materialLayers": minimums["materials"],
            "repetitionSystems": 0,
            "reviewViewpoints": 3 if quality_profile == "reference-fidelity" else 1,
        },
        "featureGroups": [
            {
                "id": "overall-silhouette",
                "name": "Overall silhouette and proportions",
                "required": True,
                "qualityCriteria": ["Bounding shape, negative space, and main mass ratios are explicit."],
                "evidenceRefs": ["full-object"],
                "failureModes": ["The model reads as a generic placeholder."],
            },
            {
                "id": "primary-structure",
                "name": "Primary structure and attachments",
                "required": True,
                "qualityCriteria": ["Major parts, hierarchy, joints, and contacts are explicit."],
                "evidenceRefs": ["full-object"],
                "failureModes": ["Parts float, intersect accidentally, or use the wrong hierarchy."],
            },
            {
                "id": "reference-lookdev",
                "name": "Material, surface, lighting, and contact shadow",
                "required": True,
                "qualityCriteria": ["Color and light response remain believable under review lighting."],
                "evidenceRefs": ["full-object"],
                "failureModes": ["The object looks flat, uniformly plastic, or detached from the ground."],
            },
        ],
        "visualDeltaChecks": [
            "silhouette and proportion delta",
            "structure and attachment delta",
            "material and lighting delta",
        ],
        "antiShallowSpecRules": [
            "Do not generate blockout before the integrated pre-spec fields and silhouette are filled.",
            "Do not continue a visual pass without a hash-bound comparison manifest and artifact-bound AI review.",
            "Do not lower global or pass-specific thresholds from a review command.",
            "Do not mark optimization complete without measured metrics and a fresh no-regression visual review.",
        ],
    }


def make_base_material(quality_profile: str = "balanced") -> dict[str, Any]:
    texture_resolution = 2048 if quality_profile == "reference-fidelity" else 1024
    return {
        "id": "base",
        "name": "Replace with observed material",
        "type": "standard",
        "shaderModel": "MeshStandardMaterial",
        "baseColor": "#8A7A5F",
        "albedo": {
            "dominant": "#8A7A5F",
            "secondary": ["#6E614B", "#A08F70"],
            "samplingNotes": "Replace with color zones sampled from the reference.",
        },
        "colorVariation": {
            "palette": ["#8A7A5F", "#6E614B", "#A08F70"],
            "pattern": "mottled",
            "amplitude": 0.12,
            "heightCorrelation": 0.2,
        },
        "textureResolution": texture_resolution,
        "textureProjection": {
            "mode": "uv",
            "repeat": [2.0, 2.0],
            "anisotropy": 8,
            "texelDensityIntent": "Keep visible detail at a stable object-space scale.",
        },
        "surfaceFrequencyBands": [
            {"id": "macro", "frequency": 2.0, "amplitude": 0.35, "role": "broad variation"},
            {"id": "meso", "frequency": 12.0, "amplitude": 0.18, "role": "visible relief"},
            {"id": "micro", "frequency": 56.0, "amplitude": 0.06, "role": "highlight breakup"},
        ],
        "roughness": {"base": 0.75, "variation": 0.12, "map": "independent-procedural-field"},
        "metalness": {"base": 0.0, "variation": 0.0},
        "specularIntensity": 0.5,
        "specularColor": "#FFFFFF",
        "envMapIntensity": 0.8,
        "normal": {"pattern": "independent-height-field", "strength": 0.25, "scale": 24.0},
        "bump": {"pattern": "none", "amplitude": 0.0},
        "displacement": {"pattern": "none", "amplitude": 0.0, "silhouetteAffects": False},
        "ambientOcclusion": {"cavityStrength": 0.2, "contactShadowBias": 0.3},
        "wear": {"edgeWear": 0.0, "scratches": [], "chips": []},
        "dirt": {"amount": 0.0, "cavityBias": 0.0, "color": "#2F2A22"},
        "localOverrides": [],
        "shaderNotes": [
            "Replace generic values with observed evidence before lookdev review.",
            "Never reuse albedo as roughness, height, normal, or AO.",
        ],
    }


def make_root_component(target_name: str, interactive: bool = False) -> dict[str, Any]:
    component = {
        "id": "root",
        "name": target_name,
        "componentType": "part",
        "level": "macro",
        "role": "body",
        "importance": 1.0,
        "confidence": 0.5,
        "primitive": "box",
        "geometryDescriptor": {
            "parameters": {},
            "topologyIntent": "blockout primitive; replace from reference",
            "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1},
            "deformationStack": [],
            "uvStrategy": "generated procedural coordinates",
            "normalStrategy": "generated vertex normals",
        },
        "parent": None,
        "attachment": None,
        "dimensions": {
            "width": 1.0,
            "height": 1.0,
            "depth": 1.0,
            "units": "relative",
            "confidence": 0.5,
        },
        "transform": {"position": [0, 0, 0], "rotation": [0, 0, 0], "scale": [1, 1, 1]},
        "actionProfile": {
            "animationRole": "root",
            "pivot": {
                "mode": "center",
                "localPosition": [0, 0, 0],
                "axis": [0, 1, 0],
                "confidence": 0.5,
            },
            "transformChannels": {
                "translate": True,
                "rotate": True,
                "scale": True,
                "bend": False,
                "twist": False,
                "detach": False,
                "visibility": True,
                "materialState": True,
            },
            "sockets": [],
            "collider": {
                "type": "box",
                "offset": [0, 0, 0],
                "scale": [1, 1, 1],
                "isTrigger": False,
            },
            "constraints": [],
            "destruction": {
                "breakable": False,
                "fractureGroup": "root",
                "seamRefs": [],
                "detachableFragments": [],
                "breakImpulse": 0.0,
                "debrisMaterial": "base",
            },
        },
        "material": "base",
        "materialLayers": ["base"],
        "deformations": [],
        "joints": [],
        "seams": [],
        "localFeatures": [],
        "surfaceDetail": {
            "macroRoughness": 0.0,
            "microRoughness": 0.0,
            "bumpAmplitude": 0.0,
            "normalPattern": "",
            "displacementPattern": "",
            "occlusionPattern": "",
            "edgeWearPattern": "",
            "notes": "Fill before lookdev if the surface is not intentionally smooth.",
        },
        "evidenceRefs": ["full-object"],
        "details": [],
        "fidelityTier": "blockout",
    }
    if not interactive:
        component["actionProfile"].pop("collider", None)
        component["actionProfile"].pop("destruction", None)
        component["actionProfile"]["transformChannels"] = {
            "translate": True,
            "rotate": True,
            "scale": True,
            "visibility": True,
        }
    return component


def load_assessment(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    payload = parse_json(path.expanduser().read_text(encoding="utf-8"), "assessment JSON")
    if not isinstance(payload, dict):
        raise ValueError("assessment must be a JSON object")
    return payload


def make_spec(
    target_name: str,
    image: str | None,
    assessment_payload: dict[str, Any] | None = None,
    complexity: str = "moderate",
    intended_use: str = "browser-prop",
    quality_profile: str = "balanced",
) -> dict[str, Any]:
    pre_spec = make_pre_spec_assessment(target_name, complexity, intended_use)
    quality_contract = make_quality_contract(complexity, quality_profile)
    surface_topology_plan: dict[str, Any] = {
        "status": "unassessed",
        "reason": "",
        "decisionRule": (
            "Classify each visible system as continuous sculpt, intentional assembly, "
            "conforming shell, embedded relief, host-bound fiber, or material-only before modules."
        ),
        "groups": [],
    }
    if assessment_payload:
        if isinstance(assessment_payload.get("preSpecAssessment"), dict):
            pre_spec = assessment_payload["preSpecAssessment"]
        if isinstance(assessment_payload.get("qualityContract"), dict):
            quality_contract = assessment_payload["qualityContract"]
        if isinstance(assessment_payload.get("surfaceTopologyPlan"), dict):
            surface_topology_plan = assessment_payload["surfaceTopologyPlan"]
        if not image and isinstance(assessment_payload.get("sourceImage"), str):
            image = assessment_payload["sourceImage"]

    passes = build_pass_plan(complexity, intended_use, quality_profile)
    pass_ids = [item["id"] for item in passes]
    visual_pass_ids = [item["id"] for item in passes if item["evidenceType"] == "visual"]
    interactive = intended_use in {"animated", "playable", "destructible"}
    review_views = ["neutral", "grazing", "reference"] if quality_profile == "reference-fidelity" else ["reference"]
    reference_fidelity = quality_profile == "reference-fidelity"
    visual_threshold = 0.85 if reference_fidelity else 0.7
    critical_threshold = 0.85 if reference_fidelity else 0.8
    important_threshold = 0.78 if reference_fidelity else 0.65
    lookdev_feature_threshold = 0.85 if reference_fidelity else 0.75
    pbr_threshold = 0.75 if reference_fidelity else 0.7
    if isinstance(pre_spec.get("specDepthDecision"), dict):
        pre_spec["specDepthDecision"]["needsMultipleReviewViews"] = (
            quality_profile == "reference-fidelity"
        )
    target_id = slugify(target_name)

    spec: dict[str, Any] = {
        "targetName": target_name,
        "targetId": target_id,
        "schemaVersion": CURRENT_SCHEMA_VERSION,
        "specRevision": 1,
        "intendedUse": intended_use,
        "qualityProfile": quality_profile,
        "sourceImage": image or "",
        "viewHypothesisPolicy": make_view_hypothesis_policy(
            complexity,
            quality_profile,
            image,
        ),
        "suitability": "conditional",
        "scores": {
            "object_isolation": 0,
            "silhouette_readability": 0,
            "depth_inference": 0,
            "primitive_decomposition": 0,
            "material_procedurality": 0,
            "occlusion_risk": 0,
            "interaction_fit": 0,
        },
        "preSpecAssessment": pre_spec,
        "surfaceTopologyPlan": surface_topology_plan,
        "qualityContract": quality_contract,
        "terminologyProfile": {
            "domain": "real-time procedural Three.js asset",
            "geometryTerms": ["silhouette", "proportion", "primitive", "bevel", "taper", "attachment"],
            "materialTerms": ["albedo", "roughness", "metalness", "normal", "ambient occlusion"],
            "lightingTerms": ["key light", "fill light", "environment light", "contact shadow"],
            "descriptionRule": "Pair plain-language observations with measurable geometry, material, or light parameters.",
        },
        "qualityTargets": {
            "targetFidelity": visual_threshold,
            "mustMatch": ["silhouette", "primary proportions", "recognizable structure", "material response"],
            "niceToHave": ["micro wear", "secondary lighting match"],
            "reviewViewpoints": review_views,
            "diagnosticTargets": {
                "silhouetteIou": 0.88 if reference_fidelity else 0.75,
                "maximumCentroidDelta": 0.02 if reference_fidelity else 0.05,
                "maximumAspectRatioDelta": 0.03 if reference_fidelity else 0.08,
                "minimumDetailEnergyRatio": 0.75 if reference_fidelity else 0.65,
                "minimumEdgeDensityRatio": 0.35 if reference_fidelity else 0.20,
                "minimumHistogramIntersection": 0.35 if reference_fidelity else 0.25,
                "maximumMeanColorDelta": 0.40 if reference_fidelity else 0.55,
                "minimumHighlightCoverageRatio": 0.10 if reference_fidelity else 0.05,
                "minimumHighlightEnergyRatio": 0.10 if reference_fidelity else 0.05,
                "acceptanceAuthority": False,
                "guardrailMode": "veto-only",
            },
        },
        "selfCorrectLoop": {
            "enabled": True,
            "reviewAfterPasses": pass_ids,
            "allowedActions": [
                "continue",
                "refine-spec",
                "refine-code",
                "refine-batch",
                "request-input",
                "stop",
            ],
            "specRefineTriggers": ["missing part", "wrong primitive", "wrong proportions", "reference ambiguity"],
            "codeRefineTriggers": ["render mismatch", "runtime failure", "performance budget exceeded"],
            "stopCriteria": ["quality target reached", "remaining gap needs a better reference or manual art"],
            "visualAcceptance": {
                "reviewer": "ai-vision",
                "threshold": visual_threshold,
                "minimumAiVisionScore": visual_threshold,
                "comparisonArtifactRequired": True,
                "layerScoresRequired": True,
                "codePixelDiffIsAcceptanceAuthority": False,
                "requiredLayerScores": [],
                "scoringRule": "AI vision reviews the full no-crop contact sheet; pass-specific scores are defined in buildPasses.",
                "featureReviewPolicy": {
                    "enabled": True,
                    "reviewUnit": "multi-view-contact-sheet",
                    "maxCriticalFeaturesPerPass": 8,
                    "maxImportantFeaturesPerPass": 3,
                    "criticalDefaultThreshold": critical_threshold,
                    "importantAverageThreshold": important_threshold,
                    "adaptiveEscalation": True,
                    "singleImagePairOnly": False,
                    "selectionRule": (
                        "Review a few identity-defining semantic systems, not every mesh; "
                        "visible face and hand regions remain independent critical targets."
                    ),
                },
            },
            "screenshotPolicy": {
                "requiredForPasses": visual_pass_ids,
                "preferredCapture": "in-app-browser-screenshot",
                "fallbackCapture": "user-supplied-screenshot-path",
                "minimumEvidence": "Required reference/render views, one combined sheet, AI scores, and critique.",
                "reviewPairRule": "Use matching camera and framing whenever possible.",
                "acceptanceAuthority": "AI vision plus pass-specific semantic gates.",
            },
        },
        "featureReviewTargets": [
            {
                "id": "overall-silhouette",
                "name": "Overall silhouette and proportions",
                "tier": "critical",
                "passIds": [
                    pass_id
                    for pass_id in ("blockout", "form", "optimization")
                    if pass_id in pass_ids
                ],
                "minimumScore": critical_threshold,
                "mustPass": True,
                "componentRefs": ["root"],
                "evidenceRefs": ["full-object"],
            },
            {
                "id": "primary-structure",
                "name": "Primary structure and attachment system",
                "tier": "critical",
                "passIds": [
                    pass_id
                    for pass_id in ("structure", "form", "optimization")
                    if pass_id in pass_ids
                ],
                "minimumScore": critical_threshold,
                "mustPass": True,
                "componentRefs": ["root"],
                "evidenceRefs": ["full-object"],
            },
            {
                "id": "reference-lookdev",
                "name": "Reference material and lighting response",
                "tier": "critical",
                "passIds": [
                    pass_id for pass_id in ("lookdev", "optimization") if pass_id in pass_ids
                ],
                "minimumScore": lookdev_feature_threshold,
                "mustPass": True,
                "componentRefs": ["root"],
                "evidenceRefs": ["full-object"],
            },
        ],
        "actionReadiness": {
            "enabled": interactive,
            "contract": "Use stable named pivot nodes; add sockets, colliders, and destruction data only when the intended use needs them.",
            "defaultRigType": "action-ready-rig" if interactive else "stable-static-root",
            "rootMotionNode": "root",
            "requiredComponentFields": ["id", "parent", "transform", "actionProfile"],
            "transformChannels": ["translate", "rotate", "scale", "visibility"],
            "authoringRules": ["Do not merge independently movable parts."],
            "destructionPolicy": {"defaultBreakable": False},
        },
        "assumptions": [],
        "coordinateFrame": {
            "front": "camera-facing side in the reference",
            "up": "image up",
            "scaleReference": "relative unit scale until first render review",
        },
        "silhouette": {
            "boundingShape": "",
            "aspectRatios": [],
            "symmetry": "",
            "dominantCurves": [],
            "negativeSpaces": [],
            "landmarks": [],
        },
        "viewEvidence": [
            {
                "id": "full-object",
                "view": "primary",
                "imageRegion": {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0, "units": "normalized"},
                "observations": [],
                "confidence": 0.5,
            }
        ],
        "componentTree": [make_root_component(target_name, interactive)],
        "materials": [make_base_material(quality_profile)],
        "repetitionSystems": [],
        "buildPasses": passes,
        "lookDevTargets": {
            "qualityPriority": quality_profile,
            "materialPass": {
                "minimumTextureResolution": 1024,
                "independentMapChannels": ["albedo", "roughness", "height", "normal", "ambient-occlusion"],
                "referencePbrExtraction": {
                    "requiredWhenSourceImagePresent": quality_profile == "reference-fidelity",
                    "targetThreshold": pbr_threshold,
                    "stopOnLowConfidence": True,
                    "acceptedLimitation": "Single-image maps are inferred material evidence, not photogrammetry.",
                },
            },
            "lightingPass": {"requiredTerms": ["key/fill/environment", "exposure/tone", "contact shadow"]},
            "screenshotReview": review_views,
        },
        "reviewHistory": [],
        "lodPlan": [
            {"tier": "near", "distance": 0, "strategy": "full accepted model"},
            {"tier": "far", "distance": 30, "strategy": "merge static parts and reduce non-silhouette detail"},
        ],
        "performanceBudget": {
            "qualityPriority": quality_profile,
            "targetTriangles": 250000,
            "maxDrawCalls": 120,
            "textureSize": 2048,
            "fpsTarget": 60,
            "optimizationPolicy": "Measure first; optimize without removing reference-critical features.",
        },
        "lightingFromPhoto": [],
        "proceduralStrategy": [
            "Match silhouette and proportions.",
            "Add only the structure required by complexity.",
            "Validate material, surface, lighting, and contact shadow together.",
            "Run interaction and performance checks only when relevant.",
        ],
        "risks": [],
    }
    sync_pipeline(spec)
    return spec


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target_name")
    parser.add_argument("--image")
    parser.add_argument(
        "--complexity",
        choices=("simple", "moderate", "complex", "ultra"),
        default="moderate",
    )
    parser.add_argument(
        "--intended-use",
        choices=("static-render", "browser-prop", "game-prop", "animated", "playable", "destructible"),
        required=True,
        help="Choose explicitly so a game/static quality request cannot silently become browser-prop.",
    )
    parser.add_argument(
        "--quality-profile",
        choices=("balanced", "reference-fidelity"),
        required=True,
        help="Choose explicitly; use reference-fidelity for close, sharp, game-quality matching.",
    )
    parser.add_argument(
        "--assessment",
        type=Path,
        help="Optional legacy assessment JSON; pre-spec is already included by this command.",
    )
    parser.add_argument(
        "--layout",
        choices=("modular", "monolithic"),
        default="modular",
        help=(
            "modular creates the v4 root contract only; add block specs later with "
            "`sculpt module add`. monolithic keeps the schema 3.1 compatibility layout."
        ),
    )
    parser.add_argument("--out", type=Path)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    try:
        spec = make_spec(
            args.target_name,
            args.image,
            load_assessment(args.assessment),
            args.complexity,
            args.intended_use,
            args.quality_profile,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    if args.layout == "modular":
        from sculpt_modules import make_manifest

        payload_object = make_manifest(spec)
    else:
        payload_object = spec
    payload = json.dumps(payload_object, indent=2, ensure_ascii=False) + "\n"
    if not args.out:
        print(payload, end="")
        return 0
    output = args.out.expanduser().resolve()
    if output.exists() and not args.force:
        parser.error(f"{output} already exists; use --force to overwrite")
    write_spec_atomic(output, payload_object)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
