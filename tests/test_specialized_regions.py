from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from generate_threejs_factory import generate  # noqa: E402
from new_sculpt_spec import make_spec  # noqa: E402
from sculpt_contract import review_spec_hash  # noqa: E402
from validate_sculpt_spec import validate_spec  # noqa: E402
from visual_feature_gate import feature_gate_failures  # noqa: E402


def _assembly(component_id: str, parent: str) -> dict[str, Any]:
    return {
        "id": component_id,
        "name": component_id.replace("-", " ").title(),
        "componentType": "assembly",
        "level": "meso",
        "role": "assembly",
        "importance": 0.95,
        "confidence": 0.9,
        "parent": parent,
        "transform": {
            "position": [0, 0, 0],
            "rotation": [0, 0, 0],
            "scale": [1, 1, 1],
        },
        "actionProfile": {
            "animationRole": "static",
            "transformChannels": {
                "translate": True,
                "rotate": True,
                "scale": True,
                "visibility": True,
            },
            "sockets": [],
            "constraints": [],
        },
        "evidenceRefs": ["full-object"],
    }


def _part(spec: dict[str, Any], component_id: str, parent: str, evidence_id: str) -> dict[str, Any]:
    part = copy.deepcopy(spec["componentTree"][0])
    part.update(
        {
            "id": component_id,
            "name": component_id.replace("-", " ").title(),
            "componentType": "part",
            "level": "meso",
            "role": "anatomical-form",
            "importance": 0.9,
            "confidence": 0.88,
            "parent": parent,
            "primitive": "sphere",
            "evidenceRefs": [evidence_id],
            "fidelityTier": "form",
        }
    )
    part["geometryDescriptor"]["parameters"] = {}
    part["actionProfile"]["animationRole"] = "static"
    return part


def _landmark(
    landmark_id: str,
    role: str,
    component_refs: list[str],
) -> dict[str, Any]:
    return {
        "id": landmark_id,
        "role": role,
        "componentRefs": component_refs,
        "criteria": [f"Match the reference {role} placement, shape, and local proportion."],
        "visible": True,
        "confidence": 0.9,
    }


def character_spec() -> dict[str, Any]:
    spec = make_spec(
        "Musician Mascot",
        None,
        complexity="complex",
        intended_use="static-render",
        quality_profile="reference-fidelity",
    )
    spec["preSpecAssessment"]["objectClass"].update(
        {
            "primaryType": "stylized animal mascot",
            "formLanguage": ["character-like", "rounded organic"],
            "structureKind": ["nested assemblies"],
            "motionPotential": ["static pose"],
            "materialFamilies": ["fur", "cloth", "painted wood"],
        }
    )
    spec["silhouette"].update(
        {
            "boundingShape": "upright mascot holding an instrument",
            "aspectRatios": ["width:height=0.75:1"],
            "dominantCurves": ["round head", "tapered torso"],
        }
    )
    spec["viewEvidence"].extend(
        [
            {
                "id": "face-closeup",
                "view": "front face crop",
                "imageRegion": {
                    "x": 0.28,
                    "y": 0.05,
                    "width": 0.44,
                    "height": 0.34,
                    "units": "normalized",
                },
                "observations": ["Eye line, gaze, muzzle, smile, cheeks, and glasses are readable."],
                "confidence": 0.94,
            },
            {
                "id": "grip-hand-closeup",
                "view": "instrument grip crop",
                "imageRegion": {
                    "x": 0.24,
                    "y": 0.40,
                    "width": 0.24,
                    "height": 0.28,
                    "units": "normalized",
                },
                "observations": ["The paw wraps around the instrument neck with visible overlap."],
                "confidence": 0.86,
            },
        ]
    )

    component_ids = [
        ("face-shell", "face-region", "face-closeup"),
        ("eye-system", "face-region", "face-closeup"),
        ("muzzle-nose", "face-region", "face-closeup"),
        ("mouth-smile", "face-region", "face-closeup"),
        ("grip-wrist", "grip-hand", "grip-hand-closeup"),
        ("grip-palm", "grip-hand", "grip-hand-closeup"),
        ("grip-digits", "grip-hand", "grip-hand-closeup"),
        ("instrument-neck", "root", "full-object"),
    ]
    spec["componentTree"].extend(
        [
            _assembly("face-region", "root"),
            _assembly("grip-hand", "root"),
            *[
                _part(spec, component_id, parent, evidence_id)
                for component_id, parent, evidence_id in component_ids
            ],
        ]
    )

    face_components = [
        "face-region",
        "face-shell",
        "eye-system",
        "muzzle-nose",
        "mouth-smile",
    ]
    hand_components = ["grip-hand", "grip-wrist", "grip-palm", "grip-digits"]
    spec["preSpecAssessment"]["specializedRegions"] = {
        "status": "declared",
        "notes": "The face defines identity; the visible paw-to-instrument contact defines the pose.",
        "regions": [
            {
                "id": "primary-face",
                "kind": "face",
                "name": "Primary face and expression",
                "representation": "stylized-animal",
                "visibility": "clear",
                "confidence": 0.93,
                "occlusionHandling": "model-visible-only",
                "assemblyRef": "face-region",
                "componentRefs": face_components,
                "evidenceRefs": ["face-closeup"],
                "reviewViewIds": ["face-closeup"],
                "featureTargetId": "primary-face-identity",
                "unknowns": [],
                "landmarks": [
                    _landmark("face-outline", "face-contour", ["face-shell"]),
                    _landmark("eyes-and-gaze", "eye-system", ["eye-system"]),
                    _landmark("nose-and-muzzle", "nose-muzzle", ["muzzle-nose"]),
                    _landmark("smile-and-mouth", "mouth-expression", ["mouth-smile"]),
                ],
                "constraints": [
                    {
                        "id": "face-proportions",
                        "type": "proportion",
                        "description": "Preserve eye spacing, muzzle width, cheek height, and jaw taper.",
                        "componentRefs": face_components,
                    },
                    {
                        "id": "face-expression",
                        "type": "expression",
                        "description": "Preserve the open friendly smile and forward attentive gaze.",
                        "componentRefs": ["eye-system", "mouth-smile"],
                    },
                ],
            },
            {
                "id": "instrument-grip-hand",
                "kind": "hand",
                "name": "Instrument grip paw",
                "representation": "stylized-paw",
                "articulationMode": "grouped-digits",
                "visibility": "clear",
                "confidence": 0.84,
                "occlusionHandling": "bounded-inference",
                "assemblyRef": "grip-hand",
                "componentRefs": hand_components,
                "evidenceRefs": ["grip-hand-closeup"],
                "reviewViewIds": ["grip-hand-closeup"],
                "featureTargetId": "instrument-grip-contact",
                "unknowns": [],
                "landmarks": [
                    _landmark("grip-wrist-form", "wrist", ["grip-wrist"]),
                    _landmark("grip-palm-form", "palm", ["grip-palm"]),
                    _landmark("grouped-digits-form", "digit-mass", ["grip-digits"]),
                    _landmark("grip-outer-contour", "outer-contour", ["grip-digits"]),
                    _landmark("instrument-contact", "pose-contact", ["grip-palm", "grip-digits"]),
                ],
                "constraints": [
                    {
                        "id": "paw-proportion",
                        "type": "proportion",
                        "description": "Keep the palm-to-wrist ratio and tapered grouped digit mass.",
                        "componentRefs": hand_components,
                    },
                    {
                        "id": "paw-grip-pose",
                        "type": "pose",
                        "description": "The paw curls around the neck instead of touching it as a sphere.",
                        "componentRefs": ["grip-palm", "grip-digits"],
                    },
                ],
                "interaction": {
                    "type": "grip",
                    "targetComponentRef": "instrument-neck",
                    "contactComponentRefs": ["grip-palm", "grip-digits"],
                    "criteria": [
                        "Visible overlap and negative space must read as a stable grip without floating or penetration."
                    ],
                },
            },
        ],
    }
    spec["featureReviewTargets"].extend(
        [
            {
                "id": "primary-face-identity",
                "name": "Primary face, gaze, and expression",
                "tier": "critical",
                "passIds": ["form", "lookdev"],
                "minimumScore": 0.85,
                "mustPass": True,
                "requiresDedicatedEvidence": True,
                "componentRefs": face_components,
                "evidenceRefs": ["face-closeup"],
                "reviewViewIds": ["face-closeup"],
                "criteria": [
                    "Face contour, eye spacing and gaze, muzzle, mouth shape, and expression match the crop."
                ],
            },
            {
                "id": "instrument-grip-contact",
                "name": "Instrument grip paw and contact",
                "tier": "critical",
                "passIds": ["structure", "form", "lookdev"],
                "minimumScore": 0.85,
                "mustPass": True,
                "requiresDedicatedEvidence": True,
                "componentRefs": [*hand_components, "instrument-neck"],
                "evidenceRefs": ["grip-hand-closeup"],
                "reviewViewIds": ["grip-hand-closeup"],
                "criteria": [
                    "Wrist, palm, grouped digits, grip pose, and contact with the instrument match the crop."
                ],
            },
        ]
    )
    return spec


def _form_review(spec: dict[str, Any]) -> dict[str, Any]:
    targets = [
        target
        for target in spec["featureReviewTargets"]
        if "form" in target.get("passIds", [])
    ]
    return {
        "featureReviews": [
            {
                "id": target["id"],
                "score": 0.9,
                "visible": True,
                "viewIds": target.get("reviewViewIds", []),
            }
            for target in targets
        ],
        "evidence": {
            "views": [
                {"viewId": "full-object"},
                {"viewId": "face-closeup"},
                {"viewId": "grip-hand-closeup"},
            ]
        },
    }


class SpecializedRegionContractTests(unittest.TestCase):
    def test_character_target_cannot_skip_specialized_region_assessment(self) -> None:
        spec = make_spec(
            "Character",
            None,
            complexity="complex",
            intended_use="static-render",
            quality_profile="reference-fidelity",
        )
        spec["preSpecAssessment"]["objectClass"]["primaryType"] = "humanoid character"
        _, warnings = validate_spec(spec)
        self.assertTrue(any("specializedRegions is unassessed" in item for item in warnings))

        spec["preSpecAssessment"]["specializedRegions"]["status"] = "none"
        _, warnings = validate_spec(spec)
        self.assertTrue(any("marked with no specialized regions needs a reason" in item for item in warnings))

    def test_face_and_interacting_hand_contract_validate(self) -> None:
        errors, _ = validate_spec(character_spec())
        self.assertEqual(errors, [])

    def test_clear_face_rejects_missing_identity_landmark(self) -> None:
        spec = character_spec()
        face = spec["preSpecAssessment"]["specializedRegions"]["regions"][0]
        face["landmarks"] = [
            landmark for landmark in face["landmarks"] if landmark["role"] != "mouth-expression"
        ]
        errors, _ = validate_spec(spec)
        self.assertTrue(any("mouth-expression" in item for item in errors))

        spec = character_spec()
        face = spec["preSpecAssessment"]["specializedRegions"]["regions"][0]
        face["landmarks"][1]["componentRefs"] = ["instrument-neck"]
        errors, _ = validate_spec(spec)
        self.assertTrue(any("must stay inside the specialized region" in item for item in errors))

    def test_clear_face_landmarks_may_share_one_continuous_geometry_host(self) -> None:
        spec = character_spec()
        face = spec["preSpecAssessment"]["specializedRegions"]["regions"][0]
        for landmark in face["landmarks"]:
            landmark["componentRefs"] = ["face-shell"]
        errors, _ = validate_spec(spec)
        self.assertFalse(
            any("landmarks must map a clear face" in item for item in errors),
            errors,
        )

    def test_clear_hand_rejects_silhouette_only_blob(self) -> None:
        spec = character_spec()
        hand = spec["preSpecAssessment"]["specializedRegions"]["regions"][1]
        hand["articulationMode"] = "silhouette-only"
        errors, _ = validate_spec(spec)
        self.assertTrue(any("silhouette-only cannot be used for a clear hand" in item for item in errors))

    def test_explicit_digit_chain_requires_real_segment_components(self) -> None:
        spec = character_spec()
        spec["intendedUse"] = "animated"
        hand = spec["preSpecAssessment"]["specializedRegions"]["regions"][1]
        thumb = _part(spec, "grip-thumb", "grip-hand", "grip-hand-closeup")
        spec["componentTree"].append(thumb)
        hand["componentRefs"].append("grip-thumb")
        hand["articulationMode"] = "explicit-digits"
        hand["landmarks"] = [
            _landmark("grip-wrist-form", "wrist", ["grip-wrist"]),
            _landmark("grip-palm-form", "palm", ["grip-palm"]),
            _landmark("grip-thumb-form", "thumb", ["grip-thumb"]),
            _landmark("grip-digits-form", "digits", ["grip-digits"]),
            _landmark("grip-joint-arc", "joint-arc", ["grip-digits"]),
        ]
        hand["digitChains"] = [
            {
                "id": "thumb-chain",
                "role": "thumb",
                "segmentCount": 2,
                "componentRefs": ["grip-thumb"],
                "criteria": ["Preserve thumb opposition and two visible segments."],
            },
            {
                "id": "grouped-finger-chain",
                "role": "finger",
                "segmentCount": 1,
                "componentRefs": ["grip-digits"],
                "criteria": ["Preserve the visible grouped finger curl."],
            },
        ]
        target = next(
            item for item in spec["featureReviewTargets"] if item["id"] == "instrument-grip-contact"
        )
        target["componentRefs"].append("grip-thumb")
        errors, _ = validate_spec(spec)
        self.assertTrue(any("exactly segmentCount articulatable" in item for item in errors))

        thumb_tip = _part(spec, "grip-thumb-tip", "grip-hand", "grip-hand-closeup")
        spec["componentTree"].append(thumb_tip)
        hand["componentRefs"].append("grip-thumb-tip")
        hand["digitChains"][0]["componentRefs"] = ["grip-thumb", "grip-thumb-tip"]
        target["componentRefs"].append("grip-thumb-tip")
        errors, _ = validate_spec(spec)
        self.assertTrue(
            any("must declare a non-static animationRole" in item for item in errors),
            errors,
        )

        for component_id in (
            "grip-wrist",
            "grip-palm",
            "grip-thumb",
            "grip-thumb-tip",
            "grip-digits",
        ):
            component = next(
                item for item in spec["componentTree"] if item.get("id") == component_id
            )
            component["actionProfile"] = {
                "animationRole": "digit-joint",
                "pivot": {
                    "mode": "hinge",
                    "localPosition": [0, 0, 0],
                    "axis": [0, 0, 1],
                    "confidence": 0.9,
                },
                "transformChannels": {
                    "translate": False,
                    "rotate": True,
                    "scale": False,
                    "visibility": True,
                },
                "sockets": [],
                "constraints": [],
            }
        errors, _ = validate_spec(spec)
        self.assertFalse(
            any(
                "action-ready component" in item
                or "must declare a non-static animationRole" in item
                or "must enable actionProfile.transformChannels.rotate" in item
                or "needs an actionProfile.pivot" in item
                for item in errors
            ),
            errors,
        )

    def test_static_hand_semantics_may_share_one_continuous_geometry_host(self) -> None:
        spec = character_spec()
        hand = spec["preSpecAssessment"]["specializedRegions"]["regions"][1]
        hand["articulationMode"] = "explicit-digits"
        hand["landmarks"] = [
            _landmark("grip-wrist-form", "wrist", ["grip-palm"]),
            _landmark("grip-palm-form", "palm", ["grip-palm"]),
            _landmark("grip-thumb-form", "thumb", ["grip-palm"]),
            _landmark("grip-digits-form", "digits", ["grip-palm"]),
            _landmark("grip-joint-arc", "joint-arc", ["grip-palm"]),
        ]
        hand["digitChains"] = [
            {
                "id": "thumb-chain",
                "role": "thumb",
                "segmentCount": 2,
                "componentRefs": ["grip-palm"],
                "criteria": ["Preserve thumb opposition and both visible bends."],
            },
            {
                "id": "finger-chain",
                "role": "finger",
                "segmentCount": 3,
                "componentRefs": ["grip-palm"],
                "criteria": ["Preserve the visible finger curl on the continuous host."],
            },
        ]
        errors, _ = validate_spec(spec)
        self.assertFalse(
            any(
                "articulatable geometry" in item or "reuses geometry" in item
                for item in errors
            ),
            errors,
        )

    def test_specialized_feature_needs_bound_closeup_and_passes_independently(self) -> None:
        spec = character_spec()
        entry = _form_review(spec)
        entry["evidence"]["views"] = [
            view for view in entry["evidence"]["views"] if view["viewId"] != "face-closeup"
        ]
        failures = feature_gate_failures(spec, entry, "form")
        self.assertTrue(any("primary-face-identity" in item and "missing dedicated" in item for item in failures))

        entry["evidence"]["views"].append({"viewId": "face-closeup"})
        face_review = next(
            item for item in entry["featureReviews"] if item["id"] == "primary-face-identity"
        )
        face_review["viewIds"] = []
        failures = feature_gate_failures(spec, entry, "form")
        self.assertTrue(any("primary-face-identity" in item and "not bound" in item for item in failures))

        face_review["viewIds"] = ["face-closeup"]
        hand_review = next(
            item for item in entry["featureReviews"] if item["id"] == "instrument-grip-contact"
        )
        hand_review["score"] = 0.6
        failures = feature_gate_failures(spec, entry, "form")
        self.assertTrue(any("instrument-grip-contact" in item and "below" in item for item in failures))

        hand_review["score"] = 0.9
        self.assertEqual(feature_gate_failures(spec, entry, "form"), [])

    def test_region_contract_is_hashed_and_exported_to_generated_runtime(self) -> None:
        spec = character_spec()
        before = review_spec_hash(spec, "form")
        spec["preSpecAssessment"]["specializedRegions"]["regions"][0]["constraints"][0][
            "description"
        ] += " Preserve cheek-to-eye height."
        after = review_spec_hash(spec, "form")
        self.assertNotEqual(before, after)
        generated = generate(spec, "form")
        self.assertIn("root.userData.specializedRegions", generated)
        self.assertIn("primary-face", generated)


if __name__ == "__main__":
    unittest.main()
