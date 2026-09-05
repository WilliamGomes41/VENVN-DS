"""Phase 1 admission gate for richtlijn inhoudelijke candidates.

Hard gate only. Soft scores / volume / ship-then-fix MUST NOT open it.
Boom path/node/outcome stay on the existing boom path. Passage register is
not a Phase-1 admission prerequisite. Full context scan is later.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

from src.beslisboom_path_v1 import CLOSED_BOOM_TYPES, review_path_for_klasse
from src.object_taxonomy_v1 import locator_of


GATE_ALLOWED = "allowed"
GATE_BLOCKED = "blocked"

REQUIRED_CANDIDATE_FIELDS = (
    "candidate_id",
    "document_id",
    "document_version",
    "source_hash",
    "section_path",
    "source_locator_start",
    "source_locator_end",
    "source_text_exact",
    "candidate_text",
    "subject_span",
    "predicate_span",
    "proposed_type",
    "type_evidence_spans",
    "context_before",
    "context_after",
    "conditions_detected",
    "exceptions_detected",
    "comparison_markers",
    "comparison_targets",
    "references_detected",
    "references_resolved",
    "abbreviations_detected",
    "abbreviations_resolved",
    "related_candidates",
    "gate_result",
    "reason_codes",
)

DUTCH_TYPE_NAMES = {
    "Aanbeveling": "recommendation",
    "Definitie": "definition",
    "Voorwaarde": "condition",
    "Uitzondering": "exception",
    "Feitelijke constatering": "factual_finding",
    "Toelichting": "explanation",
}

TYPE_CONTRACT_FIELDS = {
    "recommendation": (
        "actor_of_scope",
        "recommended_action",
        "action_object_or_goal",
        "recommendation_evidence_span",
    ),
    "definition": ("defined_term", "definiens_span"),
    "condition": ("condition_span", "condition_target"),
    "exception": ("exception_span", "exception_target"),
    "factual_finding": ("factual_claim_span",),
    "explanation": ("support_span", "supported_object"),
}

FACTUAL_FINDING_SERVING_TYPE = "explanation"

_ARRAY_FIELDS = frozenset(
    {
        "type_evidence_spans",
        "conditions_detected",
        "exceptions_detected",
        "comparison_markers",
        "comparison_targets",
        "references_detected",
        "references_resolved",
        "abbreviations_detected",
        "abbreviations_resolved",
        "related_candidates",
        "reason_codes",
        "section_path",
    }
)

_VERB_RE = re.compile(
    r"\b(?:adviseert?|aanbeveelt?|overweegt?|gebruik(?:t|en)?|bespreek(?:t)?|"
    r"verwijs(?:t)?|overleg(?:t)?|controleer(?:t)?|start|wordt|zijn|is|komt|"
    r"heeft|hebben|geven|te geven|bestaan|zie|tenzij)\b",
    re.I,
)
_ADVICE_EVIDENCE_RE = re.compile(
    r"\b(?:adviseert?|aanbeveelt?|overweeg(?:t)?)\b|"
    r"^(?:bespreek|gebruik|verwijs|overleg|controleer|start|overweeg)\b",
    re.I,
)
_PREVALENCE_RE = re.compile(
    r"\bwordt\b.+\bgebruikt\b|\bkomt\b.+\bvoor\b|\bvaker gebruikt\b|"
    r"\bvaak voor\b",
    re.I,
)
_COMPARISON_RE = re.compile(
    r"\b(?:vaker|minder vaak|meer dan|vergeleken|ten opzichte|versus|t\.o\.v\.)\b",
    re.I,
)
_COMPARISON_TARGET_RE = re.compile(
    r"\b(?:dan|vergeleken met|ten opzichte van)\s+\S+",
    re.I,
)
_ABBREV_RE = re.compile(r"\b[A-Za-z]{0,3}[A-Z][A-Za-z&]{1,10}\b")
_KNOWN_ABBREVS = {
    "V&VN": "Verpleegkundigen & Verzorgenden Nederland",
}
_REF_RE = re.compile(
    r"\bzie\s+(?:tabel|hoofdstuk|paragraaf|figuur|§)\s*\d*",
    re.I,
)
_EXCEPTION_RE = re.compile(
    r"\b(?:tenzij|behalve|uitgezonderd|uitzondering)\b",
    re.I,
)
_CONDITION_RE = re.compile(
    r"\b(?:wanneer|indien|mits|bij een|voorwaarde)\b",
    re.I,
)
_ACTOR_RE = re.compile(
    r"\b(?:de\s+|het\s+)?(?:werkgroep|richtlijn|verpleegkundige[n]?|"
    r"zorgverlener|zorgvrager|cli[eë]nt|arts|huisarts)\b",
    re.I,
)
_ADVISEERT_PARSE_RE = re.compile(
    r"(?P<subject>De\s+\w+)\s+(?P<predicate>adviseert?|aanbeveelt?)\s+"
    r"(?:(?P<actor>de\s+verpleegkundige[n]?|de\s+zorgverlener|de\s+arts)\s+)?"
    r"(?P<object>.+?)\s+(?P<action>te\s+\w+)",
    re.I,
)
_TE_INF_RE = re.compile(r"\bte\s+\w+\b", re.I)
_IMPLICIET_RE = re.compile(r"\bimpliciet\b", re.I)
_LOCATOR_LINES_RE = re.compile(r"lines:(\d+)-(\d+)")


def serving_type_for_admission_type(proposed_type: str) -> str:
    if proposed_type == "factual_finding":
        return FACTUAL_FINDING_SERVING_TYPE
    return proposed_type


def admission_of(obj: dict[str, Any]) -> dict[str, Any]:
    md = obj.get("metadata") if isinstance(obj.get("metadata"), dict) else {}
    row = md.get("admission")
    return row if isinstance(row, dict) else {}


def is_boom_object(obj: dict[str, Any]) -> bool:
    for key in ("confirmed_object_type", "object_type", "proposed_object_type"):
        if obj.get(key) in CLOSED_BOOM_TYPES:
            return True
    return False


def is_inhoudelijk_candidate(obj: dict[str, Any]) -> bool:
    if obj.get("object_type") in {"document", "heading"}:
        return False
    if obj.get("proposed_object_type") == "heading":
        return False
    if is_boom_object(obj):
        return False
    return True


def build_candidate_record(**fields: Any) -> dict[str, Any]:
    record: dict[str, Any] = {}
    for name in REQUIRED_CANDIDATE_FIELDS:
        if name in fields:
            record[name] = fields[name]
        elif name in _ARRAY_FIELDS:
            record[name] = []
        elif name == "gate_result":
            record[name] = None
        else:
            record[name] = ""
    for key, value in fields.items():
        if key not in record:
            record[key] = value
    return record


def _literal(span: Any, source: str) -> bool:
    text = str(span or "").strip()
    if not text:
        return False
    return text in source


def _present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, (list, tuple, dict)):
        return True
    return str(value).strip() != ""


def _word_count(text: str) -> int:
    blob = re.sub(r"[.!?]+$", "", re.sub(r"\s+", " ", text or "")).strip()
    return len(blob.split()) if blob else 0


def _scan_comparisons(text: str) -> tuple[list[str], list[str]]:
    markers = [m.group(0) for m in _COMPARISON_RE.finditer(text or "")]
    targets = [m.group(0) for m in _COMPARISON_TARGET_RE.finditer(text or "")]
    return markers, targets


def _scan_abbreviations(text: str) -> tuple[list[str], list[str]]:
    detected: list[str] = []
    resolved: list[str] = []
    for match in _ABBREV_RE.finditer(text or ""):
        token = match.group(0)
        caps = sum(1 for char in token if char.isupper())
        if caps < 2 or len(token) > 8:
            continue
        if token not in detected:
            detected.append(token)
        if token in _KNOWN_ABBREVS and token not in resolved:
            resolved.append(token)
    return detected, resolved


def _scan_references(text: str) -> list[str]:
    return [m.group(0) for m in _REF_RE.finditer(text or "")]


def _scan_exceptions(text: str) -> list[str]:
    found: list[str] = []
    for match in _EXCEPTION_RE.finditer(text or ""):
        start = match.start()
        snippet = re.sub(r"\s+", " ", (text or "")[start:]).strip().rstrip(".")
        if snippet and snippet not in found:
            found.append(snippet)
    return found


def _scan_conditions(text: str) -> list[str]:
    found: list[str] = []
    for match in _CONDITION_RE.finditer(text or ""):
        start = match.start()
        snippet = re.sub(r"\s+", " ", (text or "")[start:]).strip().rstrip(".")
        if snippet and snippet not in found:
            found.append(snippet)
    return found


def _has_recommendation_evidence(text: str) -> bool:
    if not text:
        return False
    if _PREVALENCE_RE.search(text):
        return False
    return bool(_ADVICE_EVIDENCE_RE.search(text))


def _enrich_from_text(candidate: dict[str, Any]) -> None:
    text = str(candidate.get("source_text_exact") or candidate.get("candidate_text") or "")
    parsed = _ADVISEERT_PARSE_RE.search(text)
    if parsed:
        if not candidate.get("subject_span"):
            candidate["subject_span"] = parsed.group("subject")
        if not candidate.get("predicate_span"):
            candidate["predicate_span"] = parsed.group("predicate")
        actor = parsed.group("actor") or parsed.group("subject")
        if not candidate.get("actor_of_scope") and actor:
            candidate["actor_of_scope"] = actor
        if not candidate.get("action_object_or_goal") and parsed.group("object"):
            candidate["action_object_or_goal"] = parsed.group("object").strip()
        if not candidate.get("recommended_action") and parsed.group("action"):
            candidate["recommended_action"] = parsed.group("action")
        if not candidate.get("recommendation_evidence_span") and _has_recommendation_evidence(text):
            candidate["recommendation_evidence_span"] = text
        if not candidate.get("type_evidence_spans"):
            candidate["type_evidence_spans"] = [parsed.group("predicate")]
    elif _ADVICE_EVIDENCE_RE.search(text):
        if not candidate.get("predicate_span"):
            match = _ADVICE_EVIDENCE_RE.search(text)
            if match:
                candidate["predicate_span"] = match.group(0)
        if not candidate.get("recommended_action"):
            te = _TE_INF_RE.search(text)
            candidate["recommended_action"] = (
                te.group(0) if te else str(candidate.get("predicate_span") or "")
            )
        actors = _ACTOR_RE.findall(text)
        if not actors:
            neighbors = " ".join(
                [
                    str(candidate.get("context_before") or ""),
                    str(candidate.get("context_after") or ""),
                ]
            )
            actors = _ACTOR_RE.findall(neighbors)
        if not candidate.get("actor_of_scope") and actors:
            candidate["actor_of_scope"] = actors[-1] if len(actors) > 1 else actors[0]
        if not candidate.get("action_object_or_goal"):
            te = _TE_INF_RE.search(text)
            if te:
                before = text[: te.start()].strip()
                words = before.split()
                candidate["action_object_or_goal"] = " ".join(words[-4:]) if words else ""
            else:
                words = re.sub(r"^[A-Za-zÀ-ÿ]+\s+", "", text).strip().rstrip(".").split()
                candidate["action_object_or_goal"] = " ".join(words[:6])
        if not candidate.get("recommendation_evidence_span") and _has_recommendation_evidence(text):
            candidate["recommendation_evidence_span"] = text
        if not candidate.get("type_evidence_spans") and candidate.get("predicate_span"):
            candidate["type_evidence_spans"] = [candidate["predicate_span"]]
    if not candidate.get("subject_span"):
        words = text.split()
        if words:
            candidate["subject_span"] = " ".join(words[:3])
    if not candidate.get("predicate_span"):
        match = _VERB_RE.search(text)
        if match:
            candidate["predicate_span"] = match.group(0)
    proposed = candidate.get("proposed_type")
    if proposed == "definition" and not candidate.get("defined_term"):
        words = text.split()
        candidate["defined_term"] = " ".join(words[:3]) if words else ""
        if not candidate.get("definiens_span") and " is " in f" {text} ":
            candidate["definiens_span"] = text
    if proposed == "condition":
        if not candidate.get("condition_span"):
            candidate["condition_span"] = text
        if not candidate.get("type_evidence_spans"):
            match = _CONDITION_RE.search(text)
            candidate["type_evidence_spans"] = [match.group(0)] if match else [text[:20]]
    if proposed == "exception":
        if not candidate.get("exception_span"):
            candidate["exception_span"] = text
        if not candidate.get("type_evidence_spans"):
            match = _EXCEPTION_RE.search(text)
            candidate["type_evidence_spans"] = [match.group(0)] if match else [text[:20]]
    if proposed == "factual_finding" and not candidate.get("factual_claim_span"):
        candidate["factual_claim_span"] = text
    if proposed == "explanation":
        if not candidate.get("support_span"):
            candidate["support_span"] = text
    if not candidate.get("conditions_detected"):
        candidate["conditions_detected"] = _scan_conditions(text)
    if not candidate.get("exceptions_detected"):
        candidate["exceptions_detected"] = _scan_exceptions(text)
    markers, targets = _scan_comparisons(text)
    if not candidate.get("comparison_markers"):
        candidate["comparison_markers"] = markers
    if not candidate.get("comparison_targets"):
        candidate["comparison_targets"] = targets
    detected, resolved = _scan_abbreviations(text)
    if not candidate.get("abbreviations_detected"):
        candidate["abbreviations_detected"] = detected
    if not candidate.get("abbreviations_resolved"):
        candidate["abbreviations_resolved"] = [
            token for token in detected if token in _KNOWN_ABBREVS
        ] or resolved
    if not candidate.get("references_detected"):
        candidate["references_detected"] = _scan_references(text)


def _has_impliciet_filler(candidate: dict[str, Any]) -> bool:
    keys = (
        "subject_span",
        "predicate_span",
        "actor_of_scope",
        "recommended_action",
        "action_object_or_goal",
        "recommendation_evidence_span",
        "defined_term",
        "definiens_span",
        "condition_span",
        "condition_target",
        "exception_span",
        "exception_target",
        "factual_claim_span",
        "support_span",
        "supported_object",
        "candidate_text",
    )
    for key in keys:
        if _IMPLICIET_RE.search(str(candidate.get(key) or "")):
            return True
    return False


def admit_candidate(
    candidate: dict[str, Any],
    *,
    soft_scores: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Hard-admit one candidate. ``soft_scores`` MAY rank only and MUST NOT open."""
    del soft_scores  # ranking only; never opens the gate
    absent_required = [
        field
        for field in REQUIRED_CANDIDATE_FIELDS
        if field not in candidate and field not in {"gate_result", "reason_codes"}
    ]
    row = build_candidate_record(**candidate)
    _enrich_from_text(row)
    codes: list[str] = []
    source = str(row.get("source_text_exact") or row.get("candidate_text") or "")
    text = str(row.get("candidate_text") or source)

    for field in absent_required:
        if field == "subject_span":
            codes.append("subject_missing")
        elif field == "predicate_span":
            codes.append("predicate_missing")
        else:
            codes.append("type_contract_incomplete")

    if not str(row.get("subject_span") or "").strip():
        codes.append("subject_missing")
    elif source and not _literal(row.get("subject_span"), source) and row.get("subject_span") not in text:
        codes.append("subject_missing")
    if not str(row.get("predicate_span") or "").strip():
        codes.append("predicate_missing")

    if _word_count(text) < 3 or not _VERB_RE.search(text):
        codes.append("incomplete_sentence")
        if _word_count(text) < 3:
            codes.append("no_independent_claim")

    start = str(row.get("source_locator_start") or "").strip()
    end = str(row.get("source_locator_end") or "").strip()
    if not start or not end:
        codes.append("locator_invalid")

    evidence = row.get("type_evidence_spans") or []
    if not evidence:
        codes.append("type_evidence_missing")

    proposed = str(row.get("proposed_type") or "")
    for field in TYPE_CONTRACT_FIELDS.get(proposed, ()):
        if not _present(row.get(field)):
            codes.append("type_contract_incomplete")
            if field == "recommendation_evidence_span":
                codes.append("recommendation_evidence_missing")
            elif field == "condition_target":
                codes.append("condition_target_missing")
            elif field == "exception_target":
                codes.append("exception_target_missing")
            elif field == "supported_object":
                codes.append("supported_object_missing")
            break

    if proposed == "recommendation":
        if not _has_recommendation_evidence(source) or not _present(row.get("recommendation_evidence_span")):
            codes.append("recommendation_evidence_missing")

    if _has_impliciet_filler(row):
        codes.append("source_fidelity_failure")

    if _EXCEPTION_RE.search(source) and not _EXCEPTION_RE.search(text):
        codes.append("source_fidelity_failure")
        row["exceptions_detected"] = []

    if row.get("comparison_markers") and not row.get("comparison_targets"):
        codes.append("comparison_target_missing")
    detected_ab = [a for a in (row.get("abbreviations_detected") or []) if a not in _KNOWN_ABBREVS]
    resolved_ab = set(row.get("abbreviations_resolved") or [])
    if any(token not in resolved_ab for token in detected_ab):
        codes.append("abbreviation_unresolved")
    if row.get("references_detected") and not row.get("references_resolved"):
        codes.append("unresolved_reference")

    if proposed == "exception" and not _present(row.get("exception_target")):
        codes.append("exception_target_missing")
        codes.append("no_independent_claim")
    if proposed == "condition" and not _present(row.get("condition_target")):
        codes.append("condition_target_missing")
    if proposed == "explanation" and not _present(row.get("supported_object")):
        codes.append("supported_object_missing")
        codes.append("no_independent_claim")

    # Phase 1 MUST NOT emit context_scan_not_done.
    unique: list[str] = []
    for code in codes:
        if code == "context_scan_not_done":
            continue
        if code not in unique:
            unique.append(code)
    row["reason_codes"] = unique
    row["gate_result"] = GATE_BLOCKED if unique else GATE_ALLOWED
    return row


def _locator_bounds(obj: dict[str, Any]) -> tuple[str, str]:
    loc = locator_of(obj) or {}
    value = str(loc.get("locator_value") or "").strip()
    match = _LOCATOR_LINES_RE.search(value)
    if match:
        token = f"lines:{match.group(1)}-{match.group(2)}"
        return token, token
    if value:
        return value, value
    return "", ""


def _object_text(obj: dict[str, Any]) -> str:
    return str((obj.get("content") or {}).get("clean_text") or obj.get("text") or "").strip()


def _neighbor_text(objects: list[dict[str, Any]], index: int, step: int) -> str:
    cursor = index + step
    while 0 <= cursor < len(objects):
        row = objects[cursor]
        if row.get("object_type") == "document":
            cursor += step
            continue
        return _object_text(row)
    return ""


def _target_for(obj: dict[str, Any], objects: list[dict[str, Any]], index: int) -> str:
    relations = list(obj.get("relations") or [])
    for rel in relations:
        if rel.get("relation_type") in {"applies_if", "except_if", "explains", "supported_by"}:
            target = str(rel.get("target_object_id") or "").strip()
            if target:
                return target
    for peer in objects:
        if peer.get("object_id") == obj.get("object_id"):
            continue
        for rel in peer.get("relations") or []:
            if rel.get("target_object_id") == obj.get("object_id") and rel.get("relation_type") in {
                "except_if",
                "applies_if",
            }:
                return str(peer.get("object_id") or "")
    return ""


def candidate_from_object(
    obj: dict[str, Any],
    *,
    objects: list[dict[str, Any]],
    index: int,
    document_version: str,
    source_hash: str,
) -> dict[str, Any]:
    text = _object_text(obj)
    start, end = _locator_bounds(obj)
    proposed = obj.get("proposed_object_type") or ""
    if not proposed and _PREVALENCE_RE.search(text):
        proposed = "recommendation"
    if not proposed and _EXCEPTION_RE.search(text):
        proposed = "exception"
    if not proposed and _CONDITION_RE.search(text):
        proposed = "condition"
    target = _target_for(obj, objects, index)
    fields: dict[str, Any] = {
        "candidate_id": obj.get("object_id") or f"cand-{index}",
        "document_id": obj.get("document_id") or "",
        "document_version": document_version or str(obj.get("object_version") or ""),
        "source_hash": source_hash or str((obj.get("source") or {}).get("source_checksum") or ""),
        "section_path": (obj.get("structure") or {}).get("section_path") or [],
        "source_locator_start": start,
        "source_locator_end": end,
        "source_text_exact": text,
        "candidate_text": text,
        "proposed_type": proposed,
        "context_before": _neighbor_text(objects, index, -1),
        "context_after": _neighbor_text(objects, index, 1),
    }
    if proposed == "condition":
        fields["condition_span"] = text
        fields["condition_target"] = target
    if proposed == "exception":
        fields["exception_span"] = text
        fields["exception_target"] = target
    if proposed == "explanation":
        fields["support_span"] = text
        fields["supported_object"] = target
    if proposed == "factual_finding":
        fields["factual_claim_span"] = text
    return build_candidate_record(**fields)


def apply_admission_gate(
    objects: list[dict[str, Any]],
    *,
    klasse: str,
    fragments: list[dict[str, Any]] | None = None,
    document_version: str,
    source_hash: str,
) -> list[dict[str, Any]]:
    del fragments  # Phase 1 uses object neighbors, not a deep freeze window
    if review_path_for_klasse(klasse) == "boom":
        return objects
    out: list[dict[str, Any]] = []
    for index, obj in enumerate(objects):
        row = obj
        if is_inhoudelijk_candidate(row):
            candidate = candidate_from_object(
                row,
                objects=objects,
                index=index,
                document_version=document_version,
                source_hash=source_hash,
            )
            admitted = admit_candidate(candidate)
            row = dict(row)
            metadata = dict(row.get("metadata") or {})
            metadata["admission"] = admitted
            row["metadata"] = metadata
        out.append(row)
    return out


def blocked_audit_lane(objects: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [obj for obj in objects if admission_of(obj).get("gate_result") == GATE_BLOCKED]


def ordinary_review_queue(
    objects: Iterable[dict[str, Any]],
    review_path: str | None = None,
) -> list[dict[str, Any]]:
    from src.operations_console_v1 import is_slow_review_duty

    return [
        obj
        for obj in objects
        if is_slow_review_duty(obj, review_path=review_path)
        and admission_of(obj).get("gate_result") != GATE_BLOCKED
    ]
