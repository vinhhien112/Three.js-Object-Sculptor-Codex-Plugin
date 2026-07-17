"""Create, resolve, and persist composable ObjectSculptSpec manifests."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from sculpt_contract import (
    CURRENT_SCHEMA_VERSION,
    adaptive_hypothesis_views,
    parse_json,
    sync_pipeline,
    write_spec_atomic,
)
from sculpt_geometry import validate_surface_topology_plan
from sculpt_module_contract import (
    MANIFEST_SCHEMA_VERSION,
    MODULE_ID_PATTERN,
    MODULE_SCHEMA_VERSION,
    SEGMENTED_FIELDS,
    ids,
    manifest_errors,
    module_document_errors,
    module_path,
)


@dataclass
class SculptDocument:
    path: Path
    raw: dict[str, Any]
    resolved: dict[str, Any]
    modules: dict[str, tuple[Path, dict[str, Any]]]

    @property
    def modular(self) -> bool:
        return is_module_manifest(self.raw)


def read_object(path: Path, label: str) -> dict[str, Any]:
    payload = parse_json(path.read_text(encoding="utf-8"), label)
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object")
    return payload


def is_module_manifest(payload: Any) -> bool:
    return isinstance(payload, dict) and payload.get("schemaVersion") == MANIFEST_SCHEMA_VERSION


def read_raw_spec(path: Path) -> dict[str, Any]:
    return read_object(path.expanduser().resolve(), "spec JSON")


def _assembly_root(target_name: str) -> dict[str, Any]:
    return {
        "id": "root",
        "name": target_name,
        "componentType": "assembly",
        "level": "macro",
        "role": "assembly-root",
        "importance": 1.0,
        "confidence": 1.0,
        "parent": None,
        "attachment": None,
        "transform": {"position": [0, 0, 0], "rotation": [0, 0, 0], "scale": [1, 1, 1]},
        "actionProfile": {
            "animationRole": "root",
            "pivot": {
                "mode": "center",
                "localPosition": [0, 0, 0],
                "axis": [0, 1, 0],
                "confidence": 1.0,
            },
            "transformChannels": {
                "translate": True,
                "rotate": True,
                "scale": True,
                "bend": False,
                "visibility": True,
            },
            "sockets": [],
            "collider": None,
            "breakable": False,
        },
        "evidenceRefs": ["full-object"],
    }


def make_manifest(base_spec: dict[str, Any]) -> dict[str, Any]:
    """Create only the global contract; module specs are authored later."""
    global_spec = copy.deepcopy(base_spec)
    global_spec["schemaVersion"] = CURRENT_SCHEMA_VERSION
    global_spec["componentTree"] = [_assembly_root(str(base_spec.get("targetName") or "Object"))]
    global_spec["materials"] = []
    global_spec["repetitionSystems"] = []
    global_spec["featureReviewTargets"] = [
        item
        for item in global_spec.get("featureReviewTargets", [])
        if isinstance(item, dict) and item.get("componentRefs") == ["root"]
    ]
    assessment = global_spec.get("preSpecAssessment")
    if isinstance(assessment, dict):
        assessment["specializedRegions"] = {
            "status": "unassessed",
            "notes": "Declare critical regions inside the module that owns their geometry.",
            "regions": [],
        }
    sync_pipeline(global_spec)
    global_spec["reviewGovernance"] = {
        "independentContextRequired": True,
        "reviewerRole": "independent-reviewer",
        "verdictArtifactRequired": True,
        "builderMayNotOverrideVerdict": True,
    }
    return {
        "schemaVersion": MANIFEST_SCHEMA_VERSION,
        "manifestRevision": 1,
        "targetName": global_spec.get("targetName"),
        "targetId": global_spec.get("targetId"),
        "sourceImage": global_spec.get("sourceImage", ""),
        "globalSpec": global_spec,
        "assemblyContract": {
            "rootComponentId": "root",
            "coordinateFrame": copy.deepcopy(global_spec.get("coordinateFrame", {})),
            "units": "relative",
            "connectorRule": (
                "Cross-module parenting must target root or a connector exported by a dependency."
            ),
            "invariants": [
                "component, material, repetition, feature, and region ids are globally unique",
                "module internals may change without invalidating dependants when exported connectors stay stable",
            ],
        },
        "coverageContract": {
            "assemblyFeatureGroups": [
                str(item["id"])
                for item in global_spec.get("featureReviewTargets", [])
                if isinstance(item, dict) and isinstance(item.get("id"), str)
            ],
            "rule": "Every required feature group has exactly one owner mode: one visual module or the assembled-pass gate.",
        },
        "modulePolicy": {
            "order": "highest-risk-ready-first",
            "failClosed": True,
            "cache": "content-and-interface-hash",
            "finalAssemblyRequiresAcceptedModules": True,
        },
        "modules": [],
    }


def _risk_tier(score: float) -> str:
    if score >= 85:
        return "critical"
    if score >= 65:
        return "high"
    if score >= 35:
        return "medium"
    return "low"


def _default_threshold(tier: str, quality_profile: str) -> float:
    base = {"low": 0.72, "medium": 0.76, "high": 0.82, "critical": 0.86}[tier]
    return max(base, 0.85 if quality_profile == "reference-fidelity" else 0.0)


def make_module(
    manifest: dict[str, Any],
    module_id: str,
    role: str,
    risk_score: float,
    depends_on: list[str],
    gate_type: str,
    template: str,
) -> dict[str, Any]:
    global_spec = manifest.get("globalSpec", {})
    quality_profile = str(global_spec.get("qualityProfile") or "balanced")
    tier = _risk_tier(risk_score)
    threshold = _default_threshold(tier, quality_profile)
    reference_fidelity = quality_profile == "reference-fidelity"
    assessment = global_spec.get("preSpecAssessment")
    complexity = assessment.get("complexity") if isinstance(assessment, dict) else None
    complexity_tier = complexity.get("tier") if isinstance(complexity, dict) else "moderate"
    policy = global_spec.get("viewHypothesisPolicy")
    configured_diagnostics = policy.get("requiredViews") if isinstance(policy, dict) else None
    canonical_diagnostics = adaptive_hypothesis_views(str(complexity_tier), quality_profile)
    selected_diagnostics = (
        [item for item in configured_diagnostics if isinstance(item, str) and item]
        if isinstance(configured_diagnostics, list)
        else []
    )
    diagnostic_views = list(dict.fromkeys([*canonical_diagnostics, *selected_diagnostics]))
    role_tokens = role.lower()
    required_scores = {
        "silhouetteProportion": max(0.7, threshold - 0.04),
        "componentStructure": max(0.7, threshold - 0.04),
        "formDetail": max(0.7, threshold - 0.04),
    }
    if any(token in role_tokens for token in ("identity", "face", "hand", "character")):
        required_scores["identity"] = threshold
    if any(token in role_tokens for token in ("material", "surface", "lookdev", "fabric", "fiber")):
        required_scores["materialSurface"] = threshold
    payload: dict[str, Any] = {
        "componentTree": [],
        "materials": [],
        "repetitionSystems": [],
        "featureReviewTargets": [],
        "viewEvidence": [],
        "specializedRegions": [],
    }
    if template == "foundation":
        from new_sculpt_spec import make_base_material, make_root_component

        component = make_root_component(str(manifest.get("targetName") or "Object"))
        component.update(
            {
                "id": f"{module_id}-body",
                "name": f"{manifest.get('targetName') or 'Object'} foundation body",
                "parent": "root",
                "attachment": {
                    "type": "rigid",
                    "parentId": "root",
                    "parentSocket": "root-origin",
                    "localStart": [0, 0, 0],
                    "localEnd": [0, 0.01, 0],
                    "contactType": "embedded",
                    "overlap": 0.01,
                    "gapTolerance": 0.002,
                    "evidenceRefs": ["full-object"],
                    "contactRule": "foundation body is anchored to the global assembly root",
                },
            }
        )
        payload["componentTree"] = [component]
        payload["materials"] = [make_base_material(quality_profile)]
    if gate_type == "visual" and payload["materials"]:
        required_scores.setdefault("materialSurface", threshold)
    return {
        "schemaVersion": MODULE_SCHEMA_VERSION,
        "moduleId": module_id,
        "revision": 1,
        "role": role,
        "dependsOn": depends_on,
        "risk": {"score": risk_score, "tier": tier, "reasons": []},
        "qualityGate": {
            "type": gate_type,
            "minimumScore": threshold,
            "requiredViews": ["reference"],
            "diagnosticViews": diagnostic_views if gate_type == "visual" else [],
            "requiredLayerScores": required_scores if gate_type == "visual" else {},
            "diagnosticThresholds": (
                {
                    "minimumSilhouetteIou": 0.63 if reference_fidelity else 0.50,
                    "maximumCentroidDelta": 0.08 if reference_fidelity else 0.12,
                    "maximumAspectRatioDelta": 0.15 if reference_fidelity else 0.22,
                    "minimumDetailEnergyRatio": 0.35 if reference_fidelity else 0.25,
                    "minimumEdgeDensityRatio": 0.25 if reference_fidelity else 0.15,
                    "minimumHistogramIntersection": 0.30 if reference_fidelity else 0.20,
                    "maximumMeanColorDelta": 0.42 if reference_fidelity else 0.55,
                    "minimumHighlightCoverageRatio": 0.12 if reference_fidelity else 0.08,
                    "minimumHighlightEnergyRatio": 0.12 if reference_fidelity else 0.08,
                }
                if gate_type == "visual"
                else {}
            ),
        },
        "contract": {
            "coordinateFrameRef": "root",
            "connectors": [],
            "invariants": [],
            "implementationFiles": [],
            "owns": {
                "components": [item["id"] for item in payload["componentTree"]],
                "materials": [item["id"] for item in payload["materials"]],
                "repetitionSystems": [],
                "featureTargets": [],
                "specializedRegions": [],
            },
        },
        "payload": payload,
    }


def entry_by_id(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("id")): item
        for item in manifest.get("modules", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }


def _selection(entries: dict[str, dict[str, Any]], selected: Iterable[str] | None) -> list[str]:
    if selected is None:
        return list(entries)
    wanted: set[str] = set()

    def include(module_id: str) -> None:
        if module_id in wanted:
            return
        if module_id not in entries:
            raise ValueError(f"unknown module {module_id!r}")
        for dependency in entries[module_id].get("dependsOn", []):
            include(str(dependency))
        wanted.add(module_id)

    for module_id in selected:
        include(module_id)
    return [module_id for module_id in entries if module_id in wanted]


def load_modules(
    manifest_path: Path,
    manifest: dict[str, Any],
    selected: Iterable[str] | None = None,
    *,
    allow_missing: bool = False,
) -> dict[str, tuple[Path, dict[str, Any]]]:
    entries = entry_by_id(manifest)
    loaded: dict[str, tuple[Path, dict[str, Any]]] = {}
    for module_id in _selection(entries, selected):
        path = module_path(manifest_path, entries[module_id].get("path"))
        if not path.is_file():
            if allow_missing:
                continue
            raise ValueError(f"module {module_id!r} is missing: {path}")
        payload = read_object(path, f"module {module_id!r}")
        if payload.get("schemaVersion") != MODULE_SCHEMA_VERSION:
            raise ValueError(f"module {module_id!r} schemaVersion must be {MODULE_SCHEMA_VERSION!r}")
        if payload.get("moduleId") != module_id:
            raise ValueError(f"module file {path} declares a different moduleId")
        loaded[module_id] = (path, payload)
    return loaded


def _merge_unique(destination: list[Any], incoming: Any, field: str, module_id: str) -> None:
    if incoming is None:
        return
    if not isinstance(incoming, list):
        raise ValueError(f"module {module_id!r} payload.{field} must be an array")
    seen = {
        item.get("id")
        for item in destination
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    for item in incoming:
        if not isinstance(item, dict):
            raise ValueError(f"module {module_id!r} payload.{field} entries must be objects")
        item_id = item.get("id")
        if not isinstance(item_id, str) or not item_id.strip():
            raise ValueError(f"module {module_id!r} payload.{field} entry id is required")
        if item_id in seen:
            raise ValueError(f"duplicate {field} id {item_id!r} across modules")
        copied = copy.deepcopy(item)
        copied.setdefault("moduleId", module_id)
        destination.append(copied)
        seen.add(item_id)


def resolve_manifest(
    manifest_path: Path,
    manifest: dict[str, Any] | None = None,
    selected: Iterable[str] | None = None,
    *,
    allow_missing: bool = False,
) -> dict[str, Any]:
    path = manifest_path.expanduser().resolve()
    raw = manifest or read_object(path, "manifest JSON")
    errors = manifest_errors(path, raw, require_files=not allow_missing and selected is None)
    if errors:
        raise ValueError("invalid module manifest: " + "; ".join(errors))
    spec = copy.deepcopy(raw["globalSpec"])
    spec["schemaVersion"] = CURRENT_SCHEMA_VERSION
    for field in SEGMENTED_FIELDS:
        value = spec.get(field)
        spec[field] = copy.deepcopy(value) if isinstance(value, list) else []
    modules = load_modules(path, raw, selected, allow_missing=allow_missing)
    entries = entry_by_id(raw)
    contract_errors: list[str] = []
    for module_id, (_, module) in modules.items():
        dependencies = {
            dependency: modules[dependency][1]
            for dependency in entries[module_id].get("dependsOn", [])
            if dependency in modules
        }
        contract_errors.extend(
            module_document_errors(module_id, module, entries[module_id], dependencies)
        )
    if contract_errors:
        raise ValueError("invalid module contract: " + "; ".join(dict.fromkeys(contract_errors)))
    regions: list[dict[str, Any]] = []
    for module_id, (_, module) in modules.items():
        payload = module.get("payload")
        if not isinstance(payload, dict):
            raise ValueError(f"module {module_id!r} payload must be an object")
        for field in SEGMENTED_FIELDS:
            _merge_unique(spec[field], payload.get(field, []), field, module_id)
        module_regions = payload.get("specializedRegions", [])
        if not isinstance(module_regions, list):
            raise ValueError(f"module {module_id!r} payload.specializedRegions must be an array")
        for region in module_regions:
            if not isinstance(region, dict):
                raise ValueError(f"module {module_id!r} specializedRegions entries must be objects")
            copied = copy.deepcopy(region)
            copied.setdefault("moduleId", module_id)
            regions.append(copied)
    if regions:
        assessment = spec.setdefault("preSpecAssessment", {})
        if not isinstance(assessment, dict):
            raise ValueError("globalSpec.preSpecAssessment must be an object")
        assessment["specializedRegions"] = {
            "status": "declared",
            "notes": "Critical regions are owned and validated by their modules.",
            "regions": regions,
        }
    global_ids: dict[str, str] = {}
    for field in SEGMENTED_FIELDS:
        for item in spec.get(field, []):
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                continue
            item_id = str(item["id"])
            previous = global_ids.get(item_id)
            if previous is not None and previous != field:
                raise ValueError(f"id {item_id!r} is reused by {previous} and {field}")
            global_ids[item_id] = field
    for region in regions:
        region_id = region.get("id")
        if isinstance(region_id, str) and region_id in global_ids:
            raise ValueError(
                f"id {region_id!r} is reused by {global_ids[region_id]} and specializedRegions"
            )
    sync_pipeline(spec)
    return spec


def load_document(path: Path, *, allow_missing: bool = True) -> SculptDocument:
    resolved_path = path.expanduser().resolve()
    raw = read_object(resolved_path, "spec JSON")
    if not is_module_manifest(raw):
        return SculptDocument(resolved_path, raw, raw, {})
    modules = load_modules(resolved_path, raw, allow_missing=allow_missing)
    return SculptDocument(
        resolved_path,
        raw,
        resolve_manifest(resolved_path, raw, allow_missing=allow_missing),
        modules,
    )


def save_document(document: SculptDocument, destination: Path | None = None) -> None:
    output = (destination or document.path).expanduser().resolve()
    if not document.modular or output != document.path:
        write_spec_atomic(output, document.resolved)
        return
    global_spec = document.raw.get("globalSpec")
    if not isinstance(global_spec, dict):
        raise ValueError("manifest globalSpec must be an object")
    original_manifest = copy.deepcopy(document.raw)
    for key, value in document.resolved.items():
        if key not in SEGMENTED_FIELDS:
            updated = copy.deepcopy(value)
            if key == "preSpecAssessment" and isinstance(updated, dict):
                original_assessment = global_spec.get("preSpecAssessment")
                if isinstance(original_assessment, dict):
                    updated["specializedRegions"] = copy.deepcopy(
                        original_assessment.get("specializedRegions")
                    )
            global_spec[key] = updated
    ownership: dict[tuple[str, str], tuple[str, str]] = {}
    for module_id, (_, module) in document.modules.items():
        payload = module.get("payload")
        if not isinstance(payload, dict):
            continue
        for field in SEGMENTED_FIELDS:
            for item in payload.get(field, []):
                if isinstance(item, dict) and isinstance(item.get("id"), str):
                    ownership[(field, str(item["id"]))] = (module_id, field)
    by_owner: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for field in SEGMENTED_FIELDS:
        for item in document.resolved.get(field, []):
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                continue
            owner = ownership.get((field, str(item["id"])))
            if owner is None:
                continue
            clean = copy.deepcopy(item)
            clean.pop("moduleId", None)
            by_owner.setdefault(owner, []).append(clean)
    for module_id, (module_file, module) in document.modules.items():
        payload = module.get("payload")
        if not isinstance(payload, dict):
            continue
        changed = False
        for field in SEGMENTED_FIELDS:
            updated = by_owner.get((module_id, field), [])
            if payload.get(field, []) != updated:
                payload[field] = updated
                changed = True
        if changed:
            module["revision"] = int(module.get("revision", 0)) + 1
            write_spec_atomic(module_file, module)
    if document.raw != original_manifest:
        document.raw["manifestRevision"] = int(original_manifest.get("manifestRevision", 0)) + 1
        write_spec_atomic(document.path, document.raw)


def add_module(
    manifest_path: Path,
    module_id: str,
    role: str,
    risk_score: float,
    depends_on: list[str],
    gate_type: str,
    template: str,
    covers: list[str] | None = None,
) -> Path:
    path = manifest_path.expanduser().resolve()
    manifest = read_object(path, "manifest JSON")
    errors = manifest_errors(path, manifest)
    if errors:
        raise ValueError("invalid module manifest: " + "; ".join(errors))
    if MODULE_ID_PATTERN.fullmatch(module_id) is None:
        raise ValueError(f"module id must match {MODULE_ID_PATTERN.pattern}")
    global_spec = manifest.get("globalSpec") if isinstance(manifest.get("globalSpec"), dict) else {}
    topology_plan = global_spec.get("surfaceTopologyPlan")
    if gate_type == "visual":
        if topology_plan is None:
            raise ValueError(
                "a visual module requires globalSpec.surfaceTopologyPlan; missing plans are readable only for legacy non-modular specs"
            )
        topology_errors, _ = validate_surface_topology_plan(
            topology_plan,
            [],
            [],
            resolve_references=False,
        )
        if topology_errors:
            raise ValueError("invalid surfaceTopologyPlan: " + "; ".join(topology_errors))
        assert isinstance(topology_plan, dict)
        topology_status = topology_plan.get("status")
        if topology_status == "unassessed":
            raise ValueError(
                "surfaceTopologyPlan must classify construction strategies before visual module specs are created"
            )
        if topology_status != "planned":
            raise ValueError("a visual module requires surfaceTopologyPlan.status 'planned'")
        topology_groups = topology_plan.get("groups", [])
        owns_topology = isinstance(topology_groups, list) and any(
            isinstance(group, dict) and group.get("ownerModuleId") == module_id
            for group in topology_groups
        )
        if not owns_topology:
            raise ValueError(
                f"visual module {module_id!r} needs at least one surfaceTopologyPlan group with matching ownerModuleId"
            )
    entries = entry_by_id(manifest)
    if module_id in entries:
        raise ValueError(f"module {module_id!r} already exists")
    unknown = [item for item in depends_on if item not in entries]
    if unknown:
        raise ValueError("unknown dependencies: " + ", ".join(unknown))
    coverage = list(dict.fromkeys(covers or []))
    quality_contract = manifest.get("globalSpec", {}).get("qualityContract", {})
    known_groups = ids(quality_contract.get("featureGroups", []))
    unknown_coverage = sorted(set(coverage) - known_groups)
    if unknown_coverage:
        raise ValueError("unknown global feature groups: " + ", ".join(unknown_coverage))
    if coverage and gate_type != "visual":
        raise ValueError("global feature-group coverage requires a visual module gate")
    if gate_type == "structural" and template != "empty":
        raise ValueError("structural modules are interface/assembly-only and must use --template empty")
    module_dir = path.parent / f"{path.stem}.modules"
    module_file = module_dir / f"{module_id}.json"
    if module_file.exists():
        raise ValueError(f"module file already exists: {module_file}")
    module = make_module(manifest, module_id, role, risk_score, depends_on, gate_type, template)
    manifest["modules"].append(
        {
            "id": module_id,
            "path": str(module_file.relative_to(path.parent)),
            "role": role,
            "riskScore": risk_score,
            "riskTier": module["risk"]["tier"],
            "dependsOn": depends_on,
            "gateType": gate_type,
            "covers": coverage,
            "required": True,
        }
    )
    write_spec_atomic(module_file, module)
    manifest["manifestRevision"] = int(manifest.get("manifestRevision", 0)) + 1
    try:
        write_spec_atomic(path, manifest)
    except (OSError, TypeError, ValueError):
        module_file.unlink(missing_ok=True)
        raise
    return module_file
