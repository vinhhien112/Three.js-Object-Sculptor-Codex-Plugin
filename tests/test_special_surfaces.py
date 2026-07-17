from __future__ import annotations

import copy
import math
import sys
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from generate_threejs_factory import generate  # noqa: E402
from new_sculpt_spec import make_spec  # noqa: E402
from sculpt_contract import pass_order, review_spec_hash  # noqa: E402
from sculpt_geometry import (  # noqa: E402
    MAX_DEFORMABLE_SAMPLED_VERTICES,
    MAX_FIBER_STRANDS,
    MAX_IMPLICIT_CELLS,
    MAX_SPECIAL_PARAMETER_MAGNITUDE,
    MAX_VOLUME_PARTICLES,
)
from sculpt_pass_orchestrator import material_gaps, pass_specific_gaps  # noqa: E402
from validate_sculpt_spec import (  # noqa: E402
    validate_special_material_compatibility,
    validate_spec,
    warning_applies_to_pass,
)


def _fill_pre_spec(spec: dict[str, Any]) -> None:
    spec["preSpecAssessment"]["objectClass"].update(
        {
            "primaryType": "generic special-surface study",
            "formLanguage": ["soft surface", "fibrous detail", "soft volume"],
            "structureKind": ["independent procedural parts"],
            "motionPotential": ["static procedural approximation"],
            "materialFamilies": ["cloth", "fiber", "organic", "volume"],
        }
    )
    spec["silhouette"].update(
        {
            "boundingShape": "four separated procedural surface samples",
            "aspectRatios": ["width:height=2:1"],
            "dominantCurves": ["draped grid and curved fiber guides"],
        }
    )


def _assembly(component_id: str = "root") -> dict[str, Any]:
    return {
        "id": component_id,
        "name": "Special Surface Root",
        "componentType": "assembly",
        "level": "macro",
        "role": "assembly",
        "importance": 1.0,
        "confidence": 1.0,
        "parent": None,
        "transform": {
            "position": [0, 0, 0],
            "rotation": [0, 0, 0],
            "scale": [1, 1, 1],
        },
        "evidenceRefs": ["full-object"],
    }


def _part(
    component_id: str,
    primitive: str,
    parameters: dict[str, Any],
    material: str,
    position: list[float],
) -> dict[str, Any]:
    return {
        "id": component_id,
        "name": component_id.replace("-", " ").title(),
        "componentType": "part",
        "level": "macro",
        "role": "surface sample",
        "importance": 0.8,
        "confidence": 0.8,
        "primitive": primitive,
        "geometryDescriptor": {
            "parameters": parameters,
            "topologyIntent": f"real {primitive} procedural geometry",
            "deformationStack": [],
            "uvStrategy": "generated local coordinates",
            "normalStrategy": "generated vertex normals",
        },
        "parent": "root",
        "attachment": None,
        "dimensions": {
            "width": 1.0,
            "height": 1.0,
            "depth": 1.0,
            "units": "relative",
            "confidence": 0.8,
        },
        "transform": {
            "position": position,
            "rotation": [0, 0, 0],
            "scale": [1, 1, 1],
        },
        "material": material,
        "materialLayers": [material],
        "deformations": [],
        "joints": [],
        "seams": [],
        "localFeatures": [],
        "surfaceDetail": {
            "macroRoughness": 0.1,
            "microRoughness": 0.1,
            "bumpAmplitude": 0.02,
            "normalPattern": "profile-specific procedural detail",
        },
        "evidenceRefs": ["full-object"],
        "details": [],
        "fidelityTier": "form",
    }


def _material(base: dict[str, Any], material_id: str, profile: str) -> dict[str, Any]:
    material = copy.deepcopy(base)
    material["id"] = material_id
    material["name"] = material_id.replace("-", " ").title()
    material["materialProfile"] = profile
    return material


def special_surface_spec() -> dict[str, Any]:
    """Generic fixture with no dependency on a local reference-image path."""

    spec = make_spec(
        "Special Surface Study",
        None,
        complexity="simple",
        intended_use="static-render",
    )
    _fill_pre_spec(spec)
    base = spec["materials"][0]
    spec["materials"] = [
        _material(base, "cloth", "cloth"),
        _material(base, "fiber", "fiber"),
        _material(base, "organic", "standard"),
        {
            **_material(base, "volume", "volume"),
            "opacity": 0.48,
            "depthWrite": False,
            "forceSinglePass": True,
        },
    ]
    spec["componentTree"] = [
        _assembly(),
        _part(
            "draped-grid",
            "deformable-surface",
            {
                "representation": "grid",
                "controlGrid": [
                    [[-0.5, 0.5, 0.0], [0.5, 0.5, 0.0]],
                    [[-0.5, -0.5, 0.06], [0.5, -0.5, 0.0]],
                ],
                "segments": [16, 16],
                "folds": [
                    {
                        "direction": [1.0, 0.0],
                        "amplitude": 0.04,
                        "frequency": 3.0,
                        "phase": 0.2,
                    }
                ],
            },
            "cloth",
            [-1.5, 0.5, 0.0],
        ),
        _part(
            "fiber-ribbons",
            "fiber-system",
            {
                "representation": "ribbon-cards",
                "guides": [
                    [[-0.25, 0.0, 0.0], [-0.22, 0.25, 0.04], [-0.14, 0.5, 0.0]],
                    [[0.1, 0.0, 0.0], [0.16, 0.22, -0.03], [0.2, 0.48, 0.02]],
                ],
                "strandsPerGuide": 12,
                "samples": 8,
                "rootWidth": 0.025,
                "tipWidth": 0.004,
                "spread": 0.035,
                "clump": 0.7,
                "curl": {"amplitude": 0.015, "frequency": 2.0, "phase": 0.1},
                "cardPlanes": 2,
                "seed": 71,
            },
            "fiber",
            [-0.45, 0.0, 0.0],
        ),
        _part(
            "soft-metaballs",
            "implicit-surface",
            {
                "representation": "metaballs",
                "bounds": {"min": [-0.8, -0.8, -0.8], "max": [0.8, 0.8, 0.8]},
                "resolution": [12, 12, 12],
                "isoLevel": 0.5,
                "sources": [
                    {
                        "position": [-0.18, 0.0, 0.0],
                        "radius": 0.62,
                        "strength": 1.0,
                        "operation": "add",
                    },
                    {
                        "position": [0.28, 0.08, 0.0],
                        "radius": 0.48,
                        "strength": 0.8,
                        "operation": "add",
                    },
                ],
                "uvProjection": "xz",
            },
            "organic",
            [0.65, 0.0, 0.0],
        ),
        _part(
            "soft-volume",
            "volume-field",
            {
                "representation": "crossed-cards",
                "bounds": {"min": [-0.6, -0.5, -0.6], "max": [0.6, 0.7, 0.6]},
                "sources": [
                    {"position": [-0.18, 0.0, 0.0], "radius": 0.4, "density": 0.8},
                    {"position": [0.22, 0.2, 0.05], "radius": 0.34, "density": 0.65},
                ],
                "particleCount": 256,
                "cardPlanes": 2,
                "cardSize": [0.08, 0.2],
                "seed": 99,
            },
            "volume",
            [1.65, 0.1, 0.0],
        ),
    ]
    return spec


def _component(spec: dict[str, Any], component_id: str) -> dict[str, Any]:
    return next(item for item in spec["componentTree"] if item.get("id") == component_id)


class SpecialGeometryContractTests(unittest.TestCase):
    def test_all_special_systems_emit_real_geometry_without_box_fallback(self) -> None:
        spec = special_surface_spec()
        errors, _ = validate_spec(spec)
        self.assertEqual(errors, [], "\n".join(errors))

        output = generate(spec, "form")
        for marker in (
            "createDeformableSurfaceGeometry(",
            "createFiberSystemGeometry(",
            "createImplicitSurfaceGeometry(",
            "createVolumeFieldGeometry(",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, output)
        self.assertNotIn("new THREE.BoxGeometry", output)
        self.assertNotIn("TODO: replace", output)

    def test_invalid_representation_and_simulation_are_rejected(self) -> None:
        ids = ("draped-grid", "fiber-ribbons", "soft-metaballs", "soft-volume")
        for component_id in ids:
            with self.subTest(component=component_id, case="representation"):
                spec = special_surface_spec()
                parameters = _component(spec, component_id)["geometryDescriptor"]["parameters"]
                parameters["representation"] = "unsupported-mode"
                errors, _ = validate_spec(spec)
                self.assertTrue(
                    any(component_id in error and "representation" in error for error in errors),
                    errors,
                )
            with self.subTest(component=component_id, case="simulation"):
                spec = special_surface_spec()
                parameters = _component(spec, component_id)["geometryDescriptor"]["parameters"]
                parameters["simulation"] = True
                errors, _ = validate_spec(spec)
                self.assertTrue(
                    any(component_id in error and "simulation" in error for error in errors),
                    errors,
                )

    def test_special_system_caps_fail_instead_of_clamping(self) -> None:
        cases = (
            (
                "draped-grid",
                lambda parameters: parameters.update({"segments": [256, 256]}),
                str(MAX_DEFORMABLE_SAMPLED_VERTICES),
            ),
            (
                "fiber-ribbons",
                lambda parameters: parameters.update(
                    {"strandsPerGuide": MAX_FIBER_STRANDS + 1}
                ),
                str(MAX_FIBER_STRANDS),
            ),
            (
                "soft-metaballs",
                lambda parameters: parameters.update({"resolution": [40, 40, 40]}),
                str(MAX_IMPLICIT_CELLS),
            ),
            (
                "soft-volume",
                lambda parameters: parameters.update(
                    {"particleCount": MAX_VOLUME_PARTICLES + 1}
                ),
                str(MAX_VOLUME_PARTICLES),
            ),
        )
        for component_id, mutate, expected in cases:
            with self.subTest(component=component_id):
                spec = special_surface_spec()
                parameters = _component(spec, component_id)["geometryDescriptor"]["parameters"]
                mutate(parameters)
                errors, _ = validate_spec(spec)
                self.assertTrue(
                    any(component_id in error and expected in error for error in errors),
                    errors,
                )

    def test_extreme_finite_parameters_are_rejected_before_float32_overflow(self) -> None:
        excessive = MAX_SPECIAL_PARAMETER_MAGNITUDE + 1
        cases = (
            (
                "draped-grid",
                lambda parameters: parameters["controlGrid"][0][0].__setitem__(
                    0, excessive
                ),
                "controlGrid",
            ),
            (
                "fiber-ribbons",
                lambda parameters: parameters.update({"rootWidth": excessive}),
                "rootWidth",
            ),
            (
                "soft-metaballs",
                lambda parameters: parameters["sources"][0].update(
                    {"radius": excessive}
                ),
                "radius",
            ),
            (
                "soft-volume",
                lambda parameters: parameters["sources"][0].update(
                    {"radius": excessive}
                ),
                "radius",
            ),
        )
        for component_id, mutate, field in cases:
            with self.subTest(component=component_id):
                spec = special_surface_spec()
                parameters = _component(spec, component_id)["geometryDescriptor"][
                    "parameters"
                ]
                mutate(parameters)
                errors, _ = validate_spec(spec)
                self.assertTrue(
                    any(component_id in error and field in error for error in errors),
                    errors,
                )

    def test_special_generation_is_deterministic_and_compact(self) -> None:
        spec = special_surface_spec()
        fiber = _component(spec, "fiber-ribbons")["geometryDescriptor"]["parameters"]
        fiber["strandsPerGuide"] = 256
        fiber["samples"] = 16
        volume = _component(spec, "soft-volume")["geometryDescriptor"]["parameters"]
        volume["particleCount"] = 1024

        first = generate(spec, "form")
        second = generate(copy.deepcopy(spec), "form")

        self.assertEqual(first, second)
        self.assertLess(len(first), 250_000)
        self.assertEqual(first.count("createFiberSystemGeometry("), 2)
        self.assertEqual(first.count("createVolumeFieldGeometry("), 2)

    def test_cloth_edge_fade_and_fiber_variation_are_bounded_and_emitted(self) -> None:
        spec = special_surface_spec()
        cloth = _component(spec, "draped-grid")["geometryDescriptor"]["parameters"]
        cloth["folds"][0]["edgeFade"] = 0.12
        fiber = _component(spec, "fiber-ribbons")["geometryDescriptor"]["parameters"]
        fiber["lengthVariation"] = 0.24
        fiber["widthVariation"] = 0.18

        errors, _ = validate_spec(spec)
        self.assertEqual(errors, [], errors)
        output = generate(spec, "form")
        self.assertIn('"edgeFade":0.12', output)
        self.assertIn('"lengthVariation":0.24', output)
        self.assertIn('"widthVariation":0.18', output)

        fiber["lengthVariation"] = 0.8
        errors, _ = validate_spec(spec)
        self.assertTrue(any("lengthVariation" in error for error in errors), errors)

    def test_special_systems_do_not_add_build_passes(self) -> None:
        baseline = make_spec(
            "Baseline",
            None,
            complexity="simple",
            intended_use="static-render",
        )
        self.assertEqual(pass_order(special_surface_spec()), pass_order(baseline))
        self.assertEqual(pass_order(baseline), ["blockout", "form", "lookdev"])


class MaterialProfileValidationTests(unittest.TestCase):
    def _spec(self) -> dict[str, Any]:
        spec = make_spec(
            "Material Profile",
            None,
            complexity="simple",
            intended_use="static-render",
        )
        _fill_pre_spec(spec)
        return spec

    def test_exact_profiles_and_layered_scalars_are_valid(self) -> None:
        for profile in ("standard", "cloth", "fiber", "glass", "liquid", "volume"):
            with self.subTest(profile=profile):
                spec = self._spec()
                spec["materials"][0].update(
                    {
                        "materialProfile": profile,
                        "sheen": {"base": 0.5},
                        "sheenRoughness": {"amount": 0.35},
                        "anisotropy": 0.7,
                        "anisotropyRotation": {"angle": math.pi / 3},
                        "transmission": {"base": 0.45},
                        "opacity": {"amount": 0.9},
                        "ior": {"base": 1.45},
                        "thickness": {"amount": 0.2},
                        "attenuationDistance": {"base": 4.0},
                        "sheenColor": "#abc",
                        "attenuationColor": "#AABBCC",
                        "emissive": "#102030",
                        "emissiveIntensity": {"amount": 1.5},
                        "dispersion": {"base": 0.08},
                        "alphaHash": False,
                        "depthWrite": True,
                        "forceSinglePass": False,
                    }
                )
                errors, _ = validate_spec(spec)
                profile_errors = [error for error in errors if "material 'base'" in error]
                self.assertEqual(profile_errors, [], profile_errors)

    def test_generator_consumes_supported_base_or_amount_layers(self) -> None:
        spec = self._spec()
        spec["materials"][0].update(
            {
                "materialProfile": "cloth",
                "sheenRoughness": {"amount": 0.35},
                "opacity": {"amount": 0.9},
                "thickness": {"amount": 0.2},
            }
        )
        output = generate(spec, "form")
        for field in ("sheenRoughness", "opacity", "thickness"):
            with self.subTest(field=field):
                self.assertIn(
                    f"readLayerNumber(spec.{field}, ['base', 'amount']",
                    output,
                )

    def test_unknown_profile_and_invalid_profile_fields_are_rejected(self) -> None:
        invalid_cases = (
            ("materialProfile", "velvet", "materialProfile"),
            ("materialProfile", ["cloth"], "materialProfile"),
            ("materialProfile", None, "materialProfile"),
            ("sheen", 1.01, "sheen"),
            ("sheenRoughness", {"base": -0.1}, "sheenRoughness"),
            ("anisotropy", {"amount": float("inf")}, "anisotropy"),
            ("anisotropyRotation", {"angle": "quarter-turn"}, "anisotropyRotation"),
            ("anisotropyRotation", None, "anisotropyRotation"),
            ("transmission", {"base": 2.0}, "transmission"),
            ("opacity", -0.01, "opacity"),
            ("ior", {"base": 2.334}, "ior"),
            ("ior", None, "ior"),
            ("thickness", {"amount": -0.01}, "thickness"),
            ("attenuationDistance", {"base": 0}, "attenuationDistance"),
            ("attenuationDistance", None, "attenuationDistance"),
            ("sheenColor", "blue", "sheenColor"),
            ("attenuationColor", "#GGGGGG", "attenuationColor"),
            ("emissive", "#12", "emissive"),
            ("emissiveIntensity", {"amount": -1}, "emissiveIntensity"),
            ("dispersion", {"base": -0.1}, "dispersion"),
            ("alphaHash", 1, "alphaHash"),
            ("depthWrite", "false", "depthWrite"),
            ("forceSinglePass", None, "forceSinglePass"),
        )
        for field, value, expected in invalid_cases:
            with self.subTest(field=field, value=value):
                spec = self._spec()
                spec["materials"][0]["materialProfile"] = "glass"
                spec["materials"][0][field] = value
                errors, _ = validate_spec(spec)
                self.assertTrue(any(expected in error for error in errors), errors)

    def test_profile_validation_is_opt_in_for_legacy_materials(self) -> None:
        legacy = self._spec()
        legacy["materials"][0].update(
            {
                "transmission": {"base": 1.5},
                "ior": "legacy-unused-value",
                "depthWrite": "legacy-unused-value",
            }
        )
        errors, warnings = validate_spec(legacy)

        self.assertFalse(any("materialProfile" in error for error in errors), errors)
        self.assertFalse(any("material 'base' ior" in error for error in errors), errors)
        self.assertFalse(any("material 'base' depthWrite" in error for error in errors), errors)
        self.assertFalse(any("materialProfile" in warning for warning in warnings), warnings)
        self.assertIn("new THREE.MeshPhysicalMaterial", generate(legacy, "form"))

    def test_fiber_and_volume_profile_mismatches_are_lookdev_only(self) -> None:
        spec = special_surface_spec()
        for material in spec["materials"]:
            if material["id"] in {"fiber", "volume"}:
                material["materialProfile"] = "cloth"

        warnings: list[str] = []
        validate_special_material_compatibility(spec, warnings)
        self.assertTrue(any("fiber-system" in warning and "fiber" in warning for warning in warnings))
        self.assertTrue(any("volume-field" in warning and "volume" in warning for warning in warnings))
        self.assertTrue(all(warning_applies_to_pass(warning, "lookdev") for warning in warnings))
        self.assertFalse(any(warning_applies_to_pass(warning, "form") for warning in warnings))
        self.assertFalse(
            any("materialProfile" in gap for gap in pass_specific_gaps(spec, "form")),
            pass_specific_gaps(spec, "form"),
        )
        form_errors, _ = validate_spec(spec, "form")
        self.assertFalse(any("materialProfile" in error for error in form_errors), form_errors)
        lookdev_gaps = material_gaps(spec)
        self.assertTrue(any("fiber-system" in gap and "materialProfile 'fiber'" in gap for gap in lookdev_gaps))
        self.assertTrue(any("volume-field" in gap and "materialProfile 'volume'" in gap for gap in lookdev_gaps))
        lookdev_errors, _ = validate_spec(spec, "lookdev")
        self.assertTrue(any("materialProfile 'fiber'" in error for error in lookdev_errors))
        self.assertTrue(any("materialProfile 'volume'" in error for error in lookdev_errors))

        errors, validated_warnings = validate_spec(spec)
        self.assertFalse(any("materialProfile" in error for error in errors), errors)
        self.assertTrue(any("fiber-system" in warning for warning in validated_warnings))
        self.assertTrue(any("volume-field" in warning for warning in validated_warnings))

    def test_review_hash_scope_tracks_geometry_and_material_profiles(self) -> None:
        spec = special_surface_spec()
        initial = {
            pass_id: review_spec_hash(spec, pass_id)
            for pass_id in ("blockout", "form", "lookdev")
        }

        material_edit = copy.deepcopy(spec)
        next(
            item for item in material_edit["materials"] if item.get("id") == "cloth"
        )["sheen"] = 0.8
        material_hashes = {
            pass_id: review_spec_hash(material_edit, pass_id)
            for pass_id in initial
        }
        self.assertEqual(material_hashes["blockout"], initial["blockout"])
        self.assertEqual(material_hashes["form"], initial["form"])
        self.assertNotEqual(material_hashes["lookdev"], initial["lookdev"])

        geometry_edit = copy.deepcopy(spec)
        folds = _component(geometry_edit, "draped-grid")["geometryDescriptor"]["parameters"]["folds"]
        folds[0]["amplitude"] = 0.08
        geometry_hashes = {
            pass_id: review_spec_hash(geometry_edit, pass_id)
            for pass_id in initial
        }
        self.assertTrue(
            all(geometry_hashes[pass_id] != initial[pass_id] for pass_id in initial),
            geometry_hashes,
        )


if __name__ == "__main__":
    unittest.main()
