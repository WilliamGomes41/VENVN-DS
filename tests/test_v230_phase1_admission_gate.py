"""Protocol v2.30 Forge Phase 1: fields, contracts, hard gate, reason codes.

Richtlijn inhoudelijke candidates only. Boom path/node/outcome stay v2.25.
Passage register is not a Phase-1 admission prerequisite. Full context scan
is Phase 2. Review UI rewrite is Phase 3. Gold/metrics are Phase 4.
PROTOCOL.md and docs/PROTOCOL_V2_* are not edited here. publish() stays
G2-BLOCKED.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.admission_gate_v1 import (
    DUTCH_TYPE_NAMES,
    FACTUAL_FINDING_SERVING_TYPE,
    GATE_ALLOWED,
    GATE_BLOCKED,
    REQUIRED_CANDIDATE_FIELDS,
    TYPE_CONTRACT_FIELDS,
    admit_candidate,
    apply_admission_gate,
    blocked_audit_lane,
    build_candidate_record,
    ordinary_review_queue,
    serving_type_for_admission_type,
)
from src.beslisboom_path_v1 import CLOSED_BOOM_TYPES
from src.object_taxonomy_v1 import CLOSED_OBJECT_TYPES
from src.operations_console_app import create_console_app
from src.operations_console_v1 import OperationsConsole, slow_review_duty
from src.operations_console_v1 import is_slow_review_duty


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "data/fixtures/v230_phase1_admission_regression.html"
DJG = "De dJG wordt in Nederland vaker gebruikt."
ADVISEERT = (
    "De werkgroep adviseert de verpleegkundige de risicofactoren "
    "scorelijst te gebruiken bij iedere intake."
)
REC_PLUS_EXC = (
    "De werkgroep adviseert calcium te geven tenzij er hypercalciëmie bestaat."
)
LONE_EXCEPTION = "Tenzij er een recente fractuur is vastgesteld."
ONE_WORD = "Scorelijst."
UNRESOLVED_REF = "Zie tabel 4 voor de cutoff van het risico."
INCOMPLETE_COMPARISON = "Deze methode is vaker effectief."
FALSE_RECOMMENDATION = "Urine-incontinentie komt bij ouderen vaak voor in Nederland."


def _console(tmp_path: Path) -> OperationsConsole:
    return OperationsConsole(
        root=tmp_path,
        source_store=tmp_path / "sources" / "private",
        runtime=tmp_path / "output" / "runtime" / "operations-console",
    )


def _accounts(console: OperationsConsole) -> dict[str, dict]:
    researcher = console.create_account(
        username="researcher.anne",
        password="anne-secret",
        roles=("researcher", "reviewer"),
        display_name="Anne Onderzoeker",
    )
    reviewer = console.create_account(
        username="reviewer.bert",
        password="bert-secret",
        roles=("reviewer",),
        display_name="Bert Reviewer",
    )
    return {"researcher": researcher, "reviewer": reviewer}


def _ingest_richtlijn(console: OperationsConsole, accounts: dict, **overrides) -> dict:
    kwargs = {
        "actor_id": accounts["researcher"]["account_id"],
        "filename": "phase1.html",
        "data": FIXTURE.read_bytes(),
        "content_type": "text/html",
        "ingest_kind": "new",
        "title": "Phase 1 admission regression",
        "version": "1.0",
        "date": "2025-04-01",
        "live_url": "",
        "class_": "richtlijn",
        "family": "fractuurpreventie",
        "named_reviewers": [accounts["reviewer"]["account_id"]],
    }
    kwargs.update(overrides)
    return console.ingest(**kwargs)


def _boom_freeze_bytes() -> bytes:
    payload = {
        "kind": "beslisboom-freeze",
        "paths": [{"id": "path-screening", "text": "Screening op valrisico"}],
        "nodes": [
            {
                "id": "node-vraag",
                "text": "Is er een verhoogd valrisico?",
                "scorelist": False,
            }
        ],
        "outcomes": [
            {
                "id": "out-verwijs",
                "text": "Verwijs naar de valpoli.",
                "applies_if": ["node-vraag"],
            }
        ],
    }
    return (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _ingest_boom(console: OperationsConsole, accounts: dict) -> dict:
    return console.ingest(
        actor_id=accounts["researcher"]["account_id"],
        filename="valrisico-boom.json",
        data=_boom_freeze_bytes(),
        content_type="application/json",
        ingest_kind="new",
        title="Valrisico boom",
        version="1.0",
        date="2025-04-01",
        live_url="",
        class_="beslisboom",
        family="valrisico",
        named_reviewers=[accounts["reviewer"]["account_id"]],
    )


def _text_of(obj: dict) -> str:
    return ((obj.get("content") or {}).get("clean_text") or obj.get("candidate_text") or "").strip()


def _admission(obj: dict) -> dict:
    return ((obj.get("metadata") or {}).get("admission") or {})


def _find_by_text(objects: list[dict], snippet: str) -> dict:
    for obj in objects:
        if snippet in _text_of(obj):
            return obj
    raise AssertionError(f"no object contains {snippet!r}")


def _complete_adviseert_candidate(**overrides) -> dict:
    record = build_candidate_record(
        candidate_id="cand-adviseert",
        document_id="doc-phase1",
        document_version="1.0",
        source_hash="a" * 64,
        section_path=["2 Aanbevelingen"],
        source_locator_start="lines:16-16",
        source_locator_end="lines:16-16",
        source_text_exact=ADVISEERT,
        candidate_text=ADVISEERT,
        subject_span="De werkgroep",
        predicate_span="adviseert",
        proposed_type="recommendation",
        type_evidence_spans=["adviseert"],
        context_before="Deze richtlijn beschrijft signalering van fractuurrisico bij ouderen in de eerste lijn.",
        context_after=DJG,
        actor_of_scope="de verpleegkundige",
        recommended_action="te gebruiken",
        action_object_or_goal="de risicofactoren scorelijst",
        recommendation_evidence_span=ADVISEERT,
    )
    record.update(overrides)
    return record


def _base_candidate(**overrides) -> dict:
    record = build_candidate_record(
        candidate_id="cand-1",
        document_id="doc-phase1",
        document_version="1.0",
        source_hash="a" * 64,
        section_path=["2 Aanbevelingen"],
        source_locator_start="lines:17-17",
        source_locator_end="lines:17-17",
        source_text_exact=DJG,
        candidate_text=DJG,
        subject_span="De dJG",
        predicate_span="wordt",
        proposed_type="recommendation",
        type_evidence_spans=[],
        context_before=ADVISEERT,
        context_after=ONE_WORD,
    )
    record.update(overrides)
    return record


# ---------------------------------------------------------------------------
# Required fields, contracts, catalog
# ---------------------------------------------------------------------------


def test_required_candidate_fields_are_the_section_4_set() -> None:
    assert REQUIRED_CANDIDATE_FIELDS == (
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


def test_dutch_type_contracts_map_to_english_tokens() -> None:
    assert DUTCH_TYPE_NAMES["Aanbeveling"] == "recommendation"
    assert DUTCH_TYPE_NAMES["Definitie"] == "definition"
    assert DUTCH_TYPE_NAMES["Voorwaarde"] == "condition"
    assert DUTCH_TYPE_NAMES["Uitzondering"] == "exception"
    assert DUTCH_TYPE_NAMES["Feitelijke constatering"] == "factual_finding"
    assert DUTCH_TYPE_NAMES["Toelichting"] == "explanation"
    assert TYPE_CONTRACT_FIELDS["recommendation"] == (
        "actor_of_scope",
        "recommended_action",
        "action_object_or_goal",
        "recommendation_evidence_span",
    )
    assert TYPE_CONTRACT_FIELDS["definition"] == ("defined_term", "definiens_span")
    assert TYPE_CONTRACT_FIELDS["condition"] == ("condition_span", "condition_target")
    assert TYPE_CONTRACT_FIELDS["exception"] == ("exception_span", "exception_target")
    assert TYPE_CONTRACT_FIELDS["factual_finding"] == ("factual_claim_span",)
    assert TYPE_CONTRACT_FIELDS["explanation"] == ("support_span", "supported_object")


def test_factual_finding_maps_to_explanation_and_is_not_a_seventh_serving_type() -> None:
    assert serving_type_for_admission_type("factual_finding") == FACTUAL_FINDING_SERVING_TYPE
    assert FACTUAL_FINDING_SERVING_TYPE == "explanation"
    assert "factual_finding" not in CLOSED_OBJECT_TYPES
    assert CLOSED_OBJECT_TYPES == (
        "heading",
        "definition",
        "explanation",
        "condition",
        "exception",
        "recommendation",
    )


def test_build_candidate_record_carries_every_required_field() -> None:
    record = _complete_adviseert_candidate()
    for field in REQUIRED_CANDIDATE_FIELDS:
        assert field in record, field
    assert record["gate_result"] in {GATE_ALLOWED, GATE_BLOCKED, None, ""}
    assert isinstance(record["reason_codes"], list)


def test_missing_required_field_blocks_even_when_soft_scores_are_perfect() -> None:
    record = _complete_adviseert_candidate()
    record.pop("subject_span")
    admitted = admit_candidate(
        record,
        soft_scores={"relevant": 1.0, "complete": 1.0, "understandable": 1.0},
    )
    assert admitted["gate_result"] == GATE_BLOCKED
    assert admitted["gate_result"] != GATE_ALLOWED
    assert "subject_missing" in admitted["reason_codes"]


def test_impliciet_prose_is_source_fidelity_failure() -> None:
    record = _complete_adviseert_candidate(
        actor_of_scope="impliciet de verpleegkundige"
    )
    admitted = admit_candidate(record)
    assert admitted["gate_result"] == GATE_BLOCKED
    assert "source_fidelity_failure" in admitted["reason_codes"]


def test_soft_scores_volume_and_ship_then_fix_must_not_open_the_gate() -> None:
    blocked = admit_candidate(_base_candidate())
    assert blocked["gate_result"] == GATE_BLOCKED
    copies = [admit_candidate(_base_candidate(candidate_id=f"vol-{i}")) for i in range(50)]
    assert all(row["gate_result"] == GATE_BLOCKED for row in copies)
    still = admit_candidate(
        _base_candidate(),
        soft_scores={"relevant": 0.99, "complete": 0.99, "understandable": 0.99},
    )
    assert still["gate_result"] == GATE_BLOCKED


def test_phase1_must_not_emit_context_scan_not_done_for_every_candidate() -> None:
    allowed = admit_candidate(_complete_adviseert_candidate())
    blocked = admit_candidate(_base_candidate())
    assert allowed["gate_result"] == GATE_ALLOWED
    assert "context_scan_not_done" not in allowed["reason_codes"]
    assert "context_scan_not_done" not in blocked["reason_codes"]


# ---------------------------------------------------------------------------
# Named regressions
# ---------------------------------------------------------------------------


def test_djg_must_not_enter_ordinary_queue_as_aanbeveling() -> None:
    admitted = admit_candidate(_base_candidate(proposed_type="recommendation"))
    assert admitted["gate_result"] == GATE_BLOCKED
    for code in (
        "recommendation_evidence_missing",
        "comparison_target_missing",
        "abbreviation_unresolved",
    ):
        assert code in admitted["reason_codes"], code
    queue = ordinary_review_queue(
        [
            {
                "object_id": "djg-1",
                "object_type": "unclassified",
                "proposed_object_type": "recommendation",
                "content": {"clean_text": DJG},
                "metadata": {"admission": admitted},
            }
        ]
    )
    assert queue == []
    assert not is_slow_review_duty(
        {
            "object_id": "djg-1",
            "object_type": "unclassified",
            "proposed_object_type": "recommendation",
            "content": {"clean_text": DJG},
            "metadata": {"admission": admitted},
        }
    )


def test_one_word_is_blocked() -> None:
    admitted = admit_candidate(
        _base_candidate(
            candidate_id="cand-one-word",
            source_text_exact=ONE_WORD,
            candidate_text=ONE_WORD,
            subject_span="",
            predicate_span="",
            proposed_type="explanation",
            type_evidence_spans=[],
            context_before=DJG,
            context_after=UNRESOLVED_REF,
        )
    )
    assert admitted["gate_result"] == GATE_BLOCKED
    assert any(
        code in admitted["reason_codes"]
        for code in (
            "incomplete_sentence",
            "no_independent_claim",
            "subject_missing",
            "predicate_missing",
        )
    )


def test_unresolved_ref_is_blocked() -> None:
    admitted = admit_candidate(
        _base_candidate(
            candidate_id="cand-ref",
            source_text_exact=UNRESOLVED_REF,
            candidate_text=UNRESOLVED_REF,
            subject_span="",
            predicate_span="Zie",
            proposed_type="explanation",
            type_evidence_spans=["Zie"],
            context_before=ONE_WORD,
            context_after=INCOMPLETE_COMPARISON,
            references_detected=["tabel 4"],
            references_resolved=[],
        )
    )
    assert admitted["gate_result"] == GATE_BLOCKED
    assert "unresolved_reference" in admitted["reason_codes"]


def test_incomplete_comparison_is_blocked() -> None:
    admitted = admit_candidate(
        _base_candidate(
            candidate_id="cand-cmp",
            source_text_exact=INCOMPLETE_COMPARISON,
            candidate_text=INCOMPLETE_COMPARISON,
            subject_span="Deze methode",
            predicate_span="is",
            proposed_type="factual_finding",
            type_evidence_spans=["is vaker effectief"],
            factual_claim_span=INCOMPLETE_COMPARISON,
            context_before=UNRESOLVED_REF,
            context_after=FALSE_RECOMMENDATION,
            comparison_markers=["vaker"],
            comparison_targets=[],
        )
    )
    assert admitted["gate_result"] == GATE_BLOCKED
    assert "comparison_target_missing" in admitted["reason_codes"]


def test_false_recommendation_is_blocked_from_ordinary_queue_as_aanbeveling() -> None:
    admitted = admit_candidate(
        _base_candidate(
            candidate_id="cand-false-rec",
            source_text_exact=FALSE_RECOMMENDATION,
            candidate_text=FALSE_RECOMMENDATION,
            subject_span="Urine-incontinentie",
            predicate_span="komt",
            proposed_type="recommendation",
            type_evidence_spans=[],
            context_before=INCOMPLETE_COMPARISON,
            context_after=LONE_EXCEPTION,
        )
    )
    assert admitted["gate_result"] == GATE_BLOCKED
    assert "recommendation_evidence_missing" in admitted["reason_codes"]
    assert ordinary_review_queue(
        [
            {
                "object_id": "false-rec",
                "proposed_object_type": "recommendation",
                "content": {"clean_text": FALSE_RECOMMENDATION},
                "metadata": {"admission": admitted},
            }
        ]
    ) == []


def test_full_adviseert_recommendation_may_be_allowed_when_contract_complete() -> None:
    admitted = admit_candidate(_complete_adviseert_candidate())
    assert admitted["gate_result"] == GATE_ALLOWED
    assert admitted["reason_codes"] == []
    assert admitted["actor_of_scope"] == "de verpleegkundige"
    assert admitted["recommended_action"]
    assert admitted["action_object_or_goal"]
    assert admitted["recommendation_evidence_span"]
    queue = ordinary_review_queue(
        [
            {
                "object_id": "rec-ok",
                "object_type": "unclassified",
                "proposed_object_type": "recommendation",
                "content": {"clean_text": ADVISEERT},
                "metadata": {"admission": admitted},
            }
        ]
    )
    assert [row["object_id"] for row in queue] == ["rec-ok"]


def test_lone_exception_is_blocked() -> None:
    admitted = admit_candidate(
        _base_candidate(
            candidate_id="cand-lone-exc",
            source_text_exact=LONE_EXCEPTION,
            candidate_text=LONE_EXCEPTION,
            subject_span="",
            predicate_span="Tenzij",
            proposed_type="exception",
            type_evidence_spans=["Tenzij"],
            exception_span=LONE_EXCEPTION,
            exception_target="",
            context_before=FALSE_RECOMMENDATION,
            context_after=REC_PLUS_EXC,
        )
    )
    assert admitted["gate_result"] == GATE_BLOCKED
    assert any(
        code in admitted["reason_codes"]
        for code in ("exception_target_missing", "no_independent_claim")
    )


def test_recommendation_plus_exception_must_keep_or_link_the_exception() -> None:
    dropped = admit_candidate(
        _complete_adviseert_candidate(
            candidate_id="cand-drop-exc",
            source_text_exact=REC_PLUS_EXC,
            candidate_text="De werkgroep adviseert calcium te geven.",
            recommendation_evidence_span="De werkgroep adviseert calcium te geven.",
            exceptions_detected=[],
            related_candidates=[],
        )
    )
    assert dropped["gate_result"] == GATE_BLOCKED
    assert "source_fidelity_failure" in dropped["reason_codes"]

    kept = admit_candidate(
        _complete_adviseert_candidate(
            candidate_id="cand-keep-exc",
            source_text_exact=REC_PLUS_EXC,
            candidate_text=REC_PLUS_EXC,
            recommendation_evidence_span=REC_PLUS_EXC,
            exceptions_detected=["tenzij er hypercalciëmie bestaat"],
            related_candidates=["cand-exc-hypercalciemie"],
        )
    )
    assert kept["gate_result"] == GATE_ALLOWED
    assert kept["exceptions_detected"]


# ---------------------------------------------------------------------------
# Boom unchanged
# ---------------------------------------------------------------------------


def test_boom_path_node_outcome_do_not_get_richtlijn_type_contract_incomplete() -> None:
    boom_rows = [
        {
            "object_id": "path-1",
            "object_type": "path",
            "proposed_object_type": "path",
            "content": {"clean_text": "Screening op valrisico"},
        },
        {
            "object_id": "node-1",
            "object_type": "node",
            "proposed_object_type": "node",
            "content": {"clean_text": "Is er een verhoogd valrisico?"},
        },
        {
            "object_id": "out-1",
            "object_type": "outcome",
            "proposed_object_type": "outcome",
            "content": {"clean_text": "Verwijs naar de valpoli."},
        },
    ]
    stamped = apply_admission_gate(
        boom_rows,
        klasse="beslisboom",
        document_version="1.0",
        source_hash="b" * 64,
    )
    for row, kind in zip(stamped, CLOSED_BOOM_TYPES):
        admission = _admission(row)
        assert row["proposed_object_type"] == kind
        assert "type_contract_incomplete" not in (admission.get("reason_codes") or [])
        assert admission.get("gate_result") != GATE_BLOCKED or admission == {}
    duty = slow_review_duty(stamped, review_path="boom")
    assert {row["object_id"] for row in duty} >= {"node-1", "out-1"}


def test_legacy_objects_without_admission_remain_in_v219_duty() -> None:
    row = {
        "object_id": "r1",
        "object_type": "unclassified",
        "proposed_object_type": "recommendation",
        "content": {"clean_text": "Bespreek het onderwerp met de zorgvrager."},
    }
    assert is_slow_review_duty(row) is True
    assert ordinary_review_queue([row]) == [row]


# ---------------------------------------------------------------------------
# Extract → ordinary queue (fixture ingest)
# ---------------------------------------------------------------------------


def test_ingest_fixture_gates_named_regressions_and_keeps_adviseert(tmp_path: Path) -> None:
    console = _console(tmp_path)
    accounts = _accounts(console)
    receipt = _ingest_richtlijn(console, accounts)
    objects = [
        obj
        for obj in console.snapshot_objects(receipt["snapshot_id"])
        if obj.get("object_type") != "document"
    ]
    djg = _find_by_text(objects, DJG)
    adviseert = _find_by_text(objects, "adviseert de verpleegkundige")
    one_word = _find_by_text(objects, "nieuwe scorelijst")
    unresolved = _find_by_text(objects, "tabel 4")
    comparison = _find_by_text(objects, "vaker effectief")
    false_rec = _find_by_text(objects, "komt bij ouderen vaak voor")
    lone_exc = _find_by_text(objects, "recente fractuur")
    rec_exc = _find_by_text(objects, "hypercalciëmie")

    assert _admission(djg)["gate_result"] == GATE_BLOCKED
    for code in (
        "recommendation_evidence_missing",
        "comparison_target_missing",
        "abbreviation_unresolved",
    ):
        assert code in _admission(djg)["reason_codes"], code
    assert _admission(adviseert)["gate_result"] == GATE_ALLOWED
    assert _admission(one_word)["gate_result"] == GATE_BLOCKED
    assert "unresolved_reference" in _admission(unresolved)["reason_codes"]
    assert "comparison_target_missing" in _admission(comparison)["reason_codes"]
    assert "recommendation_evidence_missing" in _admission(false_rec)["reason_codes"]
    assert any(
        code in _admission(lone_exc)["reason_codes"]
        for code in ("exception_target_missing", "no_independent_claim")
    )
    assert "tenzij" in _text_of(rec_exc).casefold()
    assert rec_exc.get("proposed_object_type") == "recommendation"
    assert _admission(rec_exc)["exceptions_detected"]

    ordinary = ordinary_review_queue(objects)
    ordinary_texts = [_text_of(obj) for obj in ordinary]
    assert DJG not in ordinary_texts
    assert any("adviseert de verpleegkundige" in text for text in ordinary_texts)
    assert all(_admission(obj).get("gate_result") == GATE_ALLOWED for obj in ordinary)
    assert "context_scan_not_done" not in {
        code
        for obj in objects
        for code in _admission(obj).get("reason_codes") or []
    }

    duty = slow_review_duty(objects)
    duty_texts = [_text_of(obj) for obj in duty]
    assert DJG not in duty_texts
    assert all(obj in ordinary or _admission(obj).get("gate_result") == GATE_ALLOWED for obj in duty)

    blocked = blocked_audit_lane(objects)
    blocked_texts = [_text_of(obj) for obj in blocked]
    assert DJG in blocked_texts
    assert any("scorelijst" in text for text in blocked_texts)
    assert not any(obj in ordinary for obj in blocked)

    client = TestClient(create_console_app(console))
    client.post("/login", data={"username": "researcher.anne", "password": "anne-secret"})
    review = client.get(f"/review?document={receipt['snapshot_id']}").text
    slow = review.split('class="review-lane-slow"', 1)[-1].split("review-blocked-audit", 1)[0]
    assert DJG not in slow
    assert "adviseert de verpleegkundige" in review
    assert "review-blocked-audit" in review
    assert DJG in review.split("review-blocked-audit", 1)[-1]


def test_boom_ingest_does_not_apply_richtlijn_contracts(tmp_path: Path) -> None:
    console = _console(tmp_path)
    accounts = _accounts(console)
    receipt = _ingest_boom(console, accounts)
    objects = [
        obj
        for obj in console.snapshot_objects(receipt["snapshot_id"])
        if obj.get("object_type") != "document"
    ]
    kinds = {obj.get("proposed_object_type") or obj.get("object_type") for obj in objects}
    assert kinds >= set(CLOSED_BOOM_TYPES)
    for obj in objects:
        admission = _admission(obj)
        assert "type_contract_incomplete" not in (admission.get("reason_codes") or [])
    duty = slow_review_duty(objects, review_path="boom")
    assert duty


def test_confirm_must_not_write_factual_finding_serving_type(tmp_path: Path) -> None:
    console = _console(tmp_path)
    accounts = _accounts(console)
    receipt = _ingest_richtlijn(console, accounts)
    objects = console.snapshot_objects(receipt["snapshot_id"])
    djg = _find_by_text(objects, DJG)
    from src.operations_console_v1 import ConsoleError

    with pytest.raises(ConsoleError):
        console.confirm_object_type(
            actor_id=accounts["reviewer"]["account_id"],
            snapshot_id=receipt["snapshot_id"],
            object_id=djg["object_id"],
            confirmed_object_type="factual_finding",
        )
    refreshed = next(
        obj
        for obj in console.snapshot_objects(receipt["snapshot_id"])
        if obj["object_id"] == djg["object_id"]
    )
    assert refreshed.get("confirmed_object_type") != "factual_finding"
