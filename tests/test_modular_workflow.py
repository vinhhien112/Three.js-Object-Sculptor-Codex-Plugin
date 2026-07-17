from __future__ import annotations

import copy
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from contextlib import redirect_stderr, redirect_stdout


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from generate_threejs_factory import (  # noqa: E402
    generate,
    generated_factory_contract_from_source,
    main as generate_main,
)
from append_sculpt_review import main as append_review  # noqa: E402
from new_sculpt_spec import (  # noqa: E402
    main as init_main,
    make_base_material,
    make_root_component,
    make_spec,
)
from make_visual_comparison_sheet import (  # noqa: E402
    create_sheet_pairs,
    main as compare_main,
    write_png_rgb,
)
from sculpt_contract import (  # noqa: E402
    correction_batch_from_verdict,
    file_sha256,
    pipeline_status,
    refinement_budget,
    review_spec_hash,
    visual_evidence_manifest_sha256,
    write_spec_atomic,
)
from sculpt_module_review import (  # noqa: E402
    _refinement_delta_failures,
    review_contract_failures,
)
from sculpt_module_contract import (  # noqa: E402
    MODULE_BUILD_RECEIPT_ARTIFACT_TYPE,
    MODULE_BUILD_RECEIPT_VERSION,
    module_build_receipt_path,
)
from sculpt_pass_orchestrator import main as orchestrator_main  # noqa: E402
from sculpt_modules import (  # noqa: E402
    MANIFEST_SCHEMA_VERSION,
    accept_module,
    add_module,
    check_module,
    load_document,
    make_manifest,
    module_context,
    module_status,
    preflight_module_review,
    review_module,
    resolve_manifest,
    save_document,
)
from sculpt_module_cli import main as module_cli_main  # noqa: E402
from sculpt_module_state import (  # noqa: E402
    implementation_contract_paths,
    implementation_semantic_hashes,
    module_hash,
)
from sculpt_view_hypotheses import (  # noqa: E402
    hypothesis_evidence_failures,
    hypothesis_manifest_failures,
    register_views,
    status as hypothesis_status,
)
from validate_sculpt_spec import main as validate_main  # noqa: E402


def fill_global_contract(manifest: dict) -> None:
    spec = manifest["globalSpec"]
    visual_module_ids = ("core", "hero", "identity", "placeholder", "addon")
    spec["surfaceTopologyPlan"] = {
        "status": "planned",
        "reason": "The modular test prop uses intentional rigid component boundaries.",
        "decisionRule": "Each visual fixture module owns one explicit assembled body.",
        "groups": [
            {
                "id": f"{module_id}-assembled-body",
                "strategy": "assembled-solid",
                "ownerModuleId": module_id,
                "regions": [f"{module_id} visible body"],
                "componentRefs": [f"{module_id}-body"],
                "materialRefs": [],
                "requiredTopology": "intentional-separate-parts",
                "separationReason": "This fixture represents one independently reviewable rigid module.",
                "rationale": "The test body has an intentional module boundary.",
                "evidenceRefs": ["full-object"],
                "confidence": 0.9,
            }
            for module_id in visual_module_ids
        ],
    }
    object_class = spec["preSpecAssessment"]["objectClass"]
    object_class.update(
        {
            "primaryType": "stylized test prop",
            "formLanguage": ["rounded hard-surface"],
            "structureKind": ["modular assembly"],
            "motionPotential": ["static"],
            "materialFamilies": ["painted polymer"],
        }
    )
    spec["preSpecAssessment"]["specializedRegions"] = {
        "status": "none",
        "notes": "This test prop has no visible face or hand regions.",
        "regions": [],
    }
    spec["silhouette"].update(
        {
            "boundingShape": "rounded box",
            "aspectRatios": ["width:height=1:1"],
            "dominantCurves": ["rounded outer contour"],
        }
    )
    spec["lightingFromPhoto"] = [
        "soft key light",
        "environment fill",
        "ACES tone mapping and contact shadow",
    ]


class ModularWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.manifest_path = self.root / "object-sculpt.json"
        self.manifest = make_manifest(
            make_spec(
                "Modular Prop",
                None,
                complexity="simple",
                intended_use="static-render",
                quality_profile="balanced",
            )
        )
        fill_global_contract(self.manifest)
        write_spec_atomic(self.manifest_path, self.manifest)

    @staticmethod
    def finalize_module_payload(module: dict) -> None:
        for component in module["payload"]["componentTree"]:
            component["geometryDescriptor"]["topologyIntent"] = "authored procedural test form"
            component["fidelityTier"] = "form"
            component["surfaceDetail"]["notes"] = "Intentionally smooth authored test surface."
        for material in module["payload"]["materials"]:
            material["name"] = "Authored test material"
            material["albedo"]["samplingNotes"] = "Palette is bound to the observed test fixture."
            material["shaderNotes"] = [
                "Authored values are tied to the test fixture.",
                "Albedo and scalar fields remain independent.",
            ]

    def finalize_module(self, path: Path) -> None:
        module = json.loads(path.read_text(encoding="utf-8"))
        self.finalize_module_payload(module)
        write_spec_atomic(path, module)

    def add_foundation(self, module_id: str = "core", risk: float = 90) -> Path:
        path = add_module(
            self.manifest_path,
            module_id,
            "foundation",
            risk,
            [],
            "visual",
            "foundation",
        )
        self.finalize_module(path)
        return path

    def add_visual_foundation(
        self,
        module_id: str = "hero",
        risk: float = 95,
        covers: list[str] | None = None,
    ) -> Path:
        path = add_module(
            self.manifest_path,
            module_id,
            "identity-critical hero form",
            risk,
            [],
            "visual",
            "foundation",
            covers,
        )
        self.finalize_module(path)
        return path

    def make_implementation(self, module_id: str = "hero", revision: int = 1) -> Path:
        path = self.root / "src" / f"{module_id}.ts"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"export const SCULPT_MODULE_ID = {module_id!r};\n"
            f"export const {module_id.replace('-', '_')}Revision = {revision};\n",
            encoding="utf-8",
        )
        module_path = self.root / f"{self.manifest_path.stem}.modules" / f"{module_id}.json"
        module = json.loads(module_path.read_text(encoding="utf-8"))
        module["contract"]["implementationFiles"] = [str(path.relative_to(self.root))]
        write_spec_atomic(module_path, module)
        return path

    def accept_visual(self, module_id: str, stem: str | None = None) -> dict:
        review_stem = stem or f"{module_id}-accepted"
        implementation = self.make_implementation(module_id)
        evidence_path, evidence = self.make_evidence(review_stem, module_id=module_id)
        verdict = self.make_verdict(review_stem, evidence)
        return self.review_after_preflight(
            self.manifest_path,
            module_id,
            verdict,
            evidence_path,
            [implementation],
        )

    def review_after_preflight(
        self,
        manifest_path: Path,
        module_id: str,
        verdict_path: Path,
        evidence_path: Path,
        implementation_files: list[Path],
    ) -> dict:
        preflight = preflight_module_review(
            manifest_path,
            module_id,
            evidence_path,
            implementation_files,
        )
        self.assertTrue(preflight["ok"], preflight)
        return review_module(
            manifest_path,
            module_id,
            verdict_path,
            evidence_path,
            implementation_files,
        )

    def make_evidence(
        self,
        stem: str,
        *,
        render_shift: int = 0,
        side_render_shift: int | None = None,
        sparse_mask: bool = False,
        synthetic_required: bool = False,
        render_variant: int = 0,
        single_pixel_delta: bool = False,
        module_id: str = "hero",
    ) -> tuple[Path, dict]:
        size = 64
        background = (4, 6, 10)
        reference_pixels = [background] * (size * size)
        render_pixels = [background] * (size * size)
        if sparse_mask:
            reference_pixels[32 * size + 32] = (80, 140, 220)
            render_pixels[32 * size + 33] = (82, 142, 218)
        else:
            for y in range(8, 56):
                for x in range(12, 52):
                    reference_pixels[y * size + x] = (
                        55 + (x % 7) * 5,
                        100 + (y % 9) * 4,
                        175 + ((x + y) % 5) * 7,
                    )
            for y in range(8, 56):
                for x in range(12 + render_shift, min(size, 52 + render_shift)):
                    source_x = x - render_shift
                    render_pixels[y * size + x] = (
                        57 + (source_x % 7) * 5 + render_variant,
                        102 + (y % 9) * 4,
                        173 + ((source_x + y) % 5) * 7,
                    )
        if single_pixel_delta:
            red, green, blue = render_pixels[32 * size + 32]
            render_pixels[32 * size + 32] = (min(255, red + 24), green, blue)
        reference = self.root / f"{stem}-reference.png"
        render = self.root / f"{stem}-render.png"
        comparison = self.root / f"{stem}-comparison.png"
        write_png_rgb(reference, size, size, reference_pixels)
        write_png_rgb(render, size, size, render_pixels)
        side_render = render
        if side_render_shift is not None:
            side_pixels = [background] * (size * size)
            for y in range(8, 56):
                for x in range(12 + side_render_shift, min(size, 52 + side_render_shift)):
                    source_x = x - side_render_shift
                    side_pixels[y * size + x] = (
                        57 + (source_x % 7) * 5 + render_variant,
                        102 + (y % 9) * 4,
                        173 + ((source_x + y) % 5) * 7,
                    )
            side_render = self.root / f"{stem}-side-render.png"
            write_png_rgb(side_render, size, size, side_pixels)
        required_provenance = (
            {
                "origin": "synthetic-hypothesis",
                "allowedUse": "planning-veto",
                "source": "test-image-generation",
            }
            if synthetic_required
            else {
                "origin": "observed",
                "allowedUse": "acceptance",
                "source": "test-reference",
            }
        )
        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        module_path = self.root / f"{self.manifest_path.stem}.modules" / f"{module_id}.json"
        module = json.loads(module_path.read_text(encoding="utf-8"))
        implementation_paths = implementation_contract_paths(self.manifest_path, module)
        current_module_hash = module_hash(self.manifest_path, manifest, module_id)
        resolved_spec = self.root / ".sculpt-preview" / f"{module_id}.json"
        generated_output = self.root / "src" / "generated" / f"{module_id}.generated.ts"
        resolved_payload = resolve_manifest(self.manifest_path, selected=[module_id])
        selected_pass = str(pipeline_status(resolved_payload)["currentPass"])
        generated_source = generate(
            resolved_payload,
            selected_pass,
            _geometry_prevalidated=True,
        )
        generated_contract = generated_factory_contract_from_source(generated_source)
        write_spec_atomic(resolved_spec, resolved_payload)
        generated_output.parent.mkdir(parents=True, exist_ok=True)
        generated_output.write_text(generated_source, encoding="utf-8")
        build_path = module_build_receipt_path(self.manifest_path, module_id)
        component_ids = generated_contract["expectedComponentIds"]
        mesh_ids = generated_contract["expectedMeshComponentIds"]
        build_receipt = {
            "artifactType": MODULE_BUILD_RECEIPT_ARTIFACT_TYPE,
            "version": MODULE_BUILD_RECEIPT_VERSION,
            "moduleId": module_id,
            "moduleHash": current_module_hash,
            "manifestPath": str(self.manifest_path.resolve()),
            "resolvedSpec": str(resolved_spec),
            "resolvedSpecSha256": file_sha256(resolved_spec),
            "generatedOutput": str(generated_output),
            "generatedOutputSha256": file_sha256(generated_output),
            **generated_contract,
        }
        write_spec_atomic(build_path, build_receipt)
        runtime_receipt = {
            "artifactType": "threejs-sculpt-runtime-receipt",
            "version": 1,
            "factoryId": generated_contract["factoryId"],
            "factoryExport": generated_contract["factoryExport"],
            "specSha256": generated_contract["specSha256"],
            "passId": generated_contract["passId"],
            "rootName": f"{module_id}-test-root",
            "rootAttachedToScene": True,
            "rootEffectivelyVisible": True,
            "componentIds": component_ids,
            "meshComponentIds": mesh_ids,
            "componentPrimitives": generated_contract["expectedPrimitives"],
            "missingComponentIds": [],
            "missingMeshComponentIds": [],
            "hiddenMeshComponentIds": [],
            "unexpectedGeneratedDescendantMeshes": [],
            "unexpectedVisibleMeshes": [],
            "initialGeometryFingerprint": [f"{item}:BoxGeometry:24:36" for item in mesh_ids],
            "geometryFingerprint": [f"{item}:BoxGeometry:24:36" for item in mesh_ids],
            "geometryChangedComponentIds": [],
        }
        runtime_path = self.root / f"{stem}-{module_id}-runtime.json"
        write_spec_atomic(runtime_path, runtime_receipt)
        provenance = {
            "artifactType": "threejs-sculpt-render-provenance",
            "version": 2,
            "moduleId": module_id,
            "moduleHash": current_module_hash,
            "declaredViewIds": sorted({
                item.get("id")
                for source in (
                    manifest.get("globalSpec", {}).get("viewEvidence", []),
                    module.get("payload", {}).get("viewEvidence", []),
                )
                if isinstance(source, list)
                for item in source
                if isinstance(item, dict) and isinstance(item.get("id"), str) and item.get("id")
            }),
            "implementationFiles": {
                str(path): file_sha256(path) for path in implementation_paths
            },
            "implementationSemanticFiles": implementation_semantic_hashes(implementation_paths),
            "buildReceiptPath": str(build_path),
            "buildReceiptSha256": file_sha256(build_path),
            "buildReceipt": build_receipt,
            "runtimeReceiptPath": str(runtime_path),
            "runtimeReceiptSha256": file_sha256(runtime_path),
            "runtimeReceipt": runtime_receipt,
        }
        evidence = create_sheet_pairs(
            [
                {
                    "viewId": "reference",
                    "referenceImage": reference,
                    "renderScreenshot": render,
                    "referenceProvenance": required_provenance,
                },
                {
                    "viewId": "side",
                    "referenceImage": reference,
                    "renderScreenshot": side_render,
                    "referenceProvenance": {
                        "origin": "synthetic-hypothesis",
                        "allowedUse": "planning-veto",
                        "source": "test-image-generation",
                    },
                },
            ],
            comparison,
            128,
            128,
            8,
            render_provenance=provenance,
        )
        path = self.root / f"{stem}-evidence.json"
        write_spec_atomic(path, evidence)
        return path, evidence

    def make_verdict(
        self,
        stem: str,
        evidence: dict,
        *,
        action: str = "continue",
        same_context: bool = False,
        feature_reviews: list[dict] | None = None,
        issues: list[dict] | None = None,
        corrections: list[dict] | None = None,
        resolved: list[str] | None = None,
        resolved_root_causes: list[str] | None = None,
        overall_score: float = 0.95,
        layer_score: float = 0.95,
        extra: dict | None = None,
    ) -> Path:
        normalized_issues = []
        for issue in issues or []:
            normalized = dict(issue)
            normalized.setdefault("rootCauseKey", normalized.get("id"))
            normalized.setdefault("failureClass", "geometry")
            normalized.setdefault(
                "evidenceCheck",
                f"Compare the reviewed target {normalized.get('target', 'surface')} in all bound views.",
            )
            normalized_issues.append(normalized)
        payload = {
            "artifactType": "threejs-sculpt-module-review",
            "version": 1,
            "reviewId": stem,
            "action": action,
            "builder": {"contextId": "builder-task"},
            "reviewer": {
                "contextId": "builder-task" if same_context else f"reviewer-{stem}",
                "role": "independent-reviewer",
                "model": "test-vision",
            },
            "comparisonSha256": evidence["comparisonSha256"],
            "overallScore": overall_score,
            "layerScores": {
                "silhouetteProportion": layer_score,
                "componentStructure": layer_score,
                "formDetail": layer_score,
                "identity": layer_score,
                "materialSurface": layer_score,
            },
            "featureReviews": feature_reviews or [],
            "issues": normalized_issues,
            "corrections": corrections or [],
            "resolvedIssueIds": resolved or [],
            "resolvedRootCauseKeys": resolved_root_causes if resolved_root_causes is not None else (resolved or []),
            "summary": "Independent reviewer checked the exact comparison and its critical visual systems.",
        }
        if extra:
            payload.update(extra)
        path = self.root / f"{stem}-verdict.json"
        write_spec_atomic(path, payload)
        return path

    def test_manifest_starts_with_only_global_contract(self) -> None:
        self.assertEqual(self.manifest["schemaVersion"], MANIFEST_SCHEMA_VERSION)
        self.assertEqual(self.manifest["modules"], [])
        self.assertEqual(
            [item["id"] for item in self.manifest["globalSpec"]["componentTree"]],
            ["root"],
        )
        status = module_status(self.manifest_path)
        self.assertFalse(status["assemblyReady"])
        self.assertIsNone(status["currentModule"])

    def test_imagegen_view_hypotheses_are_cached_and_source_bound(self) -> None:
        source = self.root / "source.png"
        side = self.root / "side.png"
        write_png_rgb(source, 16, 16, [(40, 90, 180)] * (16 * 16))
        write_png_rgb(side, 16, 16, [(55, 105, 190)] * (16 * 16))
        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        manifest["sourceImage"] = str(source)
        manifest["globalSpec"]["sourceImage"] = str(source)
        manifest["globalSpec"]["viewHypothesisPolicy"]["enabled"] = True
        write_spec_atomic(self.manifest_path, manifest)

        self.add_visual_foundation()
        self.make_implementation()
        blocked = check_module(self.manifest_path, "hero", strict_quality=True)
        self.assertFalse(blocked["ok"])
        self.assertTrue(
            any("view hypothesis precondition failed" in item for item in blocked["errors"]),
            blocked,
        )

        first = register_views(self.manifest_path, [f"side={side}"])
        second = register_views(self.manifest_path, [f"side={side}"])
        self.assertFalse(first["cacheHit"])
        self.assertTrue(second["cacheHit"])
        self.assertTrue(hypothesis_status(self.manifest_path)["ready"])
        self.assertTrue(
            check_module(self.manifest_path, "hero", strict_quality=True)["ok"]
        )

        evidence = {
            "views": [
                {
                    "viewId": "side",
                    "referenceSha256": file_sha256(side),
                    "referenceProvenance": {
                        "origin": "synthetic-hypothesis",
                        "allowedUse": "planning-veto",
                    },
                }
            ]
        }
        resolved = load_document(self.manifest_path).resolved
        self.assertEqual(hypothesis_manifest_failures(None, resolved), [])
        self.assertEqual(
            hypothesis_evidence_failures(
                self.manifest_path,
                resolved,
                evidence,
                ["side"],
            ),
            [],
        )
        evidence["views"][0]["referenceProvenance"] = {
            "origin": "observed-reference",
            "allowedUse": "acceptance",
        }
        provenance_failures = hypothesis_evidence_failures(
            self.manifest_path,
            resolved,
            evidence,
            ["side"],
        )
        self.assertTrue(
            any("synthetic-hypothesis/planning-veto provenance" in item for item in provenance_failures),
            provenance_failures,
        )
        evidence["views"][0]["referenceProvenance"] = {
            "origin": "synthetic-hypothesis",
            "allowedUse": "planning-veto",
        }
        evidence["views"][0]["referenceSha256"] = "0" * 64
        self.assertTrue(
            any(
                "registered ImageGen hypothesis" in item
                for item in hypothesis_evidence_failures(
                    self.manifest_path,
                    resolved,
                    evidence,
                    ["side"],
                )
            )
        )
        write_png_rgb(source, 16, 16, [(41, 91, 181)] * (16 * 16))
        stale = hypothesis_status(self.manifest_path)
        self.assertFalse(stale["ready"])
        self.assertTrue(any("stale" in item for item in stale["failures"]))
        self.assertTrue(
            any("stale" in item for item in hypothesis_manifest_failures(None, resolved))
        )

    def test_modular_pass_requires_fresh_independent_verdict(self) -> None:
        self.add_foundation("core", 90)
        self.accept_visual("core", "core-before-pass-review")
        reference = self.root / "pass-reference.png"
        render = self.root / "pass-render.png"
        comparison = self.root / "pass-comparison.png"
        pixels = [(4, 6, 10)] * (32 * 32)
        render_pixels = [(4, 6, 10)] * (32 * 32)
        for y in range(6, 27):
            for x in range(8, 25):
                pixels[y * 32 + x] = (55, 115, 190)
                render_pixels[y * 32 + x] = (58, 118, 188)
        write_png_rgb(reference, 32, 32, pixels)
        write_png_rgb(render, 32, 32, render_pixels)
        evidence = create_sheet_pairs(
            [
                {
                    "viewId": "primary",
                    "referenceImage": reference,
                    "renderScreenshot": render,
                },
                {
                    "viewId": "side",
                    "referenceImage": reference,
                    "renderScreenshot": render,
                    "referenceProvenance": {
                        "origin": "synthetic-hypothesis",
                        "allowedUse": "planning-veto",
                        "source": "test-turnaround",
                    },
                },
            ],
            comparison,
            128,
            128,
            6,
        )
        evidence_path = self.root / "pass-evidence.json"
        write_spec_atomic(evidence_path, evidence)

        resolved = load_document(self.manifest_path).resolved
        verdict_payload = {
            "artifactType": "threejs-sculpt-pass-review",
            "version": 1,
            "reviewId": "assembled-blockout-review",
            "passId": "blockout",
            "specHash": review_spec_hash(resolved, "blockout"),
            "action": "continue",
            "builder": {"contextId": "builder-task"},
            "reviewer": {
                "contextId": "builder-task",
                "role": "independent-reviewer",
                "model": "test-vision",
            },
            "comparisonSha256": evidence["comparisonSha256"],
            "overallScore": 0.95,
            "layerScores": {"silhouette": 0.95},
            "featureReviews": [
                {"id": "overall-silhouette", "score": 0.95, "visible": True}
            ],
            "issues": [],
            "corrections": [],
            "resolvedIssueIds": [],
            "summary": "Independent reviewer verified the assembled blockout across both views.",
        }
        verdict_path = self.root / "pass-verdict.json"
        write_spec_atomic(verdict_path, verdict_payload)
        with self.assertRaisesRegex(ValueError, "passing preflight receipt"):
            append_review(
                [
                    str(self.manifest_path),
                    "--pass-id", "blockout",
                    "--evidence-set-json", str(evidence_path),
                    "--verdict-json", str(verdict_path),
                ]
            )

        with redirect_stdout(io.StringIO()):
            self.assertEqual(
                append_review(
                    [
                        str(self.manifest_path),
                        "--pass-id", "blockout",
                        "--evidence-set-json", str(evidence_path),
                        "--preflight-only",
                    ]
                ),
                0,
            )

        original_render = render.read_bytes()
        write_png_rgb(render, 32, 32, [(20, 40, 80)] * (32 * 32))
        with self.assertRaisesRegex(ValueError, "evidenceFiles"):
            append_review(
                [
                    str(self.manifest_path),
                    "--pass-id", "blockout",
                    "--evidence-set-json", str(evidence_path),
                    "--verdict-json", str(verdict_path),
                ]
            )
        render.write_bytes(original_render)

        with self.assertRaisesRegex(ValueError, "requires --verdict-json"):
            append_review(
                [
                    str(self.manifest_path),
                    "--pass-id", "blockout",
                    "--action", "continue",
                    "--summary", "Builder tries to approve the assembled pass directly.",
                    "--evidence-set-json", str(evidence_path),
                    "--ai-vision-score", "0.95",
                    "--reviewer-model", "builder-model",
                    "--ai-vision-notes", "Builder inspected its own output and claimed success.",
                ]
            )

        with self.assertRaisesRegex(ValueError, "contextId must differ"):
            append_review(
                [
                    str(self.manifest_path),
                    "--pass-id", "blockout",
                    "--evidence-set-json", str(evidence_path),
                    "--verdict-json", str(verdict_path),
                ]
            )

        verdict_payload["reviewer"]["contextId"] = "reviewer-task"
        write_spec_atomic(verdict_path, verdict_payload)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(
                append_review(
                    [
                        str(self.manifest_path),
                        "--pass-id", "blockout",
                        "--evidence-set-json", str(evidence_path),
                        "--verdict-json", str(verdict_path),
                        "--in-place",
                    ]
                ),
                0,
            )
        entry = load_document(self.manifest_path).resolved["reviewHistory"][-1]
        self.assertEqual(entry["reviewerEvidence"]["builderContextId"], "builder-task")
        self.assertEqual(entry["reviewerEvidence"]["reviewerContextId"], "reviewer-task")
        policy_changed = copy.deepcopy(load_document(self.manifest_path).resolved)
        policy_changed["viewHypothesisPolicy"]["promptVersion"] = "identity-turnaround-v2"
        self.assertEqual(pipeline_status(policy_changed)["currentPass"], "blockout")
        verdict_payload["summary"] = "The verdict file was changed after it had already been accepted."
        write_spec_atomic(verdict_path, verdict_payload)
        self.assertEqual(
            pipeline_status(load_document(self.manifest_path).resolved)["currentPass"],
            "blockout",
        )

    def test_init_defaults_to_modular_and_keeps_monolithic_compatibility(self) -> None:
        modular_path = self.root / "init-modular.json"
        legacy_path = self.root / "init-monolithic.json"
        base_args = [
            "Init Test",
            "--complexity",
            "simple",
            "--intended-use",
            "static-render",
            "--quality-profile",
            "balanced",
        ]
        with redirect_stdout(io.StringIO()):
            self.assertEqual(init_main([*base_args, "--out", str(modular_path)]), 0)
            self.assertEqual(
                init_main([*base_args, "--layout", "monolithic", "--out", str(legacy_path)]),
                0,
            )
        self.assertEqual(json.loads(modular_path.read_text())["schemaVersion"], "4.0")
        self.assertEqual(json.loads(legacy_path.read_text())["schemaVersion"], "3.1")
        self.assertEqual(
            json.loads(modular_path.read_text())["globalSpec"]["surfaceTopologyPlan"]["status"],
            "unassessed",
        )

    def test_new_visual_module_requires_topology_decision_first(self) -> None:
        self.manifest["globalSpec"].pop("surfaceTopologyPlan", None)
        write_spec_atomic(self.manifest_path, self.manifest)
        with self.assertRaisesRegex(
            ValueError,
            "manifest globalSpec.surfaceTopologyPlan must be an object",
        ):
            add_module(
                self.manifest_path,
                "face",
                "continuous character face",
                98,
                [],
                "visual",
                "empty",
            )
        self.manifest["globalSpec"]["surfaceTopologyPlan"] = {
            "status": "unassessed",
            "reason": "",
            "decisionRule": "Classify visible systems before modules.",
            "groups": [],
        }
        write_spec_atomic(self.manifest_path, self.manifest)
        structural_path = add_module(
            self.manifest_path,
            "assembly-interface",
            "non-visual assembly connectors",
            1,
            [],
            "structural",
            "empty",
        )
        self.assertTrue(structural_path.is_file())
        with self.assertRaisesRegex(ValueError, "classify construction strategies"):
            add_module(
                self.manifest_path,
                "face",
                "continuous character face",
                98,
                [],
                "visual",
                "empty",
            )

        self.manifest["globalSpec"]["surfaceTopologyPlan"] = {
            "status": "planned",
            "reason": "A label alone is not an executable construction decision.",
            "decisionRule": "Every owned system needs a complete strategy contract.",
            "groups": [{"ownerModuleId": "face"}],
        }
        write_spec_atomic(self.manifest_path, self.manifest)
        with self.assertRaisesRegex(ValueError, "invalid surfaceTopologyPlan"):
            add_module(
                self.manifest_path,
                "face",
                "continuous character face",
                98,
                [],
                "visual",
                "empty",
            )

        self.manifest["globalSpec"]["surfaceTopologyPlan"] = {
            "status": "planned",
            "reason": "Face soft tissue is continuous; accessories remain assembled.",
            "decisionRule": "Semantic regions do not imply separate meshes.",
            "groups": [
                {
                    "id": "face-soft-tissue",
                    "strategy": "continuous-sculpt",
                    "ownerModuleId": "face",
                    "regions": ["head", "cheeks", "muzzle"],
                    "componentRefs": ["face-surface"],
                    "materialRefs": [],
                    "hostComponentRef": "face-surface",
                    "requiredTopology": "single-connected-surface",
                    "rationale": "No physical seam is visible.",
                    "evidenceRefs": ["full-object"],
                    "confidence": 0.9,
                }
            ],
        }
        write_spec_atomic(self.manifest_path, self.manifest)
        module_path = add_module(
            self.manifest_path,
            "face",
            "continuous character face",
            98,
            [],
            "visual",
            "empty",
        )
        self.assertTrue(module_path.is_file())
        context = module_context(self.manifest_path)
        self.assertEqual(
            [group["id"] for group in context["surfaceTopologyGroups"]],
            ["face-soft-tissue"],
        )
        self.assertTrue(
            any(reference.endswith("procedural-patterns.md") for reference in context["references"])
        )

        tampered = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        tampered["globalSpec"]["surfaceTopologyPlan"]["groups"][0].pop(
            "ownerModuleId"
        )
        write_spec_atomic(self.manifest_path, tampered)
        checked = check_module(
            self.manifest_path,
            "face",
            strict_quality=True,
            prepare_generation=True,
        )
        self.assertFalse(checked["ok"], checked)
        self.assertTrue(
            any(
                "must own at least one surfaceTopologyPlan group" in error
                for error in checked["errors"]
            ),
            checked,
        )

    def test_module_context_returns_only_hash_changed_files(self) -> None:
        module_path = self.add_visual_foundation()
        implementation = self.make_implementation()
        first = module_context(self.manifest_path)
        self.assertFalse(first["cacheHit"])
        self.assertEqual(first["moduleId"], "hero")
        self.assertEqual(
            set(first["readFiles"]),
            {
                str(self.manifest_path.resolve()),
                str(module_path.resolve()),
                str(implementation.resolve()),
                *first["references"],
            },
        )
        self.assertTrue(first["references"])
        self.assertTrue(all(Path(reference).is_file() for reference in first["references"]))
        self.assertIn(
            str(
                (
                    ROOT
                    / "skills"
                    / "object-to-threejs-procedural"
                    / "references"
                    / "procedural-patterns.md"
                ).resolve()
            ),
            first["references"],
        )

        second = module_context(self.manifest_path)
        self.assertTrue(second["cacheHit"])
        self.assertEqual(second["readFiles"], [])

        implementation.write_text(
            "export const SCULPT_MODULE_ID = 'hero';\nexport const heroRevision = 2;\n",
            encoding="utf-8",
        )
        changed = module_context(self.manifest_path)
        self.assertFalse(changed["cacheHit"])
        self.assertEqual(changed["readFiles"], [str(implementation.resolve())])

    def test_topology_group_owner_must_match_the_payload_it_classifies(self) -> None:
        module_path = self.add_visual_foundation()
        module = json.loads(module_path.read_text(encoding="utf-8"))
        material_id = module["payload"]["materials"][0]["id"]
        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        groups = manifest["globalSpec"]["surfaceTopologyPlan"]["groups"]
        hero_group = next(group for group in groups if group.get("ownerModuleId") == "hero")
        hero_group.pop("ownerModuleId")
        groups.append(
            {
                "id": "hero-owned-material",
                "strategy": "material-only",
                "ownerModuleId": "hero",
                "regions": ["hero surface response"],
                "componentRefs": [],
                "materialRefs": [material_id],
                "requiredTopology": "no-geometry",
                "rationale": "Keep the material decision explicitly owned by the hero module.",
                "evidenceRefs": ["full-object"],
                "confidence": 0.9,
            }
        )
        write_spec_atomic(self.manifest_path, manifest)

        checked = check_module(
            self.manifest_path,
            "hero",
            strict_quality=True,
            prepare_generation=True,
        )
        self.assertFalse(checked["ok"], checked)
        self.assertTrue(
            any(
                "ownerModuleId must be 'hero' to classify component 'hero-body'" in error
                for error in checked["errors"]
            ),
            checked,
        )

    def test_structural_module_context_skips_visual_workflow(self) -> None:
        add_module(
            self.manifest_path,
            "rig-interface",
            "assembly sockets and hierarchy",
            95,
            [],
            "structural",
            "empty",
        )
        packet = module_context(self.manifest_path)
        self.assertEqual(packet["qualityGate"]["type"], "structural")
        self.assertEqual(packet["implementationWarning"], "")
        self.assertEqual(
            packet["next"],
            {
                "accept": "module accept (runs the same strict module check internally)",
            },
        )
        self.assertNotIn("evaluate", packet["next"])
        self.assertFalse(
            any("implementation" in item["roles"] for item in packet["files"])
        )

    def test_visual_module_cannot_build_without_an_owned_geometry_part(self) -> None:
        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        groups = manifest["globalSpec"]["surfaceTopologyPlan"]["groups"]
        groups[:] = [group for group in groups if group.get("ownerModuleId") != "hero"]
        groups.append(
            {
                "id": "hero-material-only",
                "strategy": "material-only",
                "ownerModuleId": "hero",
                "regions": ["hero material treatment"],
                "componentRefs": [],
                "materialRefs": ["base"],
                "requiredTopology": "no-geometry",
                "rationale": "This deliberately exercises a material-only empty build.",
                "evidenceRefs": ["full-object"],
                "confidence": 0.9,
            }
        )
        write_spec_atomic(self.manifest_path, manifest)
        module_path = add_module(
            self.manifest_path,
            "hero",
            "material-only empty visual module",
            95,
            [],
            "visual",
            "empty",
        )
        module = json.loads(module_path.read_text(encoding="utf-8"))
        module["payload"]["materials"] = [make_base_material("balanced")]
        module["payload"]["repetitionSystems"] = [
            {
                "id": "orphan-grid",
                "componentRef": "root",
                "mode": "grid",
                "count": 1,
                "seed": 1,
                "parameters": {
                    "columns": 1,
                    "rows": 1,
                    "spacing": [1, 1, 0],
                    "origin": [0, 0, 0],
                },
            }
        ]
        module["contract"]["owns"]["materials"] = ["base"]
        module["contract"]["owns"]["repetitionSystems"] = ["orphan-grid"]
        write_spec_atomic(module_path, module)

        checked = check_module(
            self.manifest_path,
            "hero",
            strict_quality=True,
            prepare_generation=True,
        )
        self.assertFalse(checked["ok"], checked)
        self.assertTrue(
            any("no owned executable geometry part" in error for error in checked["errors"]),
            checked,
        )

    def test_fast_build_matches_individual_check_resolve_generate(self) -> None:
        self.add_visual_foundation()
        self.make_implementation()
        legacy_check = check_module(self.manifest_path, "hero", strict_quality=True)
        self.assertTrue(legacy_check["ok"], legacy_check)
        legacy_resolved = self.root / "legacy-resolved.json"
        write_spec_atomic(
            legacy_resolved,
            resolve_manifest(self.manifest_path, selected=["hero"]),
        )
        legacy_generated = self.root / "legacy.generated.ts"
        with redirect_stdout(io.StringIO()):
            self.assertEqual(
                generate_main(
                    [str(legacy_resolved), "--out", str(legacy_generated)]
                ),
                0,
            )

        fast_resolved = self.root / "fast-resolved.json"
        fast_generated = self.root / "fast.generated.ts"
        output = io.StringIO()
        with redirect_stdout(output):
            code = module_cli_main(
                [
                    "build",
                    str(self.manifest_path),
                    "hero",
                    "--resolved-out",
                    str(fast_resolved),
                    "--out",
                    str(fast_generated),
                ]
            )
        payload = json.loads(output.getvalue())
        self.assertEqual(code, 0, payload)
        self.assertTrue(payload["ok"], payload)
        self.assertEqual(
            json.loads(fast_resolved.read_text(encoding="utf-8")),
            json.loads(legacy_resolved.read_text(encoding="utf-8")),
        )
        self.assertEqual(
            fast_generated.read_text(encoding="utf-8"),
            legacy_generated.read_text(encoding="utf-8"),
        )

    def test_fast_build_stops_before_outputs_when_strict_check_fails(self) -> None:
        add_module(
            self.manifest_path,
            "placeholder",
            "unfinished visible placeholder",
            99,
            [],
            "visual",
            "foundation",
        )
        resolved = self.root / "should-not-exist.json"
        generated = self.root / "should-not-exist.generated.ts"
        output = io.StringIO()
        with redirect_stdout(output):
            code = module_cli_main(
                [
                    "build",
                    str(self.manifest_path),
                    "placeholder",
                    "--resolved-out",
                    str(resolved),
                    "--out",
                    str(generated),
                ]
            )
        payload = json.loads(output.getvalue())
        self.assertEqual(code, 1)
        self.assertFalse(payload["ok"])
        self.assertFalse(payload["stages"]["check"]["ok"])
        self.assertNotIn("resolve", payload["stages"])
        self.assertFalse(resolved.exists())
        self.assertFalse(generated.exists())

    def test_fast_evaluate_matches_compare_then_preflight(self) -> None:
        self.add_visual_foundation()
        self.make_implementation()
        _, source_evidence = self.make_evidence("fast-evaluate-source")
        pairs = [
            {
                "viewId": view["viewId"],
                "referenceImage": view["referenceImage"],
                "renderScreenshot": view["renderScreenshot"],
                "referenceProvenance": view.get("referenceProvenance"),
            }
            for view in source_evidence["views"]
        ]
        pairs_path = self.root / "fast-evaluate-pairs.json"
        pairs_path.write_text(json.dumps(pairs), encoding="utf-8")

        legacy_comparison = self.root / "legacy-comparison.png"
        legacy_evidence = self.root / "legacy-evidence.json"
        with redirect_stdout(io.StringIO()):
            self.assertEqual(
                compare_main(
                    [
                        "--pairs-json",
                        str(pairs_path),
                        "--out",
                        str(legacy_comparison),
                        "--manifest-out",
                        str(legacy_evidence),
                        "--sculpt-manifest",
                        str(self.manifest_path),
                        "--module-id",
                        "hero",
                        "--runtime-receipt",
                        source_evidence["renderProvenance"]["runtimeReceiptPath"],
                    ]
                ),
                0,
            )
        legacy_preflight = preflight_module_review(
            self.manifest_path,
            "hero",
            legacy_evidence,
        )
        self.assertTrue(legacy_preflight["ok"], legacy_preflight)

        fast_comparison = self.root / "fast-comparison.png"
        fast_evidence = self.root / "fast-evidence.json"
        output = io.StringIO()
        with redirect_stdout(output):
            code = module_cli_main(
                [
                    "evaluate",
                    str(self.manifest_path),
                    "hero",
                    "--pairs-json",
                    str(pairs_path),
                    "--out",
                    str(fast_comparison),
                    "--manifest-out",
                    str(fast_evidence),
                    "--runtime-receipt",
                    source_evidence["renderProvenance"]["runtimeReceiptPath"],
                ]
            )
        payload = json.loads(output.getvalue())
        self.assertEqual(code, 0, payload)
        self.assertTrue(payload["ok"], payload)
        self.assertEqual(
            payload["stages"]["preflight"]["failures"],
            legacy_preflight["failures"],
        )
        self.assertEqual(file_sha256(fast_comparison), file_sha256(legacy_comparison))

    def test_scheduler_selects_highest_risk_ready_module(self) -> None:
        add_module(
            self.manifest_path,
            "trim",
            "secondary trim",
            20,
            [],
            "structural",
            "empty",
        )
        self.add_foundation("identity", 92)
        status = module_status(self.manifest_path)
        self.assertEqual(status["currentModule"], "identity")
        blocked_check = check_module(self.manifest_path, "trim", strict_quality=True)
        self.assertFalse(blocked_check["ok"])
        self.assertTrue(any("only the current" in item for item in blocked_check["errors"]))
        with self.assertRaisesRegex(ValueError, "only the current"):
            module_cli_main(
                [
                    "resolve",
                    str(self.manifest_path),
                    "--module-id",
                    "trim",
                    "--out",
                    str(self.root / "trim-preview.json"),
                ]
            )
        with self.assertRaisesRegex(ValueError, "only the current"):
            accept_module(self.manifest_path, "trim", None, None, None)

    def test_strict_quality_rejects_unedited_foundation_template(self) -> None:
        add_module(
            self.manifest_path,
            "placeholder",
            "critical identity structure",
            99,
            [],
            "visual",
            "foundation",
        )
        checked = check_module(self.manifest_path, "placeholder", strict_quality=True)
        self.assertFalse(checked["ok"])
        self.assertTrue(any("scaffold placeholder" in item for item in checked["errors"]))

    def test_structural_gate_cannot_hide_visible_geometry(self) -> None:
        with self.assertRaisesRegex(ValueError, "interface/assembly-only"):
            add_module(
                self.manifest_path,
                "fake-structural",
                "misclassified visible face",
                95,
                [],
                "structural",
                "foundation",
            )
        module_path = add_module(
            self.manifest_path,
            "fake-structural",
            "misclassified visible face",
            95,
            [],
            "structural",
            "empty",
        )
        module = json.loads(module_path.read_text(encoding="utf-8"))
        component = make_root_component("Visible face")
        component.update({"id": "visible-face", "parent": "root"})
        material = make_base_material()
        material["id"] = "visible-face-material"
        component["material"] = material["id"]
        module["payload"]["componentTree"] = [component]
        module["payload"]["materials"] = [material]
        module["contract"]["owns"]["components"] = [component["id"]]
        module["contract"]["owns"]["materials"] = [material["id"]]
        self.finalize_module_payload(module)
        write_spec_atomic(module_path, module)
        checked = check_module(self.manifest_path, "fake-structural", strict_quality=True)
        self.assertFalse(checked["ok"])
        self.assertTrue(any("structural gate cannot own visible geometry" in item for item in checked["errors"]))

    def test_structural_gate_still_accepts_assembly_interface_only(self) -> None:
        self.add_foundation("core", 90)
        self.accept_visual("core", "core-before-interface")
        module_path = add_module(
            self.manifest_path,
            "rig-interface",
            "assembly sockets and hierarchy",
            40,
            ["core"],
            "structural",
            "empty",
        )
        module = json.loads(module_path.read_text(encoding="utf-8"))
        assembly = make_root_component("Rig interface")
        assembly.update(
            {
                "id": "rig-interface-root",
                "componentType": "assembly",
                "role": "assembly-interface",
                "importance": 0.4,
                "parent": "root",
            }
        )
        for field in (
            "dimensions",
            "material",
            "geometryDescriptor",
            "surfaceDetail",
            "fidelityTier",
            "primitive",
            "parameters",
            "materialLayers",
        ):
            assembly.pop(field, None)
        module["payload"]["componentTree"] = [assembly]
        module["contract"]["owns"]["components"] = [assembly["id"]]
        write_spec_atomic(module_path, module)
        checked = check_module(self.manifest_path, "rig-interface", strict_quality=True)
        self.assertTrue(checked["ok"], checked)
        status = accept_module(self.manifest_path, "rig-interface", None, None, None)
        self.assertIn("rig-interface", status["acceptedModules"])

    def test_visual_quality_floors_cannot_be_lowered_by_module(self) -> None:
        module_path = self.add_visual_foundation()
        self.make_implementation()
        module = json.loads(module_path.read_text(encoding="utf-8"))
        module["qualityGate"]["minimumScore"] = 0.0
        module["qualityGate"]["requiredLayerScores"] = {
            "silhouetteProportion": 0.0,
            "componentStructure": 0.0,
            "formDetail": 0.0,
            "identity": 0.0,
        }
        module["qualityGate"]["diagnosticThresholds"] = {
            "minimumSilhouetteIou": 0.0,
            "maximumCentroidDelta": 1.0,
            "maximumAspectRatioDelta": 1.0,
            "minimumDetailEnergyRatio": 0.0,
        }
        write_spec_atomic(module_path, module)
        checked = check_module(self.manifest_path, "hero", strict_quality=True)
        self.assertFalse(checked["ok"])
        self.assertTrue(any("non-lowerable" in item for item in checked["errors"]), checked)
        self.assertTrue(any("weakens" in item for item in checked["errors"]), checked)

    def test_project_metadata_cannot_pose_as_module_implementation(self) -> None:
        module_path = self.add_visual_foundation()
        metadata = self.root / "package.json"
        metadata.write_text('{"name":"not-runtime-evidence"}\n', encoding="utf-8")
        module = json.loads(module_path.read_text(encoding="utf-8"))
        module["contract"]["implementationFiles"] = ["package.json"]
        write_spec_atomic(module_path, module)
        checked = check_module(self.manifest_path, "hero", strict_quality=True)
        self.assertFalse(checked["ok"])
        self.assertTrue(any("project metadata" in item for item in checked["errors"]), checked)

    def test_unrelated_runtime_source_cannot_pose_as_module_implementation(self) -> None:
        module_path = self.add_visual_foundation()
        unrelated = self.root / "src" / "body.ts"
        unrelated.parent.mkdir(parents=True, exist_ok=True)
        unrelated.write_text(
            "export const SCULPT_MODULE_ID = 'body';\nexport const bodyRevision = 3;\n",
            encoding="utf-8",
        )
        module = json.loads(module_path.read_text(encoding="utf-8"))
        module["contract"]["implementationFiles"] = ["src/body.ts"]
        write_spec_atomic(module_path, module)
        checked = check_module(self.manifest_path, "hero", strict_quality=True)
        self.assertFalse(checked["ok"])
        self.assertTrue(any("ownership marker" in item for item in checked["errors"]), checked)

    def test_global_manifest_cannot_hide_visible_module_payload(self) -> None:
        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        rogue = make_root_component("Rogue global geometry")
        rogue.update({"id": "rogue-global-part", "parent": "root"})
        manifest["globalSpec"]["componentTree"].append(rogue)
        manifest["globalSpec"]["materials"] = [make_base_material()]
        write_spec_atomic(self.manifest_path, manifest)
        status = module_status(self.manifest_path)
        self.assertFalse(status["assemblyReady"])
        self.assertTrue(any("geometry-free assembly root" in item for item in status["errors"]), status)
        self.assertTrue(any("visible payload belongs" in item for item in status["errors"]), status)

    def test_acceptance_cache_invalidates_changed_module(self) -> None:
        module_path = self.add_foundation()
        self.make_implementation("core")
        checked = check_module(self.manifest_path, "core", strict_quality=True)
        self.assertTrue(checked["ok"], checked)
        accepted = self.accept_visual("core")
        self.assertTrue(accepted["assemblyReady"])
        self.assertTrue(accepted["modules"][0]["cacheHit"])

        module = json.loads(module_path.read_text(encoding="utf-8"))
        module["payload"]["componentTree"][0]["dimensions"]["width"] = 1.2
        write_spec_atomic(module_path, module)
        stale = module_status(self.manifest_path)
        self.assertFalse(stale["assemblyReady"])
        self.assertEqual(stale["modules"][0]["state"], "stale")

    def test_final_validate_and_generate_stay_locked_until_module_acceptance(self) -> None:
        self.add_foundation()
        output = self.root / "model.generated.ts"
        with redirect_stdout(io.StringIO()):
            self.assertEqual(validate_main([str(self.manifest_path), "--json"]), 1)
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                generate_main([str(self.manifest_path), "--out", str(output)])
        self.accept_visual("core")
        with redirect_stdout(io.StringIO()):
            self.assertEqual(validate_main([str(self.manifest_path), "--json"]), 0)
            self.assertEqual(generate_main([str(self.manifest_path), "--out", str(output)]), 0)
        self.assertTrue(output.is_file())

    def test_visual_module_acceptance_is_independent_and_artifact_bound(self) -> None:
        self.add_visual_foundation()
        implementation = self.make_implementation()
        with self.assertRaisesRegex(ValueError, "independent verdict"):
            accept_module(self.manifest_path, "hero", 1.0, None, "builder")

        synthetic_path, synthetic = self.make_evidence("synthetic", synthetic_required=True)
        synthetic_verdict = self.make_verdict("synthetic", synthetic)
        synthetic_preflight = preflight_module_review(
            self.manifest_path, "hero", synthetic_path, [implementation]
        )
        self.assertFalse(synthetic_preflight["ok"])
        self.assertTrue(
            any("synthetic hypothesis" in item for item in synthetic_preflight["failures"])
        )

        evidence_path, evidence = self.make_evidence("accepted")
        unrelated = self.root / "package.json"
        unrelated.write_text('{"name":"not-the-renderer"}\n', encoding="utf-8")
        unrelated_verdict = self.make_verdict("unrelated-file", evidence)
        with self.assertRaisesRegex(ValueError, "exactly match"):
            preflight_module_review(
                self.manifest_path,
                "hero",
                evidence_path,
                [unrelated],
            )
        self_review = self.make_verdict("self-review", evidence, same_context=True)
        with self.assertRaisesRegex(ValueError, "current passing preflight receipt"):
            review_module(
                self.manifest_path,
                "hero",
                self_review,
                evidence_path,
                [implementation],
            )
        self.assertTrue(
            preflight_module_review(
                self.manifest_path, "hero", evidence_path, [implementation]
            )["ok"]
        )
        render_path = Path(evidence["views"][0]["renderScreenshot"])
        original_render = render_path.read_bytes()
        write_png_rgb(render_path, 64, 64, [(20, 40, 80)] * (64 * 64))
        with self.assertRaisesRegex(ValueError, "evidenceFiles"):
            review_module(
                self.manifest_path,
                "hero",
                self_review,
                evidence_path,
                [implementation],
            )
        render_path.write_bytes(original_render)
        with self.assertRaisesRegex(ValueError, "contextId must differ"):
            review_module(
                self.manifest_path,
                "hero",
                self_review,
                evidence_path,
                [implementation],
            )
        original_source = implementation.read_text(encoding="utf-8")
        implementation.write_text(
            "export const SCULPT_MODULE_ID = 'hero';\nexport const heroRevision = 2;\n",
            encoding="utf-8",
        )
        stale_receipt = self.make_verdict("stale-render-receipt", evidence)
        stale_preflight = preflight_module_review(
            self.manifest_path, "hero", evidence_path, [implementation]
        )
        self.assertFalse(stale_preflight["ok"])
        self.assertTrue(
            any("renderProvenance implementation snapshot is stale" in item for item in stale_preflight["failures"])
        )
        implementation.write_text(original_source, encoding="utf-8")
        verdict = self.make_verdict("accepted", evidence)
        status = self.review_after_preflight(
            self.manifest_path,
            "hero",
            verdict,
            evidence_path,
            [implementation],
        )
        self.assertTrue(status["reviewAccepted"], status)
        self.assertTrue(status["assemblyReady"], status)
        implementation.write_text(
            "export const SCULPT_MODULE_ID = 'hero';\nexport const heroRevision = 2;\n",
            encoding="utf-8",
        )
        stale = module_status(self.manifest_path)
        self.assertFalse(stale["assemblyReady"])
        self.assertEqual(stale["modules"][0]["state"], "stale")

    def test_compare_cli_writes_current_module_render_receipt(self) -> None:
        self.add_visual_foundation()
        self.make_implementation()
        _, seed = self.make_evidence("receipt-seed")
        pairs_path = self.root / "receipt-pairs.json"
        pairs = [
            {
                "viewId": view["viewId"],
                "referenceImage": view["referenceImage"],
                "renderScreenshot": view["renderScreenshot"],
                "referenceProvenance": view["referenceProvenance"],
            }
            for view in seed["views"]
        ]
        write_spec_atomic(pairs_path, pairs)
        evidence_path = self.root / "receipt-evidence.json"
        with redirect_stdout(io.StringIO()):
            self.assertEqual(
                compare_main(
                    [
                        "--pairs-json",
                        str(pairs_path),
                        "--out",
                        str(self.root / "receipt-comparison.png"),
                        "--manifest-out",
                        str(evidence_path),
                        "--sculpt-manifest",
                        str(self.manifest_path),
                        "--module-id",
                        "hero",
                        "--runtime-receipt",
                        seed["renderProvenance"]["runtimeReceiptPath"],
                    ]
                ),
                0,
            )
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        self.assertEqual(evidence["renderProvenance"]["moduleId"], "hero")
        self.assertEqual(
            evidence["renderProvenance"]["renderSha256"],
            sorted({view["renderSha256"] for view in evidence["views"]}),
        )

    def test_preflight_rejects_missing_hidden_or_substituted_generated_runtime(self) -> None:
        self.add_visual_foundation()
        implementation = self.make_implementation()
        _, evidence = self.make_evidence("runtime-attestation")

        no_runtime = copy.deepcopy(evidence)
        no_runtime["renderProvenance"] = {
            key: value
            for key, value in no_runtime["renderProvenance"].items()
            if not key.startswith("runtimeReceipt")
        }
        no_runtime["manifestSha256"] = visual_evidence_manifest_sha256(no_runtime)
        no_runtime_path = self.root / "runtime-attestation-missing.json"
        write_spec_atomic(no_runtime_path, no_runtime)
        missing = preflight_module_review(
            self.manifest_path, "hero", no_runtime_path, [implementation]
        )
        self.assertFalse(missing["ok"])
        self.assertTrue(
            any("runtime receipt is missing" in item for item in missing["failures"]),
            missing,
        )

        substituted = copy.deepcopy(evidence)
        runtime = substituted["renderProvenance"]["runtimeReceipt"]
        runtime["rootEffectivelyVisible"] = False
        runtime["unexpectedGeneratedDescendantMeshes"] = ["nested-substitute"]
        runtime["unexpectedVisibleMeshes"] = ["custom-hand-built-substitute"]
        runtime["geometryFingerprint"] = [
            value.replace("BoxGeometry", "ForgedGeometry")
            for value in runtime["geometryFingerprint"]
        ]
        first_primitive = next(iter(runtime["componentPrimitives"]), None)
        if first_primitive:
            runtime["componentPrimitives"][first_primitive] = "forged-primitive"
        runtime_path = Path(substituted["renderProvenance"]["runtimeReceiptPath"])
        write_spec_atomic(runtime_path, runtime)
        substituted["renderProvenance"]["runtimeReceiptSha256"] = file_sha256(runtime_path)
        substituted["manifestSha256"] = visual_evidence_manifest_sha256(substituted)
        substituted_path = self.root / "runtime-attestation-substitute.json"
        write_spec_atomic(substituted_path, substituted)
        bypass = preflight_module_review(
            self.manifest_path, "hero", substituted_path, [implementation]
        )
        self.assertFalse(bypass["ok"])
        self.assertTrue(
            any("generated factory root was hidden" in item for item in bypass["failures"]),
            bypass,
        )
        self.assertTrue(
            any("unexpectedVisibleMeshes" in item for item in bypass["failures"]),
            bypass,
        )
        self.assertTrue(
            any("unexpectedGeneratedDescendantMeshes" in item for item in bypass["failures"]),
            bypass,
        )
        self.assertTrue(
            any("primitive inventory differs" in item for item in bypass["failures"]),
            bypass,
        )
        self.assertTrue(
            any("geometry differs" in item for item in bypass["failures"]),
            bypass,
        )

    def test_preflight_recomputes_generated_factory_instead_of_trusting_receipt(self) -> None:
        self.add_visual_foundation()
        implementation = self.make_implementation()
        _, evidence = self.make_evidence("forged-generated-source")
        provenance = evidence["renderProvenance"]
        build_path = Path(provenance["buildReceiptPath"])
        original_build = json.loads(build_path.read_text(encoding="utf-8"))
        generated_path = Path(original_build["generatedOutput"])
        original_source = generated_path.read_text(encoding="utf-8")
        forged_source = "\n".join(
            "export const createSculptModel = () => undefined;"
            if line.startswith("export const createSculptModel = ")
            else line
            for line in original_source.splitlines()
        )
        generated_path.write_text(forged_source, encoding="utf-8")
        forged_build = {**original_build, "generatedOutputSha256": file_sha256(generated_path)}
        write_spec_atomic(build_path, forged_build)
        forged_evidence = copy.deepcopy(evidence)
        forged_evidence["renderProvenance"]["buildReceipt"] = forged_build
        forged_evidence["renderProvenance"]["buildReceiptSha256"] = file_sha256(build_path)
        forged_evidence["manifestSha256"] = visual_evidence_manifest_sha256(forged_evidence)
        forged_evidence_path = self.root / "forged-generated-evidence.json"
        write_spec_atomic(forged_evidence_path, forged_evidence)
        result = preflight_module_review(
            self.manifest_path,
            "hero",
            forged_evidence_path,
            [implementation],
        )
        generated_path.write_text(original_source, encoding="utf-8")
        write_spec_atomic(build_path, original_build)
        self.assertFalse(result["ok"])
        self.assertTrue(
            any("not the deterministic output" in item for item in result["failures"]),
            result,
        )

    def test_request_input_requires_real_missing_evidence(self) -> None:
        module_path = self.add_visual_foundation()
        self.make_implementation()
        module = json.loads(module_path.read_text(encoding="utf-8"))
        module["payload"]["viewEvidence"].append(
            {
                "id": "rear-observed",
                "view": "rear attachment reference",
                "observations": ["Required to verify the currently occluded rear joint."],
                "confidence": 0.5,
            }
        )
        write_spec_atomic(module_path, module)
        _, evidence = self.make_evidence("request-input-contract")
        invalid_path = self.make_verdict(
            "request-input-without-blocker",
            evidence,
            action="request-input",
        )
        invalid = json.loads(invalid_path.read_text(encoding="utf-8"))
        failures = review_contract_failures(invalid, evidence)
        self.assertTrue(any("requires concrete requiredEvidence" in item for item in failures))

        valid_path = self.make_verdict(
            "request-input-with-blocker",
            evidence,
            action="request-input",
            issues=[
                {
                    "id": "rear-attachment-evidence",
                    "failureClass": "evidence",
                    "severity": "major",
                    "status": "open",
                    "target": "rear attachment topology",
                    "reason": "The required rear joint is occluded in every observed source view.",
                }
            ],
            extra={
                "requiredEvidence": [
                    {
                        "issueId": "rear-attachment-evidence",
                        "missingViewId": "rear-observed",
                        "sourceConstraint": "occluded",
                        "missingEvidence": "An observed rear reference showing the hidden attachment.",
                        "blockedCriterion": "Rear attachment topology cannot be bounded from the front image.",
                        "unblockAction": "Provide one rear or three-quarter source photograph.",
                    }
                ]
            },
        )
        valid = json.loads(valid_path.read_text(encoding="utf-8"))
        self.assertFalse(
            any("requiredEvidence" in item for item in review_contract_failures(valid, evidence))
        )
        fictional = copy.deepcopy(valid)
        fictional["requiredEvidence"][0]["missingViewId"] = "fictional-banana-angle"
        self.assertTrue(
            any("not declared" in item for item in review_contract_failures(fictional, evidence))
        )
        budget = refinement_budget(
            [
                {"action": "refine-code"},
                {"action": "refine-spec"},
                {"action": "request-input"},
            ]
        )
        self.assertTrue(budget["exhausted"])

    def test_issue_id_cannot_launder_an_unresolved_root_cause(self) -> None:
        self.add_visual_foundation()
        implementation = self.make_implementation()
        evidence_path, evidence = self.make_evidence("root-cause-before")
        issue = {
            "id": "silhouette-v1",
            "rootCauseKey": "wrong-continuous-profile",
            "severity": "major",
            "status": "open",
            "target": "hero silhouette",
            "reason": "The executable profile uses disconnected contour blocks.",
        }
        correction = {
            "issueId": "silhouette-v1",
            "target": "hero-body",
            "parameterPath": "profile.sections",
            "change": "Replace disconnected blocks with one continuous profile.",
            "expectedDelta": "The side silhouette becomes continuous.",
        }
        downgraded = {
            "overallScore": 0.82,
            "layerScores": {"silhouette": 0.82},
            "resolvedRootCauseKeys": ["wrong-continuous-profile"],
            "issues": [
                {
                    **issue,
                    "id": "silhouette-minor-alias",
                    "rootCauseKey": "renamed-minor-profile",
                    "severity": "minor",
                }
            ],
            "corrections": [
                {**correction, "issueId": "silhouette-minor-alias"}
            ],
        }
        self.assertTrue(
            any(
                "canonical issue lineage" in item
                for item in _refinement_delta_failures(
                    [
                        {
                            "action": "refine-code",
                            "accepted": False,
                            "overallScore": 0.80,
                            "layerScores": {"silhouette": 0.80},
                            "issues": [issue],
                            "corrections": [correction],
                        }
                    ],
                    downgraded,
                )
            )
        )
        first_verdict = self.make_verdict(
            "root-cause-first",
            evidence,
            action="refine-code",
            issues=[issue],
            corrections=[correction],
            overall_score=0.80,
            layer_score=0.80,
        )
        self.review_after_preflight(
            self.manifest_path,
            "hero",
            first_verdict,
            evidence_path,
            [implementation],
        )
        implementation.write_text(
            "export const SCULPT_MODULE_ID = 'hero';\nexport const heroRevision = 2;\n",
            encoding="utf-8",
        )
        changed_path, changed = self.make_evidence("root-cause-after", render_variant=20)
        changed_preflight = preflight_module_review(
            self.manifest_path, "hero", changed_path, [implementation]
        )
        self.assertTrue(changed_preflight["ok"], changed_preflight)
        relabeled_issue = {
            **issue,
            "id": "silhouette-v2-new-name",
            "rootCauseKey": "renamed-profile-defect",
            "target": "completely renamed contour target",
            "reason": "The same disconnected profile remains visible under a new issue label.",
        }
        relabeled_correction = {
            **correction,
            "issueId": "silhouette-v2-new-name",
            "target": "renamed-body-alias",
            "parameterPath": "renamed.profile.path",
        }
        relabeled_verdict = self.make_verdict(
            "root-cause-relabeled",
            changed,
            action="refine-code",
            issues=[relabeled_issue],
            corrections=[relabeled_correction],
            resolved=["silhouette-v1"],
            resolved_root_causes=["wrong-continuous-profile"],
            overall_score=0.82,
            layer_score=0.82,
        )
        with self.assertRaisesRegex(ValueError, "new blocking root cause"):
            review_module(
                self.manifest_path,
                "hero",
                relabeled_verdict,
                changed_path,
                [implementation],
            )

    def test_visual_module_rejects_blockout_and_diagnostic_mismatch(self) -> None:
        module_path = self.add_visual_foundation()
        module = json.loads(module_path.read_text(encoding="utf-8"))
        module["payload"]["componentTree"][0]["fidelityTier"] = "blockout"
        write_spec_atomic(module_path, module)
        checked = check_module(self.manifest_path, "hero", strict_quality=True)
        self.assertFalse(checked["ok"])
        self.assertTrue(any("fidelityTier 'blockout'" in item for item in checked["errors"]))

        module["payload"]["componentTree"][0].pop("fidelityTier")
        write_spec_atomic(module_path, module)
        missing_tier = check_module(self.manifest_path, "hero", strict_quality=True)
        self.assertTrue(any("no finished fidelityTier" in item for item in missing_tier["errors"]))

        module["payload"]["componentTree"][0]["fidelityTier"] = "form"
        write_spec_atomic(module_path, module)
        implementation = self.make_implementation()
        evidence_path, evidence = self.make_evidence("low-iou", render_shift=24)
        verdict = self.make_verdict("low-iou", evidence)
        result = preflight_module_review(
            self.manifest_path,
            "hero",
            evidence_path,
            [implementation],
        )
        self.assertFalse(result["ok"])
        self.assertTrue(any("silhouetteIou" in item for item in result["failures"]))
        with self.assertRaisesRegex(ValueError, "current passing preflight receipt"):
            review_module(
                self.manifest_path,
                "hero",
                verdict,
                evidence_path,
                [implementation],
            )

    def test_front_match_cannot_hide_a_failed_side_view(self) -> None:
        self.add_visual_foundation()
        implementation = self.make_implementation()
        evidence_path, _ = self.make_evidence(
            "front-good-side-bad",
            render_shift=0,
            side_render_shift=24,
        )
        result = preflight_module_review(
            self.manifest_path,
            "hero",
            evidence_path,
            [implementation],
        )
        self.assertFalse(result["ok"])
        self.assertTrue(
            any("view 'side' silhouetteIou" in item for item in result["failures"]),
            result,
        )
        self.assertFalse(
            any("view 'reference' silhouetteIou" in item for item in result["failures"]),
            result,
        )

    def test_visual_module_recomputes_diagnostics_instead_of_trusting_json(self) -> None:
        self.add_visual_foundation()
        implementation = self.make_implementation()
        evidence_path, evidence = self.make_evidence("forged-diagnostics", render_shift=24)
        for view in evidence["views"]:
            diagnostics = view["fitDiagnostics"]
            diagnostics["silhouetteIou"] = 0.99
            diagnostics["centroidDelta"] = 0.0
            diagnostics["aspectRatioDelta"] = 0.0
            diagnostics["maskDiagnostics"]["warnings"] = []
            diagnostics["maskDiagnostics"]["reference"]["foregroundCoverage"] = 0.4
            diagnostics["maskDiagnostics"]["render"]["foregroundCoverage"] = 0.4
            diagnostics["appearance"]["detailEnergyRatio"] = 0.99
            diagnostics["appearance"]["sampleCounts"] = {"reference": 4096, "render": 4096}
        evidence["manifestSha256"] = visual_evidence_manifest_sha256(evidence)
        write_spec_atomic(evidence_path, evidence)
        verdict = self.make_verdict("forged-diagnostics", evidence)
        result = preflight_module_review(
            self.manifest_path,
            "hero",
            evidence_path,
            [implementation],
        )
        self.assertFalse(result["ok"])
        self.assertTrue(
            any("deterministic pixel recomputation" in item for item in result["failures"]),
            result,
        )
        with self.assertRaisesRegex(ValueError, "current passing preflight receipt"):
            review_module(
                self.manifest_path,
                "hero",
                verdict,
                evidence_path,
                [implementation],
            )

    def test_refine_must_change_output_and_close_previous_issues(self) -> None:
        self.add_visual_foundation()
        implementation = self.make_implementation()
        evidence_path, evidence = self.make_evidence("attempt-one")
        issues = [
            {
                "id": "hero-form",
                "severity": "major",
                "status": "open",
                "target": "hero silhouette",
                "reason": "The reviewed form is too generic and needs a concrete contour correction.",
            }
        ]
        corrections = [
            {
                "issueId": "hero-form",
                "target": "hero-body",
                "parameterPath": "geometryDescriptor.parameters.profile",
                "change": "Widen the upper contour and taper the lower third.",
                "expectedDelta": "The next render has a visibly distinct reference-matching silhouette.",
            }
        ]
        refine_verdict = self.make_verdict(
            "attempt-one",
            evidence,
            action="refine-code",
            issues=issues,
            corrections=corrections,
            overall_score=0.80,
            layer_score=0.80,
        )
        first = self.review_after_preflight(
            self.manifest_path,
            "hero",
            refine_verdict,
            evidence_path,
            [implementation],
        )
        self.assertFalse(first["reviewAccepted"])

        unchanged = preflight_module_review(
            self.manifest_path,
            "hero",
            evidence_path,
            [implementation],
        )
        self.assertFalse(unchanged["ok"])
        self.assertTrue(any("no new render" in item for item in unchanged["failures"]))
        self.assertTrue(
            any("executable code change" in item for item in unchanged["failures"])
        )

        implementation.write_text(
            "export const SCULPT_MODULE_ID = 'hero';\nexport const heroRevision = 2;\n",
            encoding="utf-8",
        )
        # Normal workflows reuse fixed output names. The first reviewed render must
        # survive this overwrite in the immutable cache snapshot.
        changed_evidence_path, changed_evidence = self.make_evidence(
            "attempt-one", render_variant=12
        )
        changed_verdict = self.make_verdict(
            "attempt-three",
            changed_evidence,
            resolved=["hero-form"],
        )
        accepted = self.review_after_preflight(
            self.manifest_path,
            "hero",
            changed_verdict,
            changed_evidence_path,
            [implementation],
        )
        self.assertTrue(accepted["reviewAccepted"], accepted)
        cache = json.loads(Path(accepted["cachePath"]).read_text(encoding="utf-8"))
        self.assertEqual(len(cache["reviewAttempts"]["hero"]), 2)

    def test_mixed_refinement_is_one_atomic_batch_before_one_review(self) -> None:
        module_path = self.add_visual_foundation()
        implementation = self.make_implementation()
        evidence_path, evidence = self.make_evidence("mixed-batch-before")
        issues = [
            {
                "id": "body-proportion",
                "severity": "major",
                "status": "open",
                "target": "body proportion",
                "reason": "The module spec keeps the body too wide for the reference.",
            },
            {
                "id": "body-contour-code",
                "severity": "major",
                "status": "open",
                "target": "body contour implementation",
                "reason": "The executable contour ignores the specified upper taper.",
            },
        ]
        corrections = [
            {
                "issueId": "body-proportion",
                "scope": "spec",
                "target": "hero-body",
                "parameterPath": "dimensions.width",
                "change": "Reduce the declared body width before rebuilding geometry.",
                "expectedDelta": "The next render has a narrower reference-matching silhouette.",
            },
            {
                "issueId": "body-contour-code",
                "scope": "code",
                "target": "hero-body",
                "parameterPath": "createHeroBody.profile",
                "change": "Apply the upper taper in the executable loft profile.",
                "expectedDelta": "The upper contour visibly tapers in every reviewed view.",
            },
        ]
        fake_mixed_verdict_path = self.make_verdict(
            "mixed-batch-resolved-scope",
            evidence,
            action="refine-batch",
            issues=[issues[0], {**issues[1], "status": "resolved"}],
            corrections=corrections,
            overall_score=0.80,
            layer_score=0.80,
        )
        fake_mixed_verdict = json.loads(
            fake_mixed_verdict_path.read_text(encoding="utf-8")
        )
        fake_failures = review_contract_failures(fake_mixed_verdict, evidence)
        self.assertTrue(
            any("open issue for refinement" in item for item in fake_failures),
            fake_failures,
        )
        self.assertEqual(
            correction_batch_from_verdict(fake_mixed_verdict)["scopes"],
            ["spec"],
        )
        verdict = self.make_verdict(
            "mixed-batch-before",
            evidence,
            action="refine-batch",
            issues=issues,
            corrections=corrections,
            overall_score=0.80,
            layer_score=0.80,
        )
        first = self.review_after_preflight(
            self.manifest_path,
            "hero",
            verdict,
            evidence_path,
            [implementation],
        )
        self.assertEqual(first["state"], "needs-refinement")
        batch = first["pendingCorrectionBatch"]
        self.assertTrue(batch["atomic"])
        self.assertEqual(batch["scopes"], ["code", "spec"])
        self.assertEqual(batch["correctionCount"], 2)
        self.assertEqual(
            first["correctionBatchProgress"]["remainingScopes"],
            ["code", "spec"],
        )

        implementation.write_text(
            "export const SCULPT_MODULE_ID = 'hero';\nexport const heroRevision = 2;\n",
            encoding="utf-8",
        )
        code_only_path, _ = self.make_evidence(
            "mixed-batch-code-only", render_variant=12
        )
        code_only = preflight_module_review(
            self.manifest_path,
            "hero",
            code_only_path,
            [implementation],
        )
        self.assertFalse(code_only["ok"])
        self.assertTrue(
            any("requires a module spec change" in item for item in code_only["failures"]),
            code_only,
        )
        code_progress = module_status(self.manifest_path)["correctionBatchProgress"]
        self.assertEqual(code_progress["changedScopes"], ["code"])
        self.assertEqual(code_progress["remainingScopes"], ["spec"])

        module = json.loads(module_path.read_text(encoding="utf-8"))
        module["payload"]["componentTree"][0]["dimensions"]["width"] = 0.82
        write_spec_atomic(module_path, module)
        complete_path, _ = self.make_evidence(
            "mixed-batch-complete", render_variant=18
        )
        complete = preflight_module_review(
            self.manifest_path,
            "hero",
            complete_path,
            [implementation],
        )
        self.assertTrue(complete["ok"], complete)
        complete_status = module_status(self.manifest_path)
        self.assertEqual(complete_status["state"], "ready-to-render")
        self.assertTrue(complete_status["correctionBatchProgress"]["readyToRender"])

        residual_issue = {
            "id": "residual-contour",
            "severity": "minor",
            "status": "open",
            "target": "residual contour",
            "reason": "The improved contour still has one independently observed hard corner.",
        }
        residual_correction = {
            "issueId": "residual-contour",
            "target": "hero-body",
            "parameterPath": "createHeroBody.profile.cornerRadius",
            "change": "Round the remaining hard corner in the executable contour.",
            "expectedDelta": "The residual hard corner disappears in front and side views.",
        }
        stalled_verdict = self.make_verdict(
            "mixed-batch-stalled",
            json.loads(complete_path.read_text(encoding="utf-8")),
            action="refine-code",
            issues=[residual_issue],
            corrections=[residual_correction],
            resolved=["body-proportion", "body-contour-code"],
            overall_score=0.80,
            layer_score=0.80,
        )
        with self.assertRaisesRegex(ValueError, "independently measured progress"):
            review_module(
                self.manifest_path,
                "hero",
                stalled_verdict,
                complete_path,
                [implementation],
            )
        second_verdict = self.make_verdict(
            "mixed-batch-second",
            json.loads(complete_path.read_text(encoding="utf-8")),
            action="refine-code",
            issues=[residual_issue],
            corrections=[residual_correction],
            resolved=["body-proportion", "body-contour-code"],
            overall_score=0.82,
            layer_score=0.82,
        )
        second = review_module(
            self.manifest_path,
            "hero",
            second_verdict,
            complete_path,
            [implementation],
        )
        self.assertTrue(second["refinementBudget"]["exhausted"])

        implementation.write_text(
            "export const SCULPT_MODULE_ID = 'hero';\nexport const heroRevision = 3;\n",
            encoding="utf-8",
        )
        third_evidence_path, third_evidence = self.make_evidence(
            "mixed-batch-third", render_variant=26
        )
        third_preflight = preflight_module_review(
            self.manifest_path,
            "hero",
            third_evidence_path,
            [implementation],
        )
        self.assertTrue(third_preflight["ok"], third_preflight)
        self.assertTrue(third_preflight["refinementBudget"]["exhausted"])
        third_verdict = self.make_verdict(
            "mixed-batch-third",
            third_evidence,
            action="refine-code",
            issues=[
                {
                    **residual_issue,
                    "id": "third-partial-fix",
                    "reason": "A third partial fix must be stopped by the bounded workflow.",
                }
            ],
            corrections=[
                {
                    **residual_correction,
                    "issueId": "third-partial-fix",
                }
            ],
            resolved=["residual-contour"],
            overall_score=0.84,
            layer_score=0.84,
        )
        with self.assertRaisesRegex(ValueError, "refinement budget is exhausted"):
            review_module(
                self.manifest_path,
                "hero",
                third_verdict,
                third_evidence_path,
                [implementation],
            )

        strategy_verdict = self.make_verdict(
            "mixed-batch-strategy-reset",
            third_evidence,
            action="strategy-reset",
            extra={
                "strategyId": "continuous-profile-v2",
                "strategyChange": "Replace the stacked contour pieces with one continuous authored profile.",
                "rootCauseKeys": ["body-contour-code"],
                "falsifyingCheck": "Reject the strategy if the side-view contour still contains the hard step.",
            },
        )
        reset = review_module(
            self.manifest_path,
            "hero",
            strategy_verdict,
            third_evidence_path,
            [implementation],
        )
        self.assertEqual(reset["refinementBudget"]["usedBatches"], 0)
        self.assertEqual(reset["refinementBudget"]["usedStrategyResets"], 1)

        unchanged_after_reset = preflight_module_review(
            self.manifest_path,
            "hero",
            third_evidence_path,
            [implementation],
        )
        self.assertFalse(unchanged_after_reset["ok"])
        self.assertTrue(
            any("strategy-reset requires" in item for item in unchanged_after_reset["failures"]),
            unchanged_after_reset,
        )

        implementation.write_text(
            "export const SCULPT_MODULE_ID = 'hero';\nexport const heroRevision = 4;\n",
            encoding="utf-8",
        )
        revision_only_path, _ = self.make_evidence(
            "mixed-batch-revision-only", render_variant=34
        )
        revision_only = preflight_module_review(
            self.manifest_path,
            "hero",
            revision_only_path,
            [implementation],
        )
        self.assertFalse(revision_only["ok"])
        self.assertTrue(
            any("different topology/geometry" in item for item in revision_only["failures"]),
            revision_only,
        )
        reset_module = json.loads(module_path.read_text(encoding="utf-8"))
        reset_module["payload"]["componentTree"][0]["dimensions"]["width"] = 0.76
        write_spec_atomic(module_path, reset_module)
        tuning_only_path, _ = self.make_evidence(
            "mixed-batch-tuning-only", render_variant=36
        )
        tuning_only = preflight_module_review(
            self.manifest_path,
            "hero",
            tuning_only_path,
            [implementation],
        )
        self.assertFalse(tuning_only["ok"])
        self.assertTrue(
            any("different topology/geometry" in item for item in tuning_only["failures"]),
            tuning_only,
        )
        reset_module["payload"]["componentTree"][0]["primitive"] = "sphere"
        write_spec_atomic(module_path, reset_module)
        reset_evidence_path, reset_evidence = self.make_evidence(
            "mixed-batch-new-strategy", render_variant=38
        )
        reset_preflight = preflight_module_review(
            self.manifest_path,
            "hero",
            reset_evidence_path,
            [implementation],
        )
        self.assertTrue(reset_preflight["ok"], reset_preflight)
        post_reset_verdict = self.make_verdict(
            "mixed-batch-post-reset",
            reset_evidence,
            action="refine-code",
            issues=[residual_issue],
            corrections=[residual_correction],
            overall_score=0.85,
            layer_score=0.85,
        )
        post_reset = review_module(
            self.manifest_path,
            "hero",
            post_reset_verdict,
            reset_evidence_path,
            [implementation],
        )
        self.assertEqual(post_reset["refinementBudget"]["usedBatches"], 1)

    def test_comment_and_one_pixel_do_not_count_as_refinement(self) -> None:
        self.add_visual_foundation()
        implementation = self.make_implementation()
        evidence_path, evidence = self.make_evidence("no-op-before")
        issue = {
            "id": "identity-shape",
            "severity": "major",
            "status": "open",
            "target": "identity silhouette",
            "reason": "The visible identity form needs a material visual correction.",
        }
        correction = {
            "issueId": "identity-shape",
            "target": "hero-body",
            "parameterPath": "geometryDescriptor.parameters.profile",
            "change": "Change the visible contour, not metadata.",
            "expectedDelta": "The corrected close-up is visibly different.",
        }
        refine = self.make_verdict(
            "no-op-before",
            evidence,
            action="refine-code",
            issues=[issue],
            corrections=[correction],
            overall_score=0.80,
            layer_score=0.80,
        )
        self.review_after_preflight(
            self.manifest_path, "hero", refine, evidence_path, [implementation]
        )

        implementation.write_text(
            implementation.read_text(encoding="utf-8") + "// claimed refinement only\n",
            encoding="utf-8",
        )
        after_path, after = self.make_evidence("no-op-after", single_pixel_delta=True)
        result = preflight_module_review(
            self.manifest_path,
            "hero",
            after_path,
            [implementation],
        )
        self.assertFalse(result["ok"])
        self.assertTrue(any("executable code change" in item for item in result["failures"]), result)
        self.assertTrue(any("perceptible-change floor" in item for item in result["failures"]), result)

    def test_assembly_requires_declared_coverage_and_full_strict_spec(self) -> None:
        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        manifest["globalSpec"]["qualityContract"]["featureGroups"].append(
            {
                "id": "hero-detail",
                "name": "Hero detail",
                "required": True,
                "qualityCriteria": ["The hero detail is visible and reference-specific."],
                "evidenceRefs": ["reference"],
                "failureModes": ["The detail is omitted or generic."],
            }
        )
        write_spec_atomic(self.manifest_path, manifest)
        self.add_visual_foundation(covers=[])
        implementation = self.make_implementation()
        evidence_path, evidence = self.make_evidence("coverage-missing")
        verdict = self.make_verdict("coverage-missing", evidence)
        result = self.review_after_preflight(
            self.manifest_path,
            "hero",
            verdict,
            evidence_path,
            [implementation],
        )
        self.assertTrue(result["reviewAccepted"])
        self.assertFalse(result["assemblyReady"])
        self.assertEqual(result["coverage"]["missing"], ["hero-detail"])


    def test_assembly_runs_full_strict_validation_after_module_acceptance(self) -> None:
        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        manifest["globalSpec"]["qualityContract"]["minimumSpecDepth"]["macroComponents"] = 8
        write_spec_atomic(self.manifest_path, manifest)
        self.add_foundation()
        status = self.accept_visual("core", "shallow-core")
        self.assertTrue(status["reviewAccepted"])
        self.assertFalse(status["assemblyReady"])
        self.assertTrue(
            any("macroComponents" in item for item in status["assemblyValidationErrors"]),
            status,
        )

    def test_claimed_feature_coverage_needs_visible_independent_review(self) -> None:
        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        manifest["globalSpec"]["qualityContract"]["featureGroups"].append(
            {
                "id": "hero-detail",
                "name": "Hero detail",
                "required": True,
                "qualityCriteria": ["The hero detail is visible and reference-specific."],
                "evidenceRefs": ["reference"],
                "failureModes": ["The detail is omitted or generic."],
            }
        )
        write_spec_atomic(self.manifest_path, manifest)
        self.add_visual_foundation(covers=["hero-detail"])
        implementation = self.make_implementation()
        evidence_path, evidence = self.make_evidence("claimed-coverage")
        verdict = self.make_verdict("claimed-coverage", evidence, feature_reviews=[])
        result = self.review_after_preflight(
            self.manifest_path,
            "hero",
            verdict,
            evidence_path,
            [implementation],
        )
        self.assertFalse(result["reviewAccepted"])
        self.assertTrue(
            any("hero-detail" in item and "no independent review" in item for item in result["reviewFailures"]),
            result,
        )

    def test_dependency_must_use_exported_connector(self) -> None:
        core_path = self.add_foundation("core", 90)
        core = json.loads(core_path.read_text(encoding="utf-8"))
        core["contract"]["connectors"] = [
            {
                "id": "core-top",
                "componentRef": "core-body",
                "position": [0, 0.5, 0],
                "rotation": [0, 0, 0],
            }
        ]
        write_spec_atomic(core_path, core)
        self.accept_visual("core")

        addon_path = add_module(
            self.manifest_path,
            "addon",
            "secondary rigid attachment",
            60,
            ["core"],
            "visual",
            "empty",
        )
        addon = json.loads(addon_path.read_text(encoding="utf-8"))
        material = make_base_material()
        material["id"] = "addon-material"
        component = make_root_component("Addon")
        component.update(
            {
                "id": "addon-body",
                "parent": "core-body",
                "material": "addon-material",
                "attachment": {
                    "parentId": "core-body",
                    "parentSocket": "wrong-socket",
                    "localStart": [0, 0, 0],
                    "localEnd": [0, 0.1, 0],
                    "contactType": "embedded",
                    "overlap": 0.02,
                    "gapTolerance": 0.002,
                    "evidenceRefs": ["full-object"],
                },
            }
        )
        addon["payload"]["componentTree"] = [component]
        addon["payload"]["materials"] = [material]
        addon["qualityGate"]["requiredLayerScores"]["materialSurface"] = addon["qualityGate"]["minimumScore"]
        addon["contract"]["owns"]["components"] = ["addon-body"]
        addon["contract"]["owns"]["materials"] = ["addon-material"]
        self.finalize_module_payload(addon)
        write_spec_atomic(addon_path, addon)
        self.make_implementation("addon")

        rejected = check_module(self.manifest_path, "addon")
        self.assertFalse(rejected["ok"])
        self.assertTrue(any("exported connector" in item for item in rejected["errors"]))
        addon["payload"]["componentTree"][0]["attachment"]["parentSocket"] = "core-top"
        write_spec_atomic(addon_path, addon)
        self.make_implementation("addon")
        accepted = check_module(self.manifest_path, "addon", strict_quality=True)
        self.assertTrue(accepted["ok"], accepted)

    def test_dependency_internal_change_preserves_dependent_cache(self) -> None:
        core_path = self.add_foundation("core", 90)
        self.accept_visual("core")
        addon_path = add_module(
            self.manifest_path,
            "addon",
            "independent secondary block",
            50,
            ["core"],
            "visual",
            "foundation",
        )
        addon = json.loads(addon_path.read_text(encoding="utf-8"))
        addon["payload"]["componentTree"][0]["id"] = "addon-body"
        addon["payload"]["componentTree"][0]["material"] = "addon-material"
        addon["payload"]["materials"][0]["id"] = "addon-material"
        addon["contract"]["owns"]["components"] = ["addon-body"]
        addon["contract"]["owns"]["materials"] = ["addon-material"]
        self.finalize_module_payload(addon)
        write_spec_atomic(addon_path, addon)
        self.accept_visual("addon")
        self.assertTrue(module_status(self.manifest_path)["assemblyReady"])

        core = json.loads(core_path.read_text(encoding="utf-8"))
        core["payload"]["componentTree"][0]["dimensions"]["depth"] = 1.1
        write_spec_atomic(core_path, core)
        status = module_status(self.manifest_path)
        rows = {item["id"]: item for item in status["modules"]}
        self.assertEqual(rows["core"]["state"], "stale")
        self.assertEqual(rows["addon"]["state"], "accepted")

    def test_pipeline_sync_does_not_invalidate_module_cache(self) -> None:
        self.add_foundation()
        self.accept_visual("core")
        before = module_status(self.manifest_path)["modules"][0]["moduleHash"]
        with redirect_stdout(io.StringIO()):
            self.assertEqual(orchestrator_main(["sync", str(self.manifest_path)]), 0)
        after = module_status(self.manifest_path)
        self.assertTrue(after["assemblyReady"])
        self.assertTrue(after["modules"][0]["cacheHit"])
        self.assertEqual(after["modules"][0]["moduleHash"], before)

    def test_global_contract_change_invalidates_module_cache(self) -> None:
        self.add_foundation()
        self.accept_visual("core")
        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        manifest["globalSpec"]["silhouette"]["boundingShape"] = "wider rounded box"
        write_spec_atomic(self.manifest_path, manifest)
        status = module_status(self.manifest_path)
        self.assertFalse(status["assemblyReady"])
        self.assertEqual(status["modules"][0]["state"], "stale")

    def test_document_save_routes_material_back_to_owner(self) -> None:
        module_path = self.add_foundation()
        document = load_document(self.manifest_path)
        document.resolved["materials"][0]["baseColor"] = "#123456"
        original_review_count = len(document.resolved["reviewHistory"])
        document.resolved["reviewHistory"].append({"passId": "blockout", "action": "refine-spec"})
        save_document(document)

        module = json.loads(module_path.read_text(encoding="utf-8"))
        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(module["payload"]["materials"][0]["baseColor"], "#123456")
        self.assertEqual(len(manifest["globalSpec"]["reviewHistory"]), original_review_count + 1)
        resolved = resolve_manifest(self.manifest_path)
        self.assertEqual(resolved["materials"][0]["baseColor"], "#123456")


if __name__ == "__main__":
    unittest.main()
