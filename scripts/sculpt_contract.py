#!/usr/bin/env python3
"""Canonical workflow, evidence, and pass-state rules for procedural sculpting."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import struct
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from visual_feature_gate import feature_gate_failures


DEFAULT_PASS_ORDER = ["blockout", "form", "lookdev", "optimization"]
VISUAL_PASS_IDS = {
    "blockout",
    "structure",
    "form",
    "lookdev",
    "structural-pass",
    "form-refinement",
    "material-pass",
    "surface-pass",
    "lighting-pass",
}
RUNTIME_PASS_IDS = {"interaction", "interaction-pass"}
METRICS_PASS_IDS = {"optimization", "optimization-pass"}
REFINEMENT_ACTIONS = frozenset({"refine-spec", "refine-code", "refine-batch"})
STRATEGY_RESET_ACTION = "strategy-reset"
CORRECTION_SCOPES = frozenset({"spec", "code"})
MAX_ATOMIC_REFINEMENT_BATCHES = 2
MAX_STRATEGY_RESETS = 1

DERIVED_SPEC_FIELDS = {
    "reviewHistory",
    "visualEvidence",
    "sculptPipeline",
    "pbrExtractionHistory",
}

REALTIME_USES = {"browser-prop", "game-prop", "playable", "destructible"}
INTERACTIVE_USES = {"animated", "playable", "destructible"}
CURRENT_SCHEMA_VERSION = "3.1"
LEGACY_SCHEMA_VERSION = "2.0"
COMPONENT_TYPES = frozenset({"part", "assembly"})
VISUAL_EVIDENCE_ARTIFACT_TYPE = "threejs-sculpt-visual-evidence"
VISUAL_EVIDENCE_MANIFEST_VERSION = 1
VISUAL_EVIDENCE_GENERATOR = "threejs-object-sculptor/compare"

_SCHEMA_VERSION_PATTERN = re.compile(
    r"^(?P<major>0|[1-9][0-9]*)\.(?P<minor>0|[1-9][0-9]*)"
    r"(?:\.(?P<patch>0|[1-9][0-9]*))?$"
)


def refinement_budget(records: Any) -> dict[str, Any]:
    """Bound atomic fixes; a recorded strategy change starts one fresh fix cycle."""

    used = 0
    strategy_resets = 0
    if isinstance(records, list):
        strategy_resets = sum(
            1
            for record in records
            if isinstance(record, Mapping) and record.get("action") == STRATEGY_RESET_ACTION
        )
        for record in reversed(records):
            if not isinstance(record, Mapping):
                continue
            action = record.get("action")
            if action in REFINEMENT_ACTIONS:
                used += 1
                continue
            if action == STRATEGY_RESET_ACTION:
                break
            if action == "stop":
                break
            if action == "continue" and record.get("accepted", True) is True:
                break
    remaining = max(0, MAX_ATOMIC_REFINEMENT_BATCHES - used)
    return {
        "maximumBatches": MAX_ATOMIC_REFINEMENT_BATCHES,
        "usedBatches": used,
        "remainingBatches": remaining,
        "exhausted": remaining == 0,
        "maximumStrategyResets": MAX_STRATEGY_RESETS,
        "usedStrategyResets": strategy_resets,
        "remainingStrategyResets": max(0, MAX_STRATEGY_RESETS - strategy_resets),
    }


def correction_batch_from_verdict(verdict: Any) -> dict[str, Any]:
    """Normalize one refine verdict into the atomic batch the builder must apply."""

    if not isinstance(verdict, Mapping) or verdict.get("action") not in REFINEMENT_ACTIONS:
        return {}
    action = str(verdict["action"])
    default_scope = "spec" if action == "refine-spec" else "code" if action == "refine-code" else ""
    issues = [
        {
            "id": str(issue.get("id")),
            "rootCauseKey": str(issue.get("rootCauseKey")),
            "severity": str(issue.get("severity")),
            "target": str(issue.get("target")),
            "reason": str(issue.get("reason")),
        }
        for issue in verdict.get("issues", [])
        if isinstance(issue, Mapping)
        and issue.get("status") == "open"
        and isinstance(issue.get("id"), str)
        and issue.get("id")
    ]
    issue_ids = {issue["id"] for issue in issues}
    corrections: list[dict[str, Any]] = []
    scopes: set[str] = set()
    for index, correction in enumerate(verdict.get("corrections", [])):
        if not isinstance(correction, Mapping) or correction.get("issueId") not in issue_ids:
            continue
        scope = correction.get("scope", default_scope)
        normalized_scope = str(scope) if scope in CORRECTION_SCOPES else default_scope
        if normalized_scope:
            scopes.add(normalized_scope)
        corrections.append(
            {
                "sequence": index + 1,
                "issueId": str(correction.get("issueId")),
                "scope": normalized_scope,
                "target": str(correction.get("target")),
                "parameterPath": str(correction.get("parameterPath")),
                "change": str(correction.get("change")),
                "expectedDelta": str(correction.get("expectedDelta")),
            }
        )
    return {
        "artifactType": "threejs-sculpt-correction-batch",
        "version": 1,
        "batchId": str(verdict.get("reviewId") or "refinement"),
        "action": action,
        "atomic": True,
        "issues": issues,
        "issueIds": [issue["id"] for issue in issues],
        "rootCauseKeys": sorted({issue["rootCauseKey"] for issue in issues}),
        "scopes": sorted(scopes),
        "corrections": corrections,
        "correctionCount": len(corrections),
        "executionPolicy": "apply-all-corrections-before-render",
        "reviewPolicy": "one-render-and-one-review-after-the-complete-batch",
    }


def correction_batch_from_plan(
    action: Any,
    batch_id: Any,
    plan: Any,
) -> dict[str, Any]:
    """Keep the legacy manual correction plan on the same atomic execution contract."""

    if action not in REFINEMENT_ACTIONS or not isinstance(plan, list) or not plan:
        return {}
    scope = "spec" if action == "refine-spec" else "code"
    issues: list[dict[str, Any]] = []
    corrections: list[dict[str, Any]] = []
    for index, item in enumerate(plan):
        if not isinstance(item, Mapping):
            continue
        issue_id = f"{batch_id or 'manual-refinement'}-{index + 1}"
        reason = str(item.get("reason") or "Apply the declared correction.")
        issues.append(
            {
                "id": issue_id,
                "severity": "major",
                "target": str(item.get("target") or "model"),
                "reason": reason,
            }
        )
        value = f" to {item.get('value')!r}" if "value" in item else ""
        corrections.append(
            {
                "sequence": index + 1,
                "issueId": issue_id,
                "scope": scope,
                "target": str(item.get("target") or "model"),
                "parameterPath": str(item.get("parameterPath") or "unspecified"),
                "change": f"{item.get('action', 'update')}{value}: {reason}",
                "expectedDelta": reason,
            }
        )
    if not corrections:
        return {}
    return {
        "artifactType": "threejs-sculpt-correction-batch",
        "version": 1,
        "batchId": str(batch_id or "manual-refinement"),
        "action": str(action),
        "atomic": True,
        "issues": issues,
        "issueIds": [issue["id"] for issue in issues],
        "scopes": [scope],
        "corrections": corrections,
        "correctionCount": len(corrections),
        "executionPolicy": "apply-all-corrections-before-render",
        "reviewPolicy": "one-render-and-one-review-after-the-complete-batch",
    }


def parse_schema_version(value: Any) -> tuple[int, int, int]:
    """Parse a numeric schema version without float or lexical comparison errors."""
    if not isinstance(value, str):
        raise ValueError("schemaVersion must be a string in major.minor format")
    match = _SCHEMA_VERSION_PATTERN.fullmatch(value.strip())
    if match is None:
        raise ValueError(
            f"invalid schemaVersion {value!r}; expected major.minor or major.minor.patch"
        )
    return (
        int(match.group("major")),
        int(match.group("minor")),
        int(match.group("patch") or 0),
    )


def schema_version_at_least(
    spec_or_version: Mapping[str, Any] | str,
    minimum: str,
) -> bool:
    """Compare schema versions numerically, defaulting a missing spec field to v2.0."""
    if isinstance(spec_or_version, Mapping):
        value = spec_or_version.get("schemaVersion", LEGACY_SCHEMA_VERSION)
    else:
        value = spec_or_version
    return parse_schema_version(value) >= parse_schema_version(minimum)


def component_type(component: Mapping[str, Any]) -> str:
    """Return the additive component kind; legacy components are geometry parts."""
    value = component.get("componentType", "part")
    return value if isinstance(value, str) else str(value)


def parse_json(text: str, label: str = "JSON") -> Any:
    try:
        return json.loads(
            text,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"{label} contains non-finite number {value}")
            ),
        )
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid {label}: {exc}") from exc


def load_spec_file(path: Path) -> dict[str, Any]:
    payload = parse_json(path.read_text(encoding="utf-8"), "spec JSON")
    if not isinstance(payload, dict):
        raise ValueError("spec must be a JSON object")
    if payload.get("schemaVersion") == "4.0":
        # Lazy import avoids coupling the stable schema 3.1 engine to the
        # optional compositional manifest layer during module import.
        from sculpt_modules import resolve_manifest

        return resolve_manifest(path, payload, allow_missing=True)
    return payload


def write_spec_atomic(path: Path, spec: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(spec, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def file_sha256(path: Path) -> str:
    """Return a full content digest without loading large evidence files at once."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def image_dimensions(path: Path) -> tuple[int, int]:
    """Read PNG/JPEG dimensions and reject files that merely have an image extension."""
    with path.open("rb") as handle:
        header = handle.read(32)
        if header.startswith(b"\x89PNG\r\n\x1a\n"):
            if len(header) < 24 or header[12:16] != b"IHDR":
                raise ValueError("invalid PNG header")
            width, height = struct.unpack(">II", header[16:24])
            if width <= 0 or height <= 0:
                raise ValueError("invalid PNG dimensions")
            return width, height
        if not header.startswith(b"\xff\xd8"):
            raise ValueError("evidence must be a real PNG or JPEG image")
        handle.seek(2)
        while True:
            marker_prefix = handle.read(1)
            if not marker_prefix:
                break
            if marker_prefix != b"\xff":
                continue
            marker = handle.read(1)
            while marker == b"\xff":
                marker = handle.read(1)
            if not marker or marker in {b"\xd8", b"\xd9"}:
                continue
            length_bytes = handle.read(2)
            if len(length_bytes) != 2:
                break
            segment_length = struct.unpack(">H", length_bytes)[0]
            if segment_length < 2:
                break
            marker_value = marker[0]
            if marker_value in {
                0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF,
            }:
                payload = handle.read(segment_length - 2)
                if len(payload) < 5:
                    break
                height, width = struct.unpack(">HH", payload[1:5])
                if width <= 0 or height <= 0:
                    break
                return width, height
            handle.seek(segment_length - 2, os.SEEK_CUR)
    raise ValueError("invalid or unsupported JPEG image")


def visual_evidence_manifest_sha256(manifest: Mapping[str, Any]) -> str:
    """Digest the immutable evidence manifest, excluding compatibility aliases."""
    payload = {
        key: value
        for key, value in manifest.items()
        if key not in {"manifestSha256", "evidenceSet", "type"}
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _stored_dimensions(value: Any) -> tuple[int, int] | None:
    if not isinstance(value, dict):
        return None
    width = value.get("width")
    height = value.get("height")
    if (
        not isinstance(width, int)
        or isinstance(width, bool)
        or not isinstance(height, int)
        or isinstance(height, bool)
        or width <= 0
        or height <= 0
    ):
        return None
    return width, height


def visual_evidence_integrity_failures(evidence: Any) -> list[str]:
    """Validate evidence provenance, content hashes, image identity, and dimensions."""
    if not isinstance(evidence, dict):
        return ["visual evidence must be a comparison manifest object"]
    failures: list[str] = []
    if evidence.get("artifactType") != VISUAL_EVIDENCE_ARTIFACT_TYPE:
        failures.append("visual evidence must come from the compare command")
    if evidence.get("manifestVersion") != VISUAL_EVIDENCE_MANIFEST_VERSION:
        failures.append(
            f"visual evidence manifestVersion must be {VISUAL_EVIDENCE_MANIFEST_VERSION}"
        )
    if evidence.get("generator") != VISUAL_EVIDENCE_GENERATOR:
        failures.append("visual evidence generator provenance is missing or invalid")
    stored_manifest_hash = evidence.get("manifestSha256")
    try:
        computed_manifest_hash = visual_evidence_manifest_sha256(evidence)
    except (TypeError, ValueError):
        computed_manifest_hash = ""
    if (
        not isinstance(stored_manifest_hash, str)
        or len(stored_manifest_hash) != 64
        or stored_manifest_hash != computed_manifest_hash
    ):
        failures.append("visual evidence manifest hash is missing or does not match its contents")

    views = evidence.get("views")
    if not isinstance(views, list) or not views:
        failures.append("visual evidence manifest needs at least one view")
        return failures

    top_comparison = evidence.get("comparisonImage")
    top_hash = evidence.get("comparisonSha256")
    top_dimensions = _stored_dimensions(evidence.get("comparisonDimensions"))
    if not isinstance(top_comparison, str) or not top_comparison.strip():
        failures.append("visual evidence manifest comparisonImage is required")
    if not isinstance(top_hash, str) or len(top_hash) != 64:
        failures.append("visual evidence manifest comparisonSha256 is required")
    if top_dimensions is None:
        failures.append("visual evidence manifest comparisonDimensions are required")

    seen_view_ids: set[str] = set()
    inspected_files: dict[str, tuple[str, tuple[int, int]] | str] = {}
    for index, view in enumerate(views):
        label = f"visual evidence view {index}"
        if not isinstance(view, dict):
            failures.append(f"{label} must be an object")
            continue
        view_id = view.get("viewId")
        if not isinstance(view_id, str) or not view_id.strip():
            failures.append(f"{label} needs viewId")
        elif view_id in seen_view_ids:
            failures.append(f"duplicate visual evidence viewId {view_id!r}")
        else:
            seen_view_ids.add(view_id)
        provenance = view.get("referenceProvenance")
        if provenance is not None:
            if not isinstance(provenance, dict):
                failures.append(f"{label} referenceProvenance must be an object")
            else:
                origin = provenance.get("origin")
                allowed_use = provenance.get("allowedUse")
                if origin not in {"observed", "synthetic-hypothesis"}:
                    failures.append(f"{label} referenceProvenance.origin is invalid")
                if allowed_use not in {"acceptance", "planning-veto"}:
                    failures.append(f"{label} referenceProvenance.allowedUse is invalid")
                if origin == "synthetic-hypothesis" and allowed_use != "planning-veto":
                    failures.append(
                        f"{label} synthetic-hypothesis references may only use planning-veto"
                    )
        identities: dict[str, tuple[str, str, tuple[int, int] | None]] = {}
        for prefix, path_field in (
            ("reference", "referenceImage"),
            ("render", "renderScreenshot"),
            ("comparison", "comparisonImage"),
        ):
            path_value = view.get(path_field)
            digest_value = view.get(f"{prefix}Sha256")
            dimensions_value = _stored_dimensions(view.get(f"{prefix}Dimensions"))
            if not isinstance(path_value, str) or not path_value.strip():
                failures.append(f"{label} missing {path_field}")
                continue
            if "://" in path_value or path_value.startswith(("data:", "blob:")):
                failures.append(f"{label} {path_field} must be a local immutable file")
                continue
            if not isinstance(digest_value, str) or len(digest_value) != 64:
                failures.append(f"{label} missing {prefix}Sha256")
                continue
            if dimensions_value is None:
                failures.append(f"{label} missing {prefix}Dimensions")
                continue
            path = Path(path_value).expanduser()
            if not path.is_file():
                failures.append(f"{label} {path_field} does not exist: {path_value}")
                continue
            cache_key = str(path.resolve())
            inspected = inspected_files.get(cache_key)
            if inspected is None:
                try:
                    inspected = (file_sha256(path), image_dimensions(path))
                except (OSError, ValueError) as exc:
                    inspected = str(exc)
                inspected_files[cache_key] = inspected
            if isinstance(inspected, str):
                failures.append(
                    f"{label} {path_field} is not valid image evidence: {inspected}"
                )
                continue
            actual_digest, actual_dimensions = inspected
            if actual_digest != digest_value:
                failures.append(f"{label} {path_field} content changed after comparison")
            if actual_dimensions != dimensions_value:
                failures.append(f"{label} {path_field} dimensions changed after comparison")
            identities[prefix] = (path_value, digest_value, dimensions_value)
        reference_identity = identities.get("reference")
        render_identity = identities.get("render")
        comparison_identity = identities.get("comparison")
        if (
            reference_identity is not None
            and render_identity is not None
            and reference_identity[1] == render_identity[1]
        ):
            failures.append(f"{label} reference and render cannot be the same image content")
        if comparison_identity is not None:
            if comparison_identity[0] != top_comparison or comparison_identity[1] != top_hash:
                failures.append(f"{label} comparison identity does not match the manifest")
            if top_dimensions is not None and comparison_identity[2] != top_dimensions:
                failures.append(f"{label} comparison dimensions do not match the manifest")
    return list(dict.fromkeys(failures))


def visual_evidence_authority_failures(
    evidence: Any,
    required_view_ids: Iterable[str] | None = None,
) -> list[str]:
    """Reject synthetic hypotheses as acceptance truth while preserving legacy evidence."""
    if not isinstance(evidence, dict):
        return ["visual evidence must be a comparison manifest object"]
    failures: list[str] = []
    required = {str(item) for item in required_view_ids} if required_view_ids is not None else None
    views = evidence.get("views")
    if not isinstance(views, list):
        return ["visual evidence manifest needs views"]
    for index, view in enumerate(views):
        if not isinstance(view, dict):
            continue
        view_id = str(view.get("viewId") or "primary")
        if required is not None and view_id not in required:
            continue
        provenance = view.get("referenceProvenance")
        if provenance is None:
            continue  # schema-v1 compatibility; new compare output always records it
        if not isinstance(provenance, dict):
            failures.append(f"visual evidence view {index} has invalid reference provenance")
            continue
        if provenance.get("origin") != "observed":
            failures.append(
                f"visual evidence view {index} uses a synthetic hypothesis, not observed acceptance truth"
            )
        if provenance.get("allowedUse") != "acceptance":
            failures.append(
                f"visual evidence view {index} is limited to planning/veto and cannot approve a gate"
            )
    return list(dict.fromkeys(failures))


def is_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def complexity_minimums(complexity: str) -> dict[str, int]:
    presets = {
        "simple": {
            "components": 1,
            "materials": 1,
            "macroLayers": 1,
            "mesoLayers": 0,
            "microLayers": 0,
            "depthLevels": 1,
        },
        "moderate": {
            "components": 5,
            "materials": 2,
            "macroLayers": 1,
            "mesoLayers": 2,
            "microLayers": 1,
            "depthLevels": 2,
        },
        "complex": {
            "components": 10,
            "materials": 3,
            "macroLayers": 1,
            "mesoLayers": 3,
            "microLayers": 2,
            "depthLevels": 3,
        },
        "ultra": {
            "components": 18,
            "materials": 4,
            "macroLayers": 1,
            "mesoLayers": 5,
            "microLayers": 3,
            "depthLevels": 4,
        },
    }
    return copy.deepcopy(presets.get(complexity, presets["moderate"]))


def adaptive_hypothesis_views(complexity: str, quality_profile: str) -> list[str]:
    """Return the smallest cross-view set that can expose front-only geometry."""
    views = ["side"]
    if quality_profile == "reference-fidelity" or complexity in {"moderate", "complex", "ultra"}:
        views.insert(0, "three-quarter")
    if complexity in {"complex", "ultra"}:
        views.append("back")
    return views


def _visual_pass(
    pass_id: str,
    label: str,
    objective: str,
    acceptance: list[str],
    required_layers: dict[str, float],
    required_views: list[str] | None = None,
    diagnostic_views: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": pass_id,
        "label": label,
        "objective": objective,
        "componentRefs": ["root"],
        "acceptance": acceptance,
        "evidenceType": "visual",
        "requiredViews": required_views or ["primary"],
        "diagnosticViews": diagnostic_views or [],
        "requiredLayerScores": required_layers,
    }


def build_pass_plan(
    complexity: str = "moderate",
    intended_use: str = "browser-prop",
    quality_profile: str = "balanced",
) -> list[dict[str, Any]]:
    """Return the smallest pass plan that still covers relevant quality dimensions."""
    reference_fidelity = quality_profile == "reference-fidelity"
    diagnostic_views = adaptive_hypothesis_views(complexity, quality_profile)
    blockout_layers = {"silhouette": 0.85} if reference_fidelity else {"silhouette": 0.72}
    structure_layers = {"structure": 0.84} if reference_fidelity else {"structure": 0.72}
    form_layers = (
        {"silhouette": 0.86, "structure": 0.84, "formDetail": 0.82}
        if reference_fidelity
        else {"silhouette": 0.76, "structure": 0.74}
    )
    lookdev_layers = (
        {"material": 0.85, "lighting": 0.80}
        if reference_fidelity
        else {"material": 0.72, "lighting": 0.68}
    )
    passes = [
        _visual_pass(
            "blockout",
            "Khối chính",
            "Match overall silhouette, framing, mass, and proportions before detail.",
            [
                "Reference and render preserve the same overall silhouette.",
                "Primary masses and framing are correct before detail work.",
            ],
            blockout_layers,
            diagnostic_views=diagnostic_views,
        )
    ]
    if complexity in {"complex", "ultra"}:
        passes.append(
            _visual_pass(
                "structure",
                "Cấu trúc",
                "Resolve hierarchy, attachments, joints, and medium-scale forms.",
                [
                    "All major child parts attach cleanly to their parent.",
                    "No floating joints or accidental gaps are visible.",
                ],
                structure_layers,
                diagnostic_views=diagnostic_views,
            )
        )
    passes.append(
        _visual_pass(
            "form",
            "Hoàn thiện hình",
            "Refine shape and the visible forms needed by the selected complexity tier.",
            [
                "Macro and required meso forms match the reference.",
                "Proportion and attachment errors found in the previous review are resolved.",
            ],
            form_layers,
            diagnostic_views=diagnostic_views,
        )
    )
    lookdev_views = ["reference"]
    if quality_profile == "reference-fidelity":
        lookdev_views = ["neutral", "grazing", "reference"]
    passes.append(
        _visual_pass(
            "lookdev",
            "Vật liệu và ánh sáng",
            "Validate color, material response, surface detail, lighting, and contact shadows together.",
            [
                "Materials preserve the reference palette and roughness response.",
                "Lighting reveals form without baking the source lighting into albedo.",
                "Contact shadows and surface detail remain believable at the target view.",
            ],
            lookdev_layers,
            lookdev_views,
            diagnostic_views,
        )
    )
    if intended_use in INTERACTIVE_USES:
        passes.append(
            {
                "id": "interaction",
                "label": "Tương tác",
                "objective": "Validate only the runtime behavior required by the intended use.",
                "componentRefs": ["root"],
                "acceptance": [
                    "Required pivots, sockets, animation groups, and colliders work at runtime.",
                    "The model remains stable before, during, and after interaction.",
                ],
                "evidenceType": "runtime",
                "requiredRuntimeChecks": ["loads", "transforms", "interaction"],
            }
        )
    if intended_use in REALTIME_USES:
        optimization_layers = {
            "silhouette": form_layers["silhouette"],
            **lookdev_layers,
        }
        passes.append(
            {
                "id": "optimization",
                "label": "Hiệu năng",
                "objective": "Measure the final model against the real-time budget without changing its look.",
                "componentRefs": ["root"],
                "acceptance": [
                    "Measured FPS meets the target on the declared test device.",
                    "Draw calls and triangle count stay within the declared budget.",
                    "A nearby unchanged workflow still loads after optimization.",
                ],
                "evidenceType": "metrics",
                "requiredMetrics": ["fps", "drawCalls", "triangles"],
                "requiredArtifacts": ["performanceCapture"],
                "requiredPostOptimizationVisualReview": True,
                "requiredViews": lookdev_views,
                "diagnosticViews": diagnostic_views,
                "requiredLayerScores": optimization_layers,
                "visualBaselinePassId": "lookdev",
                "maximumVisualRegression": 0.02,
            }
        )
    return passes


def pass_order(spec: dict[str, Any]) -> list[str]:
    ids = [
        str(item["id"])
        for item in spec.get("buildPasses", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"].strip()
    ]
    if schema_version_at_least(spec, CURRENT_SCHEMA_VERSION):
        expected = [
            item["id"]
            for item in build_pass_plan(
                _spec_complexity(spec),
                str(spec.get("intendedUse") or "browser-prop"),
                str(spec.get("qualityProfile") or "balanced"),
            )
        ]
        return [*expected, *(item for item in ids if item not in expected)]
    return ids or DEFAULT_PASS_ORDER.copy()


def pass_config(spec: dict[str, Any], pass_id: str) -> dict[str, Any]:
    for item in spec.get("buildPasses", []):
        if isinstance(item, dict) and item.get("id") == pass_id:
            return item
    return {}


def _spec_complexity(spec: Mapping[str, Any]) -> str:
    assessment = spec.get("preSpecAssessment")
    complexity = assessment.get("complexity") if isinstance(assessment, dict) else None
    tier = complexity.get("tier") if isinstance(complexity, dict) else None
    return str(tier) if tier in {"simple", "moderate", "complex", "ultra"} else "moderate"


def effective_pass_config(spec: dict[str, Any], pass_id: str) -> dict[str, Any]:
    """Merge custom pass data with non-lowerable profile minimums."""
    configured = copy.deepcopy(pass_config(spec, pass_id))
    canonical = next(
        (
            item
            for item in build_pass_plan(
                _spec_complexity(spec),
                str(spec.get("intendedUse") or "browser-prop"),
                str(spec.get("qualityProfile") or "balanced"),
            )
            if item.get("id") == pass_id
        ),
        {},
    )
    if not canonical:
        return configured
    merged = copy.deepcopy(configured)
    for key, value in canonical.items():
        merged.setdefault(key, copy.deepcopy(value))
    for key in (
        "requiredViews",
        "diagnosticViews",
        "requiredMetrics",
        "requiredArtifacts",
        "requiredRuntimeChecks",
    ):
        minimum = canonical.get(key)
        selected = configured.get(key)
        if isinstance(minimum, list):
            values = [item for item in selected if isinstance(item, str)] if isinstance(selected, list) else []
            merged[key] = list(dict.fromkeys([*minimum, *values]))
    minimum_layers = canonical.get("requiredLayerScores")
    selected_layers = configured.get("requiredLayerScores")
    if isinstance(minimum_layers, dict):
        merged_layers = dict(selected_layers) if isinstance(selected_layers, dict) else {}
        for layer, floor in minimum_layers.items():
            selected = merged_layers.get(layer)
            if is_number(floor) and (not is_number(selected) or float(selected) < float(floor)):
                merged_layers[layer] = floor
        merged["requiredLayerScores"] = merged_layers
    if canonical.get("requiredPostOptimizationVisualReview") is True:
        merged["requiredPostOptimizationVisualReview"] = True
        configured_tolerance = configured.get("maximumVisualRegression")
        canonical_tolerance = canonical.get("maximumVisualRegression", 0.02)
        merged["maximumVisualRegression"] = (
            min(float(configured_tolerance), float(canonical_tolerance))
            if is_number(configured_tolerance) and float(configured_tolerance) >= 0
            else canonical_tolerance
        )
    return merged


def evidence_type(spec: dict[str, Any], pass_id: str) -> str:
    configured = pass_config(spec, pass_id).get("evidenceType")
    if configured in {"visual", "runtime", "metrics"}:
        return str(configured)
    if pass_id in RUNTIME_PASS_IDS:
        return "runtime"
    if pass_id in METRICS_PASS_IDS:
        return "metrics"
    return "visual"


def spec_content_hash(spec: dict[str, Any]) -> str:
    stable = {key: value for key, value in spec.items() if key not in DERIVED_SPEC_FIELDS}
    encoded = json.dumps(stable, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def generation_validation_hash(spec: dict[str, Any], pass_id: str) -> str:
    """Bind an in-process validation result to the exact spec and generation pass."""

    encoded = json.dumps(
        {
            "contract": "threejs-sculpt-generation-validation-v1",
            "passId": pass_id,
            "spec": spec,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def sculpt_representation_signature(spec: dict[str, Any]) -> str:
    """Hash modeling strategy classes, excluding ordinary numeric/detail tuning."""

    def structural_descriptor(value: Any) -> Any:
        if isinstance(value, Mapping):
            structural_keys = {
                "id", "type", "kind", "mode", "primitive", "strategy", "operation",
                "method", "algorithm", "componentRef", "componentRefs", "hostComponentRef",
                "parentId", "requiredTopology", "topology", "closed",
            }
            return {
                str(key): structural_descriptor(item)
                for key, item in value.items()
                if key in structural_keys
            }
        if isinstance(value, list):
            return [structural_descriptor(item) for item in value]
        if isinstance(value, (str, bool)):
            return value
        return None

    components = []
    for component in spec.get("componentTree", []):
        if not isinstance(component, Mapping):
            continue
        components.append(
            {
                key: component.get(key)
                for key in (
                    "id",
                    "componentType",
                    "parent",
                    "primitive",
                )
                if key in component
            } | {
                key: structural_descriptor(component.get(key))
                for key in ("geometryDescriptor", "modifiers", "attachment", "localFeatures")
                if key in component
            }
        )
    topology_groups = []
    topology_plan = spec.get("surfaceTopologyPlan", {})
    if isinstance(topology_plan, Mapping):
        topology_groups = [
            structural_descriptor(group)
            for group in topology_plan.get("groups", [])
            if isinstance(group, Mapping)
        ]
    encoded = json.dumps(
        {
            "contract": "threejs-sculpt-representation-v1",
            "surfaceTopologyPlan": topology_groups,
            "componentTree": components,
            "repetitionSystems": structural_descriptor(spec.get("repetitionSystems", [])),
            "specializedRegions": structural_descriptor(spec.get("specializedRegions", {})),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def review_spec_hash(spec: dict[str, Any], pass_id: str) -> str:
    """Hash only the spec content that can affect a given pass or an earlier one."""
    ids = pass_order(spec)
    index = ids.index(pass_id) if pass_id in ids else 0
    configs = [pass_config(spec, item) for item in ids[: index + 1]]
    kind = evidence_type(spec, pass_id)
    base_fields = (
        "targetName",
        "targetId",
        "schemaVersion",
        "intendedUse",
        "qualityProfile",
        "sourceImage",
        "suitability",
        "preSpecAssessment",
        "surfaceTopologyPlan",
        "qualityContract",
        "coordinateFrame",
        "silhouette",
        "viewEvidence",
        "viewHypothesisPolicy",
        "reviewGovernance",
    )
    payload: dict[str, Any] = {key: spec.get(key) for key in base_fields}
    targets = spec.get("qualityTargets") if isinstance(spec.get("qualityTargets"), dict) else {}
    payload["qualityTargets"] = {
        key: targets.get(key)
        for key in ("targetFidelity", "mustMatch")
        if key in targets
    }
    payload["buildPasses"] = configs
    payload["visualAcceptance"] = (
        spec.get("selfCorrectLoop", {}).get("visualAcceptance", {})
        if isinstance(spec.get("selfCorrectLoop"), dict)
        else {}
    )
    payload["featureReviewTargets"] = [
        target
        for target in spec.get("featureReviewTargets", [])
        if isinstance(target, dict)
        and isinstance(target.get("passIds"), list)
        and pass_id in target["passIds"]
    ]

    components = [item for item in spec.get("componentTree", []) if isinstance(item, dict)]
    composite_contract = schema_version_at_least(spec, CURRENT_SCHEMA_VERSION)
    blockout_fields = {
        "id",
        "name",
        "componentType",
        "level",
        "role",
        "importance",
        "confidence",
        "primitive",
        "geometryDescriptor",
        "parent",
        "dimensions",
        "transform",
        "evidenceRefs",
    }
    form_fields = blockout_fields | {
        "attachment", "deformations", "joints", "seams", "localFeatures", "details",
        "fidelityTier",
    }

    def hash_component(item: dict[str, Any], fields: set[str]) -> dict[str, Any]:
        projected = {key: value for key, value in item.items() if key in fields}
        if composite_contract:
            # In v3.1 omission and an explicit `part` have the same additive meaning.
            projected["componentType"] = component_type(item)
        return projected

    if pass_id == "blockout":
        payload["componentTree"] = [
            hash_component(item, blockout_fields)
            for item in components
            if item.get("level", "macro") == "macro"
        ]
        if composite_contract:
            # Repeated geometry may contribute to the v3.1 blockout silhouette.
            payload["repetitionSystems"] = spec.get("repetitionSystems", [])
    elif pass_id in {"structure", "form", "structural-pass", "form-refinement"}:
        payload["componentTree"] = [
            hash_component(item, form_fields)
            for item in components
        ]
        payload["repetitionSystems"] = spec.get("repetitionSystems", [])
    else:
        payload["componentTree"] = components
        payload["repetitionSystems"] = spec.get("repetitionSystems", [])

    post_optimization_visual = effective_pass_config(spec, pass_id).get(
        "requiredPostOptimizationVisualReview"
    ) is True
    if (
        kind == "visual"
        and pass_id in {"lookdev", "material-pass", "surface-pass", "lighting-pass"}
    ) or post_optimization_visual:
        payload["qualityTargets"]["reviewViewpoints"] = targets.get("reviewViewpoints", [])
        payload["materials"] = spec.get("materials", [])
        payload["lookDevTargets"] = spec.get("lookDevTargets", {})
        payload["lightingFromPhoto"] = spec.get("lightingFromPhoto", [])
    if kind == "runtime":
        payload["actionReadiness"] = spec.get("actionReadiness", {})
    if kind == "metrics":
        payload["performanceBudget"] = spec.get("performanceBudget", {})
        payload["lodPlan"] = spec.get("lodPlan", [])
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def visual_acceptance_threshold(spec: dict[str, Any]) -> float:
    candidates = [0.7]
    targets = spec.get("qualityTargets")
    if isinstance(targets, dict) and is_number(targets.get("targetFidelity")):
        candidates.append(float(targets["targetFidelity"]))
    loop = spec.get("selfCorrectLoop")
    if isinstance(loop, dict):
        config = loop.get("visualAcceptance")
        if isinstance(config, dict):
            for field in ("threshold", "minimumAiVisionScore"):
                if is_number(config.get(field)):
                    candidates.append(float(config[field]))
    return max(candidates)


def review_visual_views(entry: dict[str, Any]) -> list[dict[str, Any]]:
    evidence = entry.get("evidence")
    if isinstance(evidence, dict) and evidence.get("type") == "visual":
        views = evidence.get("views")
        if isinstance(views, list):
            return [item for item in views if isinstance(item, dict)]
    legacy = entry.get("visualEvidence")
    if isinstance(legacy, dict):
        return [
            {
                "viewId": legacy.get("cameraView") or "primary",
                "referenceImage": legacy.get("referenceImage")
                or legacy.get("referenceScreenshot")
                or "",
                "renderScreenshot": legacy.get("renderScreenshot") or "",
                "comparisonImage": legacy.get("comparisonImage") or "",
                "notes": legacy.get("notes") or "",
            }
        ]
    return []


def _score_for_layer(layer_scores: Any, layer: str) -> float | None:
    if not isinstance(layer_scores, dict):
        return None
    aliases = {
        "silhouette": ("silhouette", "macro", "shape"),
        "structure": ("structure", "meso", "form", "proportion"),
        "formDetail": ("formDetail", "form-detail", "detail", "localForm"),
        "material": ("material", "surface", "lookdev"),
        "lighting": ("lighting", "light", "shadow"),
    }
    for key in aliases.get(layer, (layer,)):
        value = layer_scores.get(key)
        if is_number(value):
            return float(value)
    return None


def _diagnostic_targets(spec: dict[str, Any]) -> dict[str, float]:
    reference_fidelity = spec.get("qualityProfile") == "reference-fidelity"
    floors = {
        "silhouetteIou": 0.88 if reference_fidelity else 0.75,
        "maximumCentroidDelta": 0.02 if reference_fidelity else 0.05,
        "maximumAspectRatioDelta": 0.03 if reference_fidelity else 0.08,
        "minimumDetailEnergyRatio": 0.75 if reference_fidelity else 0.65,
        "minimumEdgeDensityRatio": 0.35 if reference_fidelity else 0.20,
        "minimumHistogramIntersection": 0.35 if reference_fidelity else 0.25,
        "maximumMeanColorDelta": 0.40 if reference_fidelity else 0.55,
        "minimumHighlightCoverageRatio": 0.10 if reference_fidelity else 0.05,
        "minimumHighlightEnergyRatio": 0.10 if reference_fidelity else 0.05,
    }
    targets = spec.get("qualityTargets")
    configured = targets.get("diagnosticTargets") if isinstance(targets, dict) else None
    if not isinstance(configured, dict):
        return floors
    result = dict(floors)
    for field in (
        "silhouetteIou",
        "minimumDetailEnergyRatio",
        "minimumEdgeDensityRatio",
        "minimumHistogramIntersection",
        "minimumHighlightCoverageRatio",
        "minimumHighlightEnergyRatio",
    ):
        value = configured.get(field)
        if is_number(value):
            result[field] = max(result[field], float(value))
    for field in (
        "maximumCentroidDelta",
        "maximumAspectRatioDelta",
        "maximumMeanColorDelta",
    ):
        value = configured.get(field)
        if is_number(value):
            result[field] = min(result[field], float(value))
    return result


def _diagnostic_guardrail_failures(
    spec: dict[str, Any],
    views: list[dict[str, Any]],
    required_view_ids: list[str],
    pass_id: str,
) -> list[str]:
    failures: list[str] = []
    targets = _diagnostic_targets(spec)
    by_id = {str(view.get("viewId") or "primary"): view for view in views}
    if len(required_view_ids) == 1 and len(views) == 1 and required_view_ids[0] not in by_id:
        by_id[required_view_ids[0]] = views[0]
    for view_id in required_view_ids:
        view = by_id.get(view_id)
        if not isinstance(view, dict):
            continue
        diagnostics = view.get("fitDiagnostics")
        if not isinstance(diagnostics, dict):
            failures.append(f"visual view {view_id!r} needs reproducible fitDiagnostics")
            continue
        iou = diagnostics.get("silhouetteIou")
        centroid = diagnostics.get("centroidDelta")
        aspect = diagnostics.get("aspectRatioDelta")
        appearance = diagnostics.get("appearance")
        detail_ratio = appearance.get("detailEnergyRatio") if isinstance(appearance, dict) else None
        provenance = view.get("referenceProvenance")
        synthetic_hypothesis = (
            isinstance(provenance, dict)
            and provenance.get("origin") == "synthetic-hypothesis"
        )
        minimum_iou = min(targets["silhouetteIou"], 0.38) if synthetic_hypothesis else targets["silhouetteIou"]
        maximum_centroid = max(targets["maximumCentroidDelta"], 0.18) if synthetic_hypothesis else targets["maximumCentroidDelta"]
        maximum_aspect = max(targets["maximumAspectRatioDelta"], 0.30) if synthetic_hypothesis else targets["maximumAspectRatioDelta"]
        if not is_number(iou) or float(iou) < minimum_iou:
            failures.append(
                f"visual view {view_id!r} silhouetteIou must be >= {minimum_iou}"
            )
        if not is_number(centroid) or float(centroid) > maximum_centroid:
            failures.append(
                f"visual view {view_id!r} centroidDelta must be <= "
                f"{maximum_centroid}"
            )
        if not is_number(aspect) or float(aspect) > maximum_aspect:
            failures.append(
                f"visual view {view_id!r} aspectRatioDelta must be <= "
                f"{maximum_aspect}"
            )
        detail_relevant = not synthetic_hypothesis and pass_id in {
            "lookdev",
            "material-pass",
            "surface-pass",
            "lighting-pass",
            "optimization",
            "optimization-pass",
        }
        if detail_relevant and (
            not is_number(detail_ratio)
            or float(detail_ratio) < targets["minimumDetailEnergyRatio"]
        ):
            failures.append(
                f"visual view {view_id!r} detailEnergyRatio must be >= "
                f"{targets['minimumDetailEnergyRatio']}"
            )
        if detail_relevant:
            appearance_checks = (
                ("edgeDensityRatio", "minimumEdgeDensityRatio", lambda value, limit: value >= limit, ">="),
                (
                    "foregroundHistogramIntersection",
                    "minimumHistogramIntersection",
                    lambda value, limit: value >= limit,
                    ">=",
                ),
                ("foregroundMeanColorDelta", "maximumMeanColorDelta", lambda value, limit: value <= limit, "<="),
                (
                    "highlightCoverageRatio",
                    "minimumHighlightCoverageRatio",
                    lambda value, limit: value >= limit,
                    ">=",
                ),
                (
                    "highlightEnergyRatio",
                    "minimumHighlightEnergyRatio",
                    lambda value, limit: value >= limit,
                    ">=",
                ),
            )
            for field, target_field, predicate, relation in appearance_checks:
                value = appearance.get(field) if isinstance(appearance, dict) else None
                limit = targets[target_field]
                if not is_number(value) or not predicate(float(value), limit):
                    failures.append(
                        f"visual view {view_id!r} {field} must be {relation} {limit}"
                    )
    return failures


def visual_preflight_failures(
    spec: dict[str, Any],
    evidence: Any,
    pass_id: str,
    spec_path: Path | None = None,
) -> list[str]:
    """Run deterministic evidence vetoes before spending a reviewer-agent call."""
    failures: list[str] = []
    if not isinstance(evidence, dict):
        return ["visual evidence must be a comparison manifest object"]
    views_value = evidence.get("views")
    views = [item for item in views_value if isinstance(item, dict)] if isinstance(views_value, list) else []
    config = effective_pass_config(spec, pass_id)
    required_views = config.get("requiredViews", ["primary"])
    if not isinstance(required_views, list) or not required_views:
        required_views = ["primary"]
    required_view_ids = [str(item) for item in required_views]
    diagnostic_views = config.get("diagnosticViews", [])
    diagnostic_view_ids = (
        [str(item) for item in diagnostic_views if isinstance(item, str) and item]
        if isinstance(diagnostic_views, list)
        else []
    )
    reviewed_view_ids = list(dict.fromkeys([*required_view_ids, *diagnostic_view_ids]))
    by_id = {str(view.get("viewId") or "primary"): view for view in views}
    if len(required_view_ids) == 1 and len(views) == 1 and required_view_ids[0] not in by_id:
        by_id[required_view_ids[0]] = views[0]
    for view_id in reviewed_view_ids:
        view = by_id.get(view_id)
        if not view:
            kind = "required" if view_id in required_view_ids else "diagnostic"
            failures.append(f"missing {kind} visual view {view_id!r}")
            continue
        for field in ("referenceImage", "renderScreenshot", "comparisonImage"):
            if not isinstance(view.get(field), str) or not str(view[field]).strip():
                failures.append(f"visual view {view_id!r} missing {field}")

    failures.extend(visual_evidence_integrity_failures(evidence))
    failures.extend(visual_evidence_authority_failures(evidence, required_view_ids))
    source_image = spec.get("sourceImage")
    if isinstance(source_image, str) and source_image.strip() and "://" not in source_image:
        source_path = Path(source_image).expanduser()
        if spec_path is not None and not source_path.is_absolute():
            source_path = spec_path.expanduser().resolve().parent / source_path
        if source_path.is_file():
            source_hash = file_sha256(source_path)
            for view_id in required_view_ids:
                view = by_id.get(view_id)
                if isinstance(view, dict) and view.get("referenceSha256") != source_hash:
                    failures.append(
                        f"visual view {view_id!r} is not bound to spec.sourceImage"
                    )
    if diagnostic_view_ids:
        from sculpt_view_hypotheses import hypothesis_evidence_failures

        failures.extend(
            hypothesis_evidence_failures(
                spec_path,
                spec,
                evidence,
                diagnostic_view_ids,
            )
        )
    failures.extend(_diagnostic_guardrail_failures(spec, views, reviewed_view_ids, pass_id))
    return list(dict.fromkeys(failures))


def _visual_review_failures(
    spec: dict[str, Any],
    entry: dict[str, Any],
    pass_id: str,
    config: dict[str, Any],
    spec_path: Path | None = None,
) -> list[str]:
    evidence = entry.get("evidence")
    failures = visual_preflight_failures(spec, evidence, pass_id, spec_path)
    required_threshold = visual_acceptance_threshold(spec)
    recorded_threshold = entry.get("visualAcceptanceThreshold")
    if not is_number(recorded_threshold) or float(recorded_threshold) < required_threshold:
        failures.append(
            f"visualAcceptanceThreshold cannot be below the spec threshold {required_threshold}"
        )
    score = entry.get("aiVisionScore")
    if not is_number(score) or float(score) < required_threshold:
        failures.append(f"aiVisionScore must meet visual threshold {required_threshold}")
    required_layers = config.get("requiredLayerScores", {})
    if isinstance(required_layers, dict):
        for layer, minimum in required_layers.items():
            layer_score = _score_for_layer(entry.get("layerScores"), str(layer))
            if not is_number(minimum) or layer_score is None or layer_score < float(minimum):
                failures.append(f"layer score {layer!r} must be >= {minimum}")

    reviewer = entry.get("reviewerEvidence")
    comparison_hash = evidence.get("comparisonSha256") if isinstance(evidence, dict) else None
    if not isinstance(reviewer, dict):
        failures.append("reviewerEvidence is required for an accepted visual review")
    else:
        if reviewer.get("type") != "ai-vision":
            failures.append("reviewerEvidence.type must be 'ai-vision'")
        if not isinstance(reviewer.get("model"), str) or not reviewer["model"].strip():
            failures.append("reviewerEvidence.model is required")
        if reviewer.get("reviewedArtifactSha256") != comparison_hash:
            failures.append("reviewerEvidence is not bound to the compared image hash")
        if not isinstance(reviewer.get("reviewedAt"), str) or not reviewer["reviewedAt"].strip():
            failures.append("reviewerEvidence.reviewedAt is required")
        governance = spec.get("reviewGovernance")
        independent_required = (
            isinstance(governance, dict)
            and governance.get("independentContextRequired") is True
        )
        if independent_required:
            builder_context = reviewer.get("builderContextId")
            reviewer_context = reviewer.get("reviewerContextId")
            if not isinstance(builder_context, str) or not builder_context.strip():
                failures.append("reviewerEvidence.builderContextId is required")
            if not isinstance(reviewer_context, str) or not reviewer_context.strip():
                failures.append("reviewerEvidence.reviewerContextId is required")
            if (
                isinstance(builder_context, str)
                and isinstance(reviewer_context, str)
                and builder_context.strip() == reviewer_context.strip()
            ):
                failures.append("builder and reviewer contextId must differ")
            if reviewer.get("role") != "independent-reviewer":
                failures.append("reviewerEvidence.role must be 'independent-reviewer'")
            verdict_path_value = reviewer.get("reviewVerdict")
            verdict_hash = reviewer.get("reviewVerdictSha256")
            if not isinstance(verdict_path_value, str) or not verdict_path_value.strip():
                failures.append("reviewerEvidence.reviewVerdict is required")
            elif spec_path is not None or Path(verdict_path_value).expanduser().is_absolute():
                verdict_path = Path(verdict_path_value).expanduser()
                if not verdict_path.is_absolute():
                    assert spec_path is not None
                    verdict_path = spec_path.expanduser().resolve().parent / verdict_path
                if not verdict_path.is_file():
                    failures.append("reviewerEvidence.reviewVerdict does not exist")
                elif verdict_hash != file_sha256(verdict_path):
                    failures.append("reviewerEvidence review verdict changed after acceptance")
            if not isinstance(verdict_hash, str) or len(verdict_hash) != 64:
                failures.append("reviewerEvidence.reviewVerdictSha256 is required")
    notes = entry.get("aiVisionNotes")
    if not isinstance(notes, str) or len(notes.strip()) < 12:
        failures.append("aiVisionNotes must explain the accepted visual result")
    failures.extend(feature_gate_failures(spec, entry, pass_id))
    return failures


def _latest_history_entry(spec: dict[str, Any], pass_id: str) -> dict[str, Any] | None:
    history = spec.get("reviewHistory")
    if not isinstance(history, list):
        return None
    for item in reversed(history):
        if isinstance(item, dict) and item.get("passId") == pass_id and item.get("action") == "continue":
            return item
    return None


def _post_optimization_regression_failures(
    spec: dict[str, Any], entry: dict[str, Any], config: dict[str, Any]
) -> list[str]:
    baseline_pass = str(config.get("visualBaselinePassId") or "lookdev")
    baseline = _latest_history_entry(spec, baseline_pass)
    if baseline is None:
        return [f"post-optimization visual review needs an accepted {baseline_pass!r} baseline"]
    tolerance_value = config.get("maximumVisualRegression", 0.02)
    tolerance = float(tolerance_value) if is_number(tolerance_value) else 0.02
    failures: list[str] = []
    baseline_score = baseline.get("aiVisionScore")
    current_score = entry.get("aiVisionScore")
    if is_number(baseline_score) and (
        not is_number(current_score) or float(current_score) < float(baseline_score) - tolerance
    ):
        failures.append(
            f"post-optimization aiVisionScore regressed by more than {tolerance}"
        )
    for layer in ("material", "lighting", "silhouette", "structure", "formDetail"):
        before = _score_for_layer(baseline.get("layerScores"), layer)
        after = _score_for_layer(entry.get("layerScores"), layer)
        if before is not None and (after is None or after < before - tolerance):
            failures.append(
                f"post-optimization layer {layer!r} regressed by more than {tolerance}"
            )
    baseline_views = review_visual_views(baseline)
    current_views = review_visual_views(entry)
    baseline_by_id = {str(view.get("viewId") or "primary"): view for view in baseline_views}
    current_by_id = {str(view.get("viewId") or "primary"): view for view in current_views}
    for view_id, before_view in baseline_by_id.items():
        after_view = current_by_id.get(view_id)
        if not isinstance(after_view, dict):
            continue
        before_diagnostics = before_view.get("fitDiagnostics")
        after_diagnostics = after_view.get("fitDiagnostics")
        if not isinstance(before_diagnostics, dict) or not isinstance(after_diagnostics, dict):
            continue
        for field in ("silhouetteIou",):
            before = before_diagnostics.get(field)
            after = after_diagnostics.get(field)
            if is_number(before) and (not is_number(after) or float(after) < float(before) - tolerance):
                failures.append(
                    f"post-optimization view {view_id!r} {field} regressed by more than {tolerance}"
                )
        before_appearance = before_diagnostics.get("appearance")
        after_appearance = after_diagnostics.get("appearance")
        before_detail = before_appearance.get("detailEnergyRatio") if isinstance(before_appearance, dict) else None
        after_detail = after_appearance.get("detailEnergyRatio") if isinstance(after_appearance, dict) else None
        if is_number(before_detail) and (
            not is_number(after_detail) or float(after_detail) < float(before_detail) - tolerance
        ):
            failures.append(
                f"post-optimization view {view_id!r} detailEnergyRatio regressed by more than {tolerance}"
            )
    return failures


def review_failures(
    spec: dict[str, Any],
    entry: dict[str, Any],
    pass_id: str,
    spec_path: Path | None = None,
) -> list[str]:
    failures: list[str] = []
    if entry.get("passId") != pass_id:
        return [f"review passId must be {pass_id!r}"]
    if entry.get("action") != "continue":
        return ["latest review action is not continue"]
    current_hash = review_spec_hash(spec, pass_id)
    review_hash = entry.get("specHash")
    if schema_version_at_least(spec, "3.0") and review_hash != current_hash:
        failures.append("review was not produced for the current spec content")

    kind = evidence_type(spec, pass_id)
    config = effective_pass_config(spec, pass_id)
    if kind == "visual":
        failures.extend(_visual_review_failures(spec, entry, pass_id, config, spec_path))
    elif kind == "runtime":
        checks = entry.get("runtimeChecks")
        if not isinstance(checks, dict):
            failures.append("runtimeChecks object is required")
        else:
            for name in config.get("requiredRuntimeChecks", []):
                if checks.get(name) is not True:
                    failures.append(f"runtime check {name!r} must pass")
    else:
        metrics = entry.get("metrics")
        if not isinstance(metrics, dict):
            failures.append("measured metrics are required")
        else:
            configured_targets = config.get("metricTargets")
            if isinstance(configured_targets, dict):
                metric_targets = configured_targets
            else:
                budget = spec.get("performanceBudget")
                budget = budget if isinstance(budget, dict) else {}
                metric_targets = {
                    "fps": {"min": budget.get("fpsTarget")},
                    "drawCalls": {"max": budget.get("maxDrawCalls")},
                    "triangles": {"max": budget.get("targetTriangles")},
                }
            required_metrics = config.get("requiredMetrics")
            if not isinstance(required_metrics, list) or not required_metrics:
                required_metrics = list(metric_targets)
            for name in required_metrics:
                target = metric_targets.get(name)
                value = metrics.get(name)
                if (
                    not is_number(value)
                    or not isinstance(target, dict)
                    or not any(is_number(target.get(bound)) for bound in ("min", "max"))
                ):
                    failures.append(f"metric {name!r} is missing or invalid")
                    continue
                if is_number(target.get("min")) and float(value) < float(target["min"]):
                    failures.append(f"metric {name!r} must be >= {target['min']}")
                if is_number(target.get("max")) and float(value) > float(target["max"]):
                    failures.append(f"metric {name!r} must be <= {target['max']}")
        artifacts = entry.get("artifacts")
        if not isinstance(artifacts, dict):
            artifacts = {}
        for name in config.get("requiredArtifacts", []):
            if not artifacts.get(name):
                failures.append(f"artifact {name!r} is required")
        if config.get("requiredPostOptimizationVisualReview") is True:
            failures.extend(_visual_review_failures(spec, entry, pass_id, config, spec_path))
            failures.extend(_post_optimization_regression_failures(spec, entry, config))
    return list(dict.fromkeys(failures))


def _latest_review(
    spec: dict[str, Any],
    pass_id: str,
    after_index: int,
    *,
    require_current_hash: bool = True,
) -> tuple[int, dict[str, Any] | None]:
    history = spec.get("reviewHistory", [])
    if not isinstance(history, list):
        return -1, None
    require_hash = require_current_hash and schema_version_at_least(spec, "3.0")
    for index in range(len(history) - 1, after_index, -1):
        entry = history[index]
        if not isinstance(entry, dict) or entry.get("passId") != pass_id:
            continue
        if require_hash and entry.get("specHash") != review_spec_hash(spec, pass_id):
            continue
        return index, entry
    return -1, None


def pipeline_status(
    spec: dict[str, Any],
    spec_path: Path | None = None,
) -> dict[str, Any]:
    ids = pass_order(spec)
    completed: list[str] = []
    completion_index = -1
    current = "complete"
    state = "complete"
    latest_action = ""
    gate_failures: list[str] = []
    pending_correction_batch: dict[str, Any] = {}

    for pass_id in ids:
        index, entry = _latest_review(spec, pass_id, completion_index)
        if entry is None:
            current = pass_id
            _, previous_entry = _latest_review(
                spec,
                pass_id,
                completion_index,
                require_current_hash=False,
            )
            previous_action = (
                str(previous_entry.get("action") or "")
                if isinstance(previous_entry, dict)
                else ""
            )
            if isinstance(previous_entry, dict) and previous_action in REFINEMENT_ACTIONS:
                latest_action = previous_action
                pending_correction_batch = (
                    previous_entry.get("correctionBatch")
                    if isinstance(previous_entry.get("correctionBatch"), dict)
                    else correction_batch_from_verdict(
                        {
                            "reviewId": previous_entry.get("reviewId"),
                            "action": previous_action,
                            "issues": previous_entry.get("reviewIssues", []),
                            "corrections": previous_entry.get("reviewCorrections", []),
                        }
                    )
                )
                state = "needs-refinement"
                gate_failures = ["apply the complete pending correction batch before rendering again"]
            else:
                state = "ready"
            break
        latest_action = str(entry.get("action") or "")
        failures = review_failures(spec, entry, pass_id, spec_path)
        if not failures:
            completed.append(pass_id)
            completion_index = index
            continue
        current = pass_id
        gate_failures = failures
        state = {
            "stop": "stopped",
            "request-input": "awaiting-input",
            STRATEGY_RESET_ACTION: "needs-strategy-change",
            "refine-spec": "needs-refinement",
            "refine-code": "needs-refinement",
            "refine-batch": "needs-refinement",
        }.get(latest_action, "needs-review")
        if latest_action in REFINEMENT_ACTIONS:
            pending_correction_batch = (
                entry.get("correctionBatch")
                if isinstance(entry.get("correctionBatch"), dict)
                else correction_batch_from_verdict(
                    {
                        "reviewId": entry.get("reviewId"),
                        "action": latest_action,
                        "issues": entry.get("reviewIssues", []),
                        "corrections": entry.get("reviewCorrections", []),
                    }
                )
            )
        break

    required = [] if current == "complete" else next_required_evidence(spec, current)
    history = spec.get("reviewHistory", [])
    current_records = (
        [
            entry
            for entry in history
            if isinstance(entry, dict) and entry.get("passId") == current
        ]
        if isinstance(history, list) and current != "complete"
        else []
    )
    return {
        "passGateMode": "adaptive-sequential",
        "passOrder": ids,
        "currentPass": current,
        "completedPasses": completed,
        "lastCompletedPass": completed[-1] if completed else "",
        "state": state,
        "latestAction": latest_action,
        "pendingCorrectionBatch": pending_correction_batch,
        "refinementBudget": refinement_budget(current_records),
        "blockedReason": "; ".join(gate_failures),
        "gateFailures": gate_failures,
        "nextRequiredEvidence": required,
        "specHash": spec_content_hash(spec),
    }


def next_required_evidence(spec: dict[str, Any], pass_id: str) -> list[str]:
    config = effective_pass_config(spec, pass_id)
    evidence = [str(item) for item in config.get("acceptance", []) if str(item).strip()]
    kind = evidence_type(spec, pass_id)
    if kind == "visual":
        evidence.extend(
            [
                "hash-bound reference + render comparison manifest for every required view",
                "one artifact-bound AI reviewer record with critique and required scores",
                "latest review action=continue for the current spec",
            ]
        )
    elif kind == "runtime":
        evidence.append("all required runtime checks recorded as true")
    else:
        evidence.append("measured performance metrics and required capture artifacts")
        if config.get("requiredPostOptimizationVisualReview") is True:
            evidence.extend(
                [
                    "a fresh post-optimization comparison manifest for every required view",
                    "AI visual scores bound to that final comparison artifact",
                    "no visual score or diagnostic regression beyond the configured tolerance",
                ]
            )
    return list(dict.fromkeys(evidence))


def sync_pipeline(spec: dict[str, Any]) -> dict[str, Any]:
    payload = pipeline_status(spec)
    spec["sculptPipeline"] = payload
    return payload


def check_pass(spec: dict[str, Any], requested_pass: str) -> tuple[bool, str, dict[str, Any]]:
    status = pipeline_status(spec)
    ids = status["passOrder"]
    if requested_pass not in ids:
        return False, f"unknown build pass {requested_pass!r}; expected one of: {', '.join(ids)}", status
    if status["state"] in {"stopped", "awaiting-input"}:
        return False, f"workflow is {status['state']}: {status['blockedReason']}", status
    current = status["currentPass"]
    completed = status["completedPasses"]
    if requested_pass in completed:
        return True, f"pass {requested_pass!r} is complete and may be regenerated", status
    if requested_pass == current:
        return True, f"pass {requested_pass!r} is the current pass", status
    return False, f"pass {requested_pass!r} is locked; current pass is {current!r}", status
