# Self-Correction Loop Reference

Use this reference when a model construction pass has just finished.

## Review Order

1. Capture or collect a rendered screenshot for the current browser view.
2. Select critical semantic systems up to the spec policy cap and only the suspicious important systems. Keep every visible face and hand as an independent critical target.
3. Create one no-crop contact sheet and hash-bound manifest containing every required observed view plus registered synthetic diagnostic views with `sculpt compare`, including declared face/hand close-ups.
4. Run `module preflight` for a module or `review --preflight-only` for an assembled pass. A pass writes a hash-bound receipt; if inputs change or preflight fails, return directly to the builder and do not create a reviewer.
5. If preflight passes, spawn a fresh Codex vision reviewer context that did not build the module/pass. Give it the raw images, exact contact-sheet hash, and contracts, but no builder score, defense, or proposed verdict.
6. The reviewer inspects every current required/diagnostic view once, reports all currently visible actionable issues, and decides whether each correction belongs to spec or executable code. Do not deliberately defer a known issue to another review attempt.
7. The reviewer chooses exactly one action:
   - `continue`
   - `refine-spec`
   - `refine-code`
   - `refine-batch` when the complete correction set changes both spec and code
   - `strategy-reset` when two complete atomic batches prove the representation itself is wrong
   - `request-input`
   - `stop`
8. Copy the verdict unchanged into `module review` or modular assembled `review --verdict-json`. Recording consumes the receipt; every later review attempt starts with one fresh deterministic preflight.

For visual passes, `continue` requires every pass-declared view, verified image hashes/dimensions, a reviewer bound to the comparison hash, a global AI vision score at or above the non-lowerable spec threshold, required layer scores, and every critical feature at or above its own threshold. Module evidence also carries a render receipt for the exact module/implementation snapshot. Image diagnostics are recomputed from the referenced pixels, are veto-only, and never become the acceptance authority. Real-time optimization repeats this visual gate against the final output and may not regress beyond the configured tolerance.

## Independent verdict contract

Write one JSON verdict per review attempt. `contextId` values must identify different builder and reviewer contexts; a model name alone is not independence.

The CLI rejects identical context IDs and builder-side score overrides, but cannot cryptographically prove who produced a JSON file. Real independence is therefore an orchestration rule: actually spawn/use a fresh reviewer context and copy its verdict without improving its scores. Treat the ID check as an accidental-self-review guard, not trusted attestation.

```json
{
  "artifactType": "threejs-sculpt-module-review",
  "version": 1,
  "reviewId": "face-r2",
  "action": "continue",
  "builder": {"contextId": "builder-task-id"},
  "reviewer": {"contextId": "fresh-reviewer-id", "role": "independent-reviewer", "model": "vision-model"},
  "comparisonSha256": "<exact evidence comparison hash>",
  "overallScore": 0.88,
  "layerScores": {"silhouetteProportion": 0.9, "componentStructure": 0.88, "formDetail": 0.86},
  "featureReviews": [{"id": "face-identity", "score": 0.9, "visible": true, "viewIds": ["face-closeup"]}],
  "issues": [],
  "corrections": [],
  "resolvedIssueIds": [],
  "resolvedRootCauseKeys": [],
  "summary": "Concrete comparison result."
}
```

For an assembled pass, use the same body with `artifactType: "threejs-sculpt-pass-review"` and add the current `passId` and `specHash`. The CLI rejects a stale pass/spec/comparison binding. Do not also pass manual AI scores, model names, notes, layer scores, or feature reviews; the verdict is the sole reviewer authority.

For `refine-spec`, `refine-code`, or `refine-batch`, every open issue needs a stable `id`, stable semantic `rootCauseKey`, a `failureClass` (`topology|geometry|proportion|attachment|material|surface|lighting|evidence|performance|other`), severity, target, reason, and falsifiable `evidenceCheck` plus one correction containing `issueId`, `target`, `parameterPath`, `change`, and `expectedDelta`. `refine-batch` corrections additionally require `scope: spec|code` and must cover both scopes. The CLI returns one atomic `pendingCorrectionBatch`: apply every listed correction in one builder work phase, with no intermediate render, preflight, or reviewer call. Then render all required views once and review once. Changing issue/root-cause labels does not close a defect: the gate also derives canonical lineage from failure class, target, and correction paths.

One strategy allows at most two atomic refinement batches. A second batch is allowed only after the first produced independently measured score improvement and explicitly closed its prior blocking root-cause keys. If the second still fails, use one `strategy-reset` with `strategyId`, `strategyChange`, affected `rootCauseKeys`, and a `falsifyingCheck`; the next render is blocked until the topology/geometry representation signature changes. This is a bounded replan, not an unused code edit or extra micro-refinement. `request-input` requires an open `failureClass: evidence` issue plus non-empty `requiredEvidence[]`; each item names its `issueId`, absent `missingViewId`, `sourceConstraint`, `missingEvidence`, `blockedCriterion`, and `unblockAction`. It does not reset spent batch budget. `stop` requires a concrete reason plus verified `stopEvidence`.

The cheap preflight rejects a pending batch before reviewer creation when its required spec/code scope did not change, the previous render/comparison was reused, or the pixel delta is imperceptible. It also deterministically regenerates the factory from the current resolved spec and rejects screenshots without the matching live receipt, a detached/hidden generated root, missing/mismatched generated components, or visible untracked substitute meshes inside or outside that root. A later `continue` must name previous blocking keys in `resolvedRootCauseKeys` and improve at least one independent quality score. Comment-only edits, re-encoding, or a one-pixel change never reach the reviewer.

## Root Cause Guide

Use `refine-spec` when:

- a component is missing or invented incorrectly
- the primitive family is wrong
- proportions or coordinate frame are wrong
- material layer is under-specified
- local features are missing from the spec
- evidence refs are absent or contradict the image
- user expectation cannot be represented by current build passes

Use `refine-code` when:

- the spec is clear but generated geometry is wrong
- material parameters were not implemented
- local masks/noise/wear are missing in code
- hierarchy/pivots do not match the spec
- browser render has obvious artifacts
- performance can be improved without changing the spec

Use `request-input` when:

- the image hides essential geometry
- material cannot be inferred from the provided view
- exact branding/text/ornament is required
- the requested fidelity is incompatible with a single image

Use `stop` when:

- target fidelity is reached
- user accepted current approximation
- remaining issues require new references, manual modeling, or non-procedural assets

## Fidelity Estimate

Use a practical 0-1 scale:

- `0.2`: only rough primitive placeholder
- `0.4`: silhouette recognizable, structure incomplete
- `0.6`: macro and meso forms mostly correct, material/detail weak
- `0.75`: object reads correctly, local details approximate
- `0.85`: strong procedural match for real-time use
- `0.95`: near-reference, usually requires multiple views or manual art

Do not claim `0.9+` from a single ambiguous image unless the object is simple and symmetrical.
