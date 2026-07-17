"""Risk-first scheduling and hash-bound acceptance cache for sculpt modules."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sculpt_contract import (
    REFINEMENT_ACTIONS,
    STRATEGY_RESET_ACTION,
    component_type,
    file_sha256,
    pipeline_status,
    refinement_budget,
    sculpt_representation_signature,
    visual_evidence_authority_failures,
    visual_evidence_integrity_failures,
    write_spec_atomic,
)
from sculpt_manifest import (
    entry_by_id,
    load_modules,
    read_object,
    resolve_manifest,
)
from sculpt_module_contract import MANIFEST_SCHEMA_VERSION, SEGMENTED_FIELDS, manifest_errors


GLOBAL_DERIVED_FIELDS = {
    "reviewHistory",
    "visualEvidence",
    "sculptPipeline",
    "pbrExtractionHistory",
}

SCAFFOLD_MARKERS = (
    "blockout primitive; replace from reference",
    "replace with observed",
    "replace generic values",
    "fill before lookdev",
)
VISUAL_SCORE_FLOORS = {"low": 0.72, "medium": 0.76, "high": 0.82, "critical": 0.86}
BASE_VISUAL_LAYER_GROUPS = {
    "silhouette": ("silhouetteProportion", "silhouette"),
    "structure": ("componentStructure", "structure"),
    "form": ("formDetail",),
}


def module_representation_signature(
    manifest: dict[str, Any],
    module_id: str,
    module: dict[str, Any],
) -> str:
    """Hash only geometry/topology strategy classes owned by this module."""

    payload = module.get("payload") if isinstance(module.get("payload"), dict) else {}
    global_spec = manifest.get("globalSpec") if isinstance(manifest.get("globalSpec"), dict) else {}
    topology_plan = (
        global_spec.get("surfaceTopologyPlan")
        if isinstance(global_spec.get("surfaceTopologyPlan"), dict)
        else {}
    )
    topology_groups = [
        group
        for group in topology_plan.get("groups", [])
        if isinstance(group, dict) and group.get("ownerModuleId") == module_id
    ]
    return sculpt_representation_signature(
        {
            "surfaceTopologyPlan": {"groups": topology_groups},
            "componentTree": payload.get("componentTree", []),
            "repetitionSystems": payload.get("repetitionSystems", []),
            "specializedRegions": payload.get("specializedRegions", {}),
        }
    )
IDENTITY_ROLE_TOKENS = ("identity", "face", "hand", "character", "grip")
MATERIAL_ROLE_TOKENS = (
    "material",
    "surface",
    "lookdev",
    "fabric",
    "fiber",
    "fur",
    "hair",
    "cloth",
    "costume",
    "knit",
)
IMPLEMENTATION_SOURCE_SUFFIXES = {
    ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".html", ".css", ".glsl", ".wgsl", ".vert", ".frag"
}
IMPLEMENTATION_ASSET_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".webp", ".avif", ".svg", ".ktx2", ".hdr", ".exr", ".glb", ".gltf", ".bin", ".json", ".wasm", ".ttf", ".woff", ".woff2"
}
UNRELATED_IMPLEMENTATION_NAMES = {
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "readme.md",
    "plugin.json",
}


def _source_without_comments(source: str) -> str:
    """Remove JS/CSS-style comments without stripping quoted source literals."""
    output: list[str] = []
    index = 0
    quote: str | None = None
    escaped = False
    while index < len(source):
        if quote is not None:
            character = source[index]
            output.append(character)
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = None
            index += 1
            continue
        if source.startswith("//", index):
            newline = source.find("\n", index + 2)
            index = len(source) if newline < 0 else newline
            continue
        if source.startswith("/*", index):
            end = source.find("*/", index + 2)
            index = len(source) if end < 0 else end + 2
            continue
        if source.startswith("<!--", index):
            end = source.find("-->", index + 4)
            index = len(source) if end < 0 else end + 3
            continue
        character = source[index]
        if character in {"'", '"', "`"}:
            quote = character
        output.append(character)
        index += 1
    return "".join(output)


def implementation_semantic_hashes(files: list[Path]) -> dict[str, str]:
    """Hash executable meaning closely enough to ignore comment/whitespace-only edits."""
    hashes: dict[str, str] = {}
    for path in files:
        if path.suffix.lower() in IMPLEMENTATION_SOURCE_SUFFIXES:
            source = path.read_text(encoding="utf-8")
            uncommented = _source_without_comments(source)
            compact: list[str] = []
            quote: str | None = None
            escaped = False
            for character in uncommented:
                if quote is not None:
                    compact.append(character)
                    if escaped:
                        escaped = False
                    elif character == "\\":
                        escaped = True
                    elif character == quote:
                        quote = None
                    continue
                if character in {"'", '"', "`"}:
                    quote = character
                    compact.append(character)
                elif not character.isspace():
                    compact.append(character)
            normalized = "".join(compact)
            digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        else:
            digest = file_sha256(path)
        hashes[str(path)] = digest
    return dict(sorted(hashes.items()))


def _has_module_ownership_marker(module_id: str, sources: list[Path]) -> bool:
    escaped = re.escape(module_id)
    marker = re.compile(
        rf"(?:SCULPT_MODULE_ID|sculptModuleId)\s*(?::[^=;]+)?=\s*['\"]{escaped}['\"]",
        re.IGNORECASE,
    )
    for source_path in sources:
        try:
            semantic_source = _source_without_comments(source_path.read_text(encoding="utf-8"))
        except UnicodeDecodeError:
            continue
        if marker.search(semantic_source):
            return True
    return False


def cache_path(manifest_path: Path) -> Path:
    return manifest_path.parent / ".sculpt-cache" / manifest_path.stem / "module-state.json"


def _load_cache(manifest_path: Path) -> dict[str, Any]:
    path = cache_path(manifest_path)
    if not path.is_file():
        return {"version": 2, "modules": {}, "reviewAttempts": {}}
    payload = read_object(path, "module cache")
    if not isinstance(payload.get("modules"), dict):
        return {"version": 2, "modules": {}, "reviewAttempts": {}}
    if not isinstance(payload.get("reviewAttempts"), dict):
        payload["reviewAttempts"] = {}
    return payload


def interface_hash(module: dict[str, Any]) -> str:
    payload = {
        "contract": module.get("contract", {}),
        "role": module.get("role"),
        "moduleId": module.get("moduleId"),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def module_hash(
    manifest_path: Path,
    manifest: dict[str, Any],
    module_id: str,
    modules: dict[str, tuple[Path, dict[str, Any]]] | None = None,
) -> str:
    loaded = modules or load_modules(manifest_path, manifest)
    if module_id not in loaded:
        raise ValueError(f"module {module_id!r} is not available")
    entries = entry_by_id(manifest)
    global_spec = manifest.get("globalSpec", {})
    source = global_spec.get("sourceImage") if isinstance(global_spec, dict) else None
    source_hash = ""
    if isinstance(source, str) and source and "://" not in source:
        source_path = Path(source).expanduser()
        if not source_path.is_absolute():
            source_path = (manifest_path.parent / source_path).resolve()
        if source_path.is_file():
            source_hash = file_sha256(source_path)
    dependency_interfaces = {
        dependency: interface_hash(loaded[dependency][1])
        for dependency in entries[module_id].get("dependsOn", [])
        if dependency in loaded
    }
    global_contract = (
        {
            key: value
            for key, value in global_spec.items()
            if key not in GLOBAL_DERIVED_FIELDS and key not in SEGMENTED_FIELDS
        }
        if isinstance(global_spec, dict)
        else {}
    )
    payload = {
        "module": loaded[module_id][1],
        "moduleEntryContract": {
            "covers": entries[module_id].get("covers", []),
            "required": entries[module_id].get("required", True),
        },
        "assemblyContract": manifest.get("assemblyContract", {}),
        "globalContract": global_contract,
        "sourceHash": source_hash,
        "dependencyInterfaces": dependency_interfaces,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def _cached_acceptance_valid(record: Any, current_hash: str) -> bool:
    if not isinstance(record, dict) or record.get("moduleHash") != current_hash:
        return False
    gate_type = record.get("gateType")
    if gate_type == "visual":
        verdict_path = record.get("reviewVerdict")
        implementation_files = record.get("implementationFiles")
        if not isinstance(verdict_path, str) or not verdict_path:
            return False
        verdict = Path(verdict_path).expanduser()
        if not verdict.is_file() or record.get("reviewVerdictSha256") != file_sha256(verdict):
            return False
        if not isinstance(implementation_files, dict) or not implementation_files:
            return False
        for file_name, expected_hash in implementation_files.items():
            implementation = Path(str(file_name)).expanduser()
            if (
                not implementation.is_file()
                or not isinstance(expected_hash, str)
                or file_sha256(implementation) != expected_hash
            ):
                return False
    evidence_path = record.get("evidenceManifest")
    if not evidence_path:
        return record.get("gateType") == "structural"
    path = Path(str(evidence_path)).expanduser()
    if not path.is_file() or record.get("evidenceSha256") != file_sha256(path):
        return False
    try:
        evidence = read_object(path, "visual evidence manifest")
    except (OSError, ValueError):
        return False
    return not (
        visual_evidence_integrity_failures(evidence)
        or visual_evidence_authority_failures(evidence, record.get("requiredViews"))
    )


def _scaffold_placeholder_failures(module: dict[str, Any]) -> list[str]:
    """Find explicit template text that proves a module has not been authored yet."""
    failures: list[str] = []

    def visit(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                visit(child, f"{path}.{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, f"{path}[{index}]")
        elif isinstance(value, str):
            lowered = value.lower()
            if any(marker in lowered for marker in SCAFFOLD_MARKERS):
                failures.append(f"unfinished scaffold placeholder at {path}")

    visit(module.get("payload", {}), "payload")
    return list(dict.fromkeys(failures))


def _blockout_fidelity_failures(module: dict[str, Any]) -> list[str]:
    """A visual module is not finished while executable parts still declare blockout fidelity."""
    payload = module.get("payload")
    components = payload.get("componentTree", []) if isinstance(payload, dict) else []
    failures: list[str] = []
    for index, component in enumerate(components if isinstance(components, list) else []):
        if not isinstance(component, dict):
            continue
        is_executable = component.get("componentType", "part") != "assembly" or isinstance(
            component.get("geometryDescriptor"), dict
        )
        if is_executable:
            tier = str(component.get("fidelityTier") or "").lower()
            if tier == "blockout":
                failures.append(
                    f"executable component {component.get('id', index)!r} still has fidelityTier 'blockout'"
                )
            elif not tier:
                failures.append(
                    f"executable component {component.get('id', index)!r} has no finished fidelityTier"
                )
    return failures


def visual_gate_floor(manifest: dict[str, Any], entry: dict[str, Any]) -> float:
    tier = str(entry.get("riskTier") or "low")
    floor = VISUAL_SCORE_FLOORS.get(tier, VISUAL_SCORE_FLOORS["low"])
    global_spec = manifest.get("globalSpec")
    profile = global_spec.get("qualityProfile") if isinstance(global_spec, dict) else None
    return max(floor, 0.85 if profile == "reference-fidelity" else 0.0)


def diagnostic_floor_contract(manifest: dict[str, Any]) -> dict[str, float]:
    global_spec = manifest.get("globalSpec")
    profile = global_spec.get("qualityProfile") if isinstance(global_spec, dict) else None
    if profile == "reference-fidelity":
        return {
            "minimumSilhouetteIou": 0.63,
            "maximumCentroidDelta": 0.08,
            "maximumAspectRatioDelta": 0.15,
            "minimumDetailEnergyRatio": 0.35,
            "minimumEdgeDensityRatio": 0.25,
            "minimumHistogramIntersection": 0.30,
            "maximumMeanColorDelta": 0.42,
            "minimumHighlightCoverageRatio": 0.12,
            "minimumHighlightEnergyRatio": 0.12,
        }
    return {
        "minimumSilhouetteIou": 0.50,
        "maximumCentroidDelta": 0.12,
        "maximumAspectRatioDelta": 0.22,
        "minimumDetailEnergyRatio": 0.25,
        "minimumEdgeDensityRatio": 0.15,
        "minimumHistogramIntersection": 0.20,
        "maximumMeanColorDelta": 0.55,
        "minimumHighlightCoverageRatio": 0.08,
        "minimumHighlightEnergyRatio": 0.08,
    }


def _visual_gate_contract_failures(
    manifest: dict[str, Any], module: dict[str, Any], entry: dict[str, Any]
) -> list[str]:
    gate = module.get("qualityGate") if isinstance(module.get("qualityGate"), dict) else {}
    floor = visual_gate_floor(manifest, entry)
    failures: list[str] = []
    minimum = gate.get("minimumScore")
    if isinstance(minimum, (int, float)) and not isinstance(minimum, bool) and float(minimum) < floor:
        failures.append(
            f"visual minimumScore {float(minimum):.3f} is below non-lowerable {entry.get('riskTier')} "
            f"floor {floor:.3f}"
        )
    scores = gate.get("requiredLayerScores") if isinstance(gate.get("requiredLayerScores"), dict) else {}
    role = str(module.get("role") or "").lower()
    payload = module.get("payload") if isinstance(module.get("payload"), dict) else {}
    global_spec = manifest.get("globalSpec") if isinstance(manifest.get("globalSpec"), dict) else {}
    quality_contract = (
        global_spec.get("qualityContract")
        if isinstance(global_spec.get("qualityContract"), dict)
        else {}
    )
    covered_ids = set(entry.get("covers", []))
    covered_groups = [
        group
        for group in quality_contract.get("featureGroups", [])
        if isinstance(group, dict) and group.get("id") in covered_ids
    ]
    semantic_text = json.dumps(
        [
            payload.get("featureReviewTargets", []),
            payload.get("specializedRegions", []),
            covered_groups,
        ],
        ensure_ascii=False,
    ).lower()
    groups = dict(BASE_VISUAL_LAYER_GROUPS)
    if any(token in role or token in semantic_text for token in IDENTITY_ROLE_TOKENS):
        groups["identity"] = ("identity",)
    if payload.get("materials") or any(token in role for token in MATERIAL_ROLE_TOKENS):
        groups["material"] = ("materialSurface", "material")
    for group, aliases in groups.items():
        present = [alias for alias in aliases if alias in scores]
        required_floor = floor if group in {"identity", "material"} else max(0.70, floor - 0.04)
        if not present:
            failures.append(
                f"visual gate is missing required {group} layer ({' or '.join(aliases)})"
            )
            continue
        for layer in present:
            value = scores.get(layer)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and float(value) < required_floor:
                failures.append(
                    f"visual layer {layer!r} threshold {float(value):.3f} is below non-lowerable "
                    f"floor {required_floor:.3f}"
                )
    configured_diagnostics = gate.get("diagnosticThresholds")
    safe_diagnostics = diagnostic_floor_contract(manifest)
    if isinstance(configured_diagnostics, dict):
        for field, safe_value in safe_diagnostics.items():
            configured = configured_diagnostics.get(field)
            if not isinstance(configured, (int, float)) or isinstance(configured, bool):
                continue
            weaker = (
                float(configured) < safe_value
                if field.startswith("minimum")
                else float(configured) > safe_value
            )
            if weaker:
                failures.append(
                    f"diagnostic threshold {field}={float(configured):.3f} weakens the "
                    f"non-lowerable floor {safe_value:.3f}"
                )
    return failures


def _structural_gate_contract_failures(module: dict[str, Any]) -> list[str]:
    payload = module.get("payload") if isinstance(module.get("payload"), dict) else {}
    failures: list[str] = []
    components = payload.get("componentTree", [])
    for component in components if isinstance(components, list) else []:
        if not isinstance(component, dict):
            continue
        if component.get("componentType") != "assembly" or isinstance(
            component.get("geometryDescriptor"), dict
        ):
            failures.append(
                f"structural gate cannot own visible geometry component {component.get('id')!r}"
            )
    for field in (
        "materials",
        "repetitionSystems",
        "featureReviewTargets",
        "viewEvidence",
        "specializedRegions",
    ):
        if payload.get(field):
            failures.append(f"structural gate cannot own visual payload.{field}")
    return failures


def implementation_contract_paths(
    manifest_path: Path, module: dict[str, Any], *, require_files: bool = True
) -> list[Path]:
    contract = module.get("contract") if isinstance(module.get("contract"), dict) else {}
    values = contract.get("implementationFiles", [])
    if not isinstance(values, list) or not values:
        raise ValueError("visual module contract.implementationFiles must declare the runtime files used by its render")
    root = manifest_path.parent.resolve()
    paths: list[Path] = []
    has_runtime_source = False
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("visual module contract.implementationFiles entries must be paths")
        candidate = (root / value).resolve()
        if candidate != root and root not in candidate.parents:
            raise ValueError(f"implementation file escapes the sculpt project: {value}")
        if require_files and not candidate.is_file():
            raise ValueError(f"declared implementation file does not exist: {candidate}")
        suffix = candidate.suffix.lower()
        if candidate.name.lower() in UNRELATED_IMPLEMENTATION_NAMES:
            raise ValueError(f"project metadata is not module implementation evidence: {value}")
        if suffix not in IMPLEMENTATION_SOURCE_SUFFIXES | IMPLEMENTATION_ASSET_SUFFIXES:
            raise ValueError(f"unsupported module implementation file type: {value}")
        has_runtime_source = has_runtime_source or suffix in IMPLEMENTATION_SOURCE_SUFFIXES
        paths.append(candidate)
    if len(set(paths)) != len(paths):
        raise ValueError("visual module contract.implementationFiles contains duplicates")
    if not has_runtime_source:
        raise ValueError("visual module implementationFiles needs at least one runtime source/entry file")
    source_paths = [path for path in paths if path.suffix.lower() in IMPLEMENTATION_SOURCE_SUFFIXES]
    module_id = module.get("moduleId")
    if not isinstance(module_id, str) or not _has_module_ownership_marker(module_id, source_paths):
        raise ValueError(
            "module implementation needs an executable ownership marker such as "
            f"`export const SCULPT_MODULE_ID = {module_id!r}`; comments and unrelated module files do not bind"
        )
    return paths


def _coverage_status(manifest: dict[str, Any], entries: dict[str, dict[str, Any]]) -> dict[str, Any]:
    global_spec = manifest.get("globalSpec") if isinstance(manifest.get("globalSpec"), dict) else {}
    quality_contract = (
        global_spec.get("qualityContract")
        if isinstance(global_spec.get("qualityContract"), dict)
        else {}
    )
    groups = quality_contract.get("featureGroups", [])
    coverage_contract = manifest.get("coverageContract")
    assembly_scoped = set(
        str(item)
        for item in (
            coverage_contract.get("assemblyFeatureGroups", [])
            if isinstance(coverage_contract, dict)
            else []
        )
        if isinstance(item, str)
    )
    required = {
        str(group.get("id"))
        for group in (groups if isinstance(groups, list) else [])
        if isinstance(group, dict)
        and isinstance(group.get("id"), str)
        and group.get("required", True) is True
    }
    covered_by: dict[str, list[str]] = {}
    for module_id, entry in entries.items():
        for feature_id in entry.get("covers", []):
            if isinstance(feature_id, str):
                covered_by.setdefault(feature_id, []).append(module_id)
    return {
        "required": sorted(required),
        "moduleRequired": sorted(required - assembly_scoped),
        "covered": sorted(required & (set(covered_by) | assembly_scoped)),
        "missing": sorted(required - set(covered_by) - assembly_scoped),
        "coveredBy": {key: value for key, value in sorted(covered_by.items()) if key in required},
        "assemblyScoped": sorted(assembly_scoped),
        "duplicateOwners": {
            key: value
            for key, value in sorted(covered_by.items())
            if key in required and len(value) > 1
        },
        "modeConflicts": sorted(required & assembly_scoped & set(covered_by)),
    }


def _assembly_validation_errors(manifest_path: Path, manifest: dict[str, Any]) -> list[str]:
    try:
        spec = resolve_manifest(manifest_path, manifest)
        from validate_sculpt_spec import validate_spec

        errors, warnings = validate_spec(spec)
        return list(
            dict.fromkeys(
                [*errors]
                + [
                    f"strict quality failure: {warning.removeprefix('quality: ').strip()}"
                    for warning in warnings
                    if warning.startswith("quality:")
                ]
            )
        )
    except (OSError, ValueError) as exc:
        return [str(exc)]


def _pending_correction_attempt(attempts: Any) -> dict[str, Any] | None:
    if not isinstance(attempts, list):
        return None
    for attempt in reversed(attempts):
        if not isinstance(attempt, dict):
            continue
        if attempt.get("accepted") is True or attempt.get("action") in {"request-input", "stop"}:
            return None
        batch = attempt.get("correctionBatch")
        if isinstance(batch, dict) and batch.get("correctionCount", 0) > 0:
            return attempt
    return None


def _pending_strategy_reset(attempts: Any) -> dict[str, Any] | None:
    if not isinstance(attempts, list):
        return None
    for attempt in reversed(attempts):
        if not isinstance(attempt, dict):
            continue
        action = attempt.get("action")
        if attempt.get("accepted") is True or action in {"request-input", "stop"}:
            return None
        if action in REFINEMENT_ACTIONS:
            return None
        if action == STRATEGY_RESET_ACTION:
            return attempt
    return None


def _pending_input_request(attempts: Any) -> dict[str, Any] | None:
    if not isinstance(attempts, list):
        return None
    for attempt in reversed(attempts):
        if not isinstance(attempt, dict):
            continue
        action = attempt.get("action")
        if attempt.get("accepted") is True or action != "request-input":
            return None
        return {
            "reviewId": attempt.get("reviewId"),
            "requiredEvidence": attempt.get("requiredEvidence", []),
        }
    return None


def _strategy_reset_progress(
    manifest: dict[str, Any],
    module_id: str,
    module: dict[str, Any],
    attempt: dict[str, Any] | None,
) -> dict[str, Any]:
    if attempt is None:
        return {}
    current_signature = module_representation_signature(manifest, module_id, module)
    changed = attempt.get("representationSignature") != current_signature
    return {
        "strategyId": attempt.get("strategyId"),
        "strategyChange": attempt.get("strategyChange"),
        "rootCauseKeys": attempt.get("rootCauseKeys", []),
        "falsifyingCheck": attempt.get("falsifyingCheck"),
        "materialChangeReady": changed,
    }


def _correction_batch_progress(
    manifest_path: Path,
    module: dict[str, Any],
    current_module_hash: str,
    attempt: dict[str, Any] | None,
) -> dict[str, Any]:
    if attempt is None or not isinstance(attempt.get("correctionBatch"), dict):
        return {}
    batch = attempt["correctionBatch"]
    scopes = {
        scope for scope in batch.get("scopes", []) if scope in {"spec", "code"}
    }
    changed: set[str] = set()
    if "spec" in scopes and attempt.get("moduleHash") != current_module_hash:
        changed.add("spec")
    if "code" in scopes:
        try:
            current_semantics = implementation_semantic_hashes(
                implementation_contract_paths(manifest_path, module)
            )
        except (OSError, ValueError):
            current_semantics = {}
        if (
            current_semantics
            and attempt.get("implementationSemanticFiles") != current_semantics
        ):
            changed.add("code")
    remaining = sorted(scopes - changed)
    return {
        "batchId": batch.get("batchId"),
        "requiredScopes": sorted(scopes),
        "changedScopes": sorted(changed),
        "remainingScopes": remaining,
        "readyToRender": bool(scopes) and not remaining,
    }


def module_status(
    manifest_path: Path,
    manifest: dict[str, Any] | None = None,
    *,
    _validate_assembly: bool = True,
) -> dict[str, Any]:
    path = manifest_path.expanduser().resolve()
    raw = manifest or read_object(path, "manifest JSON")
    errors = manifest_errors(path, raw)
    cache = _load_cache(path)
    cache_modules = cache.get("modules", {}) if isinstance(cache, dict) else {}
    review_attempts = cache.get("reviewAttempts", {}) if isinstance(cache, dict) else {}
    entries = entry_by_id(raw)
    loaded = load_modules(path, raw, allow_missing=True) if not errors else {}
    accepted: set[str] = set()
    rows: list[dict[str, Any]] = []
    for module_id, entry in entries.items():
        if module_id not in loaded:
            rows.append({"id": module_id, "state": "missing", "riskScore": entry.get("riskScore")})
            continue
        current_hash = module_hash(path, raw, module_id, loaded)
        record = cache_modules.get(module_id) if isinstance(cache_modules, dict) else None
        valid = _cached_acceptance_valid(record, current_hash)
        module = loaded[module_id][1]
        attempts = (
            review_attempts.get(module_id, [])
            if isinstance(review_attempts, dict)
            else []
        )
        pending_attempt = _pending_correction_attempt(attempts)
        pending_strategy = _pending_strategy_reset(attempts)
        pending_input = _pending_input_request(attempts)
        pending_batch = (
            pending_attempt.get("correctionBatch", {})
            if isinstance(pending_attempt, dict)
            else {}
        )
        batch_progress = _correction_batch_progress(
            path,
            module,
            current_hash,
            pending_attempt,
        )
        strategy_progress = _strategy_reset_progress(
            raw,
            module_id,
            module,
            pending_strategy,
        )
        batch_budget = refinement_budget(attempts)
        gate_contract_failures = (
            _structural_gate_contract_failures(module)
            if entry.get("gateType") == "structural"
            else _visual_gate_contract_failures(raw, module, entry)
        )
        if gate_contract_failures:
            valid = False
        if valid:
            accepted.add(module_id)
        rows.append(
            {
                "id": module_id,
                "state": "accepted" if valid else ("stale" if isinstance(record, dict) else "ready"),
                "riskScore": entry.get("riskScore"),
                "riskTier": entry.get("riskTier"),
                "dependsOn": entry.get("dependsOn", []),
                "moduleHash": current_hash,
                "cacheHit": valid,
                "gateContractFailures": gate_contract_failures,
                "pendingCorrectionBatch": pending_batch,
                "correctionBatchProgress": batch_progress,
                "refinementBudget": batch_budget,
                "pendingStrategyReset": strategy_progress,
                "pendingInputRequest": pending_input or {},
            }
        )
    ready = [
        row
        for row in rows
        if row["state"] in {"ready", "stale"}
        and all(dep in accepted for dep in entries[row["id"]].get("dependsOn", []))
    ]
    current = max(ready, key=lambda row: float(row.get("riskScore") or 0), default=None)
    required_ids = {
        module_id for module_id, entry in entries.items() if entry.get("required", True) is True
    }
    coverage = _coverage_status(raw, entries)
    accepted_all = bool(required_ids) and required_ids <= accepted and not errors
    assembly_errors: list[str] = []
    if accepted_all and coverage["missing"]:
        assembly_errors.append(
            "required global feature groups are not owned by any module: "
            + ", ".join(coverage["missing"])
        )
    if accepted_all and coverage["duplicateOwners"]:
        assembly_errors.append(
            "required feature groups have multiple module owners: "
            + ", ".join(coverage["duplicateOwners"])
        )
    if accepted_all and coverage["modeConflicts"]:
        assembly_errors.append(
            "required feature groups cannot be both module-owned and assembly-owned: "
            + ", ".join(coverage["modeConflicts"])
        )
    if (
        _validate_assembly
        and accepted_all
        and not coverage["missing"]
        and not coverage["duplicateOwners"]
        and not coverage["modeConflicts"]
    ):
        assembly_errors.extend(_assembly_validation_errors(path, raw))
    all_errors = list(dict.fromkeys([*errors, *assembly_errors]))
    pending_batch = (
        current.get("pendingCorrectionBatch", {}) if isinstance(current, dict) else {}
    )
    batch_progress = (
        current.get("correctionBatchProgress", {}) if isinstance(current, dict) else {}
    )
    batch_budget = (
        current.get("refinementBudget", {}) if isinstance(current, dict) else {}
    )
    pending_strategy = (
        current.get("pendingStrategyReset", {}) if isinstance(current, dict) else {}
    )
    pending_input = (
        current.get("pendingInputRequest", {}) if isinstance(current, dict) else {}
    )
    workflow_state = (
        "awaiting-input"
        if pending_input
        else "ready-to-render"
        if pending_strategy and pending_strategy.get("materialChangeReady") is True
        else "needs-strategy-change"
        if pending_strategy
        else "ready-to-render"
        if pending_batch and batch_progress.get("readyToRender") is True
        else "needs-refinement"
        if pending_batch
        else "assembly-ready"
        if accepted_all and not assembly_errors
        else "ready"
    )
    return {
        "schemaVersion": MANIFEST_SCHEMA_VERSION,
        "targetName": raw.get("targetName"),
        "policy": "highest-risk-ready-first",
        "currentModule": current["id"] if current else None,
        "state": workflow_state,
        "pendingCorrectionBatch": pending_batch,
        "correctionBatchProgress": batch_progress,
        "refinementBudget": batch_budget,
        "pendingStrategyReset": pending_strategy,
        "pendingInputRequest": pending_input,
        "assemblyReady": _validate_assembly and accepted_all and not assembly_errors,
        "coverage": coverage,
        "assemblyValidationErrors": assembly_errors,
        "acceptedModules": [row["id"] for row in rows if row["state"] == "accepted"],
        "modules": rows,
        "errors": all_errors,
        "cachePath": str(cache_path(path)),
    }


def module_context(
    manifest_path: Path,
    module_id: str | None = None,
) -> dict[str, Any]:
    """Return one hash-aware work packet so unchanged files do not need rereading."""

    path = manifest_path.expanduser().resolve()
    manifest = read_object(path, "manifest JSON")
    status = module_status(path, manifest, _validate_assembly=False)
    selected = module_id or status.get("currentModule")
    if not isinstance(selected, str) or not selected:
        raise ValueError("no current module is ready; supply --module-id for an accepted module")
    entries = entry_by_id(manifest)
    if selected not in entries:
        raise ValueError(f"unknown module {selected!r}")
    if (
        selected != status.get("currentModule")
        and selected not in status.get("acceptedModules", [])
    ):
        raise ValueError(
            "only the current highest-risk ready module or an accepted module may be inspected; "
            f"current={status.get('currentModule')!r}"
        )

    loaded = load_modules(path, manifest, [selected])
    module_path_value, module = loaded[selected]
    payload_value = module.get("payload")
    module_payload = payload_value if isinstance(payload_value, dict) else {}
    gate = module.get("qualityGate") if isinstance(module.get("qualityGate"), dict) else {}
    gate_type = gate.get("type", "visual")
    try:
        implementation_paths = (
            implementation_contract_paths(path, module) if gate_type == "visual" else []
        )
        implementation_warning = ""
    except ValueError as exc:
        implementation_paths = []
        implementation_warning = str(exc)

    role_text = " ".join(
        [
            str(module.get("role") or ""),
            json.dumps(module_payload, ensure_ascii=False),
        ]
    ).lower()
    global_spec = manifest.get("globalSpec") if isinstance(manifest.get("globalSpec"), dict) else {}
    topology_plan = global_spec.get("surfaceTopologyPlan")
    topology_groups = topology_plan.get("groups", []) if isinstance(topology_plan, dict) else []
    module_topology_groups = [
        group
        for group in topology_groups
        if isinstance(group, dict) and group.get("ownerModuleId") == selected
    ]
    reference_names: list[str] = []
    if module_topology_groups or any(
        module_payload.get(field) for field in ("componentTree", "repetitionSystems")
    ):
        reference_names.append("procedural-patterns.md")
    if any(token in role_text for token in ("face", "hand", "finger", "paw", "anatom")):
        reference_names.append("anatomical-regions.md")
    if any(token in role_text for token in ("attach", "joint", "grip", "socket", "connector")):
        reference_names.append("attachment-joint-correctness.md")
    if module_payload.get("materials") or any(
        token in role_text for token in ("material", "cloth", "fabric", "fur", "hair", "glass", "liquid")
    ):
        reference_names.append("material-lighting-realism.md")
    if (
        status.get("pendingCorrectionBatch")
        or status.get("pendingStrategyReset")
        or status.get("pendingInputRequest")
    ):
        reference_names.append("self-correction-loop.md")
    reference_root = (
        Path(__file__).resolve().parent.parent
        / "skills"
        / "object-to-threejs-procedural"
        / "references"
    )
    references = [str((reference_root / name).resolve()) for name in dict.fromkeys(reference_names)]

    cache = _load_cache(path)
    contexts = cache.setdefault("workContexts", {})
    if not isinstance(contexts, dict):
        contexts = {}
        cache["workContexts"] = contexts
    previous = contexts.get(selected)
    previous_files = (
        previous.get("files", {})
        if isinstance(previous, dict) and isinstance(previous.get("files"), dict)
        else {}
    )
    file_roles: dict[str, set[str]] = {}

    def add_file(candidate: Path, role: str) -> None:
        resolved = candidate.expanduser().resolve()
        file_roles.setdefault(str(resolved), set()).add(role)

    add_file(path, "manifest")
    for dependency_id, (dependency_path, _) in loaded.items():
        add_file(
            dependency_path,
            "module-spec" if dependency_id == selected else "dependency-spec",
        )
    for implementation_path in implementation_paths:
        add_file(implementation_path, "implementation")
    for reference in references:
        add_file(Path(reference), "reference")

    files: list[dict[str, Any]] = []
    current_files: dict[str, str] = {}
    for file_path, roles in sorted(file_roles.items()):
        candidate = Path(file_path)
        digest = file_sha256(candidate) if candidate.is_file() else "missing"
        current_files[file_path] = digest
        files.append(
            {
                "path": file_path,
                "roles": sorted(roles),
                "sha256": digest,
                "sizeBytes": candidate.stat().st_size if candidate.is_file() else 0,
                "changedSinceLastContext": previous_files.get(file_path) != digest,
            }
        )

    context_changed = any(item["changedSinceLastContext"] for item in files) or set(
        previous_files
    ) != set(current_files)
    packet = {
        "artifactType": "threejs-sculpt-module-context",
        "version": 1,
        "moduleId": selected,
        "moduleSpecPath": str(module_path_value),
        "cacheHit": not context_changed,
        "readFiles": [
            item["path"] for item in files if item["changedSinceLastContext"]
        ],
        "files": files,
        "references": references,
        "surfaceTopologyGroups": module_topology_groups,
        "qualityGate": {
            "type": gate.get("type"),
            "minimumScore": gate.get("minimumScore"),
            "requiredViews": gate.get("requiredViews", []),
            "diagnosticViews": gate.get("diagnosticViews", []),
            "requiredLayerScores": gate.get("requiredLayerScores", {}),
        },
        "pendingCorrectionBatch": status.get("pendingCorrectionBatch", {}),
        "correctionBatchProgress": status.get("correctionBatchProgress", {}),
        "refinementBudget": status.get("refinementBudget", {}),
        "pendingStrategyReset": status.get("pendingStrategyReset", {}),
        "pendingInputRequest": status.get("pendingInputRequest", {}),
        "implementationWarning": implementation_warning,
        "next": (
            {
                "build": "module build",
                "evaluate": "module evaluate after rendering all required views",
                "review": "module review only after evaluate reports ok=true",
            }
            if gate_type == "visual"
            else {
                "accept": "module accept (runs the same strict module check internally)",
            }
        ),
    }
    contexts[selected] = {
        "files": current_files,
        "moduleHash": next(
            (
                row.get("moduleHash")
                for row in status.get("modules", [])
                if isinstance(row, dict) and row.get("id") == selected
            ),
            None,
        ),
        "recordedAt": datetime.now(timezone.utc).isoformat(),
    }
    cache["updatedAt"] = datetime.now(timezone.utc).isoformat()
    write_spec_atomic(cache_path(path), cache)
    return packet


def check_module(
    manifest_path: Path,
    module_id: str,
    strict_quality: bool = False,
    *,
    prepare_generation: bool = False,
    generation_pass: str | None = None,
) -> dict[str, Any]:
    path = manifest_path.expanduser().resolve()
    manifest = read_object(path, "manifest JSON")
    errors = manifest_errors(path, manifest)
    warnings: list[str] = []
    blocking_warnings: list[str] = []
    resolved_spec: dict[str, Any] | None = None
    validation_proof: object | None = None
    status = module_status(path, manifest)
    if module_id not in entry_by_id(manifest):
        errors.append(f"unknown module {module_id!r}")
    elif (
        module_id not in status.get("acceptedModules", [])
        and status.get("currentModule") != module_id
    ):
        errors.append(
            "only the current highest-risk ready module may be checked; "
            f"current={status.get('currentModule')!r}"
        )
    if not errors:
        try:
            spec = resolve_manifest(path, manifest, [module_id])
            selected_generation_pass: str | None = None
            if prepare_generation:
                selected_generation_pass = generation_pass
                if selected_generation_pass is None:
                    pass_status = pipeline_status(spec)
                    selected_generation_pass = (
                        str(pass_status["lastCompletedPass"] or pass_status["passOrder"][-1])
                        if pass_status["currentPass"] == "complete"
                        else str(pass_status["currentPass"])
                    )
            if selected_generation_pass is not None:
                from generate_threejs_factory import _validate_generation_spec

                spec_errors, spec_warnings, validation_proof = _validate_generation_spec(
                    spec,
                    selected_generation_pass,
                )
            else:
                from validate_sculpt_spec import validate_spec

                spec_errors, spec_warnings = validate_spec(spec)
            errors.extend(spec_errors)
            warnings.extend(spec_warnings)
            if prepare_generation and not spec_errors and validation_proof is not None:
                resolved_spec = spec
            module = load_modules(path, manifest, [module_id])[module_id][1]
            entry = entry_by_id(manifest)[module_id]
            payload = module.get("payload", {})
            owned_components = (
                [
                    item
                    for item in payload.get("componentTree", [])
                    if isinstance(item, dict)
                ]
                if isinstance(payload, dict)
                else []
            )
            if entry.get("gateType") == "visual" and not any(
                component_type(component) == "part" for component in owned_components
            ):
                errors.append(
                    f"visual module {module_id!r} has no owned executable geometry part"
                )
            elif entry.get("gateType") != "visual" and not any(
                payload.get(field) for field in ("componentTree", "repetitionSystems")
            ):
                errors.append(f"module {module_id!r} has no owned assembly or executable geometry")
            owned_ids = {
                str(item.get("id"))
                for field in ("componentTree", "materials", "specializedRegions")
                for item in payload.get(field, [])
                if isinstance(item, dict) and isinstance(item.get("id"), str)
            }
            role = str(module.get("role") or "").lower()
            placeholder_warnings = [
                f"quality: module {module_id!r} has {failure}"
                for failure in _scaffold_placeholder_failures(module)
            ]
            warnings.extend(placeholder_warnings)
            blocking_warnings.extend(placeholder_warnings)
            if entry.get("gateType") == "visual":
                errors.extend(_visual_gate_contract_failures(manifest, module, entry))
                if strict_quality:
                    from sculpt_view_hypotheses import hypothesis_manifest_failures

                    global_spec = (
                        manifest.get("globalSpec")
                        if isinstance(manifest.get("globalSpec"), dict)
                        else {}
                    )
                    hypothesis_failures = hypothesis_manifest_failures(
                        path,
                        global_spec,
                        module.get("qualityGate", {}).get("diagnosticViews", []),
                    )
                    errors.extend(
                        "view hypothesis precondition failed: " + failure
                        for failure in hypothesis_failures
                    )
                blockout_warnings = [
                    f"quality: module {module_id!r} has {failure}"
                    for failure in _blockout_fidelity_failures(module)
                ]
                warnings.extend(blockout_warnings)
                blocking_warnings.extend(blockout_warnings)
                try:
                    implementation_contract_paths(path, module)
                except ValueError as exc:
                    implementation_warning = f"quality: module {module_id!r} {exc}"
                    warnings.append(implementation_warning)
                    blocking_warnings.append(implementation_warning)
            else:
                errors.extend(_structural_gate_contract_failures(module))
            for warning in warnings:
                if "preSpecAssessment" in warning or "surfaceTopologyPlan" in warning:
                    blocking_warnings.append(warning)
                elif any(repr(item_id) in warning or item_id in warning for item_id in owned_ids):
                    blocking_warnings.append(warning)
                elif any(token in role for token in ("material", "surface", "lookdev", "lighting")) and "lookdev" in warning:
                    blocking_warnings.append(warning)
        except (OSError, ValueError) as exc:
            errors.append(str(exc))
    if strict_quality:
        errors.extend(
            f"strict quality failure: {item.removeprefix('quality: ').strip()}"
            for item in blocking_warnings
            if item.startswith("quality:")
        )
    row = next((item for item in status["modules"] if item["id"] == module_id), {})
    result: dict[str, Any] = {
        "ok": not errors,
        "moduleId": module_id,
        "errors": list(dict.fromkeys(errors)),
        "warnings": list(dict.fromkeys(warnings)),
        "blockingWarnings": list(dict.fromkeys(blocking_warnings)),
        "moduleHash": row.get("moduleHash"),
        "cacheHit": row.get("cacheHit", False),
    }
    if result["ok"] and resolved_spec is not None and validation_proof is not None:
        result["_resolvedSpec"] = resolved_spec
        result["_validationProof"] = validation_proof
    return result


def accept_module(
    manifest_path: Path,
    module_id: str,
    score: float | None,
    evidence_path: Path | None,
    reviewer_model: str | None,
    layer_scores: dict[str, Any] | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    path = manifest_path.expanduser().resolve()
    manifest = read_object(path, "manifest JSON")
    entries = entry_by_id(manifest)
    if module_id not in entries:
        raise ValueError(f"unknown module {module_id!r}")
    entry = entries[module_id]
    gate_type = str(entry.get("gateType"))
    if gate_type == "visual":
        raise ValueError(
            "visual modules require `sculpt module review` with an independent verdict artifact; "
            "direct scores are not acceptance authority"
        )
    before = module_status(path, manifest)
    if before.get("currentModule") != module_id:
        if module_id in before.get("acceptedModules", []):
            return before
        raise ValueError(
            "only the current highest-risk ready module may be accepted; "
            f"current={before.get('currentModule')!r}"
        )
    check = check_module(path, module_id, strict_quality=True)
    if not check["ok"]:
        raise ValueError("module check failed: " + "; ".join(check["errors"]))
    module = load_modules(path, manifest, [module_id])[module_id][1]
    gate = module.get("qualityGate") if isinstance(module.get("qualityGate"), dict) else {}
    threshold = float(gate.get("minimumScore", 0.0))
    cache = _load_cache(path)
    cache["version"] = 2
    records = cache.setdefault("modules", {})
    records[module_id] = {
        "moduleHash": check["moduleHash"],
        "interfaceHash": interface_hash(module),
        "gateType": gate_type,
        "score": None,
        "layerScores": {},
        "notes": notes or "structural module contract accepted",
        "threshold": threshold,
        "evidenceManifest": "",
        "evidenceSha256": "",
        "comparisonSha256": "",
        "reviewerModel": "",
        "requiredViews": [],
        "acceptedAt": datetime.now(timezone.utc).isoformat(),
    }
    cache["updatedAt"] = datetime.now(timezone.utc).isoformat()
    write_spec_atomic(cache_path(path), cache)
    return module_status(path, manifest)
