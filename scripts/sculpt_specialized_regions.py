#!/usr/bin/env python3
"""Contracts for visually sensitive face and hand regions.

The geometry generator remains component-driven.  This module prevents a
character face or hand from being accepted as an anonymous primitive by tying
visible landmarks, component hierarchy, close-up evidence, and an independent
feature gate together.
"""

from __future__ import annotations

import math
import re
from typing import Any


VALID_SPECIALIZED_REGION_STATUSES = frozenset({"unassessed", "none", "declared"})
VALID_SPECIALIZED_REGION_KINDS = frozenset({"face", "hand"})
VALID_REGION_VISIBILITY = frozenset({"clear", "partial", "occluded"})
VALID_OCCLUSION_HANDLING = frozenset(
    {"model-visible-only", "bounded-inference", "request-input", "omit-hidden-detail"}
)
VALID_HAND_ARTICULATION_MODES = frozenset(
    {"explicit-digits", "grouped-digits", "silhouette-only", "hidden"}
)
VALID_LANDMARK_ROLES = {
    "face": frozenset(
        {
            "face-contour",
            "eye-system",
            "brow-expression",
            "nose-muzzle",
            "mouth-expression",
            "jaw-cheeks",
            "ears",
        }
    ),
    "hand": frozenset(
        {
            "wrist",
            "palm",
            "thumb",
            "digits",
            "digit-mass",
            "joint-arc",
            "outer-contour",
            "pose-contact",
        }
    ),
}
REQUIRED_CLEAR_FACE_ROLES = frozenset(
    {"face-contour", "eye-system", "nose-muzzle", "mouth-expression"}
)
REQUIRED_HAND_ROLES = {
    "explicit-digits": frozenset({"wrist", "palm", "thumb", "digits", "joint-arc"}),
    "grouped-digits": frozenset({"wrist", "palm", "digit-mass", "outer-contour"}),
}
VALID_CONSTRAINT_TYPES = frozenset(
    {"proportion", "alignment", "silhouette", "expression", "pose", "contact", "asymmetry"}
)
NON_ARTICULATING_ROLES = frozenset({"", "static", "fixed", "none", "root"})
ARTICULATION_PIVOT_MODES = frozenset({"hinge", "branch", "base", "custom", "joint", "root"})
STRONG_CHARACTER_SIGNAL = re.compile(
    r"\b(character|character-like|mascot|humanoid|human|person|animal|creature|"
    r"facial|portrait|bust|hand|finger|thumb|palm)\b",
    re.IGNORECASE,
)
WEAK_FACE_SIGNAL = re.compile(r"\b(face|eye|mouth)\b", re.IGNORECASE)
STARTER_SPECIALIZED_REGION_NOTE = "inspect for identity-critical faces and hands"


def _is_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _string_array(
    value: Any,
    label: str,
    errors: list[str],
    *,
    required: bool = False,
) -> list[str]:
    if not isinstance(value, list) or not all(_nonempty_string(item) for item in value):
        errors.append(f"{label} must be an array of non-empty strings")
        return []
    if required and not value:
        errors.append(f"{label} must not be empty")
    return [str(item) for item in value]


def _unit_interval(value: Any, label: str, errors: list[str]) -> None:
    if not _is_number(value) or not 0 <= float(value) <= 1:
        errors.append(f"{label} must be a number from 0 to 1")


def _articulation_contract_failures(component: dict[str, Any]) -> list[str]:
    """Return concrete reasons a hand part cannot act as a runtime joint."""

    failures: list[str] = []
    profile = component.get("actionProfile")
    if not isinstance(profile, dict):
        return ["needs an actionProfile"]
    role = str(profile.get("animationRole") or "").strip().lower()
    if role in NON_ARTICULATING_ROLES:
        failures.append("must declare a non-static animationRole")
    channels = profile.get("transformChannels")
    if not isinstance(channels, dict) or channels.get("rotate") is not True:
        failures.append("must enable actionProfile.transformChannels.rotate")
    pivot = profile.get("pivot")
    if not isinstance(pivot, dict):
        failures.append("needs an actionProfile.pivot")
        return failures
    if pivot.get("mode") not in ARTICULATION_PIVOT_MODES:
        failures.append(
            "pivot.mode must be hinge, branch, base, custom, joint, or root"
        )
    local_position = pivot.get("localPosition")
    if not (
        isinstance(local_position, list)
        and len(local_position) == 3
        and all(_is_number(value) for value in local_position)
    ):
        failures.append("pivot.localPosition must be three finite numbers")
    axis = pivot.get("axis")
    if not (
        isinstance(axis, list)
        and len(axis) == 3
        and all(_is_number(value) for value in axis)
        and sum(float(value) ** 2 for value in axis) > 1e-12
    ):
        failures.append("pivot.axis must be a non-zero finite vector")
    return failures


def _character_like_signal(spec: dict[str, Any]) -> bool:
    assessment = spec.get("preSpecAssessment")
    object_class = assessment.get("objectClass") if isinstance(assessment, dict) else None
    fragments: list[str] = []
    primary_type = ""
    if isinstance(object_class, dict):
        if isinstance(object_class.get("primaryType"), str):
            primary_type = str(object_class["primaryType"])
        for field in ("primaryType", "formLanguage", "structureKind", "notes"):
            value = object_class.get(field)
            if isinstance(value, str):
                fragments.append(value)
            elif isinstance(value, list):
                fragments.extend(str(item) for item in value if isinstance(item, str))
    components = spec.get("componentTree")
    for component in components if isinstance(components, list) else []:
        if not isinstance(component, dict):
            continue
        for field in ("id", "name", "role"):
            value = component.get(field)
            if isinstance(value, str):
                fragments.append(value.replace("_", " ").replace("-", " "))
    text = " ".join(fragments)
    if STRONG_CHARACTER_SIGNAL.search(text) is not None:
        return True
    if re.search(r"\bface\b", primary_type, re.IGNORECASE) is not None:
        return True
    return len({match.lower() for match in WEAK_FACE_SIGNAL.findall(text)}) >= 2


def _meaningful_none_reason(value: Any) -> bool:
    return (
        _nonempty_string(value)
        and STARTER_SPECIALIZED_REGION_NOTE not in str(value).strip().lower()
    )


def _validate_constraints(
    region: dict[str, Any],
    label: str,
    kind: str,
    visibility: str,
    known_components: set[str],
    region_components: set[str],
    errors: list[str],
) -> None:
    constraints = region.get("constraints")
    if not isinstance(constraints, list):
        errors.append(f"{label}.constraints must be an array")
        return
    if visibility != "occluded" and len(constraints) < 2:
        errors.append(
            f"{label}.constraints needs at least two explicit proportion/pose criteria"
        )
    ids: set[str] = set()
    constraint_types: set[str] = set()
    for index, constraint in enumerate(constraints):
        item_label = f"{label}.constraints[{index}]"
        if not isinstance(constraint, dict):
            errors.append(f"{item_label} must be an object")
            continue
        constraint_id = constraint.get("id")
        if not _nonempty_string(constraint_id):
            errors.append(f"{item_label}.id is required")
        elif constraint_id in ids:
            errors.append(f"{label}.constraints has duplicate id {constraint_id!r}")
        else:
            ids.add(str(constraint_id))
        constraint_type = constraint.get("type")
        if constraint_type not in VALID_CONSTRAINT_TYPES:
            errors.append(
                f"{item_label}.type must be one of: "
                + ", ".join(sorted(VALID_CONSTRAINT_TYPES))
            )
        else:
            constraint_types.add(str(constraint_type))
        if not _nonempty_string(constraint.get("description")):
            errors.append(f"{item_label}.description is required")
        refs = _string_array(
            constraint.get("componentRefs", []),
            f"{item_label}.componentRefs",
            errors,
        )
        for component_id in refs:
            if component_id not in known_components:
                errors.append(
                    f"{item_label}.componentRefs references unknown component {component_id!r}"
                )
            elif component_id not in region_components:
                errors.append(
                    f"{item_label}.componentRefs must stay inside the specialized region"
                )
    if visibility != "occluded":
        required_type = "expression" if kind == "face" else "pose"
        if "proportion" not in constraint_types:
            errors.append(f"{label}.constraints needs a proportion constraint")
        if required_type not in constraint_types:
            errors.append(f"{label}.constraints needs a {required_type} constraint")


def _validate_landmarks(
    region: dict[str, Any],
    label: str,
    kind: str,
    visibility: str,
    articulation_mode: str | None,
    requires_articulation_parts: bool,
    components_by_id: dict[str, dict[str, Any]],
    region_components: set[str],
    errors: list[str],
) -> None:
    landmarks = region.get("landmarks")
    if not isinstance(landmarks, list):
        errors.append(f"{label}.landmarks must be an array")
        return
    ids: set[str] = set()
    visible_roles: set[str] = set()
    visible_component_ids: set[str] = set()
    for index, landmark in enumerate(landmarks):
        item_label = f"{label}.landmarks[{index}]"
        if not isinstance(landmark, dict):
            errors.append(f"{item_label} must be an object")
            continue
        landmark_id = landmark.get("id")
        if not _nonempty_string(landmark_id):
            errors.append(f"{item_label}.id is required")
        elif landmark_id in ids:
            errors.append(f"{label}.landmarks has duplicate id {landmark_id!r}")
        else:
            ids.add(str(landmark_id))
        role = landmark.get("role")
        if role not in VALID_LANDMARK_ROLES.get(kind, frozenset()):
            errors.append(
                f"{item_label}.role must be one of: "
                + ", ".join(sorted(VALID_LANDMARK_ROLES.get(kind, frozenset())))
            )
        visible = landmark.get("visible")
        if not isinstance(visible, bool):
            errors.append(f"{item_label}.visible must be boolean")
        elif visible and isinstance(role, str):
            visible_roles.add(role)
        confidence = landmark.get("confidence")
        _unit_interval(confidence, f"{item_label}.confidence", errors)
        refs = _string_array(
            landmark.get("componentRefs", []),
            f"{item_label}.componentRefs",
            errors,
            required=visible is True,
        )
        for component_id in refs:
            component = components_by_id.get(component_id)
            if component is None:
                errors.append(
                    f"{item_label}.componentRefs references unknown component {component_id!r}"
                )
            elif component_id not in region_components:
                errors.append(
                    f"{item_label}.componentRefs must stay inside the specialized region"
                )
            elif component.get("componentType", "part") != "part":
                errors.append(
                    f"{item_label}.componentRefs must reference geometry parts, not assemblies"
                )
            elif visible is True:
                visible_component_ids.add(component_id)
        _string_array(
            landmark.get("criteria"),
            f"{item_label}.criteria",
            errors,
            required=True,
        )

    if visibility == "clear" and kind == "face":
        missing = REQUIRED_CLEAR_FACE_ROLES - visible_roles
        if missing:
            errors.append(
                f"{label}.landmarks is missing clear-face roles: {', '.join(sorted(missing))}"
            )
        if not visible_component_ids:
            errors.append(
                f"{label}.landmarks must map clear-face regions to executable geometry"
            )
    if visibility == "clear" and kind == "hand" and articulation_mode in REQUIRED_HAND_ROLES:
        missing = REQUIRED_HAND_ROLES[articulation_mode] - visible_roles
        if missing:
            errors.append(
                f"{label}.landmarks is missing {articulation_mode} roles: "
                + ", ".join(sorted(missing))
            )
        if not visible_component_ids:
            errors.append(
                f"{label}.landmarks must map clear-hand regions to executable geometry"
            )
        if requires_articulation_parts:
            minimum_components = 4 if articulation_mode == "explicit-digits" else 3
            articulatable_components: set[str] = set()
            for component_id in sorted(visible_component_ids):
                articulation_failures = _articulation_contract_failures(
                    components_by_id[component_id]
                )
                if articulation_failures:
                    errors.extend(
                        f"{label} action-ready component {component_id!r} {failure}"
                        for failure in articulation_failures
                    )
                else:
                    articulatable_components.add(component_id)
            if len(articulatable_components) < minimum_components:
                errors.append(
                    f"{label}.landmarks must map an action-ready {articulation_mode} hand to at least "
                    f"{minimum_components} articulatable geometry components"
                )
    if visibility == "partial" and len(visible_roles) < 3:
        errors.append(f"{label}.landmarks needs at least three visible roles for partial evidence")
    minimum_partial_components = (
        2 if kind == "hand" and requires_articulation_parts else 1
    )
    if visibility == "partial" and len(visible_component_ids) < minimum_partial_components:
        errors.append(
            f"{label}.landmarks needs at least {minimum_partial_components} visible geometry "
            + ("host" if minimum_partial_components == 1 else "components")
        )


def _is_descendant(
    component_id: str,
    assembly_id: str,
    components_by_id: dict[str, dict[str, Any]],
) -> bool:
    current = component_id
    visited: set[str] = set()
    while current not in visited:
        if current == assembly_id:
            return True
        visited.add(current)
        component = components_by_id.get(current)
        parent = component.get("parent") if isinstance(component, dict) else None
        if not isinstance(parent, str):
            return False
        current = parent
    return False


def _validate_digit_chains(
    region: dict[str, Any],
    label: str,
    components_by_id: dict[str, dict[str, Any]],
    region_components: set[str],
    requires_articulation_parts: bool,
    errors: list[str],
) -> None:
    chains = region.get("digitChains")
    if not isinstance(chains, list) or len(chains) < 2:
        errors.append(
            f"{label}.digitChains needs a thumb chain and at least one finger chain"
        )
        return
    ids: set[str] = set()
    roles: list[str] = []
    used_components: set[str] = set()
    for index, chain in enumerate(chains):
        item_label = f"{label}.digitChains[{index}]"
        if not isinstance(chain, dict):
            errors.append(f"{item_label} must be an object")
            continue
        chain_id = chain.get("id")
        if not _nonempty_string(chain_id):
            errors.append(f"{item_label}.id is required")
        elif chain_id in ids:
            errors.append(f"{label}.digitChains has duplicate id {chain_id!r}")
        else:
            ids.add(str(chain_id))
        role = chain.get("role")
        if role not in {"thumb", "finger"}:
            errors.append(f"{item_label}.role must be thumb or finger")
        else:
            roles.append(str(role))
        segments = chain.get("segmentCount")
        if not isinstance(segments, int) or isinstance(segments, bool) or not 1 <= segments <= 4:
            errors.append(f"{item_label}.segmentCount must be an integer from 1 to 4")
        refs = _string_array(
            chain.get("componentRefs"),
            f"{item_label}.componentRefs",
            errors,
            required=True,
        )
        if (
            requires_articulation_parts
            and
            isinstance(segments, int)
            and not isinstance(segments, bool)
            and 1 <= segments <= 4
            and len(refs) != segments
        ):
            errors.append(
                f"{item_label}.componentRefs must contain exactly segmentCount articulatable geometry parts"
            )
        for component_id in refs:
            component = components_by_id.get(component_id)
            if component is None:
                errors.append(
                    f"{item_label}.componentRefs references unknown component {component_id!r}"
                )
            elif component_id not in region_components:
                errors.append(
                    f"{item_label}.componentRefs must stay inside the specialized region"
                )
            elif component.get("componentType", "part") != "part":
                errors.append(f"{item_label}.componentRefs must reference geometry parts")
            elif requires_articulation_parts and component_id in used_components:
                errors.append(
                    f"{item_label}.componentRefs reuses geometry from another digit chain"
                )
            else:
                used_components.add(component_id)
                if requires_articulation_parts:
                    errors.extend(
                        f"{item_label} component {component_id!r} {failure}"
                        for failure in _articulation_contract_failures(component)
                    )
        _string_array(chain.get("criteria"), f"{item_label}.criteria", errors, required=True)
    if roles.count("thumb") != 1:
        errors.append(f"{label}.digitChains must contain exactly one thumb chain")
    if "finger" not in roles:
        errors.append(f"{label}.digitChains must contain at least one finger chain")


def _validate_hand_interaction(
    region: dict[str, Any],
    label: str,
    known_components: set[str],
    components_by_id: dict[str, dict[str, Any]],
    region_components: set[str],
    target_component_refs: set[str],
    errors: list[str],
) -> bool:
    interaction = region.get("interaction")
    if interaction is None:
        return False
    if not isinstance(interaction, dict):
        errors.append(f"{label}.interaction must be an object or null")
        return False
    if not _nonempty_string(interaction.get("type")):
        errors.append(f"{label}.interaction.type is required")
    target = interaction.get("targetComponentRef")
    if not _nonempty_string(target):
        errors.append(f"{label}.interaction.targetComponentRef is required")
    elif target not in known_components:
        errors.append(
            f"{label}.interaction.targetComponentRef references unknown component {target!r}"
        )
    elif components_by_id[str(target)].get("componentType", "part") != "part":
        errors.append(f"{label}.interaction.targetComponentRef must reference a geometry part")
    elif target not in target_component_refs:
        errors.append(
            f"{label} feature target must include interaction component {target!r}"
        )
    contacts = _string_array(
        interaction.get("contactComponentRefs"),
        f"{label}.interaction.contactComponentRefs",
        errors,
        required=True,
    )
    for component_id in contacts:
        if component_id not in known_components:
            errors.append(
                f"{label}.interaction.contactComponentRefs references unknown component "
                f"{component_id!r}"
            )
        elif component_id not in region_components:
            errors.append(
                f"{label}.interaction.contactComponentRefs must stay inside the hand region"
            )
        elif components_by_id[component_id].get("componentType", "part") != "part":
            errors.append(
                f"{label}.interaction.contactComponentRefs must reference geometry parts"
            )
    _string_array(
        interaction.get("criteria"),
        f"{label}.interaction.criteria",
        errors,
        required=True,
    )
    return True


def validate_specialized_regions(
    spec: dict[str, Any],
    errors: list[str],
    warnings: list[str],
) -> None:
    """Validate optional face/hand contracts without breaking ordinary props."""

    assessment = spec.get("preSpecAssessment")
    if not isinstance(assessment, dict):
        return
    block = assessment.get("specializedRegions")
    character_like = _character_like_signal(spec)
    if block is None:
        if character_like:
            warnings.append(
                "quality: character-like target needs preSpecAssessment.specializedRegions "
                "assessment for visible faces and hands"
            )
        return
    if not isinstance(block, dict):
        errors.append("preSpecAssessment.specializedRegions must be an object")
        return
    status = block.get("status")
    if status not in VALID_SPECIALIZED_REGION_STATUSES:
        errors.append(
            "preSpecAssessment.specializedRegions.status must be unassessed, none, or declared"
        )
    notes = block.get("notes")
    if notes is not None and not isinstance(notes, str):
        errors.append("preSpecAssessment.specializedRegions.notes must be a string")
    regions = block.get("regions")
    if not isinstance(regions, list):
        errors.append("preSpecAssessment.specializedRegions.regions must be an array")
        return
    if status == "unassessed":
        warnings.append(
            "quality: preSpecAssessment.specializedRegions is unassessed; explicitly declare "
            "visible face/hand regions or set status to none with a reason"
        )
    elif status == "none":
        if regions:
            errors.append(
                "preSpecAssessment.specializedRegions.regions must be empty when status is none"
            )
        if character_like and not _meaningful_none_reason(notes):
            warnings.append(
                "quality: character-like target marked with no specialized regions needs a reason"
            )
        return
    elif status == "declared" and not regions:
        errors.append(
            "preSpecAssessment.specializedRegions.regions must not be empty when status is declared"
        )
        return
    if status != "declared":
        return

    component_items = spec.get("componentTree")
    components_by_id = {
        str(item.get("id")): item
        for item in (component_items if isinstance(component_items, list) else [])
        if isinstance(item, dict) and _nonempty_string(item.get("id"))
    }
    known_components = set(components_by_id)
    evidence_items = spec.get("viewEvidence")
    evidence_by_id = {
        str(item.get("id")): item
        for item in (evidence_items if isinstance(evidence_items, list) else [])
        if isinstance(item, dict) and _nonempty_string(item.get("id"))
    }
    target_items = spec.get("featureReviewTargets")
    target_by_id = {
        str(item.get("id")): item
        for item in (target_items if isinstance(target_items, list) else [])
        if isinstance(item, dict) and _nonempty_string(item.get("id"))
    }
    pass_items = spec.get("buildPasses")
    known_passes = {
        str(item.get("id"))
        for item in (pass_items if isinstance(pass_items, list) else [])
        if isinstance(item, dict) and _nonempty_string(item.get("id"))
    }
    loop = spec.get("selfCorrectLoop")
    acceptance = loop.get("visualAcceptance") if isinstance(loop, dict) else None
    policy = acceptance.get("featureReviewPolicy") if isinstance(acceptance, dict) else None
    default_threshold = (
        float(policy.get("criticalDefaultThreshold"))
        if isinstance(policy, dict) and _is_number(policy.get("criticalDefaultThreshold"))
        else 0.8
    )

    region_ids: set[str] = set()
    feature_target_ids: set[str] = set()
    requires_articulation_parts = spec.get("intendedUse") in {
        "animated",
        "playable",
        "destructible",
    }
    for index, region in enumerate(regions):
        label = f"preSpecAssessment.specializedRegions.regions[{index}]"
        if not isinstance(region, dict):
            errors.append(f"{label} must be an object")
            continue
        region_id = region.get("id")
        if not _nonempty_string(region_id):
            errors.append(f"{label}.id is required")
        elif region_id in region_ids:
            errors.append(f"duplicate specialized region id {region_id!r}")
        else:
            region_ids.add(str(region_id))
        kind = region.get("kind")
        if kind not in VALID_SPECIALIZED_REGION_KINDS:
            errors.append(f"{label}.kind must be face or hand")
            kind = ""
        if not _nonempty_string(region.get("name")):
            errors.append(f"{label}.name is required")
        if not _nonempty_string(region.get("representation")):
            errors.append(f"{label}.representation is required")
        visibility = region.get("visibility")
        if visibility not in VALID_REGION_VISIBILITY:
            errors.append(f"{label}.visibility must be clear, partial, or occluded")
            visibility = ""
        _unit_interval(region.get("confidence"), f"{label}.confidence", errors)
        handling = region.get("occlusionHandling")
        if handling not in VALID_OCCLUSION_HANDLING:
            errors.append(
                f"{label}.occlusionHandling must be one of: "
                + ", ".join(sorted(VALID_OCCLUSION_HANDLING))
            )

        component_refs = _string_array(
            region.get("componentRefs"),
            f"{label}.componentRefs",
            errors,
            required=visibility in {"clear", "partial"},
        )
        assembly_ref = region.get("assemblyRef")
        if visibility in {"clear", "partial"}:
            if not _nonempty_string(assembly_ref):
                errors.append(f"{label}.assemblyRef is required for a visible region")
            elif assembly_ref not in components_by_id:
                errors.append(f"{label}.assemblyRef references unknown component {assembly_ref!r}")
            elif components_by_id[str(assembly_ref)].get("componentType", "part") != "assembly":
                errors.append(f"{label}.assemblyRef must reference an assembly component")
            else:
                if assembly_ref not in component_refs:
                    errors.append(f"{label}.componentRefs must include assemblyRef {assembly_ref!r}")
                outside = [
                    component_id
                    for component_id in component_refs
                    if not _is_descendant(component_id, str(assembly_ref), components_by_id)
                ]
                if outside:
                    errors.append(
                        f"{label}.componentRefs must stay inside assemblyRef {assembly_ref!r}: "
                        + ", ".join(sorted(outside))
                    )
        evidence_refs = _string_array(
            region.get("evidenceRefs"),
            f"{label}.evidenceRefs",
            errors,
            required=True,
        )
        review_view_ids = _string_array(
            region.get("reviewViewIds"),
            f"{label}.reviewViewIds",
            errors,
            required=visibility in {"clear", "partial"},
        )
        unknowns = _string_array(
            region.get("unknowns"),
            f"{label}.unknowns",
            errors,
        )
        if visibility in {"partial", "occluded"} and not unknowns:
            errors.append(f"{label}.unknowns must describe hidden or ambiguous anatomy")
        if visibility == "occluded" and handling not in {"request-input", "omit-hidden-detail"}:
            errors.append(
                f"{label}.occlusionHandling must request input or omit hidden detail when occluded"
            )
        for component_id in component_refs:
            if component_id not in known_components:
                errors.append(f"{label}.componentRefs references unknown component {component_id!r}")
        for evidence_id in [*evidence_refs, *review_view_ids]:
            if evidence_id not in evidence_by_id:
                errors.append(f"{label} references unknown viewEvidence {evidence_id!r}")
        if visibility in {"clear", "partial"}:
            for view_id in review_view_ids:
                evidence = evidence_by_id.get(view_id)
                if view_id == "full-object":
                    errors.append(
                        f"{label}.reviewViewIds must use a dedicated region view, not full-object"
                    )
                if not isinstance(evidence, dict):
                    continue
                image_region = evidence.get("imageRegion")
                if not isinstance(image_region, dict):
                    errors.append(f"viewEvidence {view_id!r} needs imageRegion for region review")
                observations = evidence.get("observations")
                if not isinstance(observations, list) or not any(
                    _nonempty_string(item) for item in observations
                ):
                    errors.append(
                        f"viewEvidence {view_id!r} needs concrete face/hand observations"
                    )

        articulation_mode: str | None = None
        if kind == "hand":
            articulation_mode = region.get("articulationMode")
            if articulation_mode not in VALID_HAND_ARTICULATION_MODES:
                errors.append(
                    f"{label}.articulationMode must be one of: "
                    + ", ".join(sorted(VALID_HAND_ARTICULATION_MODES))
                )
            if articulation_mode == "silhouette-only" and visibility == "clear":
                errors.append(
                    f"{label}.articulationMode silhouette-only cannot be used for a clear hand"
                )
            if articulation_mode == "hidden" and visibility != "occluded":
                errors.append(f"{label}.articulationMode hidden requires occluded visibility")
            if articulation_mode == "explicit-digits":
                _validate_digit_chains(
                    region,
                    label,
                    components_by_id,
                    set(component_refs),
                    requires_articulation_parts,
                    errors,
                )

        _validate_landmarks(
            region,
            label,
            str(kind),
            str(visibility),
            articulation_mode,
            requires_articulation_parts,
            components_by_id,
            set(component_refs),
            errors,
        )
        _validate_constraints(
            region,
            label,
            str(kind),
            str(visibility),
            known_components,
            set(component_refs),
            errors,
        )

        feature_target_id = region.get("featureTargetId")
        target: dict[str, Any] = {}
        if visibility in {"clear", "partial"}:
            if not _nonempty_string(feature_target_id):
                errors.append(f"{label}.featureTargetId is required for a visible region")
            elif feature_target_id in feature_target_ids:
                errors.append(
                    f"specialized regions must not share feature target {feature_target_id!r}"
                )
            elif feature_target_id not in target_by_id:
                errors.append(
                    f"{label}.featureTargetId references unknown target {feature_target_id!r}"
                )
            else:
                feature_target_ids.add(str(feature_target_id))
                target = target_by_id[str(feature_target_id)]
                if target.get("tier") != "critical" or target.get("mustPass") is not True:
                    errors.append(
                        f"{label} feature target must be critical and mustPass"
                    )
                if target.get("requiresDedicatedEvidence") is not True:
                    errors.append(
                        f"{label} feature target requiresDedicatedEvidence must be true"
                    )
                raw_target_components = target.get("componentRefs")
                target_components = (
                    set(raw_target_components) if isinstance(raw_target_components, list) else set()
                )
                missing_components = set(component_refs) - target_components
                if missing_components:
                    errors.append(
                        f"{label} feature target is missing componentRefs: "
                        + ", ".join(sorted(missing_components))
                    )
                raw_target_evidence = target.get("evidenceRefs")
                target_evidence = (
                    set(raw_target_evidence) if isinstance(raw_target_evidence, list) else set()
                )
                missing_evidence = set(evidence_refs) - target_evidence
                if missing_evidence:
                    errors.append(
                        f"{label} feature target is missing evidenceRefs: "
                        + ", ".join(sorted(missing_evidence))
                    )
                raw_target_views = target.get("reviewViewIds")
                target_views = (
                    set(raw_target_views) if isinstance(raw_target_views, list) else set()
                )
                missing_views = set(review_view_ids) - target_views
                if missing_views:
                    errors.append(
                        f"{label} feature target is missing reviewViewIds: "
                        + ", ".join(sorted(missing_views))
                    )
                minimum = target.get("minimumScore", default_threshold)
                if not _is_number(minimum) or float(minimum) < default_threshold:
                    errors.append(
                        f"{label} feature target minimumScore cannot be lower than "
                        f"{default_threshold}"
                    )
                raw_target_passes = target.get("passIds")
                target_passes = (
                    set(raw_target_passes) if isinstance(raw_target_passes, list) else set()
                )
                required_passes = {
                    pass_id
                    for pass_id in ("form", "lookdev", "optimization")
                    if pass_id in known_passes
                }
                missing_passes = required_passes - target_passes
                if missing_passes:
                    errors.append(
                        f"{label} feature target is missing passIds: "
                        + ", ".join(sorted(missing_passes))
                    )
                _string_array(
                    target.get("criteria"),
                    f"{label} feature target criteria",
                    errors,
                    required=True,
                )

        if kind == "hand":
            has_interaction = _validate_hand_interaction(
                region,
                label,
                known_components,
                components_by_id,
                set(component_refs),
                (
                    set(target.get("componentRefs", []))
                    if isinstance(target.get("componentRefs"), list)
                    else set()
                ),
                errors,
            )
            if has_interaction and "structure" in known_passes:
                target_pass_ids = target.get("passIds")
                if "structure" not in (
                    set(target_pass_ids) if isinstance(target_pass_ids, list) else set()
                ):
                    errors.append(
                        f"{label} interacting hand feature target must include the structure pass"
                    )


def specialized_regions_payload(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """Return declared region metadata for generated-model debugging."""

    assessment = spec.get("preSpecAssessment")
    block = assessment.get("specializedRegions") if isinstance(assessment, dict) else None
    if not isinstance(block, dict) or block.get("status") != "declared":
        return []
    regions = block.get("regions")
    return [item for item in regions if isinstance(item, dict)] if isinstance(regions, list) else []
