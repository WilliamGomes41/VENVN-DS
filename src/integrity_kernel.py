#!/usr/bin/env python3
"""Protocol v2 integrity kernel.

Single authority for canonical hashing, exact review snapshots, schema validation,
source-binary verification and source-fragment integrity.
"""
from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft202012Validator, FormatChecker

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
KERNEL_VERSION = "integrity-kernel-v1.0.0"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def stable_hash(value: Any) -> str:
    return sha256_bytes(canonical_json(value))


def _provenance_for_hash(provenance: dict[str, Any]) -> dict[str, Any]:
    p = deepcopy(provenance)
    # Self-referential/derived hash fields never participate in their own hash.
    p.pop("content_hash", None)
    p.pop("canonical_object_hash", None)
    return p


def canonical_object_payload(obj: dict[str, Any]) -> dict[str, Any]:
    """Return the immutable/reviewable canonical payload.

    Governance is excluded because review/publication state is mutable lifecycle
    metadata. All clinical/technical content, source, structure, uncertainty,
    relations, risk and transformation provenance are included.
    """
    required = [
        "object_id", "document_id", "object_version", "parent_object_id",
        "object_type", "source", "structure", "content", "logic", "relations",
        "decision_graph", "risk", "uncertainty", "provenance",
    ]
    payload: dict[str, Any] = {}
    for key in required:
        if key == "provenance":
            payload[key] = _provenance_for_hash(obj.get(key) or {})
        else:
            payload[key] = deepcopy(obj.get(key))
    for extra in (
        "proposed_object_type",
        "confirmed_object_type",
        "confirmed_relations",
        "proposed_recommendation_strength",
        "confirmed_recommendation_strength",
        "no_action",
        "metadata",
    ):
        if extra not in obj:
            continue
        if extra == "metadata":
            md = deepcopy(obj.get(extra) or {})
            md.pop("admission", None)
            if md:
                payload[extra] = md
            continue
        payload[extra] = deepcopy(obj.get(extra))
    return payload


def compute_canonical_object_hash(obj: dict[str, Any]) -> str:
    return stable_hash(canonical_object_payload(obj))


def exact_review_snapshot_hash(obj: dict[str, Any]) -> str:
    """Hash the exact canonical object version presented for review.

    This intentionally equals the canonical-object hash. A later change to any
    reviewable field invalidates the review, while governance mutations do not.
    """
    return compute_canonical_object_hash(obj)


def stamp_canonical_hashes(obj: dict[str, Any]) -> dict[str, Any]:
    h = compute_canonical_object_hash(obj)
    obj.setdefault("provenance", {})["canonical_object_hash"] = h
    # Backward-compatible alias used by retrieval/release records.
    obj["provenance"]["content_hash"] = h
    return obj


def validate_hashes(obj: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    expected = compute_canonical_object_hash(obj)
    p = obj.get("provenance") or {}
    if p.get("canonical_object_hash") != expected:
        errors.append("canonical_object_hash_mismatch")
    if p.get("content_hash") != expected:
        errors.append("content_hash_mismatch")
    return errors


def load_validator(schema_path: Path) -> Draft202012Validator:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    return Draft202012Validator(schema, format_checker=FormatChecker())


def schema_errors(obj: dict[str, Any], schema_path: Path) -> list[str]:
    validator = load_validator(schema_path)
    return [f"{'.'.join(str(x) for x in e.absolute_path)}: {e.message}" for e in validator.iter_errors(obj)]


def verify_source_binary(binary_path: Path, expected_sha256: str | None = None) -> dict[str, Any]:
    if not binary_path.exists() or not binary_path.is_file():
        return {"verified": False, "error": "source_binary_missing", "path": str(binary_path)}
    actual = sha256_file(binary_path)
    result: dict[str, Any] = {
        "verified": True,
        "algorithm": "sha256",
        "sha256": actual,
        "path": str(binary_path.resolve()),
        "size_bytes": binary_path.stat().st_size,
    }
    if expected_sha256 is not None:
        normalized = expected_sha256.lower().strip()
        if not SHA256_RE.match(normalized):
            result.update({"verified": False, "error": "expected_sha256_invalid_format"})
        elif actual != normalized:
            result.update({"verified": False, "error": "source_binary_checksum_mismatch", "expected_sha256": normalized})
    return result


def validate_source_integrity(source: dict[str, Any], verified_sources: dict[str, str] | None = None) -> list[str]:
    errors: list[str] = []
    checksum = (source.get("source_checksum") or "").lower()
    if source.get("integrity_status") != "verified":
        errors.append("source_integrity_not_verified")
    if not SHA256_RE.match(checksum):
        errors.append("source_checksum_not_sha256")
    if verified_sources is not None:
        source_id = source.get("source_id")
        if not source_id or verified_sources.get(source_id) != checksum:
            errors.append("source_checksum_not_in_verified_registry")
    return errors


def validate_source_fragments(obj: dict[str, Any], raw_objects: dict[str, dict[str, Any]] | None = None) -> list[str]:
    errors: list[str] = []
    refs = (obj.get("provenance") or {}).get("source_fragments") or []
    if obj.get("object_type") != "document" and not refs:
        errors.append("source_fragments_missing")
        return errors
    for ref in refs:
        rid = ref.get("raw_object_id")
        rhash = ref.get("raw_content_hash")
        if not rid or not rhash or not SHA256_RE.match(str(rhash)):
            errors.append("source_fragment_invalid")
            continue
        if raw_objects is not None:
            raw = raw_objects.get(rid)
            if not raw:
                errors.append(f"source_fragment_not_found:{rid}")
                continue
            if raw.get("fragment_id"):
                # Raw schema v1.0 is PDF-centric. v1.1 adds a source-neutral
                # source_locator. Preserve the old hash contract for legacy
                # fragments and include the locator only when present.
                keys = ["fragment_id","document_id","source_id","source_page","bbox"]
                if "source_locator" in raw:
                    keys.append("source_locator")
                keys += ["raw_text","clean_text","section_path","heading","sequence","parser_version"]
                try:
                    actual = stable_hash({k: raw[k] for k in keys})
                except KeyError:
                    errors.append(f"source_fragment_malformed:{rid}")
                    continue
                if raw.get("fragment_hash") != actual:
                    errors.append(f"raw_fragment_self_hash_mismatch:{rid}")
            else:
                actual = ((raw.get("technical") or {}).get("content_hash"))
            if actual != rhash:
                errors.append(f"source_fragment_hash_mismatch:{rid}")
    return errors


def validate_parent_relations(objects: Iterable[dict[str, Any]]) -> list[str]:
    rows = list(objects)
    by_id = {o["object_id"]: o for o in rows}
    ids = set(by_id)
    errors: list[str] = []
    for o in rows:
        oid = o["object_id"]
        parent = o.get("parent_object_id")
        if parent == oid:
            errors.append(f"self_parent:{oid}")
        if parent and parent not in ids:
            errors.append(f"missing_parent:{oid}:{parent}")
        for rel in o.get("relations") or []:
            target = rel.get("target_object_id")
            if target == oid:
                errors.append(f"self_relation:{oid}:{rel.get('relation_type')}")
            if target and target not in ids:
                errors.append(f"missing_relation_target:{oid}:{target}")

    # parent_object_id is the canonical hierarchy edge. It must be acyclic.
    for start in by_id:
        seen: set[str] = set()
        current = start
        while current in by_id:
            if current in seen:
                errors.append(f"parent_cycle:{start}")
                break
            seen.add(current)
            parent = by_id[current].get("parent_object_id")
            if not parent:
                break
            current = str(parent)

    # When a confirmed child relation is stored as well, it must agree with
    # the canonical parent edge. Proposed relations are deliberately ignored.
    from src.heading_parent_list_v1 import (
        is_heading_object,
        mark_heading_roles,
        parent_proposal_may_bind,
    )
    from src.serving_relations_v1 import binding_relations

    marked_rows = mark_heading_roles(rows)
    marked_by_id = {o["object_id"]: o for o in marked_rows}

    for o in rows:
        oid = o["object_id"]
        parent = o.get("parent_object_id")
        relation_parents = {
            rel["target_object_id"]
            for rel in binding_relations(o)
            if rel["relation_type"] == "child"
        }
        if relation_parents and relation_parents != ({parent} if parent else set()):
            errors.append(f"parent_relation_mismatch:{oid}")

        for child_id in {
            rel["target_object_id"]
            for rel in binding_relations(o)
            if rel["relation_type"] == "parent"
        }:
            child = by_id.get(child_id)
            if child is not None and child.get("parent_object_id") != oid:
                errors.append(f"parent_relation_mismatch:{child_id}")

        if parent and parent in marked_by_id:
            marked_object = marked_by_id[oid]
            parent_obj = marked_by_id[parent]
            if is_heading_object(marked_object) and is_heading_object(parent_obj):
                if not parent_proposal_may_bind(marked_object, parent_obj, marked_rows):
                    errors.append(f"invalid_parent_structure:{oid}:{parent}")
    return list(dict.fromkeys(errors))

def load_verified_source_registry(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data.get("sources", data if isinstance(data, list) else [])
    out: dict[str, str] = {}
    for row in rows:
        checksum = str(row.get("source_checksum") or "").lower()
        binary_path = row.get("binary_path")
        if not (row.get("integrity_status") == "verified" and SHA256_RE.match(checksum) and row.get("source_id") and binary_path):
            continue
        bp = Path(str(binary_path))
        if not bp.exists() or not bp.is_file():
            continue
        if sha256_file(bp) != checksum:
            continue
        out[str(row["source_id"])] = checksum
    return out


def load_raw_objects(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    out = {}
    for x in rows:
        key = x.get("fragment_id") or x.get("object_id")
        if key:
            out[str(key)] = x
    return out
