"""Closed object-type taxonomy for Protocol v2.12.

Extraction records structure and provenance only. The machine MAY propose a
type. A human MUST confirm before the type is published. unclassified is the
default, not a sixth advice type.
"""
from __future__ import annotations

import re
from typing import Any

from src.serving_relations_v1 import HISTORICAL_NON_SERVING_TYPES

CLOSED_OBJECT_TYPES = (
    "heading",
    "definition",
    "explanation",
    "condition",
    "exception",
    "recommendation",
)
CONTAINER_TYPES = frozenset({"document"})
DEFAULT_OBJECT_TYPE = "unclassified"
HEADING_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6")
STRUCTURAL_HEADING_LABELS = frozenset(
    {
        "inhoud",
        "inhoudsopgave",
        "colofon",
        "literatuur",
        "begrippen",
        "begrippenlijst",
        "bijlage",
        "bijlagen",
        "samenvatting",
        "inleiding",
        "aanbevelingen",
        "doorverwijzen",
        "verantwoording",
        "methodiek",
        "diagnostiek",
    }
)
_NUMBERED_HEADING_RE = re.compile(r"^\d+(?:\.\d+)*\.?\s+\S.{0,100}$")
CLASS_ORDER = {
    "richtlijn": 4,
    "handreiking": 3,
    "artikel": 2,
    "transcript": 1,
    "podcast": 1,
    "beslisboom": 0,
}
HISTORICAL_FACT_TYPES = HISTORICAL_NON_SERVING_TYPES

_DEFINITION_RE = re.compile(r"\b(?:is een|wordt genoemd|definitie|betekent)\b", re.I)
_EXCEPTION_RE = re.compile(r"\b(?:behalve|tenzij|uitgezonderd|uitzondering)\b", re.I)
_CONDITION_RE = re.compile(r"\b(?:bij een|indien|wanneer|mits|voorwaarde)\b", re.I)
_EXPLANATION_RE = re.compile(r"\b(?:omdat|namelijk|daardoor|helpt omdat|verklaar)\b", re.I)
_RECOMMENDATION_RE = re.compile(
    r"\b(?:adviseer|aanbevel|gebruik|verwijs|bespreek|overleg|controleer|start)\w*\b",
    re.I,
)
CLOSED_RECOMMENDATION_STRENGTHS = ("doen", "overweeg", "niet_doen")
STRENGTH_STAMP_LABELS = {
    "doen": "DOEN",
    "overweeg": "OVERWEEG",
    "niet_doen": "NIET DOEN",
}
STRENGTH_STAMP_ALIASES = {
    "doen": "doen",
    "overweeg": "overweeg",
    "niet doen": "niet_doen",
    "niet_doen": "niet_doen",
    "afraden": "niet_doen",
}
STRENGTH_STAMP_SENTENCES = {
    "doen": "Sterkte van de aanbeveling: DOEN — dit moet de zorgverlener doen.",
    "overweeg": "Sterkte van de aanbeveling: OVERWEEG — de zorgverlener moet dit overwegen.",
    "niet_doen": "Sterkte van de aanbeveling: NIET DOEN — de zorgverlener moet dit niet doen.",
}
_LIST_NUMBER_RE = re.compile(r"^\d+\.?$")
_TIMESTAMP_RE = re.compile(
    r"(?:gemaakt op\s+)?\d{1,2}-\d{1,2}-\d{4}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?",
    re.I,
)
_TRAILING_HANGER_RE = re.compile(
    r"(?:van|te|de|het|een|en|of|zijn|haar|hun|dit|deze|die|tot|voor|met|op|aan|"
    r"bij|naar|om|als|dan|dat|specifiek|specifieke)$",
    re.I,
)
# Evidence starters (Protocol v2.18): Eventueel / Bijvoorbeeld / Zoals — not a closed list.
_TRAILING_CLAUSE_STARTER_RE = re.compile(
    r"^(?:"
    r"eventueel|bijvoorbeeld|bijv\.?|zoals|"
    r"alsook|waaronder|inclusief|"
    r"onder andere|o\.a\.|met name|eveneens|tevens|"
    r"daarnaast|hierbij"
    r")\b",
    re.I,
)


def _normalized_stamp_key(text: str) -> str:
    blob = re.sub(r"\s+", " ", text or "").strip().casefold()
    blob = blob.rstrip(".!?:;")
    return blob


def is_strength_stamp(text: str) -> bool:
    """True only for a bare V&VN strength word, not 'Overweeg verwijzing…'."""
    return _normalized_stamp_key(text) in STRENGTH_STAMP_ALIASES


def stamp_value(text: str) -> str | None:
    return STRENGTH_STAMP_ALIASES.get(_normalized_stamp_key(text))


def is_closed_recommendation_strength(value: str | None) -> bool:
    return value in CLOSED_RECOMMENDATION_STRENGTHS


def recommendation_strength_sentence(value: str | None) -> str:
    if not is_closed_recommendation_strength(value):
        return ""
    return STRENGTH_STAMP_SENTENCES[value]


def is_list_number_only(text: str) -> bool:
    return bool(_LIST_NUMBER_RE.fullmatch(re.sub(r"\s+", "", text or "")))


def is_raw_timestamp(text: str) -> bool:
    blob = re.sub(r"\s+", " ", text or "").strip()
    if not blob:
        return False
    return bool(_TIMESTAMP_RE.fullmatch(blob))


def is_lone_trailing_word(text: str) -> bool:
    blob = re.sub(r"\s+", " ", text or "").strip()
    if not blob or is_strength_stamp(blob) or looks_like_structural_heading(blob):
        return False
    words = re.sub(r"[.!?]+$", "", blob).split()
    return len(words) == 1


def is_truncated_sentence(text: str) -> bool:
    blob = re.sub(r"\s+", " ", text or "").strip()
    if not blob or is_strength_stamp(blob) or looks_like_structural_heading(blob):
        return False
    if is_list_number_only(blob) or is_raw_timestamp(blob):
        return True
    if is_lone_trailing_word(blob):
        return True
    if blob[0].islower():
        return True
    if re.search(r"[.!?]$", blob):
        return False
    last = blob.split()[-1].rstrip(",;:")
    return bool(_TRAILING_HANGER_RE.fullmatch(last))


def is_continuation_fragment(text: str) -> bool:
    """True for a trailing clause that MUST stay with the previous sentence.

    Protocol v2.18: extract MUST NOT split a grammatical continuation into a
    new object. Lowercase hangers and Eventueel / Bijvoorbeeld / Zoals-style
    starters are evidence, not a closed list. Exception/condition sentences
    that start a new meaning unit (Tenzij, Behalve, Indien) are not this.
    """
    blob = re.sub(r"\s+", " ", text or "").strip()
    if not blob:
        return False
    if is_strength_stamp(blob) or looks_like_structural_heading(blob):
        return False
    if blob[0].islower() or is_lone_trailing_word(blob):
        return True
    return bool(_TRAILING_CLAUSE_STARTER_RE.match(blob))


def normalize_visible_prose(text: str) -> str:
    """Ordinary whitespace normalisation for freeze-prose identity (v2.18)."""
    return re.sub(r"\s+", " ", text or "").strip()


def is_tiny_confirmable_text(text: str) -> bool:
    """True when confirmable text cannot stand as one meaning unit."""
    blob = re.sub(r"\s+", " ", text or "").strip()
    if not blob:
        return True
    if is_list_number_only(blob) or is_strength_stamp(blob) or is_raw_timestamp(blob):
        return True
    if is_lone_trailing_word(blob):
        return True
    return False


KENNISPLATFORM_CHROME_LABELS = frozenset(
    {
        "home",
        "richtlijnen",
        "meedenken",
        "tools",
        "kennisinstituut v&vn",
        "kennisinstituut v en vn",
        "veelgestelde vragen",
        "kennisplatform",
    }
)


def is_kennisplatform_chrome_text(text: str) -> bool:
    """True for kennisplatform nav/shell labels. Evidence, not a heading allowlist."""
    blob = re.sub(r"\s+", " ", text or "").strip().casefold()
    blob = blob.replace("&amp;", "&").replace("v & vn", "v&vn")
    return blob in KENNISPLATFORM_CHROME_LABELS


def recommendation_strength_ui_applies(obj: dict[str, Any]) -> bool:
    """Sterkte is active only on stored/confirmed recommendation or outcome.

    A machine proposal MUST NOT show the picker. proposed_object_type alone
    is not enough.
    """
    confirmed = obj.get("confirmed_object_type") or ""
    stored = obj.get("object_type") or ""
    allowed = {"recommendation", "outcome"}
    if confirmed:
        return confirmed in allowed
    if stored and stored != DEFAULT_OBJECT_TYPE:
        return stored in allowed
    return False


def looks_like_structural_heading(text: str) -> bool:
    """True for TOC crumbs and short structural titles, not ordinary sentences.

    Numbered clinical prose ('1. Bespreek incontinentie met de patiënt.') is
    content, not a heading. A terminal period or an advice/meaning cue keeps
    the object unclassified so it stays in the slow review lane.
    """
    blob = re.sub(r"\s+", " ", text or "").strip()
    if not blob or len(blob) > 120:
        return False
    if re.search(r"[.!?]$", blob):
        return False
    if blob.casefold() in STRUCTURAL_HEADING_LABELS:
        return True
    if not _NUMBERED_HEADING_RE.match(blob):
        return False
    if (
        _DEFINITION_RE.search(blob)
        or _EXCEPTION_RE.search(blob)
        or _CONDITION_RE.search(blob)
        or _EXPLANATION_RE.search(blob)
        or _RECOMMENDATION_RE.search(blob)
    ):
        return False
    return len(blob.split()) <= 8


def fragment_is_heading(fragment: dict[str, Any]) -> bool:
    text = re.sub(r"\s+", " ", str(fragment.get("clean_text") or fragment.get("raw_text") or "")).strip()
    if is_strength_stamp(text) or is_kennisplatform_chrome_text(text):
        return False
    loc = fragment.get("source_locator") or {}
    value = str(loc.get("locator_value") or "").lower()
    if loc.get("locator_type") == "web_line_range" and any(
        f";{tag}:" in value for tag in HEADING_TAGS
    ):
        return True
    heading = re.sub(r"\s+", " ", str(fragment.get("heading") or "")).strip()
    if heading and heading == text:
        return True
    return looks_like_structural_heading(text)


def propose_object_type(text: str, *, is_heading: bool = False) -> str | None:
    if is_strength_stamp(text):
        return None
    if is_heading:
        return "heading"
    blob = text or ""
    if _DEFINITION_RE.search(blob):
        return "definition"
    if _EXCEPTION_RE.search(blob):
        if _RECOMMENDATION_RE.search(blob):
            return "recommendation"
        return "exception"
    if _CONDITION_RE.search(blob):
        return "condition"
    if _EXPLANATION_RE.search(blob):
        return "explanation"
    if _RECOMMENDATION_RE.search(blob):
        return "recommendation"
    return None


def extract_object_type(fragment: dict[str, Any]) -> tuple[str, str | None]:
    text = (fragment.get("clean_text") or fragment.get("raw_text") or "").strip()
    if is_strength_stamp(text) or is_kennisplatform_chrome_text(text):
        return DEFAULT_OBJECT_TYPE, None
    heading = fragment_is_heading(fragment)
    if heading:
        return "heading", "heading"
    return DEFAULT_OBJECT_TYPE, propose_object_type(text, is_heading=False)


def is_closed_confirmed_type(value: str | None) -> bool:
    return value in CLOSED_OBJECT_TYPES


def published_object_type(record: dict[str, Any]) -> str:
    """Return the type that may be served.

    Only a human-confirmed type from the closed set is served. Unconfirmed
    proposals, unclassified, historical types, published_at shortcuts and
    missing-proposed-key fallbacks MUST NOT be served.
    """
    md = record.get("metadata") if "metadata" in record else record
    confirmed = md.get("confirmed_object_type")
    if is_closed_confirmed_type(confirmed):
        return confirmed
    return DEFAULT_OBJECT_TYPE


def serving_block_reason(record: dict[str, Any]) -> str | None:
    """Why this record MUST NOT be served. None when it may be served."""
    if not locator_of(record):
        return "source_locator_missing"
    md = record.get("metadata") if "metadata" in record else record
    confirmed = md.get("confirmed_object_type")
    raw_type = md.get("object_type")
    if confirmed in HISTORICAL_NON_SERVING_TYPES or (
        not is_closed_confirmed_type(confirmed) and raw_type in HISTORICAL_NON_SERVING_TYPES
    ):
        return "historical_type_not_served"
    served = published_object_type(record)
    if not is_closed_confirmed_type(served):
        if md.get("proposed_object_type") and not confirmed:
            return "unconfirmed_proposal"
        return "unclassified_object"
    return None


def source_class_of(record: dict[str, Any]) -> str | None:
    md = record.get("metadata") if "metadata" in record else record
    if md.get("source_class"):
        return md["source_class"]
    for topic in md.get("topic") or []:
        text = str(topic)
        if text.startswith("class:") and not text.startswith("class-weight:"):
            return text.split(":", 1)[1]
    return None


def locator_of(record: dict[str, Any]) -> dict[str, Any] | None:
    md = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    loc = md.get("source_locator")
    if isinstance(loc, dict) and str(loc.get("locator_value") or "").strip():
        return loc
    for frag in (record.get("provenance") or {}).get("source_fragments") or []:
        sl = frag.get("source_locator") or {}
        if str(sl.get("locator_value") or "").strip():
            return sl
    obj = record.get("knowledge_object") or record
    for frag in (obj.get("provenance") or {}).get("source_fragments") or []:
        sl = frag.get("source_locator") or {}
        if str(sl.get("locator_value") or "").strip():
            return sl
    return None


def type_fits_question(question_kind: str, object_type: str) -> bool:
    if object_type in {DEFAULT_OBJECT_TYPE, "heading", "document"}:
        return False
    if object_type in HISTORICAL_NON_SERVING_TYPES:
        return False
    if question_kind == "action_advice":
        return object_type in {"recommendation", "condition", "exception"}
    if question_kind == "definition":
        return object_type == "definition"
    if question_kind == "explanation":
        return object_type == "explanation"
    if object_type in CLOSED_OBJECT_TYPES:
        return True
    return False


def is_advice_weight(question_kind: str, object_type: str) -> bool:
    return question_kind == "action_advice" and object_type == "recommendation"
