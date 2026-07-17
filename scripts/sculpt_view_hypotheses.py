#!/usr/bin/env python3
"""Register and verify cached ImageGen view hypotheses for cross-view vetoes."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

from sculpt_contract import (
    adaptive_hypothesis_views,
    file_sha256,
    image_dimensions,
    parse_json,
    write_spec_atomic,
)


VIEW_HYPOTHESIS_ARTIFACT_TYPE = "threejs-sculpt-view-hypotheses"
VIEW_HYPOTHESIS_VERSION = 1
DEFAULT_PROMPT_VERSION = "identity-turnaround-v1"
SPATIAL_VIEW_IDS = {"three-quarter", "side", "back"}


def make_view_hypothesis_policy(
    complexity: str,
    quality_profile: str,
    source_image: str | None,
) -> dict[str, Any]:
    return {
        "enabled": bool(source_image),
        "generator": "built-in-imagegen",
        "promptVersion": DEFAULT_PROMPT_VERSION,
        "requiredViews": adaptive_hypothesis_views(complexity, quality_profile),
        "allowedUse": "planning-veto",
        "acceptanceAuthority": False,
        "generationContract": (
            "Preserve the exact object identity, proportions, parts, materials, and scale; "
            "generate only the named unseen view on a neutral background; do not redesign or add parts."
        ),
        "manifestPath": "",
        "manifestSha256": "",
        "cacheKey": "",
    }


def _resolve_local_path(root: Path, value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value.strip() or "://" in value:
        raise ValueError(f"{label} must be a local file path")
    candidate = Path(value).expanduser()
    resolved = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"{label} does not exist: {resolved}")
    return resolved


def _cache_key(source_sha256: str, prompt_version: str) -> str:
    payload = f"{source_sha256}\n{prompt_version}\n".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _manifest_path(spec_path: Path, cache_key: str) -> Path:
    return (
        spec_path.parent
        / ".sculpt-cache"
        / spec_path.stem
        / f"view-hypotheses-{cache_key[:20]}.json"
    )


def _read_object(path: Path, label: str) -> dict[str, Any]:
    payload = parse_json(path.read_text(encoding="utf-8"), label)
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object")
    return payload


def _parse_view(value: str, root: Path) -> tuple[str, Path]:
    view_id, separator, path_value = value.partition("=")
    view_id = view_id.strip()
    if not separator or view_id not in SPATIAL_VIEW_IDS:
        raise ValueError(
            "--view must use three-quarter=PATH, side=PATH, or back=PATH"
        )
    return view_id, _resolve_local_path(root, path_value.strip(), f"view {view_id!r}")


def _policy(spec: dict[str, Any]) -> dict[str, Any]:
    value = spec.get("viewHypothesisPolicy")
    return value if isinstance(value, dict) else {}


def _required_views(spec: dict[str, Any]) -> list[str]:
    values = _policy(spec).get("requiredViews", [])
    if not isinstance(values, list):
        return []
    return list(dict.fromkeys(str(item) for item in values if item in SPATIAL_VIEW_IDS))


def _registered_manifest_path(spec_path: Path | None, policy: dict[str, Any]) -> Path:
    manifest_value = policy.get("manifestPath")
    if spec_path is None:
        if not isinstance(manifest_value, str) or not manifest_value.strip() or "://" in manifest_value:
            raise ValueError("view hypothesis manifest must be a local file path")
        candidate = Path(manifest_value).expanduser()
        if not candidate.is_absolute():
            raise ValueError(
                "view hypothesis manifestPath must be absolute when validation has no spec path"
            )
        return _resolve_local_path(candidate.parent, manifest_value, "view hypothesis manifest")
    root = spec_path.expanduser().resolve().parent
    return _resolve_local_path(root, manifest_value, "view hypothesis manifest")


def hypothesis_manifest_failures(
    spec_path: Path | None,
    spec: dict[str, Any],
    required_view_ids: Iterable[str] | None = None,
) -> list[str]:
    """Validate the registered cache against the current source and generated files."""
    policy = _policy(spec)
    if policy.get("enabled") is not True:
        return []
    failures: list[str] = []
    root = spec_path.expanduser().resolve().parent if spec_path is not None else None
    requested = {
        str(item)
        for item in (required_view_ids if required_view_ids is not None else _required_views(spec))
        if str(item) in SPATIAL_VIEW_IDS
    }
    try:
        manifest_path = _registered_manifest_path(spec_path, policy)
    except (OSError, ValueError) as exc:
        return [str(exc)]
    stored_file_hash = policy.get("manifestSha256")
    actual_file_hash = file_sha256(manifest_path)
    if stored_file_hash != actual_file_hash:
        failures.append("view hypothesis manifest changed after registration")
    try:
        manifest = _read_object(manifest_path, "view hypothesis manifest")
    except (OSError, ValueError) as exc:
        return [*failures, str(exc)]
    if manifest.get("artifactType") != VIEW_HYPOTHESIS_ARTIFACT_TYPE:
        failures.append("view hypothesis artifact type is invalid")
    if manifest.get("version") != VIEW_HYPOTHESIS_VERSION:
        failures.append(f"view hypothesis version must be {VIEW_HYPOTHESIS_VERSION}")
    if manifest.get("promptVersion") != policy.get("promptVersion"):
        failures.append("view hypothesis prompt version is stale")
    source_value = spec.get("sourceImage")
    source_root = root
    if spec_path is None:
        source_candidate = (
            Path(source_value).expanduser()
            if isinstance(source_value, str) and source_value.strip() and "://" not in source_value
            else None
        )
        if source_candidate is None or not source_candidate.is_absolute():
            source_value = manifest.get("sourceImage")
        source_root = manifest_path.parent
    try:
        assert source_root is not None
        source_path = _resolve_local_path(source_root, source_value, "spec.sourceImage")
    except (OSError, ValueError) as exc:
        failures.append(str(exc))
        source_path = None
    if source_path is not None:
        source_hash = file_sha256(source_path)
        if manifest.get("sourceSha256") != source_hash:
            failures.append("view hypotheses are stale for the current source image")
        if manifest.get("cacheKey") != _cache_key(source_hash, str(policy.get("promptVersion") or "")):
            failures.append("view hypothesis cache key is invalid")
    views = manifest.get("views")
    if not isinstance(views, list):
        failures.append("view hypothesis manifest views must be an array")
        views = []
    by_id: dict[str, dict[str, Any]] = {}
    for index, view in enumerate(views):
        if not isinstance(view, dict):
            failures.append(f"view hypothesis views[{index}] must be an object")
            continue
        view_id = view.get("viewId")
        if view_id not in SPATIAL_VIEW_IDS:
            failures.append(f"view hypothesis views[{index}].viewId is invalid")
            continue
        if view_id in by_id:
            failures.append(f"duplicate view hypothesis {view_id!r}")
            continue
        by_id[str(view_id)] = view
        try:
            image_root = root if root is not None else manifest_path.parent
            image_path = _resolve_local_path(
                image_root,
                view.get("image"),
                f"view hypothesis {view_id!r}",
            )
        except (OSError, ValueError) as exc:
            failures.append(str(exc))
            continue
        if view.get("sha256") != file_sha256(image_path):
            failures.append(f"view hypothesis {view_id!r} image changed after registration")
        if view.get("origin") != "synthetic-hypothesis" or view.get("allowedUse") != "planning-veto":
            failures.append(f"view hypothesis {view_id!r} must remain synthetic planning-veto evidence")
    missing = requested - set(by_id)
    if missing:
        failures.append("missing registered view hypotheses: " + ", ".join(sorted(missing)))
    return list(dict.fromkeys(failures))


def hypothesis_evidence_failures(
    spec_path: Path | None,
    spec: dict[str, Any],
    evidence: dict[str, Any],
    diagnostic_view_ids: Iterable[str],
) -> list[str]:
    """Bind synthetic diagnostic references to the registered ImageGen cache."""
    policy = _policy(spec)
    if policy.get("enabled") is not True:
        return []
    diagnostics = {str(item) for item in diagnostic_view_ids if str(item) in SPATIAL_VIEW_IDS}
    evidence_views = {
        str(item.get("viewId")): item
        for item in evidence.get("views", [])
        if isinstance(item, dict) and isinstance(item.get("viewId"), str)
    }
    failures: list[str] = []
    for view_id in sorted(diagnostics):
        view = evidence_views.get(view_id)
        provenance = view.get("referenceProvenance") if isinstance(view, dict) else None
        if not isinstance(provenance, dict) or (
            provenance.get("origin") != "synthetic-hypothesis"
            or provenance.get("allowedUse") != "planning-veto"
        ):
            failures.append(
                f"diagnostic view {view_id!r} must use the registered synthetic-hypothesis/planning-veto provenance"
            )
    failures.extend(hypothesis_manifest_failures(spec_path, spec, diagnostics))
    if failures:
        return list(dict.fromkeys(failures))
    manifest_path = _registered_manifest_path(spec_path, policy)
    manifest = _read_object(manifest_path, "view hypothesis manifest")
    registered = {
        str(item.get("viewId")): item
        for item in manifest.get("views", [])
        if isinstance(item, dict) and isinstance(item.get("viewId"), str)
    }
    for view_id in sorted(diagnostics):
        if evidence_views[view_id].get("referenceSha256") != registered[view_id].get("sha256"):
            failures.append(
                f"synthetic diagnostic view {view_id!r} is not the registered ImageGen hypothesis"
            )
    return list(dict.fromkeys(failures))


def register_views(
    spec_path: Path,
    view_arguments: list[str],
    prompt_version: str | None = None,
) -> dict[str, Any]:
    from sculpt_modules import load_document, save_document

    path = spec_path.expanduser().resolve()
    document = load_document(path)
    spec = document.resolved
    policy = _policy(spec)
    if not policy:
        raise ValueError("spec has no viewHypothesisPolicy; migrate or reinitialize it first")
    source_path = _resolve_local_path(path.parent, spec.get("sourceImage"), "spec.sourceImage")
    selected_prompt = str(prompt_version or policy.get("promptVersion") or DEFAULT_PROMPT_VERSION).strip()
    if not selected_prompt:
        raise ValueError("prompt version must be non-empty")
    parsed: dict[str, Path] = {}
    for argument in view_arguments:
        view_id, image_path = _parse_view(argument, path.parent)
        if view_id in parsed:
            raise ValueError(f"duplicate --view {view_id!r}")
        parsed[view_id] = image_path
    source_hash = file_sha256(source_path)
    image_dimensions(source_path)
    cache_key = _cache_key(source_hash, selected_prompt)
    output = _manifest_path(path, cache_key)
    existing_views: dict[str, dict[str, Any]] = {}
    if output.is_file():
        existing = _read_object(output, "cached view hypothesis manifest")
        if (
            existing.get("artifactType") != VIEW_HYPOTHESIS_ARTIFACT_TYPE
            or existing.get("version") != VIEW_HYPOTHESIS_VERSION
            or existing.get("sourceSha256") != source_hash
            or existing.get("promptVersion") != selected_prompt
        ):
            raise ValueError("existing view hypothesis cache has incompatible provenance")
        existing_views = {
            str(item.get("viewId")): item
            for item in existing.get("views", [])
            if isinstance(item, dict) and isinstance(item.get("viewId"), str)
        }
    cache_hit = True
    for view_id, image_path in parsed.items():
        image_dimensions(image_path)
        digest = file_sha256(image_path)
        previous = existing_views.get(view_id)
        if previous is not None and previous.get("sha256") != digest:
            raise ValueError(
                f"cached {view_id!r} hypothesis already exists for this source/prompt; "
                "reuse it or increment --prompt-version explicitly"
            )
        if previous is None:
            cache_hit = False
            existing_views[view_id] = {
                "viewId": view_id,
                "image": str(image_path),
                "sha256": digest,
                "origin": "synthetic-hypothesis",
                "allowedUse": "planning-veto",
            }
    required = set(_required_views(spec))
    missing = required - set(existing_views)
    if missing:
        raise ValueError(
            "registration is incomplete; provide generated views: " + ", ".join(sorted(missing))
        )
    manifest = {
        "artifactType": VIEW_HYPOTHESIS_ARTIFACT_TYPE,
        "version": VIEW_HYPOTHESIS_VERSION,
        "generator": "built-in-imagegen",
        "sourceImage": str(source_path),
        "sourceSha256": source_hash,
        "promptVersion": selected_prompt,
        "cacheKey": cache_key,
        "views": [existing_views[key] for key in sorted(existing_views)],
        "acceptanceAuthority": False,
        "allowedUse": "planning-veto",
    }
    write_spec_atomic(output, manifest)
    policy.update(
        {
            "enabled": True,
            "generator": "built-in-imagegen",
            "promptVersion": selected_prompt,
            "manifestPath": str(output),
            "manifestSha256": file_sha256(output),
            "cacheKey": cache_key,
        }
    )
    document.resolved["viewHypothesisPolicy"] = policy
    save_document(document, path)
    return {
        "ok": True,
        "cacheHit": cache_hit,
        "cacheKey": cache_key,
        "manifest": str(output),
        "registeredViews": sorted(existing_views),
        "requiredViews": sorted(required),
    }


def status(spec_path: Path) -> dict[str, Any]:
    from sculpt_modules import load_document

    path = spec_path.expanduser().resolve()
    document = load_document(path)
    spec = document.resolved
    policy = _policy(spec)
    failures = hypothesis_manifest_failures(path, spec)
    return {
        "enabled": policy.get("enabled") is True,
        "ready": policy.get("enabled") is not True or not failures,
        "requiredViews": _required_views(spec),
        "manifest": policy.get("manifestPath", ""),
        "cacheKey": policy.get("cacheKey", ""),
        "failures": failures,
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    register = subparsers.add_parser("register", help="Register built-in ImageGen outputs once")
    register.add_argument("spec", type=Path)
    register.add_argument("--view", action="append", default=[])
    register.add_argument("--prompt-version")
    inspect = subparsers.add_parser("status", help="Check source/hash/view cache freshness")
    inspect.add_argument("spec", type=Path)
    args = parser.parse_args(argv)
    result = (
        register_views(args.spec, args.view, args.prompt_version)
        if args.command == "register"
        else status(args.spec)
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("ok", result.get("ready")) is True else 1


if __name__ == "__main__":
    raise SystemExit(main(__import__("sys").argv[1:]))
