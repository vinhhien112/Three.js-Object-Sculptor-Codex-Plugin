from __future__ import annotations

import copy
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from append_sculpt_review import (  # noqa: E402
    _pending_pass_batch_failures,
    _pass_refinement_progress_failures,
    main as append_review,
)
from extract_reference_pbr import make_tileable_rgb  # noqa: E402
from generate_threejs_factory import generate, scale_vector  # noqa: E402
from make_visual_comparison_sheet import (  # noqa: E402
    appearance_diagnostics,
    create_sheet_pairs,
    main as comparison_main,
    read_png,
    resize_contain,
    write_png_rgb,
)
from new_sculpt_spec import make_spec  # noqa: E402
from sculpt_contract import (  # noqa: E402
    build_pass_plan,
    correction_batch_from_verdict,
    file_sha256,
    pipeline_status,
    review_failures,
    review_spec_hash,
    sculpt_representation_signature,
    visual_evidence_integrity_failures,
    visual_evidence_manifest_sha256,
    write_spec_atomic,
)
from sculpt_pass_orchestrator import pass_specific_gaps  # noqa: E402
from validate_sculpt_spec import load_spec, validate_spec  # noqa: E402


def fill_pre_spec(spec: dict) -> None:
    object_class = spec["preSpecAssessment"]["objectClass"]
    object_class.update(
        {
            "primaryType": "test prop",
            "formLanguage": ["hard-surface"],
            "structureKind": ["single body"],
            "motionPotential": ["static prop"],
            "materialFamilies": ["painted wood"],
        }
    )
    spec["silhouette"].update(
        {
            "boundingShape": "tall rounded rectangle",
            "aspectRatios": ["width:height=1:2"],
            "dominantCurves": ["rounded upper contour"],
        }
    )


def comparison_manifest(root: Path, label: str, view_id: str = "primary") -> dict:
    reference = root / f"{label}-reference.png"
    render = root / f"{label}-render.png"
    sheet = root / f"{label}-comparison.png"
    background = (4, 6, 10)
    reference_pixels = [background] * (32 * 32)
    render_pixels = [background] * (32 * 32)
    for y in range(7, 26):
        for x in range(9, 24):
            variation = 8 if (x + y) % 2 else 0
            reference_pixels[y * 32 + x] = (55 + variation, 115 + variation, 190)
            render_pixels[y * 32 + x] = (58 + variation, 118 + variation, 188)
    write_png_rgb(reference, 32, 32, reference_pixels)
    write_png_rgb(render, 32, 32, render_pixels)
    pairs = [{"viewId": view_id, "referenceImage": reference, "renderScreenshot": render}]
    if view_id != "side":
        pairs.append(
            {
                "viewId": "side",
                "referenceImage": reference,
                "renderScreenshot": render,
                "referenceProvenance": {
                    "origin": "synthetic-hypothesis",
                    "allowedUse": "planning-veto",
                    "source": "test-turnaround",
                },
            }
        )
    payload = create_sheet_pairs(
        pairs,
        sheet,
        128,
        128,
        6,
    )
    return {key: value for key, value in payload.items() if key != "evidenceSet"}


def visual_entry(spec: dict, pass_id: str, root: Path, view_id: str = "primary") -> dict:
    feature_ids = [
        target["id"]
        for target in spec["featureReviewTargets"]
        if pass_id in target["passIds"]
    ]
    layers = {
        "blockout": {"silhouette": 0.82},
        "form": {"silhouette": 0.83, "structure": 0.81},
        "structure": {"structure": 0.82},
        "lookdev": {"material": 0.81, "lighting": 0.78},
        "optimization": {"silhouette": 0.83, "material": 0.81, "lighting": 0.78},
    }[pass_id]
    evidence = comparison_manifest(root, pass_id, view_id)
    evidence["type"] = "visual"
    return {
        "passId": pass_id,
        "action": "continue",
        "specHash": review_spec_hash(spec, pass_id),
        "summary": f"{pass_id} visual test passed",
        "aiVisionScore": 0.84,
        "visualAcceptanceThreshold": 0.7,
        "layerScores": layers,
        "featureReviews": [
            {"id": feature_id, "score": 0.86, "visible": True}
            for feature_id in feature_ids
        ],
        "evidence": evidence,
        "reviewerEvidence": {
            "type": "ai-vision",
            "model": "test-vision-model",
            "reviewedArtifactSha256": evidence["comparisonSha256"],
            "reviewedAt": "2026-07-15T00:00:00+00:00",
        },
        "aiVisionNotes": "Synthetic evidence matches the expected test silhouette.",
    }


class PassPlanTests(unittest.TestCase):
    def test_pass_plan_is_adaptive(self) -> None:
        simple_static = [
            item["id"]
            for item in build_pass_plan("simple", "static-render", "balanced")
        ]
        complex_playable = [
            item["id"]
            for item in build_pass_plan("complex", "playable", "reference-fidelity")
        ]
        self.assertEqual(simple_static, ["blockout", "form", "lookdev"])
        self.assertEqual(
            complex_playable,
            ["blockout", "structure", "form", "lookdev", "interaction", "optimization"],
        )
        lookdev = next(
            item
            for item in build_pass_plan("complex", "playable", "reference-fidelity")
            if item["id"] == "lookdev"
        )
        self.assertEqual(lookdev["requiredViews"], ["neutral", "grazing", "reference"])
        self.assertEqual(lookdev["requiredLayerScores"]["material"], 0.85)
        form = next(
            item
            for item in build_pass_plan("complex", "playable", "reference-fidelity")
            if item["id"] == "form"
        )
        self.assertEqual(form["requiredLayerScores"]["formDetail"], 0.82)

    def test_reference_fidelity_raises_visual_bar_without_changing_balanced(self) -> None:
        balanced = make_spec(
            "Balanced",
            None,
            complexity="simple",
            intended_use="static-render",
            quality_profile="balanced",
        )
        quality = make_spec(
            "Quality",
            None,
            complexity="simple",
            intended_use="static-render",
            quality_profile="reference-fidelity",
        )

        self.assertEqual(balanced["qualityTargets"]["targetFidelity"], 0.7)
        self.assertEqual(quality["qualityTargets"]["targetFidelity"], 0.85)
        self.assertEqual(balanced["materials"][0]["textureResolution"], 1024)
        self.assertEqual(quality["materials"][0]["textureResolution"], 2048)
        self.assertFalse(
            quality["qualityTargets"]["diagnosticTargets"]["acceptanceAuthority"]
        )

    def test_init_integrates_pre_spec_and_has_one_fps_source(self) -> None:
        spec = make_spec("Test", None, complexity="simple", intended_use="browser-prop")
        self.assertEqual(spec["schemaVersion"], "3.1")
        self.assertIn("preSpecAssessment", spec)
        self.assertNotIn("visualEvidence", spec)
        self.assertNotIn("fpsTarget", spec["qualityTargets"])
        self.assertEqual(spec["performanceBudget"]["fpsTarget"], 60)
        self.assertEqual(spec["sculptPipeline"]["passGateMode"], "adaptive-sequential")


class StateContractTests(unittest.TestCase):
    def test_second_independent_pass_batch_requires_progress_and_closed_blockers(self) -> None:
        spec = {"reviewHistory": [
            {
                "passId": "blockout",
                "action": "refine-code",
                "aiVisionScore": 0.80,
                "layerScores": {"silhouette": 0.80},
                "reviewIssues": [
                    {
                        "id": "silhouette-width",
                        "rootCauseKey": "silhouette-width",
                        "severity": "major",
                        "status": "open",
                    }
                ],
            }
        ]}
        stalled = {
            "overallScore": 0.80,
            "layerScores": {"silhouette": 0.80},
            "resolvedIssueIds": ["silhouette-width"],
            "resolvedRootCauseKeys": ["silhouette-width"],
        }
        self.assertTrue(
            any(
                "did not improve" in item
                for item in _pass_refinement_progress_failures(spec, "blockout", stalled)
            )
        )
        unresolved = {
            "overallScore": 0.82,
            "layerScores": {"silhouette": 0.82},
            "resolvedIssueIds": [],
            "resolvedRootCauseKeys": [],
        }
        self.assertTrue(
            any(
                "not explicitly resolved" in item
                for item in _pass_refinement_progress_failures(spec, "blockout", unresolved)
            )
        )
        progressed = {
            **unresolved,
            "resolvedIssueIds": ["silhouette-width"],
            "resolvedRootCauseKeys": ["silhouette-width"],
        }
        self.assertEqual(
            _pass_refinement_progress_failures(spec, "blockout", progressed),
            [],
        )

    def test_assembled_pass_rejects_relabeling_and_cosmetic_strategy_reset(self) -> None:
        spec = make_spec("Assembled", None, complexity="simple")
        previous = {
            "passId": "blockout",
            "action": "refine-code",
            "aiVisionScore": 0.80,
            "layerScores": {"silhouette": 0.80},
            "reviewIssues": [
                {
                    "id": "old-name",
                    "rootCauseKey": "stable-old-root",
                    "severity": "major",
                    "status": "open",
                }
            ],
        }
        spec["reviewHistory"] = [previous]
        relabeled = {
            "overallScore": 0.82,
            "layerScores": {"silhouette": 0.82},
            "resolvedRootCauseKeys": ["stable-old-root"],
            "issues": [
                {
                    "id": "new-name",
                    "rootCauseKey": "laundered-new-root",
                    "severity": "minor",
                    "status": "open",
                }
            ],
        }
        self.assertTrue(
            any(
                "canonical issue lineage" in item
                for item in _pass_refinement_progress_failures(spec, "blockout", relabeled)
            )
        )

        current_signature = sculpt_representation_signature(spec)
        spec["reviewHistory"] = [
            {
                "passId": "blockout",
                "action": "strategy-reset",
                "representationSignature": current_signature,
                "evidence": {"comparisonSha256": "old", "views": []},
            }
        ]
        evidence = {"comparisonSha256": "new", "views": []}
        self.assertTrue(
            any(
                "different topology/geometry" in item
                for item in _pending_pass_batch_failures(spec, "blockout", evidence)
            )
        )
        tuned = copy.deepcopy(spec)
        tuned["componentTree"][0]["dimensions"]["width"] = 1.25
        tuned["componentTree"][0]["geometryDescriptor"]["topologyIntent"] = (
            "A newly worded but still purely descriptive topology sentence."
        )
        self.assertEqual(
            sculpt_representation_signature(tuned),
            sculpt_representation_signature(spec),
        )
        changed = copy.deepcopy(spec)
        changed["componentTree"][0]["primitive"] = "sphere"
        self.assertNotEqual(
            sculpt_representation_signature(changed),
            sculpt_representation_signature(spec),
        )

    def test_reference_refinement_records_root_cause_and_corrections(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            spec_path = Path(directory) / "spec.json"
            spec = make_spec(
                "Correction",
                None,
                complexity="simple",
                intended_use="static-render",
                quality_profile="reference-fidelity",
            )
            fill_pre_spec(spec)
            write_spec_atomic(spec_path, spec)
            corrections = json.dumps(
                [
                    {
                        "target": "root",
                        "parameterPath": "transform.scale",
                        "action": "scale",
                        "reason": "silhouette is too small in frame",
                        "value": 1.08,
                    }
                ]
            )
            self.assertEqual(
                append_review(
                    [
                        str(spec_path),
                        "--pass-id",
                        "blockout",
                        "--action",
                        "refine-code",
                        "--summary",
                        "Correct framing before form work.",
                        "--root-cause",
                        "camera-framing",
                        "--correction-plan-json",
                        corrections,
                        "--in-place",
                    ]
                ),
                0,
            )
            updated = load_spec(spec_path)
            entry = updated["reviewHistory"][-1]
            self.assertEqual(entry["rootCause"], "camera-framing")
            self.assertEqual(entry["correctionPlan"][0]["action"], "scale")
            self.assertTrue(entry["correctionBatch"]["atomic"])
            self.assertEqual(entry["correctionBatch"]["correctionCount"], 1)
            second_args = [
                str(spec_path),
                "--pass-id",
                "blockout",
                "--action",
                "refine-code",
                "--summary",
                "Apply the final consolidated correction batch.",
                "--root-cause",
                "camera-framing",
                "--correction-plan-json",
                corrections,
                "--in-place",
            ]
            self.assertEqual(append_review(second_args), 0)
            self.assertTrue(
                pipeline_status(load_spec(spec_path))["refinementBudget"]["exhausted"]
            )
            with self.assertRaisesRegex(ValueError, "refinement budget is exhausted"):
                append_review(second_args)
            errors, warnings = validate_spec(updated)
            self.assertEqual(errors, [], errors)
            self.assertFalse(any("structured correctionPlan" in item for item in warnings))

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.evidence_root = Path(temporary.name)
        self.spec = make_spec("State Test", None, complexity="simple", intended_use="static-render")
        fill_pre_spec(self.spec)

    def test_future_review_cannot_unlock_later(self) -> None:
        form = visual_entry(self.spec, "form", self.evidence_root)
        blockout = visual_entry(self.spec, "blockout", self.evidence_root)
        self.spec["reviewHistory"] = [form, blockout]
        status = pipeline_status(self.spec)
        self.assertEqual(status["completedPasses"], ["blockout"])
        self.assertEqual(status["currentPass"], "form")

    def test_latest_refinement_invalidates_older_continue(self) -> None:
        refine_verdict = {
            "reviewId": "blockout-refine",
            "action": "refine-code",
            "issues": [
                {
                    "id": "shape",
                    "severity": "major",
                    "status": "open",
                    "target": "silhouette",
                    "reason": "The silhouette is too wide.",
                }
            ],
            "corrections": [
                {
                    "issueId": "shape",
                    "target": "root",
                    "parameterPath": "geometryDescriptor.parameters.profile",
                    "change": "Narrow the executable profile.",
                    "expectedDelta": "The next render has a narrower silhouette.",
                }
            ],
        }
        self.spec["reviewHistory"] = [
            visual_entry(self.spec, "blockout", self.evidence_root),
            {
                "passId": "blockout",
                "action": "refine-code",
                "specHash": review_spec_hash(self.spec, "blockout"),
                "reviewId": "blockout-refine",
                "reviewIssues": refine_verdict["issues"],
                "reviewCorrections": refine_verdict["corrections"],
                "correctionBatch": correction_batch_from_verdict(refine_verdict),
            },
        ]
        status = pipeline_status(self.spec)
        self.assertEqual(status["currentPass"], "blockout")
        self.assertEqual(status["state"], "needs-refinement")
        self.assertEqual(status["pendingCorrectionBatch"]["correctionCount"], 1)

        self.spec["silhouette"]["boundingShape"] = "narrower edited silhouette"
        stale_status = pipeline_status(self.spec)
        self.assertEqual(stale_status["state"], "needs-refinement")
        self.assertEqual(stale_status["pendingCorrectionBatch"]["issueIds"], ["shape"])

    def test_relevant_edit_stales_review_but_lookdev_edit_does_not_stale_shape(self) -> None:
        self.spec["reviewHistory"] = [visual_entry(self.spec, "blockout", self.evidence_root)]
        self.spec["silhouette"]["boundingShape"] = "changed shape"
        self.assertEqual(pipeline_status(self.spec)["currentPass"], "blockout")

        stable = make_spec("Scoped Test", None, complexity="simple", intended_use="static-render")
        fill_pre_spec(stable)
        stable["reviewHistory"] = [
            visual_entry(stable, "blockout", self.evidence_root),
            visual_entry(stable, "form", self.evidence_root),
        ]
        stable["lightingFromPhoto"] = ["new lookdev-only light"]
        self.assertEqual(pipeline_status(stable)["currentPass"], "lookdev")

    def test_metrics_pass_requires_real_measurements(self) -> None:
        spec = make_spec("Metrics", None, complexity="simple", intended_use="browser-prop")
        entry = {
            "passId": "optimization",
            "action": "continue",
            "specHash": review_spec_hash(spec, "optimization"),
            "metrics": {"fps": 58, "drawCalls": 80, "triangles": 150000},
            "artifacts": {"performanceCapture": "capture.json"},
        }
        failures = review_failures(spec, entry, "optimization")
        self.assertTrue(any("fps" in failure for failure in failures))
        self.assertTrue(any("visual" in failure for failure in failures))
        entry["metrics"]["fps"] = 61
        baseline = visual_entry(spec, "lookdev", self.evidence_root, "reference")
        spec["reviewHistory"] = [baseline]
        visual = visual_entry(spec, "optimization", self.evidence_root, "reference")
        entry.update(
            {
                key: value
                for key, value in visual.items()
                if key not in {"passId", "action", "specHash"}
            }
        )
        entry["specHash"] = review_spec_hash(spec, "optimization")
        self.assertEqual(review_failures(spec, entry, "optimization"), [])

    def test_runtime_pass_requires_named_boolean_checks(self) -> None:
        spec = make_spec("Runtime", None, complexity="simple", intended_use="animated")
        entry = {
            "passId": "interaction",
            "action": "continue",
            "specHash": review_spec_hash(spec, "interaction"),
            "runtimeChecks": {"loads": True, "transforms": True, "interaction": False},
        }
        self.assertTrue(any("interaction" in failure for failure in review_failures(spec, entry, "interaction")))
        entry["runtimeChecks"]["interaction"] = True
        self.assertEqual(review_failures(spec, entry, "interaction"), [])

    def test_reference_pbr_needs_confirmed_crop_and_browser_urls(self) -> None:
        spec = make_spec(
            "PBR",
            "reference.png",
            complexity="simple",
            intended_use="static-render",
            quality_profile="reference-fidelity",
        )
        spec["componentTree"][0]["surfaceDetail"]["notes"] = "intentionally smooth surface"
        spec["lightingFromPhoto"] = [
            "key light",
            "environment fill",
            "tone mapping and contact shadow",
        ]
        maps = {
            channel: {"url": f"/maps/{channel}.png"}
            for channel in ("albedo", "roughness", "height", "normal", "ao")
        }
        spec["materials"][0]["referencePbr"] = {
            "usable": True,
            "materialCropConfirmed": False,
            "maps": maps,
        }
        self.assertTrue(any("confirmed material-crop" in gap for gap in pass_specific_gaps(spec, "lookdev")))
        spec["materials"][0]["referencePbr"]["materialCropConfirmed"] = True
        self.assertEqual(pass_specific_gaps(spec, "lookdev"), [])


class GeneratorAndValidatorTests(unittest.TestCase):
    def test_dimensions_and_transform_scale_are_multiplied(self) -> None:
        value = scale_vector(
            {"dimensions": {"width": 2, "height": 3, "depth": 4}},
            {"scale": [0.5, 2, 1]},
        )
        self.assertEqual(value, "1.0, 6.0, 4.0")
        sphere = scale_vector(
            {"primitive": "sphere", "dimensions": {"radius": 2}},
            {"scale": [1, 1, 1]},
        )
        self.assertEqual(sphere, "4.0, 4.0, 4.0")

    def test_generated_root_key_is_reserved_and_blockout_is_cheap(self) -> None:
        spec = make_spec("Generated", None, complexity="simple", intended_use="static-render")
        fill_pre_spec(spec)
        output = generate(spec, "blockout")
        self.assertIn("{ '$root': root }", output)
        self.assertIn("@generated by threejs-object-sculptor", output)
        self.assertIn('materialMap["base"] = new THREE.MeshStandardMaterial', output)
        self.assertIn("wireframe: options.wireframe ?? false });", output)
        self.assertNotIn("wireframe: options.wireframe ?? false }});", output)
        self.assertNotIn("Record<string, any>", output)

    def test_quality_generator_bounds_runtime_maps_and_exports_review_rig(self) -> None:
        spec = make_spec(
            "Quality Rig",
            None,
            complexity="simple",
            intended_use="static-render",
            quality_profile="reference-fidelity",
        )
        fill_pre_spec(spec)
        output = generate(spec, "lookdev")
        self.assertIn("Math.min(1024", output)
        self.assertIn("minimumRuntimeSize = qualityFirst ? 1024 : 256", output)
        self.assertIn("applyProfileSurface", output)
        self.assertIn("componentSurfaceMaterial", output)
        self.assertIn(".castShadow = true;", output)
        self.assertIn("object-sculpt-3.1/evidence-v1", output)
        self.assertIn("configureQualityRigLookDevRenderer", output)
        self.assertIn("frameQualityRigForReview", output)
        self.assertIn("createQualityRigContactShadow", output)

    def test_local_surface_layers_are_executable_and_validated(self) -> None:
        spec = make_spec(
            "Layered Material",
            None,
            complexity="simple",
            intended_use="static-render",
            quality_profile="reference-fidelity",
        )
        fill_pre_spec(spec)
        material = spec["materials"][0]
        material["dirt"] = {
            "amount": 0.24,
            "cavityBias": 0.85,
            "color": "#302820",
        }
        material["wear"] = {"edgeWear": 0.18, "scratches": [], "chips": []}
        material["specularIntensity"] = 0.42
        material["specularColor"] = "#F3EBDD"
        material["envMapIntensity"] = 0.7
        material["localOverrides"] = [
            {
                "id": "observed-cuff-dust",
                "type": "dust",
                "amount": 0.35,
                "color": "#817666",
                "roughnessDelta": 0.2,
                "heightDelta": 0.01,
                "evidenceRefs": ["front-material-closeup"],
                "mask": {
                    "pattern": "cavity",
                    "frequency": 24,
                    "threshold": 0.55,
                    "contrast": 3.5,
                    "cavityBias": 0.9,
                    "uvCenter": [0.32, 0.7],
                    "uvScale": [0.2, 0.15],
                    "feather": 0.3,
                    "seed": 17,
                },
            }
        ]
        output = generate(spec, "lookdev")
        self.assertIn("function materialLocalLayers", output)
        self.assertIn("sampleLocalLayerMask", output)
        self.assertIn("heightDeltaField", output)
        self.assertIn("material.metalnessMap = textures.metalness", output)
        self.assertIn("material.specularIntensity", output)
        self.assertIn("localMaterialLayerCount", output)
        errors, _ = validate_spec(spec)
        self.assertFalse(any("localOverrides" in error for error in errors), errors)

        invalid = copy.deepcopy(spec)
        invalid["materials"][0]["localOverrides"][0].pop("mask")
        errors, _ = validate_spec(invalid)
        self.assertTrue(
            any("mask must be an executable mask object" in error for error in errors),
            errors,
        )

    def test_material_map_evidence_is_valid_metadata_but_not_an_executable_layer(self) -> None:
        spec = make_spec("Material Evidence", None, complexity="simple", intended_use="static-render")
        fill_pre_spec(spec)
        spec["materials"][0]["localOverrides"] = [
            {
                "id": "pbr-provenance",
                "type": "material-map-evidence",
                "evidenceRefs": ["full-object"],
                "channels": ["albedo", "roughness", "normal"],
            }
        ]
        errors, _ = validate_spec(spec)
        self.assertFalse(any("localOverrides" in error for error in errors), errors)
        output = generate(spec, "lookdev")
        self.assertIn("if (type === 'material-map-evidence') return", output)

    def test_offline_pbr_border_blend_is_tile_safe(self) -> None:
        size = 8
        pixels = bytearray()
        for y in range(size):
            for x in range(size):
                pixels.extend((x * 30, y * 30, (x + y) * 12))
        blended = make_tileable_rgb(bytes(pixels), size, 0.25)
        for y in range(size):
            left = (y * size) * 3
            right = (y * size + size - 1) * 3
            self.assertEqual(blended[left : left + 3], blended[right : right + 3])
        for x in range(size):
            top = x * 3
            bottom = ((size - 1) * size + x) * 3
            self.assertEqual(blended[top : top + 3], blended[bottom : bottom + 3])

    def test_validator_detects_missing_core_field_and_parent_cycle(self) -> None:
        spec = make_spec("Cycle", None, complexity="simple", intended_use="static-render")
        child = copy.deepcopy(spec["componentTree"][0])
        child["id"] = "child"
        child["parent"] = "root"
        spec["componentTree"][0]["parent"] = "child"
        del child["dimensions"]
        spec["componentTree"].append(child)
        errors, _ = validate_spec(spec)
        self.assertTrue(any("parent cycle" in error for error in errors))
        self.assertTrue(any("missing core field 'dimensions'" in error for error in errors))

    def test_json_loader_rejects_nan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            path.write_text('{"value": NaN}', encoding="utf-8")
            with self.assertRaises(ValueError):
                load_spec(path)

    def test_atomic_writer_rejects_nan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            with self.assertRaises(ValueError):
                write_spec_atomic(path, {"value": float("nan")})
            self.assertFalse(path.exists())


class ComparisonTests(unittest.TestCase):
    def test_highlight_diagnostics_detect_missing_surface_response(self) -> None:
        size = 16
        reference_pixels = [
            (245, 245, 245, 255) if index % 4 == 0 else (70, 80, 95, 255)
            for index in range(size * size)
        ]
        render_pixels = [(90, 100, 115, 255)] * (size * size)
        mask = [True] * (size * size)
        diagnostics = appearance_diagnostics(
            (size, size, reference_pixels, mask),
            (size, size, render_pixels, mask),
        )
        self.assertEqual(diagnostics["highlightCoverageRatio"], 0.0)
        self.assertEqual(diagnostics["highlightEnergyRatio"], 0.0)

    def test_pairs_help_documents_reference_provenance_shape(self) -> None:
        output = io.StringIO()
        with self.assertRaises(SystemExit) as raised, redirect_stdout(output):
            comparison_main(["--help"])
        self.assertEqual(raised.exception.code, 0)
        help_text = output.getvalue()
        self.assertIn("referenceProvenance={origin: observed|synthetic-", help_text)
        self.assertIn("hypothesis", help_text)
        self.assertIn("allowedUse: acceptance|planning-veto", help_text)

    def test_contain_preserves_wide_image_edges(self) -> None:
        pixels = [
            (255, 0, 0, 255),
            (10, 10, 10, 255),
            (20, 20, 20, 255),
            (0, 0, 255, 255),
            (255, 0, 0, 255),
            (10, 10, 10, 255),
            (20, 20, 20, 255),
            (0, 0, 255, 255),
        ]
        result = resize_contain(4, 2, pixels, 4, 4)
        self.assertEqual(result[4], (255, 0, 0))
        self.assertEqual(result[7], (0, 0, 255))
        self.assertNotEqual(result[0], (255, 0, 0))

    def test_multi_view_sheet_returns_one_evidence_set(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference = root / "reference.png"
            render = root / "render.png"
            out = root / "sheet.png"
            rgb = [(40, 80, 120)] * 16
            write_png_rgb(reference, 4, 4, rgb)
            write_png_rgb(render, 4, 4, rgb)
            payload = create_sheet_pairs(
                [
                    {"viewId": "front", "referenceImage": reference, "renderScreenshot": render},
                    {"viewId": "side", "referenceImage": reference, "renderScreenshot": render},
                ],
                out,
                128,
                128,
                6,
            )
            self.assertTrue(out.exists())
            self.assertEqual(len(payload["evidenceSet"]), 2)
            self.assertTrue(all(item["comparisonImage"] == str(out.resolve()) for item in payload["evidenceSet"]))
            self.assertTrue(
                all(item["fitDiagnostics"]["acceptanceAuthority"] is False for item in payload["evidenceSet"])
            )
            appearance = payload["evidenceSet"][0]["fitDiagnostics"]["appearance"]
            self.assertIn("highlightCoverageRatio", appearance)
            self.assertIn("highlightEnergyRatio", appearance)
            self.assertIn("edgeDensityRatio", appearance)
            self.assertIn("foregroundHistogramIntersection", appearance)
            width, height, _ = read_png(out)
            self.assertEqual(width, 128 * 2 + 6 * 3)
            self.assertGreater(height, 128 * 2)

    def test_silhouette_diagnostics_expose_alignment_without_approving(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference = root / "reference.png"
            render = root / "render.png"
            out = root / "sheet.png"
            diagnostics_dir = root / "diagnostics"
            reference_pixels = [(0, 0, 0)] * (32 * 32)
            render_pixels = [(0, 0, 0)] * (32 * 32)
            for y in range(8, 25):
                for x in range(9, 23):
                    reference_pixels[y * 32 + x] = (240, 240, 240)
                    shifted_x = min(31, x + 3)
                    render_pixels[y * 32 + shifted_x] = (240, 240, 240)
            write_png_rgb(reference, 32, 32, reference_pixels)
            write_png_rgb(render, 32, 32, render_pixels)

            payload = create_sheet_pairs(
                [{"viewId": "front", "referenceImage": reference, "renderScreenshot": render}],
                out,
                128,
                128,
                6,
                diagnostics_dir,
            )
            evidence = payload["evidenceSet"][0]
            diagnostics = evidence["fitDiagnostics"]
            self.assertFalse(diagnostics["acceptanceAuthority"])
            self.assertLess(diagnostics["silhouetteIou"], 1.0)
            self.assertLess(diagnostics["alignmentHints"]["translateX"], 0.0)
            self.assertTrue(Path(evidence["diagnosticOverlay"]).exists())


class QualityGateRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.spec = make_spec(
            "Regression",
            None,
            complexity="simple",
            intended_use="static-render",
        )
        fill_pre_spec(self.spec)

    def test_manifest_rejects_non_image_same_content_and_tampering(self) -> None:
        manifest = comparison_manifest(self.root, "integrity")
        manifest["type"] = "visual"
        self.assertEqual(visual_evidence_integrity_failures(manifest), [])

        same = copy.deepcopy(manifest)
        view = same["views"][0]
        view["renderScreenshot"] = view["referenceImage"]
        view["renderSha256"] = view["referenceSha256"]
        view["renderDimensions"] = view["referenceDimensions"]
        same["manifestSha256"] = visual_evidence_manifest_sha256(same)
        self.assertTrue(
            any("same image content" in item for item in visual_evidence_integrity_failures(same))
        )

        not_image = copy.deepcopy(manifest)
        not_image_view = not_image["views"][0]
        not_image_view["referenceImage"] = "/etc/hosts"
        not_image_view["referenceSha256"] = file_sha256(Path("/etc/hosts"))
        not_image_view["referenceDimensions"] = {"width": 1, "height": 1}
        not_image["manifestSha256"] = visual_evidence_manifest_sha256(not_image)
        self.assertTrue(
            any("not valid image evidence" in item for item in visual_evidence_integrity_failures(not_image))
        )

        render_path = Path(manifest["views"][0]["renderScreenshot"])
        write_png_rgb(render_path, 2, 2, [(255, 0, 0)] * 4)
        self.assertTrue(
            any("changed after comparison" in item for item in visual_evidence_integrity_failures(manifest))
        )

    def test_review_cannot_lower_threshold_or_forge_low_diagnostics(self) -> None:
        entry = visual_entry(self.spec, "blockout", self.root)
        entry["visualAcceptanceThreshold"] = 0.0
        entry["aiVisionScore"] = 0.1
        failures = review_failures(self.spec, entry, "blockout")
        self.assertTrue(any("cannot be below" in item for item in failures))
        self.assertTrue(any("aiVisionScore" in item for item in failures))

        entry = visual_entry(self.spec, "lookdev", self.root, "reference")
        entry["evidence"]["views"][0]["fitDiagnostics"]["appearance"][
            "detailEnergyRatio"
        ] = 0.1
        entry["evidence"]["manifestSha256"] = visual_evidence_manifest_sha256(
            entry["evidence"]
        )
        failures = review_failures(self.spec, entry, "lookdev")
        self.assertTrue(any("detailEnergyRatio" in item for item in failures))

        entry = visual_entry(self.spec, "lookdev", self.root, "reference")
        entry["evidence"]["views"][0]["fitDiagnostics"]["appearance"][
            "highlightEnergyRatio"
        ] = 0.01
        entry["evidence"]["manifestSha256"] = visual_evidence_manifest_sha256(
            entry["evidence"]
        )
        failures = review_failures(self.spec, entry, "lookdev")
        self.assertTrue(any("highlightEnergyRatio" in item for item in failures))

    def test_synthetic_side_material_is_not_treated_as_observed_truth(self) -> None:
        entry = visual_entry(self.spec, "lookdev", self.root, "reference")
        side = next(view for view in entry["evidence"]["views"] if view["viewId"] == "side")
        side["fitDiagnostics"]["appearance"].update(
            {
                "detailEnergyRatio": 0.0,
                "edgeDensityRatio": 0.0,
                "foregroundHistogramIntersection": 0.0,
                "foregroundMeanColorDelta": 1.0,
                "highlightCoverageRatio": 0.0,
                "highlightEnergyRatio": 0.0,
            }
        )
        entry["evidence"]["manifestSha256"] = visual_evidence_manifest_sha256(
            entry["evidence"]
        )
        failures = review_failures(self.spec, entry, "lookdev")
        self.assertFalse(
            any("visual view 'side'" in item and "Ratio" in item for item in failures),
            failures,
        )

    def test_append_cli_rejects_downward_threshold_override(self) -> None:
        spec_path = self.root / "spec.json"
        manifest_path = self.root / "manifest.json"
        write_spec_atomic(spec_path, self.spec)
        manifest_path.write_text(
            json.dumps(comparison_manifest(self.root, "cli")), encoding="utf-8"
        )
        with self.assertRaisesRegex(ValueError, "cannot lower"):
            append_review(
                [
                    str(spec_path),
                    "--pass-id", "blockout",
                    "--action", "continue",
                    "--summary", "Attempt a lower threshold.",
                    "--evidence-set-json", str(manifest_path),
                    "--ai-vision-score", "0.9",
                    "--reviewer-model", "test-vision-model",
                    "--ai-vision-notes", "The comparison was inspected for this regression test.",
                    "--visual-threshold", "0.1",
                    "--layer-scores-json", '{"silhouette":0.9}',
                    "--feature-reviews-json", '[{"id":"overall-silhouette","score":0.9,"visible":true}]',
                ]
            )

    def test_each_hero_material_must_have_executable_evidence(self) -> None:
        weak = copy.deepcopy(self.spec["materials"][0])
        weak["id"] = "weak-hero"
        weak["name"] = "Weak hero material"
        weak["colorVariation"] = {"palette": ["#777777"], "amplitude": 0}
        weak["albedo"] = {"dominant": "#777777", "secondary": []}
        weak["roughness"] = {"base": 0.8, "variation": 0}
        weak["normal"] = {"strength": 0}
        weak["bump"] = {"amplitude": 0}
        weak["displacement"] = {"amplitude": 0}
        weak["ambientOcclusion"] = {"cavityStrength": 0}
        weak["shaderNotes"] = []
        self.spec["materials"].append(weak)
        self.spec["componentTree"][0]["material"] = "weak-hero"
        gaps = pass_specific_gaps(self.spec, "lookdev")
        self.assertTrue(any("weak-hero" in item and "palette" in item for item in gaps))
        self.assertTrue(any("weak-hero" in item and "roughness" in item for item in gaps))


class EndToEndReviewTests(unittest.TestCase):
    def test_simple_static_pipeline_keeps_all_visual_gates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec_path = root / "spec.json"
            spec = make_spec("End To End", None, complexity="simple", intended_use="static-render")
            fill_pre_spec(spec)
            spec_path.write_text(json.dumps(spec), encoding="utf-8")

            def record(pass_id: str, view_id: str, layers: dict, features: list[str]) -> None:
                evidence = json.dumps(comparison_manifest(root, pass_id, view_id))
                argv = [
                    str(spec_path),
                    "--pass-id", pass_id,
                    "--action", "continue",
                    "--summary", f"{pass_id} passed",
                    "--evidence-set-json", evidence,
                    "--ai-vision-score", "0.84",
                    "--reviewer-model", "test-vision-model",
                    "--ai-vision-notes", "The synthetic render matches silhouette and test layers.",
                    "--layer-scores-json", json.dumps(layers),
                    "--feature-reviews-json", json.dumps(
                        [{"id": feature, "score": 0.86, "visible": True} for feature in features]
                    ),
                    "--in-place",
                ]
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(append_review(argv), 0)

            record("blockout", "primary", {"silhouette": 0.8}, ["overall-silhouette"])
            self.assertEqual(pipeline_status(load_spec(spec_path))["currentPass"], "form")
            record(
                "form",
                "primary",
                {"silhouette": 0.82, "structure": 0.8},
                ["overall-silhouette", "primary-structure"],
            )

            updated = load_spec(spec_path)
            updated["componentTree"][0]["surfaceDetail"].update(
                {"bumpAmplitude": 0.2, "normalPattern": "fine grain"}
            )
            updated["lightingFromPhoto"] = [
                "soft key light from upper left",
                "cool fill and environment reflection",
                "ACES tone mapping, exposure 1.0, and soft contact shadow",
            ]
            spec_path.write_text(json.dumps(updated), encoding="utf-8")
            self.assertEqual(pipeline_status(load_spec(spec_path))["currentPass"], "lookdev")
            record(
                "lookdev",
                "reference",
                {"material": 0.8, "lighting": 0.75},
                ["reference-lookdev"],
            )
            status = pipeline_status(load_spec(spec_path))
            self.assertEqual(status["state"], "complete")
            self.assertEqual(status["completedPasses"], ["blockout", "form", "lookdev"])


if __name__ == "__main__":
    unittest.main()
