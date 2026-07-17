from __future__ import annotations

import copy
import re
import sys
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from generate_threejs_factory import generate  # noqa: E402
from migrate_sculpt_spec import migrate_spec  # noqa: E402
from new_sculpt_spec import make_spec  # noqa: E402
from sculpt_contract import (  # noqa: E402
    parse_schema_version,
    pass_order,
    schema_version_at_least,
)
from sculpt_geometry import (  # noqa: E402
    MAX_GEOMETRY_SEGMENTS,
    MAX_INSTANCE_COUNT,
    validate_repetition_systems,
)
from validate_sculpt_spec import validate_spec  # noqa: E402


def _fill_pre_spec(spec: dict[str, Any]) -> None:
    spec["preSpecAssessment"]["objectClass"].update(
        {
            "primaryType": "compound musician mascot",
            "formLanguage": ["character-like", "hard-surface accessory"],
            "structureKind": ["nested assemblies", "mixed procedural parts"],
            "motionPotential": ["stable static hierarchy"],
            "materialFamilies": ["painted polymer", "coated metal"],
        }
    )
    spec["silhouette"].update(
        {
            "boundingShape": "upright rounded mascot carrying a small instrument",
            "aspectRatios": ["width:height=0.72:1"],
            "dominantCurves": ["rounded head and torso", "curved instrument body"],
        }
    )


def _assembly(
    component_id: str,
    parent: str | None,
    *,
    level: str = "macro",
) -> dict[str, Any]:
    return {
        "id": component_id,
        "name": component_id.replace("-", " ").title(),
        "componentType": "assembly",
        "level": level,
        "role": "assembly",
        "importance": 0.9,
        "confidence": 0.8,
        "parent": parent,
        "transform": {
            "position": [0, 0, 0],
            "rotation": [0, 0, 0],
            "scale": [1, 1, 1],
        },
        "actionProfile": {
            "animationRole": "root" if parent is None else "static",
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


def _part(
    component_id: str,
    parent: str,
    primitive: str,
    parameters: dict[str, Any] | None,
    *,
    level: str = "meso",
    dimensions: dict[str, Any] | None = None,
) -> dict[str, Any]:
    descriptor: dict[str, Any] = {
        "topologyIntent": f"procedural {primitive} geometry",
        "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1},
        "deformationStack": [],
        "uvStrategy": "generated procedural coordinates",
        "normalStrategy": "generated vertex normals",
    }
    if parameters is not None:
        descriptor["parameters"] = parameters
    return {
        "id": component_id,
        "name": component_id.replace("-", " ").title(),
        "componentType": "part",
        "level": level,
        "role": "surface detail" if level == "micro" else "body",
        "importance": 0.82,
        "confidence": 0.78,
        "primitive": primitive,
        "geometryDescriptor": descriptor,
        "parent": parent,
        "attachment": None,
        "dimensions": dimensions
        or {
            "width": 0.4,
            "height": 0.4,
            "depth": 0.2,
            "units": "relative",
            "confidence": 0.78,
        },
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
        "material": "base",
        "materialLayers": ["base"],
        "deformations": [],
        "joints": [],
        "seams": [],
        "localFeatures": [],
        "surfaceDetail": {
            "macroRoughness": 0.1,
            "microRoughness": 0.1,
            "bumpAmplitude": 0.0,
            "normalPattern": "intentionally smooth",
        },
        "evidenceRefs": ["full-object"],
        "details": [],
        "fidelityTier": "form",
    }


def compound_musician_mascot_spec() -> dict[str, Any]:
    """A path-independent fixture inspired by, but not coupled to, the supplied image."""
    spec = make_spec(
        "Compound Musician Mascot",
        None,
        complexity="complex",
        intended_use="static-render",
    )
    spec["schemaVersion"] = "3.1"
    _fill_pre_spec(spec)
    spec["componentTree"] = [
        _assembly("root", None),
        _assembly("mascot-rig", "root"),
        _part(
            "torso",
            "mascot-rig",
            "capsule",
            {},
            level="macro",
            dimensions={"radius": 0.42, "length": 0.85, "units": "relative", "confidence": 0.8},
        ),
        _part(
            "glasses-rim",
            "mascot-rig",
            "tube",
            {
                "path": [
                    [-0.22, 0.08, 0.0],
                    [-0.12, 0.18, 0.0],
                    [0.0, 0.20, 0.0],
                    [0.12, 0.18, 0.0],
                    [0.22, 0.08, 0.0],
                ],
                "radius": 0.018,
                "tubularSegments": 32,
                "radialSegments": 8,
                "closed": False,
            },
        ),
        _part(
            "tail-sweep",
            "mascot-rig",
            "curve-sweep",
            {
                "path": [
                    [0.0, 0.0, 0.0],
                    [-0.28, 0.04, -0.02],
                    [-0.52, -0.16, -0.04],
                    [-0.60, -0.42, -0.02],
                ],
                "profile": [[-0.08, 0.0], [0.0, 0.055], [0.08, 0.0], [0.0, -0.055]],
                "pathSegments": 36,
                "closedPath": False,
                "closedProfile": True,
            },
        ),
        _assembly("instrument", "root"),
        _part(
            "instrument-body",
            "instrument",
            "extrude",
            {
                "shape": [
                    [-0.24, -0.42],
                    [0.18, -0.42],
                    [0.30, -0.12],
                    [0.22, 0.32],
                    [0.0, 0.44],
                    [-0.25, 0.30],
                    [-0.31, -0.10],
                ],
                "holes": [],
                "depth": 0.12,
                "curveSegments": 8,
                "bevelEnabled": True,
                "bevelThickness": 0.02,
                "bevelSize": 0.015,
                "bevelSegments": 2,
            },
        ),
        _part(
            "instrument-peg",
            "instrument",
            "lathe",
            {
                "profile": [[0.025, -0.08], [0.05, -0.03], [0.05, 0.03], [0.025, 0.08]],
                "segments": 24,
                "phiStart": 0.0,
                "phiLength": 6.283185307179586,
            },
            level="micro",
        ),
        _part(
            "coat-buttons",
            "mascot-rig",
            "instanced-cluster",
            {
                "repetitionSystemRef": "coat-button-grid",
                "sourcePrimitive": "sphere",
                "sourceParameters": {"radius": 0.015, "widthSegments": 10, "heightSegments": 6},
            },
            level="micro",
        ),
    ]
    spec["repetitionSystems"] = [
        {
            "id": "coat-button-grid",
            "componentRef": "coat-buttons",
            "mode": "grid",
            "count": 1000,
            "seed": 90210,
            "parameters": {
                "columns": 40,
                "rows": 25,
                "spacing": [0.032, 0.032, 0.0],
                "origin": [-0.624, -0.384, 0.0],
            },
        }
    ]
    for build_pass in spec["buildPasses"]:
        if build_pass["id"] in {"structure", "form"}:
            build_pass["componentRefs"] = ["root", "mascot-rig", "instrument"]
    return spec


def _errors_for_part(primitive: str, parameters: dict[str, Any] | None) -> list[str]:
    spec = compound_musician_mascot_spec()
    spec["componentTree"] = [
        _assembly("root", None),
        _part("invalid-part", "root", primitive, parameters),
    ]
    spec["repetitionSystems"] = []
    errors, _ = validate_spec(spec)
    return errors


class CompoundSchemaTests(unittest.TestCase):
    def test_schema_versions_are_compared_numerically(self) -> None:
        self.assertGreater(parse_schema_version("3.10"), parse_schema_version("3.2"))
        self.assertTrue(schema_version_at_least("3.10", "3.2"))
        self.assertFalse(schema_version_at_least("3.2", "3.10"))

    def test_assembly_root_and_mixed_part_hierarchy_validate(self) -> None:
        spec = compound_musician_mascot_spec()
        errors, _ = validate_spec(spec)
        self.assertEqual(errors, [], "\n".join(errors))

        output = generate(spec, "form")
        for assembly_id in ("root", "mascot-rig", "instrument"):
            self.assertRegex(output, rf'nodes\["{re.escape(assembly_id)}"\]\s*=')
            self.assertNotIn(f'meshes["{assembly_id}"]', output)
        self.assertRegex(
            output,
            r'\(nodes\["root"\]\s*\?\?\s*root\)\.add\(node_mascot_rig_',
        )
        self.assertRegex(
            output,
            r'\(nodes\["instrument"\]\s*\?\?\s*root\)\.add\(node_instrument_body_',
        )

    def test_component_type_is_limited_to_part_or_assembly(self) -> None:
        spec = compound_musician_mascot_spec()
        spec["componentTree"][1]["componentType"] = "collection"
        errors, _ = validate_spec(spec)
        self.assertTrue(
            any("mascot-rig" in error and "componentType" in error for error in errors),
            errors,
        )

    def test_assembly_needs_no_primitive_dimensions_or_material(self) -> None:
        assembly = compound_musician_mascot_spec()["componentTree"][0]
        self.assertNotIn("primitive", assembly)
        self.assertNotIn("geometryDescriptor", assembly)
        self.assertNotIn("dimensions", assembly)
        self.assertNotIn("material", assembly)
        errors, _ = validate_spec(compound_musician_mascot_spec())
        self.assertFalse(any("'root'" in error for error in errors), errors)

    def test_legacy_3_0_basic_part_remains_compatible(self) -> None:
        legacy = make_spec(
            "Legacy Basic Prop",
            None,
            complexity="simple",
            intended_use="static-render",
        )
        legacy["schemaVersion"] = "3.0"
        _fill_pre_spec(legacy)
        root = legacy["componentTree"][0]
        root.pop("componentType", None)
        descriptor = root.get("geometryDescriptor")
        if isinstance(descriptor, dict):
            descriptor.pop("parameters", None)

        errors, _ = validate_spec(legacy)
        self.assertEqual(errors, [], "\n".join(errors))
        output = generate(legacy, "blockout")
        self.assertIn("new THREE.BoxGeometry", output)
        self.assertIn('meshes["root"]', output)

    def test_migration_3_0_to_3_1_is_additive_and_preserves_audit_history(self) -> None:
        legacy = make_spec(
            "Legacy Compound Prop",
            None,
            complexity="simple",
            intended_use="static-render",
        )
        legacy["schemaVersion"] = "3.0"
        legacy_root = legacy["componentTree"][0]
        legacy_root.pop("componentType", None)
        legacy_root["geometryDescriptor"].pop("parameters", None)
        legacy["reviewHistory"] = [
            {
                "passId": "blockout",
                "action": "refine-code",
                "summary": "Retained as audit evidence after migration.",
            }
        ]
        original = copy.deepcopy(legacy)

        migrated, report = migrate_spec(legacy)

        self.assertEqual(legacy, original, "migration must not mutate the caller's input")
        self.assertEqual(migrated["schemaVersion"], "3.1")
        self.assertEqual(migrated["componentTree"][0]["componentType"], "part")
        self.assertEqual(
            migrated["componentTree"][0]["geometryDescriptor"]["parameters"],
            {},
        )
        self.assertEqual(migrated["reviewHistory"], original["reviewHistory"])
        self.assertTrue(report["changed"])
        self.assertTrue(report["reviewHistoryPreserved"])

    def test_migration_to_3_1_is_idempotent(self) -> None:
        legacy = make_spec(
            "Idempotent Legacy Prop",
            None,
            complexity="simple",
            intended_use="static-render",
        )
        legacy["schemaVersion"] = "3.0"
        legacy["componentTree"][0].pop("componentType", None)
        legacy["componentTree"][0]["geometryDescriptor"].pop("parameters", None)

        once, _ = migrate_spec(legacy)
        twice, report = migrate_spec(once)

        self.assertEqual(twice, once)
        self.assertFalse(report["changed"])


class GeometryHandlerTests(unittest.TestCase):
    def test_box_edge_treatment_and_local_features_emit_real_geometry(self) -> None:
        spec = compound_musician_mascot_spec()
        torso = next(item for item in spec["componentTree"] if item.get("id") == "torso")
        torso["primitive"] = "box"
        torso["geometryDescriptor"]["parameters"] = {
            "widthSegments": 4,
            "heightSegments": 4,
            "depthSegments": 4,
        }
        torso["geometryDescriptor"]["edgeTreatment"] = {
            "type": "rounded",
            "radiusRatio": 0.12,
            "segments": 4,
        }
        torso["localFeatures"] = [
            {
                "id": "jacket-seam",
                "type": "seam-line",
                "path": [[-0.1, 0.2, 0.51], [0.0, 0.0, 0.51], [0.1, -0.2, 0.51]],
                "radius": 0.008,
                "segments": 24,
                "material": "base",
            },
            {
                "id": "coat-button",
                "type": "button",
                "position": [0.0, 0.1, 0.51],
                "radius": 0.025,
                "scale": [1.0, 1.0, 0.4],
            },
        ]

        errors, _ = validate_spec(spec)
        self.assertEqual(errors, [], errors)
        output = generate(spec, "form")
        self.assertIn("function createRoundedBoxGeometry", output)
        self.assertIn("createRoundedBoxGeometry(1,1,1,0.12,4)", output)
        self.assertIn('meshes["torso::jacket-seam"]', output)
        self.assertIn('meshes["torso::coat-button"]', output)

    def test_edge_treatment_fails_when_it_would_be_metadata_only(self) -> None:
        spec = compound_musician_mascot_spec()
        torso = next(item for item in spec["componentTree"] if item.get("id") == "torso")
        torso["geometryDescriptor"]["edgeTreatment"] = {
            "type": "rounded",
            "radiusRatio": 0.1,
            "segments": 4,
        }
        errors, _ = validate_spec(spec)
        self.assertTrue(
            any("edgeTreatment" in error and "only for box" in error for error in errors),
            errors,
        )

    def test_geometry_and_repetition_limits_fail_instead_of_silent_clamping(self) -> None:
        tube_errors = _errors_for_part(
            "tube",
            {
                "path": [[0, 0, 0], [0, 1, 0]],
                "radius": 0.02,
                "tubularSegments": MAX_GEOMETRY_SEGMENTS + 1,
            },
        )
        self.assertTrue(
            any(
                "tubularSegments" in error and str(MAX_GEOMETRY_SEGMENTS) in error
                for error in tube_errors
            ),
            tube_errors,
        )
        repetition_errors = validate_repetition_systems(
            [
                {
                    "id": "oversized-grid",
                    "mode": "grid",
                    "parameters": {"columns": MAX_INSTANCE_COUNT + 1, "rows": 1},
                }
            ]
        )
        self.assertTrue(
            any(str(MAX_INSTANCE_COUNT) in error for error in repetition_errors),
            repetition_errors,
        )

    def test_blockout_proxy_is_used_only_when_explicitly_declared(self) -> None:
        spec = compound_musician_mascot_spec()
        root = _assembly("root", None)
        tube = next(item for item in spec["componentTree"] if item.get("id") == "glasses-rim")
        tube["parent"] = "root"
        tube["level"] = "macro"
        tube["dimensions"] = {
            "width": 999,
            "height": 999,
            "depth": 999,
            "units": "relative",
            "confidence": 0.8,
        }
        tube["transform"]["scale"] = [2, 3, 4]
        spec["componentTree"] = [root, tube]
        spec["repetitionSystems"] = []

        real_output = generate(spec, "blockout")
        self.assertIn("THREE.TubeGeometry", real_output)
        self.assertNotIn("new THREE.BoxGeometry", real_output)
        self.assertIn(".scale.set(2.0, 3.0, 4.0)", real_output)

        tube["blockoutProxy"] = {"primitive": "box", "parameters": {}}
        proxy_output = generate(spec, "blockout")
        self.assertIn("new THREE.BoxGeometry", proxy_output)
        self.assertNotIn("THREE.TubeGeometry", proxy_output)

    def test_every_advanced_primitive_uses_real_geometry(self) -> None:
        output = generate(compound_musician_mascot_spec(), "form")
        expected_markers = {
            "tube": "THREE.TubeGeometry",
            "lathe": "THREE.LatheGeometry",
            "extrude": "ExtrudeGeometry",
            "curve-sweep": "createCurveSweepGeometry",
            "instanced-cluster": "THREE.InstancedMesh",
        }
        for primitive, marker in expected_markers.items():
            with self.subTest(primitive=primitive):
                self.assertIn(marker, output)

        self.assertNotIn("box fallback", output.lower())
        self.assertNotIn("TODO: replace", output)
        self.assertNotIn("new THREE.BoxGeometry", output)
        self.assertNotIn("new THREE.CylinderGeometry", output)

    def test_primitive_field_remains_the_canonical_handler_selector(self) -> None:
        spec = compound_musician_mascot_spec()
        tube = next(item for item in spec["componentTree"] if item.get("id") == "glasses-rim")
        tube["geometryDescriptor"]["primitive"] = "box"
        output = generate(spec, "form")
        self.assertIn("THREE.TubeGeometry", output)
        self.assertNotIn("new THREE.BoxGeometry", output)

    def test_unsupported_primitive_is_rejected_without_generator_fallback(self) -> None:
        spec = compound_musician_mascot_spec()
        part = next(item for item in spec["componentTree"] if item.get("id") == "glasses-rim")
        part["primitive"] = "quantum-blob"

        errors, _ = validate_spec(spec)
        self.assertTrue(
            any("glasses-rim" in error and "quantum-blob" in error for error in errors),
            errors,
        )
        with self.assertRaisesRegex(ValueError, r"(?i)(quantum-blob.*unsupported|unsupported.*quantum-blob)"):
            generate(spec, "form")

    def test_missing_parameters_object_is_rejected_for_advanced_geometry(self) -> None:
        errors = _errors_for_part("tube", None)
        self.assertTrue(
            any("invalid-part" in error and "parameters" in error for error in errors),
            errors,
        )

    def test_malformed_primitive_parameters_report_the_component(self) -> None:
        cases = (
            ("tube", {"path": [[0, 0, 0]], "radius": 0.02}, "path"),
            ("lathe", {"profile": [[0.1, 0.0]], "segments": 16}, "profile"),
            ("extrude", {"shape": [[0, 0], [1, 0]], "depth": 0.1}, "shape"),
            (
                "curve-sweep",
                {
                    "path": [[0, 0, 0], [0, 1, 0]],
                    "profile": [[0, 0], [0.1, 0]],
                },
                "profile",
            ),
            (
                "instanced-cluster",
                {
                    "repetitionSystemRef": "missing-system",
                    "sourcePrimitive": "sphere",
                },
                "missing-system",
            ),
        )
        for primitive, parameters, expected_fragment in cases:
            with self.subTest(primitive=primitive):
                errors = _errors_for_part(primitive, parameters)
                self.assertTrue(
                    any(
                        "invalid-part" in error and expected_fragment in error
                        for error in errors
                    ),
                    errors,
                )

    def test_degenerate_paths_and_profiles_are_rejected(self) -> None:
        cases = (
            (
                "tube",
                {"path": [[0, 0, 0], [0, 0, 0]], "radius": 0.02},
                "identical consecutive points",
            ),
            (
                "lathe",
                {"profile": [[0.1, 0.0], [0.2, 0.0]], "segments": 16},
                "non-zero local y range",
            ),
            (
                "extrude",
                {"shape": [[0, 0], [1, 0], [2, 0]], "depth": 0.1},
                "non-zero area",
            ),
            (
                "curve-sweep",
                {
                    "path": [[0, 0, 0], [0, 1, 0]],
                    "profile": [[0, 0], [1, 0], [2, 0]],
                    "closedProfile": True,
                },
                "non-zero area",
            ),
        )
        for primitive, parameters, expected_fragment in cases:
            with self.subTest(primitive=primitive):
                errors = _errors_for_part(primitive, parameters)
                self.assertTrue(
                    any(expected_fragment in error for error in errors),
                    errors,
                )

    def test_thousand_instances_are_emitted_as_one_compact_instanced_mesh(self) -> None:
        spec = compound_musician_mascot_spec()
        cluster = next(item for item in spec["componentTree"] if item.get("id") == "coat-buttons")
        spec["componentTree"] = [_assembly("root", None), cluster]
        cluster["parent"] = "root"
        output = generate(spec, "form")

        self.assertEqual(output.count("new THREE.InstancedMesh"), 1)
        self.assertIn("setMatrixAt", output)
        self.assertIn("1000", output)
        self.assertLess(len(output), 100_000)
        self.assertNotIn("instance-999", output)

    def test_compound_component_count_does_not_add_build_passes(self) -> None:
        baseline = make_spec(
            "Single Complex Prop",
            None,
            complexity="complex",
            intended_use="static-render",
        )
        compound = compound_musician_mascot_spec()
        self.assertEqual(pass_order(compound), pass_order(baseline))
        self.assertEqual(pass_order(compound), ["blockout", "structure", "form", "lookdev"])


if __name__ == "__main__":
    unittest.main()
