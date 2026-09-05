"""FastAPI HTML surface for the internal operations console.

Task-oriented researcher door over the knowledge kernel (Protocol v2.9).
Not the Product API, not a care-app frontend, and not a public website.
Chat is not a room.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import quote

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from src.four_eyes_v1 import requires_four_eyes
from src.beslisboom_path_v1 import CLOSED_BOOM_TYPES, review_path_for_klasse
from src.klasse_wijzigen_v1 import is_cross_model_class_change
from src.heading_parent_list_v1 import (
    heading_role,
    heading_visible_text,
    is_heading_object,
    mark_heading_roles,
    parent_choice_list,
    parent_proposal_may_bind,
    parse_outline_number,
)
from src.object_taxonomy_v1 import (
    CLOSED_OBJECT_TYPES,
    CLOSED_RECOMMENDATION_STRENGTHS,
    STRENGTH_STAMP_LABELS,
    recommendation_strength_sentence,
    recommendation_strength_ui_applies,
)
from src.admission_gate_v1 import blocked_audit_lane
from src.operations_console_v1 import (
    ALLOWED_CLASSES,
    ALLOWED_DELETE_NEXT,
    CONSOLE_VERSION,
    ConsoleError,
    OperationsConsole,
    REPO_ROOT,
    remaining_not_duty,
    remaining_unclassified,
    review_card_sentence,
    review_row_status,
    review_row_title,
    review_stacks,
    slow_review_duty,
)
from src.open_original_v1 import researcher_visible_prose
from src.serving_relations_v1 import CLOSED_RELATION_TYPES, proposed_relations

SERVICE_VERSION = CONSOLE_VERSION
COOKIE = "console_session"
BRAND_DIR = REPO_ROOT / "assets" / "brand"
RESEARCHER_ROOMS = frozenset({"ingest", "tree", "review"})
HELP_ONCE = (
    "Interne operations console voor richtlijnonderzoekers en reviewers. "
    "Dit is niet de Product API. Niet ontworpen voor verpleegkundigen. "
    "Chat is geen kamer in deze console. Geen parallel ingestpad voor engineers "
    "als onderzoekerservaring."
)
STATUS_LABELS = {
    "captured_not_published": "ingevoerd, niet gepubliceerd",
    "needs_review": "wacht op beoordeling",
    "approved": "goedgekeurd",
    "rejected": "afgewezen",
}
OBJECT_TYPE_LABELS = {
    "unclassified": "Nog niet geclassificeerd",
    "heading": "Kop",
    "definition": "Definitie",
    "explanation": "Toelichting",
    "condition": "Voorwaarde",
    "exception": "Uitzondering",
    "recommendation": "Aanbeveling",
    "path": "Pad",
    "node": "Knoop",
    "outcome": "Uitkomst",
}
BLOCKER_LABELS = {
    "second_named_reviewer_required": "Nog een andere benoemde reviewer moet goedkeuren.",
    "blocked_pending_immutable_locator": "Duurzame opslag ontbreekt; publicatie blijft geblokkeerd.",
    "object_tuple_required": "Publicatie vereist review gebonden aan object, versie, hash, bevestigd type, reviewer en besluit.",
    "four_eyes_required": "High-risk objecten vereisen four-eyes: een tweede benoemde reviewer op hetzelfde objecttupel.",
}
ERROR_COPY = {
    "not_authenticated": "Je bent niet aangemeld.",
    "invalid_credentials": "Gebruikersnaam of wachtwoord is onjuist.",
    "uploader_cannot_be_sole_required_reviewer": "De uploader mag reviewer zijn, maar niet de enige.",
    "word_not_first_wave": "Word-bestanden horen niet bij de first wave. Lever HTML of PDF in.",
    "story_html_boom_player_out_of_first_wave": "Kennisplatform-boomplayers horen niet bij de first wave.",
    "story_html_alone_insufficient": "story.html alleen is onvoldoende. Lever een gehashte boom-freeze in.",
    "live_rest_not_sole_source": "Live kennisplatform-REST is niet de bron van waarheid. Lever een gehashte boom-freeze in.",
    "live_rest_sole_source": "Live kennisplatform-REST is niet de bron van waarheid. Lever een gehashte boom-freeze in.",
    "outcome_review_failed": "Deze uitkomst kan niet worden bevestigd. Bind via geldt-indien, splits kogels of vul de lege uitkomst.",
    "outcome_relation_unconfirmed": "Bevestig eerst de geldt-indienrelatie naar een node of pad.",
    "outcome_strength_required": "Kies DOEN, OVERWEEG of NIET DOEN voor een handelingsuitkomst.",
    "empty_boom_freeze": "De boom-freeze heeft geen nodes en uitkomsten.",
    "invalid_boom_freeze": "Dit bestand is geen geldige beslisboom-freeze.",
    "condition_fused_into_outcome": "Een voorwaarde mag niet alleen in de uitkomsttekst zitten. Bind via geldt-indien.",
    "official_file_or_url_required": "Kies een HTML-, PDF- of boom-freezebestand, of een URL.",
    "named_reviewers_required": "Kies minstens één andere reviewer dan jezelf.",
    "publisher_role_required": "Publiceren vereist de rol publisher.",
    "reviewer_role_required": "Review vereist de rol reviewer.",
    "researcher_role_required": "Inleveren vereist de rol researcher.",
    "live_url_html_not_allowed": "Een live HTML-URL kan niet worden ingeleverd. Lever een HTML-bestand of een PDF-URL in.",
    "unknown_object_type": "Kies een type uit de gesloten set.",
    "blocked_candidate_not_reviewable": "Deze kandidaat is geblokkeerd door de toelatingspoort. Bevestigen of goedkeuren kan niet; revisie of afwijzen blijft mogelijk.",
    "object_type_not_confirmed": "Kies een type uit de gesloten set.",
    "unknown_role": "Alleen researcher, reviewer of publisher zijn toegestaan.",
    "forbidden_reviewer_identity": "Deze identiteit mag niet als reviewer worden aangemaakt.",
    "unknown_relation_type": "Kies alleen relaties uit de gesloten set.",
    "open_original_required": "Open eerst de bronpassage. Type bevestigen zonder het origineel is niet toegestaan.",
    "source_locator_missing": "De bronpassage ontbreekt; type bevestigen is niet toegestaan.",
    "freeze_bytes_missing": "Het geüploade origineel ontbreekt; type bevestigen is niet toegestaan.",
    "locator_kind_mismatch": "De locator past niet bij dit bestand.",
    "unsupported_locator": "Deze locator kan niet worden geopend.",
    "invalid_review_decision": "Kies een besluit: goedkeuren, revisie vragen of afwijzen.",
    "review_comment_required": "Geef een toelichting bij een revisieverzoek of afwijzing.",
    "source_date_required": "Vul de publicatiedatum uit het colofon in.",
    "invalid_source_date": "Gebruik een geldige kalenderdatum.",
    "source_version_required": "Vul de versie van de freeze in.",
    "invalid_source_version": "Versie is alleen getallen met punten, bijvoorbeeld 1.0. Geen jaartal en geen v-voorvoegsel.",
    "fast_lane_heading_required": "Batch-bevestiging geldt alleen voor koppen.",
    "recommendation_strength_requires_recommendation": "Sterkte hoort alleen bij een aanbeveling.",
    "invalid_parent_structure": "Deze ouder is niet structureel geldig. Kies een kop die hiërarchisch boven dit object staat.",
    "unknown_recommendation_strength": "Kies DOEN, OVERWEEG of NIET DOEN.",
    "published_objects_must_not_be_rewritten": "Gepubliceerde objecten worden niet herschreven.",
    "unknown_snapshot": "Dit document is niet gevonden.",
    "delete_confirmation_required": "Bevestig eerst dat je dit unpublished document wilt verwijderen.",
    "delete_title_confirmation_required": "Typ de exacte documenttitel om te bevestigen.",
    "published_projection_must_not_be_deleted": "Een gepubliceerde projectie wordt niet verwijderd.",
    "unpublished_delete_role_required": "Verwijderen van unpublished documenten vereist researcher of reviewer.",
    "hide_selected_objects_forbidden": "Geselecteerde objecten in een freeze die in Review blijft, worden niet verborgen.",
    "cross_model_direct_change_blocked": "Directe klassewijziging tussen niet-boom en beslisboom is geblokkeerd. Re-extract van dezelfde freeze is vereist.",
    "class_change_confirmation_required": "Bevestig eerst de consequentie van Klasse wijzigen.",
    "published_class_change_blocked": "Een gepubliceerd document wordt niet herschreven. Klasse wijzigen blijft fail-closed.",
    "cross_model_reextract_required": "Cross-model vereist re-extract van dezelfde freeze naar een nieuwe objectgrafiek.",
    "source_identity_must_not_change": "De bron blijft ongewijzigd: SHA-256, titel, versie en herkomst wijzigen niet.",
}
RELATION_LABELS = {
    "applies_if": "geldt indien",
    "except_if": "geldt niet indien",
    "defines": "definieert",
    "explains": "licht toe",
    "supported_by": "onderbouwd door",
    "supersedes": "vervangt",
    "parent": "bovenliggend",
    "child": "onderliggend",
}


def _esc(value: Any) -> str:
    return (
        str(value or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _status_label(state: str) -> str:
    return STATUS_LABELS.get(state, state.replace("_", " "))


def _object_type_label(value: str | None) -> str:
    return OBJECT_TYPE_LABELS.get(value or "", (value or "").replace("_", " "))


def _beeldmerk() -> str:
    return (
        '<img class="beeldmerk" src="/brand/venvn-beeldmerk.png" '
        'width="94" height="32" alt="v&amp;vn">'
    )


def _metis_wordmark() -> str:
    return (
        '<img class="metis-wordmark" src="/brand/metis-wordmark.jpg" '
        'width="1000" height="363" alt="Metis — V&amp;VN Data Services">'
    )


def _metis_mark() -> str:
    return (
        '<img class="metis-mark" src="/brand/metis-mark.jpg" '
        'width="96" height="72" alt="">'
    )


def _login_brand() -> str:
    return f"""
    <div class="login-brand">
      <a class="login-wordmark" href="/login" aria-label="Metis — V&amp;VN Data Services">
        {_metis_wordmark()}
      </a>
      <div class="venvn-endorsement">
        <span>Een dienst van</span>
        {_beeldmerk()}
      </div>
    </div>
    """


def _page(body: str) -> str:
    return f"""<!doctype html>
<html lang="nl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>V&amp;VN Data Services — Interne operations console</title>
<link rel="stylesheet" href="/brand/console.css">
</head>
<body>
<div class="shell">
<div class="canvas">
{body}
</div>
</div>
<script>
document.querySelectorAll('[data-review-form]').forEach((form) => {{
  const decision = form.querySelector('[name="decision"]');
  const type = form.querySelector('[name="confirmed_object_type"]');
  const comment = form.querySelector('[name="comment"]');
  const commentField = form.querySelector('[data-comment-field]');
  const correctionField = form.querySelector('[data-correction-field]');
  const hint = form.querySelector('[data-decision-hint]');
  const submit = form.querySelector('[data-submit-review]');
  const stamp = form.querySelector('[data-stamp-block]');
  const strength = form.querySelector('[name="recommendation_strength"]');
  const strengthTypes = new Set(['recommendation', 'outcome']);
  const updateStamp = () => {{
    const liveType = type.value;
    const show = strengthTypes.has(liveType);
    if (stamp) stamp.hidden = !show;
    if (strength) {{
      strength.disabled = !show;
      if (!show) strength.value = '';
    }}
  }};
  const update = () => {{
    const value = decision.value;
    const needsComment = value === 'revise' || value === 'reject';
    const needsType = value === 'approve';
    commentField.hidden = !needsComment;
    correctionField.hidden = value !== 'revise';
    comment.required = needsComment;
    type.required = needsType;
    submit.disabled = !value || (needsType && !type.value) || (needsComment && !comment.value.trim());
    if (!value) {{ hint.textContent = 'Kies eerst een besluit.'; submit.textContent = 'Review vastleggen'; }}
    else if (value === 'approve') {{ hint.textContent = 'Bevestig ook het type voordat je goedkeurt.'; submit.textContent = 'Goedkeuring vastleggen'; }}
    else if (value === 'revise') {{ hint.textContent = 'Licht toe wat aangepast moet worden.'; submit.textContent = 'Revisieverzoek versturen'; }}
    else {{ hint.textContent = 'Licht toe waarom dit kennisobject wordt afgewezen.'; submit.textContent = 'Afwijzing vastleggen'; }}
    updateStamp();
  }};
  decision.addEventListener('change', update);
  type.addEventListener('change', update);
  comment.addEventListener('input', update);
  updateStamp();
  update();
}});
</script>
</body>
</html>
"""


def _help(*, room: str = "") -> str:
    if room in RESEARCHER_ROOMS:
        return ""
    return f"""
    <details class="help">
      <summary>Over deze console</summary>
      <p>{_esc(HELP_ONCE)}</p>
    </details>
    """


def _nav(account: dict[str, Any] | None, current: str = "", counts: dict[str, int] | None = None) -> str:
    who = (
        f'{_esc(account.get("display_name"))} · rollen: {", ".join(_esc(r) for r in account.get("roles") or [])}'
        if account
        else "niet aangemeld"
    )
    counts = counts or {}
    rooms = [
        ("ingest", "/ingest", "Inleveren", counts.get("ingest", 0)),
        ("tree", "/tree", "Documentenhiërarchie", counts.get("tree", 0)),
        ("review", "/review", "Review", counts.get("review", 0)),
        ("publish", "/publish", "Publiceren", counts.get("publish", 0)),
        ("accounts", "/accounts", "Accounts", 0),
    ]
    links = []
    for key, href, label, count in rooms:
        current_attr = ' aria-current="page"' if current == key else ""
        badge = f'<span class="badge">{count}</span>' if count else ""
        links.append(f'<a href="{href}"{current_attr}>{label}{badge}</a>')
    links.append('<a class="quiet" href="/logout">Uitloggen</a>')
    return f"""
    <header class="topbar">
      <a class="brand metis-brand" href="/" aria-label="Metis — V&amp;VN Data Services">
        <span class="metis-mark-frame">{_metis_mark()}</span>
        <span class="brand-copy">
          <strong>Metis</strong>
          <span>V&amp;VN Data Services</span>
        </span>
      </a>
      <nav class="rooms">{"".join(links)}</nav>
      <div class="who">{who}</div>
    </header>
    """


def _class_options(selected: str = "richtlijn") -> str:
    return "".join(
        f'<option value="{_esc(name)}"{" selected" if name == selected else ""}>{_esc(name)}</option>'
        for name in ALLOWED_CLASSES
    )


def _document_options(rows: list[dict[str, Any]], selected: str = "") -> str:
    options = ['<option value="">Kies een document</option>']
    for row in rows:
        label = f'{row["title"]} · {row["version"]} · {row["family"]}'
        snap = row["snapshot_id"]
        options.append(
            f'<option value="{_esc(snap)}"{" selected" if snap == selected else ""}>{_esc(label)}</option>'
        )
    return "".join(options)


def _type_options(confirmed: str, *, review_path: str = "richtlijn") -> str:
    placeholder_selected = " selected" if not confirmed else ""
    options = [
        f'<option value="" disabled{placeholder_selected}>nog niet bevestigd</option>'
    ]
    names = CLOSED_BOOM_TYPES if review_path == "boom" else CLOSED_OBJECT_TYPES
    for name in names:
        selected = " selected" if name == confirmed else ""
        options.append(f'<option value="{name}"{selected}>{_esc(_object_type_label(name))}</option>')
    return "".join(options)


def _relation_checkboxes(obj: dict[str, Any], objects: list[dict[str, Any]]) -> str:
    by_id = {row.get("object_id"): row for row in objects}
    confirmed = {
        (row.get("relation_type"), row.get("target_object_id"))
        for row in (obj.get("confirmed_relations") or [])
        if row.get("relation_type") in CLOSED_RELATION_TYPES
    }
    proposed = [
        row
        for row in proposed_relations(obj)
        if row.get("relation_type") in CLOSED_RELATION_TYPES
    ]
    if not proposed:
        return ""
    boxes = []
    for row in proposed:
        rel = row["relation_type"]
        target_id = row["target_object_id"]
        target = by_id.get(target_id) or {}
        target_text = (
            (target.get("content") or {}).get("heading")
            or (target.get("content") or {}).get("clean_text")
            or target_id
        )
        checked = " checked" if (rel, target_id) in confirmed else ""
        if rel in {"child", "parent"} and is_heading_object(obj) and is_heading_object(target):
            child, parent = (obj, target) if rel == "child" else (target, obj)
            if not parent_proposal_may_bind(child, parent, objects):
                checked = ""
        label = RELATION_LABELS.get(rel, rel)
        boxes.append(
            f'<div class="relation-choice"><p class="relation-copy">Dit kennisobject is '
            f'<b>{_esc(label)}</b> aan:</p><label class="check">'
            f'<input type="checkbox" name="relation" value="{_esc(rel)}:{_esc(target_id)}"{checked}>'
            f'<span>{_esc(target_text)}</span></label></div>'
        )
    return (
        '<fieldset class="relations">'
        "<legend>Voorgestelde relatie</legend>"
        '<p class="field-help">Klopt deze voorgestelde relatie? Vink hem aan om deze te bevestigen.</p>'
        f"{''.join(boxes)}"
        '<button class="btn-secondary" type="submit" form="relations-'
        f'{_esc(obj["object_id"])}">Relatie bevestigen</button>'
        "</fieldset>"
    )


def _review_location(console: OperationsConsole, snapshot_id: str, object_id: str | None = None) -> str:
    """Redirect only to a stored snapshot (and optional object), never raw form bytes."""
    envelope = console._envelope(snapshot_id)
    snap = quote(str(envelope["snapshot_id"]), safe="")
    if not object_id:
        return f"/review?document={snap}"
    known = next(
        (
            row["object_id"]
            for row in console.snapshot_objects(envelope["snapshot_id"])
            if row["object_id"] == object_id
        ),
        None,
    )
    if known is None:
        raise ConsoleError("unknown_object")
    return f"/review?document={snap}&object={quote(str(known), safe='')}"


def _document_card_heading(row: dict[str, Any]) -> str:
    return f"""
      <header>
        <p class="doc-title">{_esc(row["title"])}</p>
      </header>
      <p class="meta">
        <span>versie <b>{_esc(row["version"])}</b></span>
        <span>onderwerp <b>{_esc(row["family"])}</b></span>
        <span>klasse <b>{_esc(row["class"])}</b></span>
        <span>status <b>{_esc(_status_label(row.get("status") or row.get("state") or ""))}</b></span>
      </p>
    """


def _can_delete_unpublished(account: dict[str, Any] | None) -> bool:
    roles = set((account or {}).get("roles") or [])
    return bool(roles & {"researcher", "reviewer"})


def _unpublished_delete_control(
    row: dict[str, Any],
    *,
    account: dict[str, Any] | None,
    console: OperationsConsole,
    next_path: str,
) -> str:
    """Researcher Dutch delete control. Documentenhiërarchie only. Type-to-confirm title."""
    if next_path != "/tree":
        return ""
    if not _can_delete_unpublished(account):
        return ""
    snap = str(row.get("snapshot_id") or "")
    if not snap:
        return ""
    try:
        if console.snapshot_is_published(snap):
            return ""
    except ConsoleError:
        return ""
    title = str(row.get("title") or "")
    target = next_path if next_path in ALLOWED_DELETE_NEXT else "/tree"
    return f"""
      <form class="delete-unpublished" method="post" action="/documents/delete">
        <input type="hidden" name="snapshot_id" value="{_esc(snap)}">
        <input type="hidden" name="next" value="{_esc(target)}">
        <p class="delete-title-confirm">
          <span>Documenttitel</span>
          <strong class="delete-title-shown">{_esc(title)}</strong>
        </p>
        <label>Typ de exacte documenttitel
          <input name="confirm_title" autocomplete="off" required>
        </label>
        <label class="check">
          <input type="checkbox" name="confirm" value="1">
          <span>Ik bevestig dat ik dit unpublished document wil verwijderen</span>
        </label>
        <button class="btn-secondary" type="submit">Verwijder unpublished document</button>
      </form>
    """


def _ingested_document_list(
    console: OperationsConsole,
    documents: list[dict[str, Any]],
    account: dict[str, Any],
) -> str:
    if not documents:
        return (
            "<h2>Ingeleverde documenten</h2>"
            '<p class="muted">Nog geen documenten.</p>'
        )
    cards = []
    for row in documents:
        cards.append(
            f"""
            <article class="doc-card">
              {_document_card_heading({**row, "status": row["state"]})}
            </article>
            """
        )
    return f"<h2>Ingeleverde documenten</h2><div class=\"doc-list\">{''.join(cards)}</div>"


def _strength_options(selected: str | None) -> str:
    options = ['<option value="">Nog niet vastgelegd</option>']
    for name in CLOSED_RECOMMENDATION_STRENGTHS:
        mark = " selected" if name == selected else ""
        options.append(
            f'<option value="{name}"{mark}>{_esc(STRENGTH_STAMP_LABELS[name])}</option>'
        )
    return "".join(options)


def _stamp_block(obj: dict[str, Any], *, hidden: bool = False) -> str:
    proposed = obj.get("proposed_recommendation_strength") or ""
    confirmed = obj.get("confirmed_recommendation_strength") or ""
    shown = confirmed or proposed
    sentence = recommendation_strength_sentence(shown) if shown else (
        "Sterkte van de aanbeveling: kies DOEN, OVERWEEG of NIET DOEN."
    )
    hidden_attr = " hidden" if hidden else ""
    disabled_attr = " disabled" if hidden else ""
    return f"""
                    <section class="review-step review-stamp" data-stamp-block{hidden_attr}>
                      <h4>Sterkte van de aanbeveling</h4>
                      <p class="stamp-sentence">{_esc(sentence)}</p>
                      <label for="strength-{_esc(obj["object_id"])}">Sterkte</label>
                      <select id="strength-{_esc(obj["object_id"])}" name="recommendation_strength"{disabled_attr}>
                        {_strength_options(shown or None)}
                      </select>
                    </section>
    """


def _parent_choice_block(
    obj: dict[str, Any],
    objects: list[dict[str, Any]],
    snapshot_id: str,
    *,
    include_submit: bool = False,
) -> str:
    marked = mark_heading_roles(objects)
    toc_rows = [
        row
        for row in marked
        if is_heading_object(row) and heading_role(row, marked) == "toc"
    ]
    choice = parent_choice_list(objects)
    toc_items = []
    for row in toc_rows:
        text = heading_visible_text(row)
        toc_items.append(f'<li data-heading-role="toc">{_esc(text)}</li>')
    toc_html = ""
    if toc_items:
        toc_html = (
            '<aside class="toc-headings" aria-label="Inhoudsopgave">'
            "<p class=\"field-help\">Inhoudsopgave-regels zijn gemarkeerd en horen niet in de ouderlijst.</p>"
            f"<ul>{''.join(toc_items)}</ul>"
            "</aside>"
        )
    confirmed_parent = {
        row.get("target_object_id")
        for row in (obj.get("confirmed_relations") or [])
        if row.get("relation_type") == "child"
    }
    snap = quote(str(snapshot_id), safe="")
    form_id = f'relations-{_esc(obj["object_id"])}'
    choice_items = []
    for row in choice:
        text = heading_visible_text(row)
        outline = parse_outline_number(text)
        outline_attr = f' data-outline="{".".join(str(part) for part in outline)}"' if outline else ""
        locator = ""
        loc = (row.get("provenance") or {}).get("source_locator") or row.get("source_locator") or {}
        page = loc.get("page") or loc.get("locator_value")
        if page:
            locator = f' <span class="muted">({_esc(page)})</span>'
        object_id = str(row.get("object_id") or "")
        href = f"/review?document={snap}&object={quote(object_id, safe='')}"
        object_attr = f' data-object-id="{_esc(object_id)}"' if object_id else ""
        if not object_id or object_id == obj.get("object_id"):
            choice_items.append(
                f'<li data-heading-role="body"{outline_attr}{object_attr}>'
                f'<a href="{_esc(href)}">{_esc(text)}</a>{locator}</li>'
            )
            continue
        selected = ""
        if object_id in confirmed_parent:
            if not is_heading_object(obj) or not is_heading_object(row) or parent_proposal_may_bind(
                obj, row, objects
            ):
                selected = " checked"
        choice_items.append(
            f'<li data-heading-role="body"{outline_attr}{object_attr}>'
            f'<label class="parent-choice-row">'
            f'<input type="radio" name="parent_choice" value="{_esc(object_id)}" form="{form_id}"{selected}>'
            f'<a href="{_esc(href)}">{_esc(text)}</a>{locator}'
            f"</label></li>"
        )
    if not choice_items and not toc_html:
        return ""
    list_html = ""
    if choice_items:
        list_html = f'<ol class="parent-choice-rows">{"".join(choice_items)}</ol>'
    submit = ""
    if include_submit and choice_items:
        submit = (
            '<button class="btn-secondary" type="submit" '
            f'form="{form_id}">Ouder kiezen</button>'
        )
    return f"""
                  <section class="parent-choice" aria-label="Koppen uit de hoofdtekst">
                    {toc_html}
                    <div data-parent-choice-list>
                      <h4>Koppen uit de hoofdtekst</h4>
                      <p class="field-help">Ouderkeuze volgt de documentstructuur uit het documentlichaam, niet de inhoudsopgave. Kies een kop of open de kop om te navigeren.</p>
                      {list_html}
                      {submit}
                    </div>
                  </section>
    """


def create_console_app(console: OperationsConsole | None = None) -> FastAPI:
    state = console or OperationsConsole(root=REPO_ROOT)
    app = FastAPI(
        title="V&VN Data Services Internal Operations Console",
        version=SERVICE_VERSION,
        description="Internal researcher/reviewer console. Not the Product API. Chat is not a room.",
    )
    if BRAND_DIR.is_dir():
        app.mount("/brand", StaticFiles(directory=str(BRAND_DIR)), name="brand")

    def _current(request: Request) -> dict[str, Any] | None:
        token = request.cookies.get(COOKIE)
        try:
            return state.session_account(token)
        except ConsoleError:
            return None

    def _require(request: Request) -> dict[str, Any]:
        account = _current(request)
        if not account:
            raise ConsoleError("not_authenticated")
        return account

    def _counts(account: dict[str, Any] | None) -> dict[str, int]:
        if not account:
            return {}
        return state.waiting_task_counts(account["account_id"])

    @app.exception_handler(ConsoleError)
    async def console_errors(_request: Request, exc: ConsoleError) -> HTMLResponse:
        status = 401 if exc.code in {"not_authenticated", "invalid_credentials"} else 403 if "role_required" in exc.code else 400
        message = ERROR_COPY.get(exc.code, "Deze actie is niet toegestaan.")
        body = _page(
            f"""
            {_nav(None)}
            <section class="room">
              <h1>Actie niet uitgevoerd</h1>
              <div class="banner err">{_esc(message)}</div>
              <p class="muted">{_esc(exc.code)}</p>
              <p><a href="/login">Naar aanmelden</a></p>
            </section>
            {_help()}
            """
        )
        return HTMLResponse(body, status_code=status)

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def home(request: Request) -> str:
        account = _current(request)
        if not account:
            return _page(
                f"""
                <section class="room login-card">
                  {_login_brand()}
                  <h1>Interne operations console</h1>
                  <p class="lead">Meld je aan om documenten in te leveren, te reviewen of te publiceren.</p>
                  <p><a class="btn-primary" href="/login" style="display:inline-block;text-decoration:none;">Aanmelden</a></p>
                </section>
                {_help()}
                """
            )
        return RedirectResponse("/ingest", status_code=303)

    @app.get("/login", response_class=HTMLResponse)
    def login_form() -> str:
        return _page(
            f"""
            <section class="room login-card">
              {_login_brand()}
              <h1>Aanmelden</h1>
              <p class="lead">Interne account. Geen open registratie.</p>
              <form method="post" action="/login">
                <label for="gebruikersnaam">Gebruikersnaam</label>
                <input id="gebruikersnaam" name="username" autocomplete="username" required>
                <label for="wachtwoord">Wachtwoord</label>
                <input id="wachtwoord" type="password" name="password" autocomplete="current-password" required>
                <button class="btn-primary" type="submit">Aanmelden</button>
              </form>
            </section>
            {_help()}
            """
        )

    @app.post("/login")
    def login(username: str = Form(...), password: str = Form(...)) -> RedirectResponse:
        session = state.authenticate(username, password)
        response = RedirectResponse("/ingest", status_code=303)
        response.set_cookie(COOKIE, session["token"], httponly=True, samesite="lax")
        return response

    @app.get("/logout")
    def logout(request: Request) -> RedirectResponse:
        state.logout(request.cookies.get(COOKIE))
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(COOKIE)
        return response

    @app.get("/ingest", response_class=HTMLResponse)
    def ingest_get(request: Request) -> str:
        account = _require(request)
        reviewers = state.list_reviewer_accounts()
        options = "".join(
            f'<option value="{_esc(row["account_id"])}">{_esc(row["display_name"])} ({_esc(row["username"])})</option>'
            for row in reviewers
        )
        documents = state.list_envelopes()
        return _page(
            f"""
            {_nav(account, "ingest", _counts(account))}
            <section class="room">
              <h1>Document inleveren</h1>
              <p class="lead">Lever HTML, PDF of een gehashte beslisboom-freeze in. Klasse bepaalt het reviewpad.</p>
              <form method="post" action="/ingest" enctype="multipart/form-data">
                <div class="sections">
                  <div class="section">
                    <h3>Bron</h3>
                    <label for="file">Bestand (HTML, PDF of boom-freeze)</label>
                    <input id="file" type="file" name="file">
                    <label for="url">Of PDF-URL (exacte bytes worden direct vastgelegd)</label>
                    <input id="url" name="url" placeholder="https://...">
                  </div>
                  <div class="section">
                    <h3>Document</h3>
                    <div class="field-row">
                      <div>
                        <label for="title">Titel</label>
                        <input id="title" name="title" required>
                      </div>
                      <div>
                        <label for="version">Versie van de freeze</label>
                        <input id="version" name="version" required pattern="[0-9]+(\\.[0-9]+)*" inputmode="numeric" placeholder="bijv. 2.13" autocomplete="off">
                        <p class="field-help">Alleen getallen met punten, bijvoorbeeld 1.0 of 2.13. Geen jaartal.</p>
                      </div>
                    </div>
                    <div class="field-row">
                      <div>
                        <label for="date">Publicatiedatum (colofon)</label>
                        <div class="date-nl-wrap">
                          <input id="date" name="date" type="date" required lang="nl" autocomplete="off">
                          <p class="date-nl-display" data-date-nl aria-live="polite">dd-mm-jjjj</p>
                        </div>
                        <p class="field-help">Datum uit het colofon / publicatiedatum, weergave dd-mm-jjjj. Leeg is niet toegestaan.</p>
                      </div>
                      <div>
                        <label for="class_">Klasse</label>
                        <select id="class_" name="class_">{_class_options()}</select>
                      </div>
                    </div>
                    <label for="family">Onderwerp</label>
                    <input id="family" name="family" required autocomplete="off">
                    <label for="ingest_kind">Nieuw of nieuwe versie</label>
                    <select id="ingest_kind" name="ingest_kind">
                      <option value="new">Nieuw document</option>
                      <option value="new_version">Nieuwe versie van een bestaand document</option>
                    </select>
                    <div id="replaces-row" hidden>
                      <label for="replaces_document">Bestaand document</label>
                      <select id="replaces_document" name="replaces_document">{_document_options(documents)}</select>
                    </div>
                    <label for="live_url">Live URL (optioneel)</label>
                    <input id="live_url" name="live_url">
                  </div>
                  <div class="section">
                    <h3>Reviewers</h3>
                    <label for="named_reviewers">Benoemde reviewers</label>
                    <select id="named_reviewers" name="named_reviewers" multiple size="6">{options}</select>
                    <p class="muted">De uploader mag reviewer zijn, maar niet de enige.</p>
                  </div>
                </div>
                <button class="btn-primary" type="submit">Inleveren</button>
              </form>
              {_ingested_document_list(state, documents, account)}
            </section>
            {_help(room="ingest")}
            <script>
            (function () {{
              var kind = document.getElementById("ingest_kind");
              var row = document.getElementById("replaces-row");
              function sync() {{ row.hidden = kind.value !== "new_version"; }}
              kind.addEventListener("change", sync);
              sync();
              var date = document.getElementById("date");
              var out = document.querySelector("[data-date-nl]");
              function showNl() {{
                if (!date || !out) return;
                if (!date.value) {{ out.textContent = "dd-mm-jjjj"; return; }}
                var parts = date.value.split("-");
                if (parts.length !== 3) {{ out.textContent = "dd-mm-jjjj"; return; }}
                out.textContent = parts[2] + "-" + parts[1] + "-" + parts[0];
              }}
              if (date) {{
                date.addEventListener("input", showNl);
                date.addEventListener("change", showNl);
                showNl();
              }}
            }})();
            </script>
            """
        )

    @app.post("/ingest", response_class=HTMLResponse)
    async def ingest_post(
        request: Request,
        ingest_kind: str = Form(...),
        title: str = Form(...),
        version: str = Form(...),
        date: str = Form(...),
        live_url: str = Form(""),
        class_: str = Form(...),
        family: str = Form(...),
        url: str = Form(""),
        replaces_document: str = Form(""),
        named_reviewers: list[str] = Form(default=[]),
        file: UploadFile | None = File(None),
    ) -> str:
        account = _require(request)
        filename = None
        data = None
        content_type = None
        if file is not None and file.filename:
            filename = file.filename
            data = await file.read()
            content_type = file.content_type
        if isinstance(named_reviewers, str):
            named_reviewers = [named_reviewers] if named_reviewers.strip() else []
        receipt = state.ingest(
            actor_id=account["account_id"],
            filename=filename,
            data=data or None,
            content_type=content_type,
            url=url.strip() or None,
            ingest_kind=ingest_kind,
            title=title,
            version=version,
            date=date,
            live_url=live_url,
            class_=class_,
            family=family,
            named_reviewers=named_reviewers,
            replaces_snapshot_id=replaces_document.strip() or None,
        )
        return _page(
            f"""
            {_nav(account, "ingest", _counts(account))}
            <section class="room">
              <h1>Document ingeleverd</h1>
              <p class="lead">Vastgelegd en klaar voor review.</p>
              <div class="doc-card">
                {_document_card_heading({**receipt, "status": receipt["state"]})}
              </div>
              <p><a class="btn-secondary" href="/review">Naar review</a> <a class="btn-secondary" href="/tree">Naar documentenhierarchie</a></p>
            </section>
            {_help(room="ingest")}
            """
        )

    @app.get("/tree", response_class=HTMLResponse)
    def tree(request: Request) -> str:
        account = _require(request)
        can_move = "researcher" in account["roles"] or "publisher" in account["roles"]
        can_promote = "reviewer" in account["roles"]
        payload = state.family_tree()
        blocks: list[str] = []
        for family, node in payload["families"].items():
            cards = []
            for child in node["children"]:
                actions = []
                if can_move:
                    actions.append(
                        f"""
                        <form method="post" action="/tree/move">
                          <input type="hidden" name="snapshot_id" value="{_esc(child["snapshot_id"])}">
                          <input type="hidden" name="title" value="{_esc(child["title"])}">
                          <input type="hidden" name="version" value="{_esc(child["version"])}">
                          <input type="hidden" name="family" value="{_esc(child["family"])}">
                          <label>Nieuw onderwerp
                            <input name="new_family" required placeholder="onderwerp">
                          </label>
                          <button class="btn-secondary" type="submit">Verplaatsen</button>
                        </form>
                        """
                    )
                if can_promote:
                    actions.append(
                        f"""
                        <form method="post" action="/tree/promote">
                          <input type="hidden" name="snapshot_id" value="{_esc(child["snapshot_id"])}">
                          <input type="hidden" name="title" value="{_esc(child["title"])}">
                          <input type="hidden" name="version" value="{_esc(child["version"])}">
                          <input type="hidden" name="family" value="{_esc(child["family"])}">
                          <label>Nieuwe klasse
                            <select name="new_class">{_class_options(child["class"])}</select>
                          </label>
                          <div class="klasse-wijzigen-consequence">
                            <p>De bron blijft ongewijzigd: freeze-bytes, SHA-256, titel, versie en herkomst wijzigen niet.</p>
                            <p>Same-model (richtlijn-pad onderling): objecten blijven; in deze golf geldt volle herreview.</p>
                            <p>Cross-model (niet-boom ↔ beslisboom): directe wijziging is geblokkeerd; re-extract van dezelfde freeze is vereist; nieuwe objectgrafiek; volle herreview; eerdere objecten blijven als audithistorie.</p>
                          </div>
                          <label class="check">
                            <input type="checkbox" name="confirm" value="1">
                            <span>Ik bevestig de consequentie van Klasse wijzigen</span>
                          </label>
                          <button class="btn-secondary" type="submit">Klasse wijzigen</button>
                        </form>
                        """
                    )
                cards.append(
                    f"""
                    <article class="doc-card">
                      {_document_card_heading(child)}
                      <div class="doc-actions">{"".join(actions)}</div>
                      {_unpublished_delete_control(child, account=account, console=state, next_path="/tree")}
                    </article>
                    """
                )
            blocks.append(
                f'<h2>Onderwerp {_esc(family)}</h2><div class="doc-list">{"".join(cards)}</div>'
            )
        empty = '<p class="muted">Nog geen documenten. Lever eerst een document in.</p>'
        return _page(
            f"""
            {_nav(account, "tree", _counts(account))}
            <section class="room">
              <h1>Documentenhiërarchie</h1>
              <p class="lead">Documenten per onderwerp en klasse. Verplaatsen of klasse wijzigen vanaf het document.</p>
              {"".join(blocks) or empty}
            </section>
            {_help(room="tree")}
            """
        )

    @app.post("/tree/move")
    def tree_move(
        request: Request,
        new_family: str = Form(...),
        snapshot_id: str = Form(""),
        title: str = Form(""),
        version: str = Form(""),
        family: str = Form(""),
    ) -> RedirectResponse:
        account = _require(request)
        if snapshot_id.strip():
            state.move_family(actor_id=account["account_id"], snapshot_id=snapshot_id, new_family=new_family)
        else:
            state.move_family_document(
                actor_id=account["account_id"],
                title=title,
                version=version,
                family=family,
                new_family=new_family,
            )
        return RedirectResponse("/tree", status_code=303)

    @app.post("/tree/promote")
    def tree_promote(
        request: Request,
        new_class: str = Form(...),
        snapshot_id: str = Form(""),
        title: str = Form(""),
        version: str = Form(""),
        family: str = Form(""),
        confirm: str = Form(""),
    ) -> RedirectResponse:
        account = _require(request)
        confirmed = str(confirm or "").strip().lower() in {"1", "on", "true", "yes", "ja"}
        if not confirmed:
            raise ConsoleError("class_change_confirmation_required")
        if snapshot_id.strip():
            current = next(
                (row for row in state.list_envelopes() if row["snapshot_id"] == snapshot_id),
                None,
            )
            if current is None:
                raise ConsoleError("unknown_snapshot")
            state.promote_class(
                actor_id=account["account_id"],
                snapshot_id=snapshot_id,
                new_class=new_class,
                reextract=is_cross_model_class_change(current["class"], new_class),
            )
        else:
            document = state.resolve_document(title=title, version=version, family=family)
            state.promote_class_document(
                actor_id=account["account_id"],
                title=title,
                version=version,
                family=family,
                new_class=new_class,
                reextract=is_cross_model_class_change(document["class"], new_class),
            )
        return RedirectResponse("/tree", status_code=303)

    @app.post("/documents/delete")
    def documents_delete(
        request: Request,
        snapshot_id: str = Form(...),
        confirm: str = Form(""),
        confirm_title: str = Form(""),
        next_path: str = Form("/tree", alias="next"),
        object_ids: list[str] = Form(default=[]),
    ) -> RedirectResponse:
        account = _require(request)
        chosen: list[str] = []
        if isinstance(object_ids, str):
            chosen = [object_ids] if object_ids.strip() else []
        elif object_ids:
            chosen = [str(item).strip() for item in object_ids if str(item).strip()]
        if chosen:
            raise ConsoleError("hide_selected_objects_forbidden")
        confirmed = str(confirm or "").strip().lower() in {"1", "on", "true", "yes", "ja"}
        state.delete_unpublished_snapshot(
            actor_id=account["account_id"],
            snapshot_id=snapshot_id,
            confirmed=confirmed,
            confirm_title=confirm_title,
        )
        target = (next_path or "").strip() or "/tree"
        if target not in ALLOWED_DELETE_NEXT:
            target = "/tree"
        return RedirectResponse(target, status_code=303)

    @app.get("/review", response_class=HTMLResponse)
    def review_get(request: Request, document: str = "", object: str = "") -> str:
        account = _require(request)
        chosen = document.strip()
        envelopes = state.list_envelopes()
        chosen_row = next((row for row in envelopes if row["snapshot_id"] == chosen), None)
        picker = ""
        if chosen_row:
            picker = f"""
              <div class="review-document-context">
                <span>Document</span>
                <b>{_esc(chosen_row["title"])}</b>
                <span>versie {_esc(chosen_row["version"])}</span>
                <span>onderwerp {_esc(chosen_row["family"])}</span>
                <span>klasse {_esc(chosen_row["class"])}</span>
                <a href="/review">Ander document kiezen</a>
              </div>
            """
        cards = []
        if not chosen:
            for row in envelopes:
                cards.append(
                    f"""
                    <article class="doc-card">
                      {_document_card_heading({**row, "status": row["state"]})}
                      <p class="lead">Beoordeel Koppen als structuur en Inhoud als kennisobjecten.</p>
                      <p><a class="btn-primary" href="/review?document={_esc(row["snapshot_id"])}">Beoordeel</a></p>
                    </article>
                    """
                )
        objects_html = ""
        chosen_object_id = object.strip()
        if chosen_row:
            objects_html += (
                f'<div class="doc-card">{_document_card_heading({**chosen_row, "status": chosen_row["state"]})}'
                "</div>"
            )
            snapshot_objects = state.snapshot_objects(chosen)
            review_path = review_path_for_klasse(chosen_row["class"])
            if not chosen_object_id:
                def _index_item(obj: dict[str, Any], *, checkbox: bool = False) -> str:
                    title = review_row_title(obj)
                    status = review_row_status(obj)
                    link = (
                        f'<a class="review-row-title" href="/review?document={_esc(chosen)}&object={_esc(obj["object_id"])}">'
                        f"{_esc(title)}</a>"
                    )
                    status_html = f'<span class="review-row-status">{_esc(status)}</span>'
                    if checkbox:
                        return (
                            '<li class="review-row">'
                            f'<label class="check"><input type="checkbox" name="object_ids" '
                            f'value="{_esc(obj["object_id"])}">{link}{status_html}</label>'
                            "</li>"
                        )
                    return f'<li class="review-row">{link}{status_html}</li>'

                koppen, _old_inhoud = review_stacks(snapshot_objects, review_path=review_path)
                duty = slow_review_duty(snapshot_objects, review_path=review_path)
                leftover = remaining_unclassified(snapshot_objects)
                leftover_ids = {row.get("object_id") for row in leftover}
                leftover_other = [
                    obj
                    for obj in remaining_not_duty(snapshot_objects)
                    if obj.get("object_id") not in leftover_ids
                ]
                blocked = blocked_audit_lane(snapshot_objects) if review_path != "boom" else []
                fast_items = "".join(_index_item(obj, checkbox=True) for obj in koppen)
                slow_items = "".join(_index_item(obj) for obj in duty)
                leftover_html = ""
                if leftover:
                    leftover_html = f"""
                      <aside class="review-leftover-unclassified">
                        <p>Resterend unclassified: {len(leftover)}. Niet als één-voor-één plicht. Unclassified wordt niet geserveerd.</p>
                      </aside>
                    """
                other_html = ""
                if leftover_other:
                    other_html = (
                        f'<p class="review-leftover-other">Overige objecten in de store: '
                        f"{len(leftover_other)}. Niet de onderzoekerplicht voor handelingsadvies.</p>"
                    )
                blocked_html = ""
                if blocked:
                    typed = frozenset(
                        {
                            "recommendation",
                            "condition",
                            "exception",
                            "definition",
                            "explanation",
                        }
                    )
                    shown = [
                        obj
                        for obj in blocked
                        if (obj.get("proposed_object_type") or obj.get("confirmed_object_type") or obj.get("object_type"))
                        in typed
                    ]
                    hidden = [obj for obj in blocked if obj not in shown]
                    blocked_items = "".join(_index_item(obj) for obj in shown)
                    hidden_ids = " ".join(_esc(obj["object_id"]) for obj in hidden)
                    hidden_html = (
                        f'<p class="review-blocked-store-ids">{hidden_ids}</p>' if hidden_ids else ""
                    )
                    shown_list = (
                        f'<ol class="object-index">{blocked_items}</ol>' if blocked_items else ""
                    )
                    blocked_html = f"""
                      <aside class="review-blocked-audit" aria-label="Geblokkeerde kandidaten">
                        <p>Geblokkeerde kandidaten (poort): {len(blocked)}. Niet de gewone beoordelingsplicht.</p>
                        {shown_list}
                        {hidden_html}
                      </aside>
                    """
                if review_path == "boom":
                    fast_title = f"Paden ({len(koppen)})"
                    fast_lead = "Bevestig paden als structuur, nooit als advies."
                    fast_button = "Bevestig geselecteerde paden als structuur"
                    slow_title = f"Knopen en uitkomsten ({len(duty)})"
                    slow_lead = "Beoordeel knopen die advies poorten en uitkomsten. Dat is de handplicht."
                else:
                    fast_title = f"Koppen ({len(koppen)})"
                    fast_lead = "Bevestig koppen als structuur, nooit als advies."
                    fast_button = "Bevestig geselecteerde koppen als structuur"
                    slow_title = f"Inhoud ({len(duty)})"
                    slow_lead = "Beoordeel voorgestelde aanbevelingen plus voorwaarden, uitzonderingen en ieder high-risk object. Dat is de handplicht."
                objects_html += f"""
                    <section class="review-lane-fast">
                      <h2>{fast_title}</h2>
                      <p class="lead">{fast_lead}</p>
                      <form method="post" action="/review/headings/batch-confirm">
                        <input type="hidden" name="snapshot_id" value="{_esc(chosen)}">
                        <ol class="object-index">{fast_items}</ol>
                        <button class="btn-primary" type="submit">{fast_button}</button>
                      </form>
                    </section>
                    <section class="review-lane-slow">
                      <h2>{slow_title}</h2>
                      <p class="lead">{slow_lead}</p>
                      <ol class="object-index">{slow_items}</ol>
                      {leftover_html}
                      {other_html}
                    </section>
                    {blocked_html}
                """
            else:
                obj = next((row for row in snapshot_objects if row["object_id"] == chosen_object_id), None)
                if obj is None:
                    raise ConsoleError("unknown_object")
                heading = review_card_sentence(obj)
                obj_text = (obj.get("content") or {}).get("clean_text") or ""
                heading_norm = " ".join(heading.split())
                body_norm = " ".join(str(obj_text).split())
                object_text_html = ""
                if body_norm and body_norm != heading_norm and not body_norm.startswith(
                    heading_norm.rstrip("…")
                ):
                    object_text_html = f'<div class="object-text"><p>{_esc(obj_text)}</p></div>'
                proposed = obj.get("proposed_object_type") or ""
                confirmed = obj.get("confirmed_object_type") or ""
                type_options = _type_options(confirmed, review_path=review_path)
                passage_ok = False
                passage_html = ""
                try:
                    opened = state.open_source_passage(snapshot_id=chosen, object_id=obj["object_id"])
                    passage_ok = True
                    passage_html = f"""
                  <aside class="review-card-bronpassage" aria-label="Exacte bronpassage">
                    <h4>Onderbouwing uit het brondocument</h4>
                    <p class="bronpassage-prose">{_esc(researcher_visible_prose(opened.get("passage") or ""))}</p>
                    <p><a class="btn-secondary" href="/review/bronpassage?document={_esc(chosen)}&object={_esc(obj["object_id"])}">Bekijk in brondocument</a></p>
                  </aside>
                    """
                except ConsoleError:
                    passage_html = (
                        '<aside class="review-card-bronpassage" aria-label="Exacte bronpassage ontbreekt">'
                        "<h4>Onderbouwing uit het brondocument</h4>"
                        '<p class="muted">Bronpassage ontbreekt; type bevestigen en goedkeuren zijn '
                        "uitgeschakeld tot het origineel open kan.</p></aside>"
                    )
                type_disabled = "" if passage_ok else " disabled"
                approve_disabled = "" if passage_ok else " disabled"
                four_eyes_html = ""
                if requires_four_eyes(obj, confirmed_type=confirmed or None):
                    four_eyes_html = (
                        '<div class="banner warn">Dit object vereist four-eyes: '
                        "<b>tweede reviewer nodig</b>.</div>"
                    )
                relation_form = ""
                relation_boxes = _relation_checkboxes(obj, snapshot_objects)
                parent_choice_html = _parent_choice_block(
                    obj,
                    snapshot_objects,
                    chosen,
                    include_submit=not bool(relation_boxes),
                )
                if parent_choice_html or relation_boxes:
                    relation_form = f"""
                  <form id="relations-{_esc(obj["object_id"])}" method="post" action="/review/relations">
                    <input type="hidden" name="snapshot_id" value="{_esc(chosen)}">
                    <input type="hidden" name="object_id" value="{_esc(obj["object_id"])}">
                    {relation_boxes}
                  </form>
                    """
                stamp_html = _stamp_block(
                    obj, hidden=not recommendation_strength_ui_applies(obj)
                )
                objects_html += f"""
                <p><a class="btn-secondary" href="/review?document={_esc(chosen)}">Terug naar Inhoud</a></p>
                <article class="object review-card-two-column">
                  <section class="review-card-object" aria-label="Kennisobject en reviewbesluit">
                  <header class="review-object-heading">
                    <p class="eyebrow">Te beoordelen kennisobject</p>
                    <h3>{_esc(heading)}</h3>
                    <p class="meta"><span>status <b>{_esc(review_row_status(obj))}</b></span><span>huidig type <b>{_esc(_object_type_label(obj.get("object_type")))}</b></span>{"<span>typevoorstel <b>" + _esc(_object_type_label(proposed)) + "</b></span>" if proposed else ""}</p>
                  </header>
                  {four_eyes_html}
                  {object_text_html}
                  {parent_choice_html}
                  {relation_form}
                  <form class="review-decision-form" method="post" action="/review" data-review-form>
                    <input type="hidden" name="snapshot_id" value="{_esc(chosen)}">
                    <input type="hidden" name="object_id" value="{_esc(obj["object_id"])}">
                    <section class="review-step">
                      <h4>1. Classificatie</h4>
                      <label for="type-{_esc(obj["object_id"])}">Welk type kennisobject is dit?</label>
                      <select id="type-{_esc(obj["object_id"])}" name="confirmed_object_type"{type_disabled}>{type_options}</select>
                    </section>
                    {stamp_html}
                    <section class="review-step">
                      <h4>2. Besluit</h4>
                      <label for="decision-{_esc(obj["object_id"])}">Wat is je besluit over dit kennisobject?</label>
                      <select id="decision-{_esc(obj["object_id"])}" name="decision" required>
                      <option value="" selected disabled>Kies een besluit</option>
                      <option value="approve"{approve_disabled}>Goedkeuren</option>
                      <option value="revise">Revisie vragen</option>
                      <option value="reject">Afwijzen</option>
                      </select>
                      <p class="field-help" data-decision-hint>Kies eerst een besluit.</p>
                      <div class="decision-comment" data-comment-field hidden>
                        <label for="comment-{_esc(obj["object_id"])}">Toelichting</label>
                        <textarea id="comment-{_esc(obj["object_id"])}" name="comment"></textarea>
                      </div>
                      <div class="decision-correction" data-correction-field hidden>
                        <label for="correction-{_esc(obj["object_id"])}">Voorgestelde correctie</label>
                        <textarea id="correction-{_esc(obj["object_id"])}" name="proposed_correction"></textarea>
                      </div>
                      <button class="btn-primary" type="submit" disabled data-submit-review>Review vastleggen</button>
                    </section>
                  </form>
                  </section>
                  {passage_html}
                </article>
                """
        empty = '<p class="muted">Nog geen documenten om te reviewen.</p>' if not envelopes else ""
        lead = "Beoordeel Koppen als structuur en Inhoud als kennisobjecten."
        return _page(
            f"""
            {_nav(account, "review", _counts(account))}
            <section class="room">
              <h1>Review</h1>
              <p class="lead">{lead}</p>
              {picker}
              {"".join(cards) if not chosen else ""}
              {objects_html or empty}
            </section>
            {_help(room="review")}
            """
        )

    @app.get("/review/bronpassage", response_class=HTMLResponse)
    def review_bronpassage(request: Request, document: str = "", object: str = "") -> str:
        account = _require(request)
        chosen = document.strip()
        object_id = object.strip()
        if not chosen or not object_id:
            raise ConsoleError("unknown_object")
        opened = state.open_source_passage(snapshot_id=chosen, object_id=object_id)
        return _page(
            f"""
            {_nav(account, "review", _counts(account))}
            <section class="room">
              <h1>Bronpassage</h1>
              <p class="lead">Exacte plaats in het geüploade origineel.</p>
              <article class="object">
                <p class="bronpassage-prose">{_esc(researcher_visible_prose(opened.get("passage") or ""))}</p>
              </article>
              <p><a class="btn-secondary" href="/review?document={_esc(chosen)}&object={_esc(object_id)}">Terug naar review</a></p>
            </section>
            {_help(room="review")}
            """
        )

    @app.post("/review")
    def review_post(
        request: Request,
        snapshot_id: str = Form(...),
        object_id: str = Form(...),
        decision: str = Form(""),
        comment: str = Form(""),
        proposed_correction: str = Form(""),
        confirmed_object_type: str = Form(""),
        recommendation_strength: str = Form(""),
    ) -> RedirectResponse:
        account = _require(request)
        if decision not in {"approve", "revise", "reject"}:
            raise ConsoleError("invalid_review_decision")
        if decision in {"revise", "reject"} and not comment.strip():
            raise ConsoleError("review_comment_required")
        state.review_object(
            actor_id=account["account_id"],
            snapshot_id=snapshot_id,
            object_id=object_id,
            decision=decision,
            comment=comment,
            proposed_correction=proposed_correction,
            confirmed_object_type=confirmed_object_type.strip() or None,
            recommendation_strength=recommendation_strength.strip() or None,
        )
        if decision == "revise" and proposed_correction.strip():
            state.correct_object(
                actor_id=account["account_id"],
                snapshot_id=snapshot_id,
                object_id=object_id,
                patch={
                    "reason": comment or "reviewer correction",
                    "operations": [{"op": "set", "path": "content.clean_text", "value": proposed_correction.strip()}],
                },
            )
        return RedirectResponse(_review_location(state, snapshot_id, object_id), status_code=303)

    @app.post("/review/headings/batch-confirm")
    def review_headings_batch_confirm(
        request: Request,
        snapshot_id: str = Form(...),
        object_ids: list[str] = Form(default=[]),
    ) -> RedirectResponse:
        account = _require(request)
        raw = [object_ids] if isinstance(object_ids, str) else list(object_ids or [])
        state.batch_confirm_headings(
            actor_id=account["account_id"],
            snapshot_id=snapshot_id,
            object_ids=raw,
        )
        return RedirectResponse(_review_location(state, snapshot_id), status_code=303)

    @app.post("/review/relations")
    def review_relations_post(
        request: Request,
        snapshot_id: str = Form(...),
        object_id: str = Form(...),
        relation: list[str] = Form(default=[]),
        parent_choice: str = Form(default=""),
    ) -> RedirectResponse:
        account = _require(request)
        raw = [relation] if isinstance(relation, str) else list(relation or [])
        rows = []
        for item in raw:
            rel_type, sep, target = item.partition(":")
            if not sep or not rel_type or not target:
                raise ConsoleError("unknown_relation_type")
            rows.append({"relation_type": rel_type, "target_object_id": target})
        chosen_parent = (parent_choice or "").strip()
        if chosen_parent and chosen_parent != object_id:
            already = any(
                row.get("relation_type") == "child" and row.get("target_object_id") == chosen_parent
                for row in rows
            )
            if not already:
                rows.append({"relation_type": "child", "target_object_id": chosen_parent})
        state.confirm_relations(
            actor_id=account["account_id"],
            snapshot_id=snapshot_id,
            object_id=object_id,
            relations=rows,
        )
        return RedirectResponse(_review_location(state, snapshot_id, object_id), status_code=303)

    @app.get("/publish", response_class=HTMLResponse)
    def publish_get(request: Request) -> str:
        account = _require(request)
        rows = []
        for envelope in state.list_envelopes():
            considered = None
            try:
                considered = state.consider_publish(actor_id=account["account_id"], snapshot_id=envelope["snapshot_id"])
            except ConsoleError as exc:
                considered = {"blockers": [exc.code], "publish_allowed": False}
            blockers = considered.get("blockers") or []
            blocker_text = " ".join(BLOCKER_LABELS.get(code, code) for code in blockers) or "Geen extra blockers in deze kamer."
            rows.append(
                f"""
                <article class="doc-card">
                  {_document_card_heading({**envelope, "status": envelope["state"]})}
                  <div class="banner warn">{_esc(blocker_text)}</div>
                </article>
                """
            )
        return _page(
            f"""
            {_nav(account, "publish", _counts(account))}
            <section class="room">
              <h1>Publiceren</h1>
              <p class="lead">Apart besluit over een gereviewd document. Zonder duurzame opslag blijft publicatie geblokkeerd.</p>
              <div class="doc-list">{"".join(rows) or '<p class="muted">Nog geen documenten.</p>'}</div>
            </section>
            {_help()}
            """
        )

    @app.get("/accounts", response_class=HTMLResponse)
    def accounts_get(request: Request) -> str:
        account = _require(request)
        rows = []
        for row in sorted(state._accounts.values(), key=lambda item: item["username"]):
            public = state._public_account(row)
            role_boxes = "".join(
                f'<label class="check"><input type="checkbox" name="roles" value="{name}"'
                f'{" checked" if name in public["roles"] else ""}>{name}</label>'
                for name in ("researcher", "reviewer", "publisher")
            )
            role_form = ""
            if "publisher" in account["roles"]:
                role_form = f"""
                  <form method="post" action="/accounts/roles">
                    <input type="hidden" name="account_id" value="{_esc(public["account_id"])}">
                    <p>Rollen wijzigen</p>
                    {role_boxes}
                    <button class="btn-secondary" type="submit">Rollen wijzigen</button>
                  </form>
                """
            rows.append(
                f"""
                <article class="doc-card">
                  <p class="doc-title">{_esc(public["display_name"])}</p>
                  <p class="meta">
                    <span>gebruikersnaam <b>{_esc(public["username"])}</b></span>
                    <span>rollen <b>{", ".join(_esc(r) for r in public["roles"])}</b></span>
                  </p>
                  {role_form}
                </article>
                """
            )
        form = ""
        if "publisher" in account["roles"]:
            form = """
              <form method="post" action="/accounts">
                <div class="sections">
                  <div class="section">
                    <h3>Nieuwe gebruiker</h3>
                    <label for="username">Gebruikersnaam</label>
                    <input id="username" name="username" required>
                    <label for="display_name">Weergavenaam</label>
                    <input id="display_name" name="display_name" required>
                    <label for="password">Wachtwoord</label>
                    <input id="password" type="password" name="password" required>
                    <label for="roles">Rol</label>
                    <select id="roles" name="roles">
                      <option value="researcher">researcher</option>
                      <option value="reviewer">reviewer</option>
                      <option value="publisher">publisher</option>
                    </select>
                    <button class="btn-primary" type="submit">Gebruiker aanmaken</button>
                  </div>
                </div>
              </form>
            """
        return _page(
            f"""
            {_nav(account, "accounts", _counts(account))}
            <section class="room">
              <h1>Accounts</h1>
              <p class="lead">Interne gebruikers. Alleen een publisher maakt accounts en wijzigt rollen.</p>
              {form}
              <div class="doc-list">{"".join(rows) or '<p class="muted">Nog geen accounts.</p>'}</div>
            </section>
            {_help()}
            """
        )

    @app.post("/accounts", response_class=HTMLResponse)
    def accounts_post(
        request: Request,
        username: str = Form(...),
        display_name: str = Form(...),
        password: str = Form(...),
        roles: str = Form(...),
    ) -> HTMLResponse:
        account = _require(request)
        state.create_managed_account(
            actor_id=account["account_id"],
            username=username,
            display_name=display_name,
            password=password,
            roles=[item.strip() for item in roles.split(",") if item.strip()],
        )
        return RedirectResponse("/accounts", status_code=303)

    @app.post("/accounts/roles")
    def accounts_roles_post(
        request: Request,
        account_id: str = Form(...),
        roles: list[str] = Form(default=[]),
    ) -> RedirectResponse:
        account = _require(request)
        chosen: list[str] = []
        if isinstance(roles, str):
            chosen = [roles]
        elif roles:
            chosen = list(roles)
        if not chosen:
            raise ConsoleError("unknown_role")
        state.assign_roles(
            actor_id=account["account_id"],
            account_id=account_id,
            roles=chosen,
        )
        return RedirectResponse("/accounts", status_code=303)

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "service": "operations-console",
            "version": SERVICE_VERSION,
            "product_api": False,
            "chat_room": False,
            "nurse_frontend": False,
        }

    return app


def create_app() -> FastAPI:
    return create_console_app()
