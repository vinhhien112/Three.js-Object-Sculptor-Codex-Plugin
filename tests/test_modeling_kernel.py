from __future__ import annotations

import copy
import math
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from generate_threejs_factory import (  # noqa: E402
    _validate_generation_spec,
    generate,
    write_generated_spec,
)
from sculpt_contract import (  # noqa: E402
    pipeline_status,
    review_spec_hash,
)
from sculpt_geometry import MAX_INSTANCE_COUNT, MAX_LOFT_VERTICES  # noqa: E402
from validate_sculpt_spec import validate_spec  # noqa: E402
from tests.test_special_surfaces import (  # noqa: E402
    _assembly,
    _part,
    special_surface_spec,
)


def _component(spec: dict[str, Any], component_id: str) -> dict[str, Any]:
    return next(item for item in spec["componentTree"] if item.get("id") == component_id)


def sculpted_face_spec() -> dict[str, Any]:
    spec = special_surface_spec()
    spec["targetName"] = "Continuous Beaver Face"
    face = _part(
        "face-surface",
        "sculpted-surface",
        {
            "representation": "field-sculpt",
            "bounds": {"min": [-1.2, -1.2, -1.0], "max": [1.2, 1.2, 1.0]},
            "resolution": [20, 20, 18],
            "isoLevel": 0.36,
            "connectivity": "single-surface",
            "sources": [
                {
                    "id": "head",
                    "shape": "ellipsoid",
                    "position": [0.0, 0.12, 0.0],
                    "radii": [0.7, 0.82, 0.55],
                    "strength": 1.0,
                    "falloff": 0.72,
                    "operation": "add",
                },
                {
                    "id": "muzzle",
                    "shape": "ellipsoid",
                    "position": [0.0, -0.12, 0.42],
                    "radii": [0.52, 0.3, 0.3],
                    "strength": 0.72,
                    "falloff": 1.1,
                    "operation": "add",
                },
            ],
            "surfaceModifiers": [
                {
                    "id": "left-cheek",
                    "type": "inflate",
                    "position": [-0.36, -0.05, 0.34],
                    "radii": [0.3, 0.25, 0.22],
                    "strength": 0.38,
                    "falloff": 1.4,
                },
                {
                    "id": "right-cheek",
                    "type": "inflate",
                    "position": [0.36, -0.05, 0.34],
                    "radii": [0.3, 0.25, 0.22],
                    "strength": 0.38,
                    "falloff": 1.4,
                },
                {
                    "id": "left-fur-ridge",
                    "type": "ridge",
                    "start": [-0.48, -0.04, 0.32],
                    "end": [-0.66, -0.16, 0.28],
                    "radius": 0.1,
                    "strength": 0.2,
                    "falloff": 2.0,
                },
                {
                    "id": "mouth-crease",
                    "type": "crease",
                    "start": [-0.2, -0.3, 0.48],
                    "end": [0.2, -0.3, 0.48],
                    "radius": 0.07,
                    "strength": 0.12,
                    "falloff": 2.2,
                },
            ],
            "uvProjection": "xy",
        },
        "organic",
        [0, 0, 0],
    )
    glasses = _part(
        "glasses",
        "torus",
        {"radius": 0.24, "tube": 0.025, "radialSegments": 8, "tubularSegments": 24},
        "organic",
        [0, 0.2, 0.55],
    )
    spec["componentTree"] = [_assembly(), face, glasses]
    spec["surfaceTopologyPlan"] = {
        "status": "planned",
        "reason": "The face is soft tissue; glasses are a real separate accessory.",
        "decisionRule": "Semantic landmarks may share one mesh.",
        "groups": [
            {
                "id": "face-soft-tissue",
                "strategy": "continuous-sculpt",
                "regions": ["head", "cheeks", "muzzle", "jaw"],
                "componentRefs": ["face-surface"],
                "materialRefs": [],
                "hostComponentRef": "face-surface",
                "requiredTopology": "single-connected-surface",
                "rationale": "These observed forms transition without a physical seam.",
                "evidenceRefs": ["full-object"],
                "confidence": 0.94,
            },
            {
                "id": "cheek-fur-relief",
                "strategy": "surface-relief",
                "regions": ["large cheek tufts"],
                "componentRefs": ["face-surface"],
                "materialRefs": [],
                "hostComponentRef": "face-surface",
                "requiredTopology": "embedded-in-host",
                "rationale": "The tufts alter the same cheek silhouette rather than floating in front.",
                "evidenceRefs": ["full-object"],
                "confidence": 0.9,
            },
            {
                "id": "glasses-assembly",
                "strategy": "assembled-solid",
                "regions": ["glasses"],
                "componentRefs": ["glasses"],
                "materialRefs": [],
                "requiredTopology": "intentional-separate-parts",
                "separationReason": "Glasses are a rigid accessory with a visible contact boundary.",
                "rationale": "The accessory must remain distinct from the soft face.",
                "evidenceRefs": ["full-object"],
                "confidence": 0.99,
            },
        ],
    }
    return spec


def modeling_kernel_spec() -> dict[str, Any]:
    spec = special_surface_spec()
    spec["targetName"] = "General Organic Modeling Kernel"
    body = _part(
        "organic-body",
        "section-loft",
        {
            "representation": "elliptical-sections",
            "sections": [
                {"position": [0.0, -0.9, 0.0], "radii": [0.3, 0.24], "twist": 0.0},
                {"position": [-0.04, -0.25, 0.03], "radii": [0.5, 0.34], "twist": 0.04},
                {"position": [0.03, 0.5, 0.0], "radii": [0.43, 0.3], "twist": -0.03},
                {"position": [0.0, 0.92, 0.0], "radii": [0.24, 0.2], "twist": 0.0},
            ],
            "radialSegments": 24,
            "segmentsPerSpan": 5,
            "capStart": True,
            "capEnd": True,
        },
        "organic",
        [0, 0, 0],
    )
    shell = _part(
        "fitted-shell",
        "conforming-shell",
        {
            "representation": "loft-shell",
            "bodyRef": "organic-body",
            "clearance": 0.025,
            "thickness": 0.05,
            "coverage": {
                "vRange": [0.14, 0.82],
                "angleStart": 0.0,
                "angleLength": math.tau,
            },
            "openings": [
                {"id": "front-opening", "center": [0.25, 0.55], "radius": [0.08, 0.16]}
            ],
            "folds": [
                {
                    "direction": [0.2, 1.0],
                    "amplitude": 0.012,
                    "frequency": 3.0,
                    "phase": 0.1,
                }
            ],
        },
        "cloth",
        [0, 0, 0],
    )
    shell["parent"] = "organic-body"
    branches = _part(
        "branch-form",
        "branch-network",
        {
            "representation": "branch-graph",
            "nodes": [
                {"id": "root", "position": [1.1, -0.9, 0.0], "radius": 0.16},
                {"id": "trunk", "position": [1.08, 0.1, 0.0], "radius": 0.12},
                {"id": "left", "position": [0.72, 0.85, 0.04], "radius": 0.045},
                {"id": "right", "position": [1.45, 0.78, -0.03], "radius": 0.05},
            ],
            "edges": [
                {"from": "root", "to": "trunk", "controlPoints": [[1.0, -0.35, 0.05]]},
                {"from": "trunk", "to": "left", "controlPoints": [[0.92, 0.48, 0.03]]},
                {"from": "trunk", "to": "right", "controlPoints": [[1.28, 0.45, -0.02]]},
            ],
            "radialSegments": 10,
            "segmentsPerEdge": 9,
            "junctionSegments": 8,
            "capEnds": True,
        },
        "organic",
        [0, 0, 0],
    )
    branches["geometryDescriptor"]["deformationStack"] = [
        {
            "type": "bend",
            "axis": "y",
            "direction": "x",
            "amount": 0.12,
            "start": 0.1,
            "end": 0.95,
            "power": 1.0,
        }
    ]
    scatter = _part(
        "masked-scatter",
        "surface-scatter",
        {
            "representation": "loft-surface",
            "surfaceRef": "organic-body",
            "basePrimitive": "cone",
            "baseParameters": {"radialSegments": 7},
            "count": 36,
            "seed": 91,
            "uRange": [0.0, 1.0],
            "vRange": [0.18, 0.88],
            "excludeMasks": [{"uRange": [0.4, 0.6], "vRange": [0.35, 0.72]}],
            "normalOffset": 0.04,
            "scaleRange": [0.75, 1.2],
            "baseScale": [0.025, 0.08, 0.025],
            "spinRange": math.pi,
            "alignToNormal": True,
            "baseRotation": [0.0, 0.0, 0.0],
        },
        "organic",
        [0, 0, 0],
    )
    scatter["parent"] = "organic-body"
    implicit = _part(
        "blended-organic-form",
        "implicit-surface",
        {
            "representation": "metaballs",
            "bounds": {"min": [-1.0, -1.0, -0.8], "max": [1.0, 1.0, 0.8]},
            "resolution": [12, 12, 10],
            "isoLevel": 0.42,
            "sources": [
                {
                    "shape": "ellipsoid",
                    "position": [-0.25, 0.05, 0.0],
                    "radii": [0.5, 0.7, 0.36],
                    "strength": 1.0,
                    "operation": "add",
                },
                {
                    "shape": "capsule",
                    "start": [0.1, -0.45, 0.0],
                    "end": [0.28, 0.5, 0.0],
                    "radius": 0.28,
                    "strength": 0.85,
                    "operation": "add",
                },
                {
                    "shape": "sphere",
                    "position": [0.45, 0.38, 0.0],
                    "radius": 0.32,
                    "strength": 0.72,
                    "operation": "add",
                },
            ],
            "uvProjection": "xz",
        },
        "organic",
        [-1.4, 0, 0],
    )
    spec["componentTree"] = [
        _assembly(),
        body,
        shell,
        branches,
        scatter,
        implicit,
    ]
    return spec


class GeneralModelingKernelTests(unittest.TestCase):
    def test_sculpted_face_is_one_welded_surface_with_embedded_relief(self) -> None:
        spec = sculpted_face_spec()
        errors, _ = validate_spec(spec)
        self.assertEqual(errors, [], "\n".join(errors))

        output = generate(spec, "form")
        self.assertIn("createSculptedSurfaceGeometry(", output)
        self.assertIn("createWeldedImplicitGeometry(", output)
        self.assertIn('"type":"inflate"', output)
        self.assertIn('"type":"ridge"', output)
        self.assertIn('"type":"crease"', output)
        self.assertIn("geometry.setIndex(indices)", output)
        self.assertIn("assertSingleClosedSurfaceGeometry(geometry)", output)
        self.assertIn("non-manifold vertex link", output)

    def test_sculpted_surface_rejects_disconnected_field_regions(self) -> None:
        spec = sculpted_face_spec()
        parameters = _component(spec, "face-surface")["geometryDescriptor"]["parameters"]
        parameters["sources"] = [
            {
                "id": "left-island",
                "shape": "sphere",
                "position": [-0.72, 0.0, 0.0],
                "radius": 0.2,
                "strength": 1.0,
                "falloff": 1.0,
                "operation": "add",
            },
            {
                "id": "right-island",
                "shape": "sphere",
                "position": [0.72, 0.0, 0.0],
                "radius": 0.2,
                "strength": 1.0,
                "falloff": 1.0,
                "operation": "add",
            },
        ]
        parameters["surfaceModifiers"] = []
        errors, _ = validate_spec(spec)
        self.assertTrue(any("disconnected solid regions" in error for error in errors), errors)

    def test_sculpted_surface_rejects_enclosed_void_that_splits_surface(self) -> None:
        spec = sculpted_face_spec()
        parameters = _component(spec, "face-surface")["geometryDescriptor"]["parameters"]
        parameters["sources"] = [
            {
                "id": "outer-volume",
                "shape": "sphere",
                "position": [0.0, 0.0, 0.0],
                "radius": 0.7,
                "strength": 1.0,
                "falloff": 0.7,
                "operation": "add",
            },
            {
                "id": "sealed-cavity",
                "shape": "sphere",
                "position": [0.0, 0.0, 0.0],
                "radius": 0.25,
                "strength": 1.3,
                "falloff": 1.2,
                "operation": "subtract",
            },
        ]
        parameters["surfaceModifiers"] = []
        errors, _ = validate_spec(spec)
        self.assertTrue(any("enclosed void" in error for error in errors), errors)

    def test_sculpted_surface_rejects_excessive_field_work_before_sampling(self) -> None:
        spec = sculpted_face_spec()
        parameters = _component(spec, "face-surface")["geometryDescriptor"]["parameters"]
        parameters["resolution"] = [64, 64, 63]
        for index in range(4):
            parameters["sources"].append(
                {
                    "id": f"overlap-{index}",
                    "shape": "sphere",
                    "position": [0.0, 0.0, 0.0],
                    "radius": 0.3,
                    "strength": 0.1,
                    "falloff": 1.0,
                    "operation": "add",
                }
            )
        errors, _ = validate_spec(spec)
        self.assertTrue(any("field workload exceeds" in error for error in errors), errors)

    def test_sculpted_surface_rejects_iso_equal_to_a_single_grid_peak(self) -> None:
        spec = sculpted_face_spec()
        parameters = _component(spec, "face-surface")["geometryDescriptor"]["parameters"]
        parameters.update(
            {
                "bounds": {"min": [-1.0, -1.0, -1.0], "max": [1.0, 1.0, 1.0]},
                "resolution": [9, 9, 9],
                "isoLevel": 1.0,
                "sources": [
                    {
                        "id": "grid-peak",
                        "shape": "sphere",
                        "position": [0.0, 0.0, 0.0],
                        "radius": 0.5,
                        "strength": 1.0,
                        "falloff": 0.5,
                        "operation": "add",
                    }
                ],
                "surfaceModifiers": [],
            }
        )
        spec["surfaceTopologyPlan"]["groups"] = [
            group
            for group in spec["surfaceTopologyPlan"]["groups"]
            if group["strategy"] != "surface-relief"
        ]
        errors, _ = validate_spec(spec)
        self.assertTrue(any("strictly inside the sampled field range" in error for error in errors), errors)

    def test_sculpted_surface_runtime_keeps_validator_field_precision(self) -> None:
        spec = sculpted_face_spec()
        parameters = _component(spec, "face-surface")["geometryDescriptor"]["parameters"]
        parameters.update(
            {
                "bounds": {"min": [-1.0, -1.0, -1.0], "max": [1.0, 1.0, 1.0]},
                "resolution": [9, 9, 9],
                "isoLevel": (1.0 + math.exp(-0.05 / 1e12)) / 2.0,
                "sources": [
                    {
                        "id": "narrow-double-range",
                        "shape": "sphere",
                        "position": [0.0, 0.0, 0.0],
                        "radius": 1e6,
                        "strength": 1.0,
                        "falloff": 0.05,
                        "operation": "add",
                    }
                ],
                "surfaceModifiers": [],
            }
        )
        spec["surfaceTopologyPlan"]["groups"] = [
            group
            for group in spec["surfaceTopologyPlan"]["groups"]
            if group["strategy"] != "surface-relief"
        ]
        errors, _ = validate_spec(spec)
        self.assertEqual(errors, [], "\n".join(errors))
        self.assertIn("new Float64Array", generate(spec, "form"))

    def test_sculpted_surface_rejects_unresolvable_scales_without_crashing(self) -> None:
        sources = (
            {
                "id": "tiny-sphere",
                "shape": "sphere",
                "position": [0.0, 0.0, 0.0],
                "radius": 1e-300,
                "strength": 1.0,
                "falloff": 0.5,
                "operation": "add",
            },
            {
                "id": "tiny-ellipsoid",
                "shape": "ellipsoid",
                "position": [0.0, 0.0, 0.0],
                "radii": [1e-300, 0.5, 0.5],
                "strength": 1.0,
                "falloff": 0.5,
                "operation": "add",
            },
            {
                "id": "tiny-capsule",
                "shape": "capsule",
                "start": [0.0, -0.25, 0.0],
                "end": [0.0, 0.25, 0.0],
                "radius": 1e-300,
                "strength": 1.0,
                "falloff": 0.5,
                "operation": "add",
            },
        )
        for source in sources:
            with self.subTest(shape=source["shape"]):
                spec = sculpted_face_spec()
                parameters = _component(spec, "face-surface")["geometryDescriptor"]["parameters"]
                parameters["sources"] = [source]
                parameters["surfaceModifiers"] = []
                spec["surfaceTopologyPlan"]["groups"] = [
                    group
                    for group in spec["surfaceTopologyPlan"]["groups"]
                    if group["strategy"] != "surface-relief"
                ]
                errors, _ = validate_spec(spec)
                self.assertTrue(
                    any("minimum resolvable field scale" in error for error in errors),
                    errors,
                )

    def test_topology_plan_rejects_detached_mesh_for_embedded_relief(self) -> None:
        spec = sculpted_face_spec()
        relief = next(
            group
            for group in spec["surfaceTopologyPlan"]["groups"]
            if group["strategy"] == "surface-relief"
        )
        relief["componentRefs"] = ["face-surface", "glasses"]
        errors, _ = validate_spec(spec)
        self.assertTrue(any("detached relief meshes are not allowed" in error for error in errors), errors)

    def test_continuous_sculpt_cannot_hide_a_detached_component(self) -> None:
        spec = sculpted_face_spec()
        continuous = next(
            group
            for group in spec["surfaceTopologyPlan"]["groups"]
            if group["strategy"] == "continuous-sculpt"
        )
        continuous["componentRefs"].append("glasses")
        errors, _ = validate_spec(spec)
        self.assertTrue(any("exactly its one host" in error for error in errors), errors)

    def test_embedded_relief_cannot_hide_detached_local_feature_meshes(self) -> None:
        spec = sculpted_face_spec()
        face = _component(spec, "face-surface")
        face["localFeatures"] = [
            {
                "id": "floating-cheek-fur",
                "type": "raised-ridge",
                "path": [[-0.4, 0.0, 0.5], [-0.6, -0.1, 0.45]],
                "radius": 0.025,
                "segments": 12,
                "material": "organic",
            }
        ]
        errors, _ = validate_spec(spec)
        self.assertTrue(any("detached localFeatures" in error for error in errors), errors)

    def test_topology_plan_changes_invalidate_visual_review_hashes(self) -> None:
        spec = sculpted_face_spec()
        previous_hash = review_spec_hash(spec, "form")
        spec["surfaceTopologyPlan"]["groups"][0]["rationale"] = (
            "The observed seam interpretation changed and must be reviewed again."
        )
        self.assertNotEqual(review_spec_hash(spec, "form"), previous_hash)

    def test_visible_geometry_cannot_disable_or_skip_topology_planning(self) -> None:
        spec = sculpted_face_spec()
        spec["surfaceTopologyPlan"] = {
            "status": "not-required",
            "reason": "Attempted bypass.",
            "decisionRule": "",
            "groups": [],
        }
        errors, _ = validate_spec(spec)
        self.assertTrue(any("not-required cannot be used" in error for error in errors), errors)

        spec = sculpted_face_spec()
        spec["surfaceTopologyPlan"] = {
            "status": "unassessed",
            "reason": "",
            "decisionRule": "Classify before generating.",
            "groups": [],
        }
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValueError, "must be planned"):
                write_generated_spec(
                    spec,
                    Path(temporary) / "blocked.generated.ts",
                    pass_id="form",
                    force=True,
                )

    def test_generation_validation_reuse_is_bound_to_exact_spec_and_pass(self) -> None:
        spec = sculpted_face_spec()
        status = pipeline_status(spec)
        selected_pass = str(status["currentPass"])
        errors, _, proof = _validate_generation_spec(spec, selected_pass)
        self.assertEqual(errors, [], "\n".join(errors))
        self.assertIsNotNone(proof)
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "valid.generated.ts"
            with (
                mock.patch(
                    "generate_threejs_factory.validate_spec",
                    side_effect=AssertionError("full spec validation ran twice"),
                ) as duplicate_spec_validation,
                mock.patch(
                    "sculpt_pass_orchestrator.validate_geometry_component",
                    side_effect=AssertionError("pass geometry validation ran twice"),
                ) as duplicate_pass_geometry_validation,
                mock.patch(
                    "sculpt_geometry.validate_geometry_component",
                    side_effect=AssertionError("emitter geometry validation ran twice"),
                ) as duplicate_emitter_geometry_validation,
            ):
                write_generated_spec(
                    spec,
                    output,
                    pass_id=selected_pass,
                    force=True,
                    _validation_proof=proof,
                )
            duplicate_spec_validation.assert_not_called()
            duplicate_pass_geometry_validation.assert_not_called()
            duplicate_emitter_geometry_validation.assert_not_called()
            self.assertTrue(output.is_file())

            changed = copy.deepcopy(spec)
            changed["targetName"] = "Changed after validation"
            with self.assertRaisesRegex(ValueError, "proof does not match"):
                write_generated_spec(
                    changed,
                    Path(temporary) / "stale.generated.ts",
                    pass_id=selected_pass,
                    force=True,
                    _validation_proof=proof,
                )

            with self.assertRaisesRegex(ValueError, "proof does not match"):
                write_generated_spec(
                    spec,
                    Path(temporary) / "forged.generated.ts",
                    pass_id=selected_pass,
                    force=True,
                    _validation_proof={  # type: ignore[arg-type]
                        "passId": selected_pass,
                        "specSha256": "caller-supplied-hash",
                    },
                )

    def test_topology_plan_supports_organic_tree_shell_and_bound_detail(self) -> None:
        spec = modeling_kernel_spec()

        def group(
            group_id: str,
            strategy: str,
            component_refs: list[str],
            topology: str,
            *,
            host: str | None = None,
            separation: str | None = None,
        ) -> dict[str, Any]:
            payload: dict[str, Any] = {
                "id": group_id,
                "strategy": strategy,
                "regions": [group_id],
                "componentRefs": component_refs,
                "materialRefs": [],
                "requiredTopology": topology,
                "rationale": f"Observed {group_id} construction strategy.",
                "evidenceRefs": ["full-object"],
                "confidence": 0.9,
            }
            if host:
                payload["hostComponentRef"] = host
            if separation:
                payload["separationReason"] = separation
            return payload

        spec["surfaceTopologyPlan"] = {
            "status": "planned",
            "reason": "Hybrid organic target needs multiple intentional surface strategies.",
            "decisionRule": "Classify by physical continuity rather than semantic names.",
            "groups": [
                group(
                    "organic-body",
                    "continuous-sculpt",
                    ["organic-body"],
                    "single-connected-surface",
                    host="organic-body",
                ),
                group(
                    "fitted-clothing",
                    "conforming-shell",
                    ["fitted-shell"],
                    "host-conforming",
                    host="organic-body",
                ),
                group(
                    "tree-branches",
                    "assembled-solid",
                    ["branch-form"],
                    "intentional-separate-parts",
                    separation=(
                        "The branch-network primitive uses overlapping branch tubes; a hero fused "
                        "tree must use sculpted-surface instead."
                    ),
                ),
                group(
                    "surface-detail",
                    "fiber-strand",
                    ["masked-scatter"],
                    "host-bound-strands",
                    host="organic-body",
                ),
                group(
                    "separate-soft-prop",
                    "assembled-solid",
                    ["blended-organic-form"],
                    "intentional-separate-parts",
                    separation="This is a separate held prop with its own boundary.",
                ),
            ],
        }
        errors, _ = validate_spec(spec)
        self.assertEqual(errors, [], "\n".join(errors))

        false_continuity = copy.deepcopy(spec)
        branch_group = next(
            group
            for group in false_continuity["surfaceTopologyPlan"]["groups"]
            if group["id"] == "tree-branches"
        )
        branch_group.update(
            {
                "strategy": "continuous-sculpt",
                "requiredTopology": "single-connected-surface",
                "hostComponentRef": "branch-form",
            }
        )
        branch_group.pop("separationReason")
        errors, _ = validate_spec(false_continuity)
        self.assertTrue(any("continuous host must use" in error for error in errors), errors)

        wrong_fiber_host = copy.deepcopy(spec)
        alternate_body = copy.deepcopy(_component(wrong_fiber_host, "organic-body"))
        alternate_body["id"] = "other-body"
        wrong_fiber_host["componentTree"].append(alternate_body)
        scatter = _component(wrong_fiber_host, "masked-scatter")
        scatter["parent"] = "other-body"
        scatter["geometryDescriptor"]["parameters"]["surfaceRef"] = "other-body"
        wrong_fiber_host["surfaceTopologyPlan"]["groups"].append(
            {
                "id": "other-body",
                "strategy": "continuous-sculpt",
                "regions": ["alternate test host"],
                "componentRefs": ["other-body"],
                "materialRefs": [],
                "hostComponentRef": "other-body",
                "requiredTopology": "single-connected-surface",
                "rationale": "Keep alternate host covered so only the fiber binding is under test.",
                "evidenceRefs": ["full-object"],
                "confidence": 0.9,
            }
        )
        errors, _ = validate_spec(wrong_fiber_host)
        self.assertTrue(any("must bind surfaceRef and parent" in error for error in errors), errors)

        floating_fiber = copy.deepcopy(spec)
        strand = _component(floating_fiber, "masked-scatter")
        strand["primitive"] = "fiber-system"
        strand["parent"] = "root"
        strand["attachment"] = {"parentId": "organic-body"}
        strand["transform"]["position"] = [10.0, 10.0, 10.0]
        strand["geometryDescriptor"]["parameters"] = {
            "representation": "ribbon-cards",
            "guides": [[[0.0, 0.0, 0.0], [0.0, 0.25, 0.0]]],
            "strandsPerGuide": 2,
            "samples": 4,
            "rootWidth": 0.02,
            "tipWidth": 0.005,
            "spread": 0.01,
            "clump": 0.8,
            "curl": {"amplitude": 0.0, "frequency": 0.0, "phase": 0.0},
            "cardPlanes": 1,
            "seed": 7,
        }
        errors, _ = validate_spec(floating_fiber)
        self.assertTrue(any("must use runtime parent" in error for error in errors), errors)

        strand["parent"] = "organic-body"
        errors, _ = validate_spec(floating_fiber)
        self.assertTrue(
            any("must use an identity transform" in error for error in errors),
            errors,
        )

    def test_kernel_validates_and_emits_real_general_geometry(self) -> None:
        spec = modeling_kernel_spec()
        errors, _ = validate_spec(spec)
        self.assertEqual(errors, [], "\n".join(errors))

        output = generate(spec, "form")
        for marker in (
            "createSectionLoftGeometry(",
            "createConformingShellGeometry(",
            "createBranchNetworkGeometry(",
            "applyGeometryModifiers(",
            "applySurfaceScatterLayout(",
            "implicitSourceNormalizedDistance(",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, output)
        self.assertIn('"shape":"ellipsoid"', output)
        self.assertIn('"shape":"capsule"', output)
        self.assertNotIn('"transforms":', output)
        self.assertNotIn("TODO: replace", output)

    def test_generation_is_deterministic_and_compact(self) -> None:
        spec = modeling_kernel_spec()
        first = generate(spec, "form")
        second = generate(copy.deepcopy(spec), "form")
        self.assertEqual(first, second)
        self.assertLess(len(first), 220_000)

        dense_spec = modeling_kernel_spec()
        _component(dense_spec, "masked-scatter")["geometryDescriptor"]["parameters"][
            "count"
        ] = MAX_INSTANCE_COUNT
        dense_output = generate(dense_spec, "form")
        self.assertLess(abs(len(dense_output) - len(first)), 1_000)
        self.assertNotIn('"transforms":', dense_output)

    def test_host_linkage_is_fail_closed(self) -> None:
        cases = (
            ("bodyRef", "missing-body", "unknown component"),
            ("parent", "root", "must parent"),
        )
        for field, value, expected in cases:
            with self.subTest(field=field):
                spec = modeling_kernel_spec()
                shell = _component(spec, "fitted-shell")
                if field == "bodyRef":
                    shell["geometryDescriptor"]["parameters"][field] = value
                else:
                    shell[field] = value
                errors, _ = validate_spec(spec)
                self.assertTrue(any(expected in error for error in errors), errors)

        spec = modeling_kernel_spec()
        shell = _component(spec, "fitted-shell")
        shell["transform"]["position"] = [0.1, 0.0, 0.0]
        errors, _ = validate_spec(spec)
        self.assertTrue(any("identity transform" in error for error in errors), errors)

        spec = modeling_kernel_spec()
        body = _component(spec, "organic-body")
        body["geometryDescriptor"]["deformationStack"] = [
            {
                "type": "noise",
                "axis": "y",
                "amount": 0.01,
                "frequency": 4.0,
                "seed": 7,
            }
        ]
        errors, _ = validate_spec(spec)
        self.assertTrue(any("deformed host" in error for error in errors), errors)

        spec = modeling_kernel_spec()
        shell = _component(spec, "fitted-shell")
        shell["geometryDescriptor"]["deformationStack"] = [
            {"type": "twist", "axis": "y", "amount": 0.1}
        ]
        errors, _ = validate_spec(spec)
        self.assertTrue(any("must be empty for conforming-shell" in error for error in errors), errors)

    def test_branch_cycles_and_scatter_overmasking_are_rejected(self) -> None:
        spec = modeling_kernel_spec()
        branch = _component(spec, "branch-form")["geometryDescriptor"]["parameters"]
        branch["edges"].append({"from": "left", "to": "root", "controlPoints": []})
        errors, _ = validate_spec(spec)
        self.assertTrue(any("acyclic" in error for error in errors), errors)

        spec = modeling_kernel_spec()
        scatter = _component(spec, "masked-scatter")["geometryDescriptor"]["parameters"]
        scatter["excludeMasks"] = [{"uRange": [0.0, 1.0], "vRange": [0.0, 1.0]}]
        errors, _ = validate_spec(spec)
        self.assertTrue(any("entire requested scatter surface" in error for error in errors), errors)

        spec = modeling_kernel_spec()
        scatter = _component(spec, "masked-scatter")["geometryDescriptor"]["parameters"]
        scatter["excludeMasks"] = [
            {"uRange": [0.0, 0.5], "vRange": [0.0, 1.0]},
            {"uRange": [0.5, 1.0], "vRange": [0.0, 1.0]},
        ]
        errors, _ = validate_spec(spec)
        self.assertTrue(any("entire requested scatter surface" in error for error in errors), errors)

    def test_no_op_shell_openings_and_disconnected_branches_are_rejected(self) -> None:
        spec = modeling_kernel_spec()
        opening = _component(spec, "fitted-shell")["geometryDescriptor"]["parameters"]["openings"][0]
        opening["radius"] = [0.0001, 0.0001]
        errors, _ = validate_spec(spec)
        self.assertTrue(any("would do nothing" in error for error in errors), errors)

        spec = modeling_kernel_spec()
        branch = _component(spec, "branch-form")["geometryDescriptor"]["parameters"]
        branch["nodes"].append(
            {"id": "isolated", "position": [2.0, 0.0, 0.0], "radius": 0.05}
        )
        errors, _ = validate_spec(spec)
        self.assertTrue(any("exactly one root" in error for error in errors), errors)

    def test_invalid_modifiers_implicit_sources_and_caps_are_rejected(self) -> None:
        spec = modeling_kernel_spec()
        branch = _component(spec, "branch-form")
        branch["geometryDescriptor"]["deformationStack"][0]["type"] = "voxel-remesh"
        errors, _ = validate_spec(spec)
        self.assertTrue(any("bend, taper, bulge, twist, or noise" in error for error in errors), errors)

        spec = modeling_kernel_spec()
        source = _component(spec, "blended-organic-form")["geometryDescriptor"]["parameters"]["sources"][1]
        source["end"] = list(source["start"])
        errors, _ = validate_spec(spec)
        self.assertTrue(any("start and end must not coincide" in error for error in errors), errors)

        spec = modeling_kernel_spec()
        body_parameters = _component(spec, "organic-body")["geometryDescriptor"]["parameters"]
        body_parameters["radialSegments"] = 129
        errors, _ = validate_spec(spec)
        self.assertTrue(any("radialSegments" in error for error in errors), errors)
        self.assertGreater(MAX_LOFT_VERTICES, 0)


if __name__ == "__main__":
    unittest.main()
