"""Internal operations console MVP — knowledge-kernel surface for researchers.

Protocol v2.6/v2.8: ingest mailbox, family × class tree, named reviewers,
mandatory review return-loop, local G0 identity. Capture is not publication.
Azure deployments can bind exact source bytes to the G2 canonical Blob store;
local ``sources/private/`` remains the G0 stand-in for local development.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from copy import deepcopy
from datetime import date, datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Callable, Iterable

from src.admission_gate_v1 import (
    GATE_BLOCKED,
    admission_of,
    apply_admission_gate,
    is_admission_blocked,
)
from src.atomic_split_v1 import proposed_relations_for_units
from src.beslisboom_path_v1 import (
    CLOSED_BOOM_TYPES,
    CLOSED_KLASSEN,
    boom_freeze_errors,
    boom_spec_from_fragments,
    extract_boom_fragments,
    is_confirmable_type_for_path,
    is_geen_actie_outcome,
    is_live_rest_sole_source,
    is_live_rest_url,
    map_geen_actie,
    outcome_review_errors,
    review_path_for_klasse,
    stamp_boom_flags,
)
from src.context_aware_split_v1 import split_context_aware_units
from src.extract_html_v1 import extract as extract_html
from src.extract_pdf_v2 import extract as extract_pdf
from src.four_eyes_v1 import (
    mark_four_eyes_on_object,
    publish_authorization_contract,
    requires_four_eyes,
)
from src.g2_source_store import G2SourceStoreError, ImmutableSourceStore, is_g2_locator
from src.integrity_kernel import compute_canonical_object_hash, sha256_bytes, stamp_canonical_hashes
from src.klasse_wijzigen_v1 import (
    DOCUMENT_CLASS_CHANGED_EVENT,
    is_cross_model_class_change,
    source_identity_fields,
)
from src.object_taxonomy_v1 import (
    is_closed_recommendation_strength,
)
from src.open_original_v1 import OpenOriginalError, open_source_passage
from src.publish_authorization_v1 import invalidate_for_object, still_matches, tuple_record
from src.review_ledger import append_event
from src.review_workflow_v3 import apply_reviews
from src.revision_workflow import bump_patch, create_revision
from src.semantic_transform_generic_v1 import transform as transform_generic
from src.serving_relations_v1 import (
    binding_relations,
    confirm_relation_set,
    is_closed_relation_type,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_V12 = REPO_ROOT / "schemas" / "knowledge_object.schema.v1.2.json"
CONSOLE_VERSION = "operations-console-v1.0.0"
CAPTURED = "captured_not_published"
PUBLISHED_ENVELOPE_STATES = frozenset({"published", "superseded", "withdrawn"})
UNPUBLISHED_DELETE_EVENT = "unpublished_snapshot_deleted"
CLASS_CHANGE_HISTORY_DIRNAME = "class_change_history"
PUBLISHED_PROJECTION_FILENAME = "published_projection.jsonl"
ALLOWED_DELETE_NEXT = frozenset({"/ingest", "/review", "/tree"})
ALLOWED_ROLES = frozenset({"researcher", "reviewer", "publisher"})
ALLOWED_CLASSES = CLOSED_KLASSEN
SOURCE_VERSION_RE = re.compile(r"^[0-9]+(\.[0-9]+)*$")
YEAR_AS_VERSION_RE = re.compile(r"^(19|20)\d{2}$")
ISO_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
NL_DATE_RE = re.compile(r"^(\d{2})-(\d{2})-(\d{4})$")
SAFE_PATH_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")
SNAPSHOT_ID_RE = re.compile(r"^snap-[0-9a-f]{16}-[0-9a-f]{8}$")
STORE_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
REVIEW_TYPE_NAMES = frozenset(
    {
        "unclassified",
        "heading",
        "definition",
        "explanation",
        "condition",
        "exception",
        "recommendation",
        "document",
        "path",
        "node",
        "outcome",
    }
)
CLASS_ORDER = {
    "richtlijn": 4,
    "handreiking": 3,
    "artikel": 2,
    "transcript": 1,
    "podcast": 1,
    "beslisboom": 0,
}
FORBIDDEN_REVIEWER_IDENTITIES = frozenset(
    {
        "ai",
        "grok bot",
        "grok",
        "metis",
        "implementation engineer",
        "auditor",
    }
)
WORD_TYPES = {
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
BOOM_MARKERS = (
    'data-kennisplatform-player="boom"',
    "kennisplatform-boom-player",
    'class="boom-player"',
    "articulate-rise",
    "storyline-player",
    "window.playerconfig",
)
PBKDF2_ROUNDS = 80_000


class ConsoleError(ValueError):
    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        super().__init__(message or code)


UrlFetcher = Callable[[str], tuple[bytes, str, str]]


def _has_path_escape(value: str) -> bool:
    return (not value) or value in {".", ".."} or "/" in value or "\\" in value or ".." in value


def safe_path_token(value: str, *, pattern: re.Pattern[str] | None = None, code: str = "invalid_store_path") -> str:
    """Allowlist a single path component. Reject separators and ``..`` before any join."""
    raw = "" if value is None else str(value)
    if _has_path_escape(raw):
        raise ConsoleError(code)
    matched = (pattern or SAFE_PATH_TOKEN_RE).fullmatch(raw)
    if matched is None:
        raise ConsoleError(code)
    return matched.group(0)


def safe_store_filename(value: str) -> str:
    """Freeze upload name must be a single basename. ``Path.name`` is not enough (``..``)."""
    return safe_path_token(value, pattern=SAFE_PATH_TOKEN_RE, code="invalid_store_path")


def safe_snapshot_id(snapshot_id: str) -> str:
    return safe_path_token(snapshot_id, pattern=SNAPSHOT_ID_RE, code="unknown_snapshot")


def safe_path_under(root: Path, *parts: str) -> Path:
    """Resolve ``root/parts`` and require the result to stay under ``root``."""
    if not parts:
        raise ConsoleError("invalid_store_path")
    resolved_root = Path(os.path.realpath(os.fspath(root)))
    tokens = [safe_path_token(part) for part in parts]
    joined = os.path.join(os.fspath(resolved_root), *tokens)
    resolved = Path(os.path.realpath(joined))
    root_s = os.fspath(resolved_root)
    resolved_s = os.fspath(resolved)
    if os.path.commonpath([root_s, resolved_s]) != root_s:
        raise ConsoleError("invalid_store_path")
    return resolved


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def normalize_ingest_source_date(value: str | None) -> str:
    """Persist freeze colofon/publicatiedatum as ISO YYYY-MM-DD.

    Screen locale may be DD-MM-YYYY. Stored bytes MUST be ISO, no time/tz.
    Empty is rejected. Today and ingest-click MUST NOT be substituted.
    """
    raw = "" if value is None else str(value)
    if not raw.strip():
        raise ConsoleError("source_date_required")
    if re.search(r"\s", raw):
        raw = raw.strip()
        if not raw:
            raise ConsoleError("source_date_required")
    iso = None
    if ISO_DATE_RE.fullmatch(raw):
        iso = raw
    else:
        matched = NL_DATE_RE.fullmatch(raw)
        if matched:
            day, month, year = matched.groups()
            iso = f"{year}-{month}-{day}"
    if iso is None:
        raise ConsoleError("invalid_source_date")
    try:
        parsed = date.fromisoformat(iso)
    except ValueError as exc:
        raise ConsoleError("invalid_source_date") from exc
    if parsed.isoformat() != iso:
        raise ConsoleError("invalid_source_date")
    return iso


def validate_ingest_source_version(value: str | None) -> str:
    """Require dotted non-negative integers. Reject year-as-version and spaces."""
    raw = "" if value is None else str(value)
    if not raw:
        raise ConsoleError("source_version_required")
    if re.search(r"\s", raw) or raw != raw.strip():
        raise ConsoleError("invalid_source_version")
    if YEAR_AS_VERSION_RE.fullmatch(raw) or not SOURCE_VERSION_RE.fullmatch(raw):
        raise ConsoleError("invalid_source_version")
    return raw


def _authoritative_review_type(obj: dict[str, Any]) -> str | None:
    confirmed = obj.get("confirmed_object_type")
    stored = obj.get("object_type")
    proposed = obj.get("proposed_object_type")
    if confirmed:
        return confirmed
    if stored and stored != "unclassified":
        return stored
    return proposed


def inferred_review_path(obj: dict[str, Any]) -> str:
    authoritative = _authoritative_review_type(obj)
    if authoritative in CLOSED_BOOM_TYPES or obj.get("proposed_object_type") in CLOSED_BOOM_TYPES:
        return "boom"
    return "richtlijn"


def review_lane(obj: dict[str, Any], review_path: str | None = None) -> str:
    """Queue routing from the first authoritative type. Not a speed switch.

    Confirmed type wins. Stored type wins over a stale proposal. Proposed
    type is used only when the object is still unclassified, so a human
    reclassification to recommendation cannot be batch-overwritten as heading.
    On the boom path, ``path`` is structure (fast) and never advice.
    """
    if obj.get("object_type") == "document":
        return "document"
    path = review_path or inferred_review_path(obj)
    authoritative = _authoritative_review_type(obj)
    if path == "boom":
        if authoritative == "path":
            return "fast"
        return "slow"
    if authoritative == "heading":
        return "fast"
    return "slow"


def review_row_title(obj: dict[str, Any], *, max_len: int = 160) -> str:
    """Freeze source sentence or real heading. MUST NOT use type name or kernel id."""
    content = obj.get("content") or {}
    text = re.sub(
        r"\s+",
        " ",
        str(content.get("clean_text") or content.get("raw_text") or ""),
    ).strip()
    if text and text.casefold() not in REVIEW_TYPE_NAMES and not text.startswith(("console-", "snap-")):
        snippet = text
    else:
        snippet = "Kennisobject"
    if len(snippet) > max_len:
        return snippet[: max_len - 1] + "…"
    return snippet


def review_card_sentence(obj: dict[str, Any]) -> str:
    """Open-card freeze sentence once. Full text; not truncated; not a type name."""
    return review_row_title(obj, max_len=10_000)


def review_row_status(obj: dict[str, Any]) -> str:
    """Short same-line status. waiting / classified / confirmed in onderzoekerstaal."""
    confirmed = obj.get("confirmed_object_type")
    status = (obj.get("governance") or {}).get("validation_status") or ""
    if status == "approved" or confirmed:
        if status == "approved":
            return "bevestigd"
        return "geclassificeerd"
    return "wacht"


def review_stacks(
    objects: Iterable[dict[str, Any]],
    review_path: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Koppen (fast heading) vs all slow content. Document rows are not stacks.

    The old Inhoud enumeration is every slow object, including leftover
    unclassified. Protocol v2.19 presents slow duty separately: use
    ``slow_review_duty`` for the researcher-required cards. On the boom
    path the fast stack is ``path`` structure.
    """
    rows = [obj for obj in objects if obj.get("object_type") != "document"]
    koppen = [obj for obj in rows if review_lane(obj, review_path=review_path) == "fast"]
    inhoud = [obj for obj in rows if review_lane(obj, review_path=review_path) != "fast"]
    return koppen, inhoud


SLOW_REVIEW_DUTY_TYPES = frozenset({"recommendation", "condition", "exception"})
SLOW_BOOM_DUTY_TYPES = frozenset({"node", "outcome"})


def is_slow_review_duty(obj: dict[str, Any], review_path: str | None = None) -> bool:
    """True for the researcher-required slow hand work (Protocol v2.19).

    Proposed or stored ``recommendation``, ``condition``, ``exception``, or
    any high-risk object. Headings stay in Koppen. Leftover unclassified is
    not this duty. MUST NOT auto-confirm types. MUST NOT treat leftover as
    light enough to skip four-eyes or to serve. On the boom path, ``node``
    and ``outcome`` are the duty types.
    """
    if obj.get("object_type") == "document":
        return False
    path = review_path or inferred_review_path(obj)
    if path != "boom" and admission_of(obj).get("gate_result") == GATE_BLOCKED:
        return False
    if review_lane(obj, review_path=path) == "fast":
        return False
    if requires_four_eyes(obj, confirmed_type=obj.get("confirmed_object_type") or None):
        return True
    duty_types = SLOW_BOOM_DUTY_TYPES if path == "boom" else SLOW_REVIEW_DUTY_TYPES
    confirmed = obj.get("confirmed_object_type")
    stored = obj.get("object_type")
    proposed = obj.get("proposed_object_type")
    if confirmed in duty_types:
        return True
    if stored in duty_types:
        return True
    if proposed in duty_types:
        return True
    return False


def slow_review_duty(
    objects: Iterable[dict[str, Any]],
    review_path: str | None = None,
) -> list[dict[str, Any]]:
    """Presented Inhoud cards: recommendation + condition/exception/high-risk."""
    return [obj for obj in objects if is_slow_review_duty(obj, review_path=review_path)]


def remaining_unclassified(objects: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Leftover unclassified that MUST NOT be equal one-by-one duty cards.

    Stored objects remain. Presentation of duty is not deletion. Hiding
    stored fragments without a new extract remains forbidden.
    """
    rows = [obj for obj in objects if obj.get("object_type") != "document"]
    leftover: list[dict[str, Any]] = []
    for obj in rows:
        if is_slow_review_duty(obj) or review_lane(obj) == "fast":
            continue
        if obj.get("confirmed_object_type"):
            continue
        stored = obj.get("object_type")
        if stored and stored != "unclassified":
            continue
        leftover.append(obj)
    return leftover


def remaining_not_duty(objects: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Slow objects that are not the presented researcher duty."""
    rows = [obj for obj in objects if obj.get("object_type") != "document"]
    return [
        obj
        for obj in rows
        if review_lane(obj) != "fast" and not is_slow_review_duty(obj)
    ]


def _slug(value: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return text or "document"


def _normalize_identity(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().lower()


def _is_forbidden_identity(value: str) -> bool:
    return _normalize_identity(value) in FORBIDDEN_REVIEWER_IDENTITIES


def _hash_password(password: str, salt_hex: str | None = None) -> tuple[str, str]:
    salt = bytes.fromhex(salt_hex) if salt_hex else os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ROUNDS)
    return salt.hex(), digest.hex()


def _atomic_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(payload)
    tmp.replace(path)


def default_url_fetcher(url: str) -> tuple[bytes, str, str]:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ConsoleError("url_scheme_not_allowed")
    request = urllib.request.Request(url, method="GET", headers={"User-Agent": "vvn-operations-console/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = response.read()
            content_type = str(response.headers.get("Content-Type") or "")
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise ConsoleError("url_snapshot_failed") from exc
    filename = Path(parsed.path).name or "snapshot.bin"
    return data, content_type, filename


def _is_word_bytes(data: bytes, filename: str, content_type: str | None) -> bool:
    name = Path(filename).name.lower()
    ctype = (content_type or "").split(";")[0].strip().lower()
    if name.endswith(".docx") or name.endswith(".doc") or ctype in WORD_TYPES:
        return True
    if data.startswith(b"\xd0\xcf\x11\xe0"):
        return True
    if data.startswith(b"PK"):
        try:
            with zipfile.ZipFile(BytesIO(data)) as archive:
                return any(item.startswith("word/") for item in archive.namelist())
        except zipfile.BadZipFile:
            return False
    return False


def _is_boom_player(data: bytes, filename: str) -> bool:
    name = Path(filename).name.lower()
    if name == "story.html":
        return True
    text = data.decode("utf-8", errors="replace").lower()
    return any(marker in text for marker in BOOM_MARKERS)


def classify_official_file(data: bytes, filename: str, content_type: str | None) -> str:
    if _is_word_bytes(data, filename, content_type):
        raise ConsoleError("word_not_first_wave")
    if _is_boom_player(data, filename):
        raise ConsoleError("story_html_boom_player_out_of_first_wave")
    name = Path(filename).name.lower()
    ctype = (content_type or "").split(";")[0].strip().lower()
    if name.endswith(".pdf") or data.startswith(b"%PDF") or ctype == "application/pdf":
        return "pdf"
    if (
        name.endswith(".html")
        or name.endswith(".htm")
        or ctype in {"text/html", "application/xhtml+xml"}
        or data.lstrip().lower().startswith(b"<!doctype html")
        or data.lstrip().lower().startswith(b"<html")
    ):
        return "html"
    raise ConsoleError("unsupported_official_file")


def _spec_from_fragments(
    *,
    document_id: str,
    title: str,
    family: str,
    class_: str,
    fragments: list[dict[str, Any]],
    content_kind: str,
) -> dict[str, Any]:
    objects: list[dict[str, Any]] = [
        {
            "object_id": f"{document_id}-document",
            "object_type": "document",
            "text": title,
            "review_track": "technical",
        }
    ]
    meaning_units = split_context_aware_units(fragments, document_id=document_id)
    proposed_relations_for_units(meaning_units)
    objects.extend(meaning_units)
    return {
        "spec_version": "console-ingest-1.0",
        "document_id": document_id,
        "object_version": "1.0",
        "target_group": [],
        "care_setting": [],
        "topic": [family, f"class:{class_}", f"source-kind:{content_kind}"],
        "objects": objects,
    }


class OperationsConsole:
    def __init__(
        self,
        *,
        root: Path,
        source_store: Path | None = None,
        runtime: Path | None = None,
        immutable_source_store: ImmutableSourceStore | None = None,
        url_fetcher: UrlFetcher | None = None,
        schema_path: Path | None = None,
    ) -> None:
        self.root = Path(root)
        self.source_store = Path(source_store or self.root / "sources" / "private")
        self.runtime = Path(runtime or self.root / "output" / "runtime" / "operations-console")
        self.immutable_source_store = immutable_source_store
        self.url_fetcher = url_fetcher or default_url_fetcher
        self.schema_path = Path(schema_path or SCHEMA_V12)
        self.source_store.mkdir(parents=True, exist_ok=True)
        self.runtime.mkdir(parents=True, exist_ok=True)
        self._accounts_path = self.runtime / "accounts.json"
        self._sessions_path = self.runtime / "sessions.json"
        self._envelopes_path = self.runtime / "envelopes.json"
        self._objects_dir = self.runtime / "objects"
        self._objects_dir.mkdir(parents=True, exist_ok=True)
        self._ledger_path = self.runtime / "review_ledger.jsonl"
        self._bindings_path = self.runtime / "publish_authorizations.json"
        self._accounts: dict[str, dict[str, Any]] = self._load_map(self._accounts_path)
        self._sessions: dict[str, dict[str, Any]] = self._load_map(self._sessions_path)
        self._envelopes: dict[str, dict[str, Any]] = self._load_map(self._envelopes_path)
        self._bindings: dict[str, list[dict[str, Any]]] = self._load_map(self._bindings_path)  # type: ignore[assignment]
        if self._bindings_path.exists():
            loaded = json.loads(self._bindings_path.read_text(encoding="utf-8"))
            self._bindings = {key: list(value) for key, value in loaded.items()}
        else:
            self._bindings = {}

    def _load_map(self, path: Path) -> dict[str, dict[str, Any]]:
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    def _save_accounts(self) -> None:
        _atomic_write(self._accounts_path, self._accounts)

    def _save_sessions(self) -> None:
        _atomic_write(self._sessions_path, self._sessions)

    def _save_envelopes(self) -> None:
        _atomic_write(self._envelopes_path, self._envelopes)

    def _save_bindings(self) -> None:
        _atomic_write(self._bindings_path, self._bindings)

    def _objects_path(self, snapshot_id: str) -> Path:
        if ".." in snapshot_id or "/" in snapshot_id or "\\" in snapshot_id:
            raise ConsoleError("unknown_snapshot")
        token = safe_snapshot_id(snapshot_id)
        return safe_path_under(self._objects_dir, f"{token}.jsonl")

    def _load_objects(self, snapshot_id: str) -> list[dict[str, Any]]:
        if ".." in snapshot_id or "/" in snapshot_id or "\\" in snapshot_id:
            raise ConsoleError("unknown_snapshot")
        path = self._objects_path(snapshot_id)
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def _save_objects(self, snapshot_id: str, rows: list[dict[str, Any]]) -> None:
        if ".." in snapshot_id or "/" in snapshot_id or "\\" in snapshot_id:
            raise ConsoleError("unknown_snapshot")
        path = self._objects_path(snapshot_id)
        path.write_text(
            "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
            encoding="utf-8",
        )

    def _account(self, account_id: str) -> dict[str, Any]:
        account = self._accounts.get(account_id)
        if not account:
            raise ConsoleError("unknown_account")
        return account

    def _require_role(self, account_id: str, role: str) -> dict[str, Any]:
        account = self._account(account_id)
        if role not in account["roles"]:
            raise ConsoleError(f"{role}_role_required")
        return account

    def _envelope(self, snapshot_id: str) -> dict[str, Any]:
        envelope = self._envelopes.get(snapshot_id)
        if not envelope:
            raise ConsoleError("unknown_snapshot")
        return envelope

    def create_account(
        self,
        username: str,
        password: str,
        roles: Iterable[str],
        display_name: str | None = None,
    ) -> dict[str, Any]:
        username = username.strip()
        display = (display_name or username).strip()
        if not username or not password:
            raise ConsoleError("account_fields_required")
        if _is_forbidden_identity(username) or _is_forbidden_identity(display):
            raise ConsoleError("forbidden_reviewer_identity")
        if any(row["username"] == username for row in self._accounts.values()):
            raise ConsoleError("username_already_exists")
        role_set = sorted(set(roles))
        if any(role not in ALLOWED_ROLES for role in role_set):
            raise ConsoleError("unknown_role")
        salt, digest = _hash_password(password)
        account_id = f"acc-{uuid.uuid4().hex[:12]}"
        record = {
            "account_id": account_id,
            "username": username,
            "display_name": display,
            "roles": role_set,
            "password_salt": salt,
            "password_hash": digest,
            "created_at": utc_now(),
        }
        self._accounts[account_id] = record
        self._save_accounts()
        return self._public_account(record)

    def public_signup(self, **_kwargs: Any) -> dict[str, Any]:
        raise ConsoleError("public_signup_forbidden")

    def _public_account(self, record: dict[str, Any]) -> dict[str, Any]:
        return {
            "account_id": record["account_id"],
            "username": record["username"],
            "display_name": record["display_name"],
            "roles": list(record["roles"]),
        }

    def authenticate(self, username: str, password: str) -> dict[str, Any]:
        record = next((row for row in self._accounts.values() if row["username"] == username), None)
        if not record:
            raise ConsoleError("invalid_credentials")
        _, digest = _hash_password(password, record["password_salt"])
        if not secrets.compare_digest(digest, record["password_hash"]):
            raise ConsoleError("invalid_credentials")
        token = secrets.token_hex(32)
        session = {
            "token": token,
            "account_id": record["account_id"],
            "username": record["username"],
            "roles": list(record["roles"]),
            "created_at": utc_now(),
        }
        self._sessions[token] = session
        self._save_sessions()
        return dict(session)

    def session_account(self, token: str | None) -> dict[str, Any]:
        if not token or token not in self._sessions:
            raise ConsoleError("not_authenticated")
        account = self._account(self._sessions[token]["account_id"])
        return self._public_account(account)

    def logout(self, token: str | None) -> None:
        if token and token in self._sessions:
            del self._sessions[token]
            self._save_sessions()

    def list_reviewer_accounts(self) -> list[dict[str, Any]]:
        return [self._public_account(row) for row in self._accounts.values() if "reviewer" in row["roles"]]

    def _resolve_named_reviewers(self, named_reviewers: list[str], uploader_id: str) -> list[str]:
        if not named_reviewers:
            raise ConsoleError("named_reviewers_required")
        resolved: list[str] = []
        for raw in named_reviewers:
            value = str(raw).strip()
            if _is_forbidden_identity(value):
                raise ConsoleError("forbidden_reviewer_identity")
            account = self._accounts.get(value) or next(
                (row for row in self._accounts.values() if row["username"] == value or row["display_name"] == value),
                None,
            )
            if account is None:
                raise ConsoleError("forbidden_reviewer_identity" if _is_forbidden_identity(value) else "unknown_reviewer")
            if _is_forbidden_identity(account["username"]) or _is_forbidden_identity(account["display_name"]):
                raise ConsoleError("forbidden_reviewer_identity")
            if "reviewer" not in account["roles"]:
                raise ConsoleError("named_reviewer_must_have_reviewer_role")
            resolved.append(account["account_id"])
        unique = list(dict.fromkeys(resolved))
        others = [account_id for account_id in unique if account_id != uploader_id]
        if not others:
            raise ConsoleError("uploader_cannot_be_sole_required_reviewer")
        return unique

    def create_managed_account(
        self,
        *,
        actor_id: str,
        username: str,
        password: str,
        roles: Iterable[str],
        display_name: str | None = None,
    ) -> dict[str, Any]:
        self._require_role(actor_id, "publisher")
        return self.create_account(username, password, roles, display_name=display_name)

    def assign_roles(self, *, actor_id: str, account_id: str, roles: Iterable[str]) -> dict[str, Any]:
        self._require_role(actor_id, "publisher")
        account = self._account(account_id)
        role_set = sorted(set(roles))
        if any(role not in ALLOWED_ROLES for role in role_set):
            raise ConsoleError("unknown_role")
        account["roles"] = role_set
        self._save_accounts()
        return self._public_account(account)

    def waiting_task_counts(self, account_id: str) -> dict[str, int]:
        account = self._account(account_id)
        roles = set(account["roles"])
        ingest = 0
        review = 0
        publish = 0
        tree = 0
        for envelope in self._envelopes.values():
            objects = self._load_objects(envelope["snapshot_id"])
            statuses = {(row.get("governance") or {}).get("validation_status") for row in objects}
            if envelope.get("uploader_account_id") == account_id and statuses & {"revise", "rejected"}:
                ingest += 1
            if account_id in (envelope.get("named_reviewers") or []) and "reviewer" in roles:
                if "needs_review" in statuses or not objects:
                    review += 1
                elif objects and all(
                    (row.get("governance") or {}).get("validation_status") == "needs_review"
                    or row.get("object_type") == "document"
                    for row in objects
                ):
                    review += 1
                elif "needs_review" in statuses:
                    review += 1
            if envelope.get("clinical_rereview_required") and roles & {"researcher", "reviewer", "publisher"}:
                tree += 1
            if "publisher" in roles and envelope.get("state") == CAPTURED:
                publish += 1
        if "reviewer" in roles:
            review = len(
                [
                    envelope
                    for envelope in self._envelopes.values()
                    if account_id in (envelope.get("named_reviewers") or [])
                    and any(
                        (row.get("governance") or {}).get("validation_status") == "needs_review"
                        for row in self._load_objects(envelope["snapshot_id"])
                    )
                ]
            )
        if "publisher" not in roles:
            publish = 0
        if "researcher" not in roles:
            ingest = 0
        return {
            "ingest": ingest,
            "tree": tree,
            "review": review,
            "publish": publish,
            "accounts": 0,
        }

    def object_review_bindings(self, snapshot_id: str) -> list[dict[str, Any]]:
        self._envelope(snapshot_id)
        current = {row["object_id"]: row for row in self.snapshot_objects(snapshot_id)}
        out = []
        for row in self._bindings.get(snapshot_id, []):
            item = dict(row)
            obj = current.get(item.get("object_id"))
            item["valid"] = bool(obj) and still_matches(item, obj)
            out.append(item)
        return out

    def confirm_object_type(
        self,
        *,
        actor_id: str,
        snapshot_id: str,
        object_id: str,
        confirmed_object_type: str,
    ) -> dict[str, Any]:
        reviewer = self._require_role(actor_id, "reviewer")
        if actor_id not in self._envelope(snapshot_id)["named_reviewers"]:
            raise ConsoleError("reviewer_not_named_on_snapshot")
        review_path = review_path_for_klasse(self._envelope(snapshot_id)["class"])
        if not is_confirmable_type_for_path(confirmed_object_type, review_path):
            raise ConsoleError("unknown_object_type")
        current = self.snapshot_objects(snapshot_id)
        target = next((row for row in current if row["object_id"] == object_id), None)
        if target is None:
            raise ConsoleError("unknown_object")
        if target.get("object_type") == "document":
            raise ConsoleError("unknown_object_type")
        if is_admission_blocked(target, review_path=review_path):
            raise ConsoleError("blocked_candidate_not_reviewable")
        self._require_open_original(snapshot_id, object_id)
        if target.get("confirmed_object_type") != confirmed_object_type:
            target["object_version"] = bump_patch(str(target.get("object_version") or "1.0"))
        target["confirmed_object_type"] = confirmed_object_type
        target["object_type"] = confirmed_object_type
        if confirmed_object_type not in {"recommendation", "outcome"}:
            target.pop("confirmed_recommendation_strength", None)
        mark_four_eyes_on_object(target, confirmed_type=confirmed_object_type)
        stamp_canonical_hashes(target)
        history = [
            row
            for row in self._load_objects(snapshot_id)
            if not (row["object_id"] == object_id and row["object_version"] == target["object_version"])
        ]
        history.append(target)
        self._save_objects(snapshot_id, history)
        self._bindings[snapshot_id] = invalidate_for_object(self._bindings.get(snapshot_id, []), object_id)
        self._save_bindings()
        return deepcopy(target)

    def confirm_relations(
        self,
        *,
        actor_id: str,
        snapshot_id: str,
        object_id: str,
        relations: list[dict[str, Any]],
    ) -> dict[str, Any]:
        reviewer = self._require_role(actor_id, "reviewer")
        if actor_id not in self._envelope(snapshot_id)["named_reviewers"]:
            raise ConsoleError("reviewer_not_named_on_snapshot")
        current = self.snapshot_objects(snapshot_id)
        target = next((row for row in current if row["object_id"] == object_id), None)
        if target is None:
            raise ConsoleError("unknown_object")
        for row in relations:
            if not is_closed_relation_type(row.get("relation_type")):
                raise ConsoleError("unknown_relation_type")
        try:
            confirmed = confirm_relation_set(relations)
        except ValueError as exc:
            raise ConsoleError("unknown_relation_type") from exc
        from src.heading_parent_list_v1 import is_heading_object, parent_proposal_may_bind

        for row in confirmed:
            if row.get("relation_type") not in {"parent", "child"}:
                continue
            peer = next((item for item in current if item.get("object_id") == row.get("target_object_id")), None)
            if peer is None:
                continue
            if row["relation_type"] == "child":
                child, parent = target, peer
            else:
                child, parent = peer, target
            if is_heading_object(child) and is_heading_object(parent):
                if not parent_proposal_may_bind(child, parent, current):
                    raise ConsoleError("invalid_parent_structure")
        previous_relations = binding_relations(target)
        previous_child_relations = [
            row
            for row in previous_relations
            if row.get("relation_type") == "child"
        ]
        confirmed_parents = [
            row["target_object_id"]
            for row in confirmed
            if row.get("relation_type") == "child"
        ]
        if len(confirmed_parents) > 1:
            raise ConsoleError("multiple_parents_not_allowed")
        canonical_parent_changed = False
        if confirmed_parents:
            canonical_parent_changed = target.get("parent_object_id") != confirmed_parents[0]
            target["parent_object_id"] = confirmed_parents[0]
        elif previous_child_relations:
            canonical_parent_changed = target.get("parent_object_id") is not None
            target["parent_object_id"] = None
        if target.get("confirmed_relations") != confirmed or canonical_parent_changed:
            target["object_version"] = bump_patch(str(target.get("object_version") or "1.0"))
        target["confirmed_relations"] = confirmed
        stamp_canonical_hashes(target)

        previous_children = {
            row["target_object_id"]
            for row in previous_relations
            if row.get("relation_type") == "parent"
        }
        confirmed_children = {
            row["target_object_id"]
            for row in confirmed
            if row.get("relation_type") == "parent"
        }
        peer_updates: list[dict[str, Any]] = []
        for child_id in previous_children | confirmed_children:
            peer = next((item for item in current if item.get("object_id") == child_id), None)
            if peer is None:
                continue
            if child_id in confirmed_children:
                desired_parent = object_id
            else:
                # Only undo the parent assignment that this relation created.
                # The child may have been re-parented independently since then.
                if peer.get("parent_object_id") != object_id:
                    continue
                desired_parent = None
            if peer.get("parent_object_id") == desired_parent:
                continue
            updated_peer = deepcopy(peer)
            updated_peer["parent_object_id"] = desired_parent
            updated_peer["object_version"] = bump_patch(
                str(updated_peer.get("object_version") or "1.0")
            )
            stamp_canonical_hashes(updated_peer)
            peer_updates.append(updated_peer)
        history = [
            row
            for row in self._load_objects(snapshot_id)
            if not (row["object_id"] == object_id and row["object_version"] == target["object_version"])
        ]
        history.append(target)
        history.extend(peer_updates)
        self._save_objects(snapshot_id, history)
        self._bindings[snapshot_id] = invalidate_for_object(self._bindings.get(snapshot_id, []), object_id)
        for peer in peer_updates:
            self._bindings[snapshot_id] = invalidate_for_object(
                self._bindings.get(snapshot_id, []), peer["object_id"]
            )
        self._save_bindings()
        _ = reviewer
        return deepcopy(target)

    def open_source_passage(self, *, snapshot_id: str, object_id: str) -> dict[str, Any]:
        envelope = self._envelope(snapshot_id)
        target = next((row for row in self.snapshot_objects(snapshot_id) if row["object_id"] == object_id), None)
        if target is None:
            raise ConsoleError("unknown_object")
        freeze_path = Path(envelope["binary_path"])
        freeze_bytes = freeze_path.read_bytes() if freeze_path.exists() else None
        expected_digest = str(envelope["sha256"])
        if freeze_bytes is not None and sha256_bytes(freeze_bytes) != expected_digest:
            freeze_bytes = None
        immutable_locator = envelope.get("immutable_storage_locator")
        if freeze_bytes is None and immutable_locator and self.immutable_source_store is not None:
            try:
                freeze_bytes = self.immutable_source_store.load_verified(immutable_locator)
            except G2SourceStoreError as exc:
                raise ConsoleError("immutable_source_recovery_failed") from exc
            if sha256_bytes(freeze_bytes) != expected_digest:
                raise ConsoleError("immutable_source_recovery_failed")
            _atomic_write_bytes(freeze_path, freeze_bytes)
        try:
            return open_source_passage(
                freeze_bytes=freeze_bytes,
                content_kind=envelope["content_kind"],
                locator=None,
                object_record=target,
            )
        except OpenOriginalError as exc:
            raise ConsoleError(exc.code) from exc

    def _require_open_original(self, snapshot_id: str, object_id: str) -> dict[str, Any]:
        try:
            return self.open_source_passage(snapshot_id=snapshot_id, object_id=object_id)
        except ConsoleError as exc:
            if exc.code in {
                "source_locator_missing",
                "freeze_bytes_missing",
                "locator_kind_mismatch",
                "unsupported_locator",
            }:
                raise ConsoleError("open_original_required") from exc
            raise

    def ingest(
        self,
        *,
        actor_id: str,
        ingest_kind: str,
        title: str,
        version: str,
        date: str,
        live_url: str,
        class_: str,
        family: str,
        named_reviewers: list[str],
        filename: str | None = None,
        data: bytes | None = None,
        content_type: str | None = None,
        url: str | None = None,
        replaces_snapshot_id: str | None = None,
    ) -> dict[str, Any]:
        self._require_role(actor_id, "researcher")
        if ingest_kind not in {"new", "new_version"}:
            raise ConsoleError("invalid_ingest_kind")
        if class_ not in ALLOWED_CLASSES:
            raise ConsoleError("invalid_class")
        family_hook = family.strip()
        if not family_hook or not title.strip():
            raise ConsoleError("ingest_fields_required")
        source_version = safe_path_token(
            validate_ingest_source_version(version),
            pattern=SOURCE_VERSION_RE,
            code="invalid_source_version",
        )
        source_date = safe_path_token(
            normalize_ingest_source_date(date),
            pattern=ISO_DATE_RE,
            code="invalid_source_date",
        )
        reviewers = self._resolve_named_reviewers(named_reviewers, actor_id)
        review_path = review_path_for_klasse(class_)
        candidate_url = url or live_url or ""
        if review_path == "boom" and is_live_rest_url(candidate_url):
            if data is None or is_live_rest_sole_source(
                data=data or b"",
                live_url=candidate_url,
                filename=filename or "",
            ):
                raise ConsoleError("live_rest_not_sole_source")
        if url:
            data, fetched_type, fetched_name = self.url_fetcher(url)
            filename = filename or fetched_name
            content_type = content_type or fetched_type
        if data is None:
            raise ConsoleError("official_file_or_url_required")
        filename = filename or "source.bin"
        if ".." in filename or "/" in filename or "\\" in filename:
            raise ConsoleError("invalid_store_path")
        filename = safe_store_filename(filename)
        if review_path == "boom":
            freeze_errors = boom_freeze_errors(
                data=data,
                filename=filename,
                live_url=live_url or url or "",
            )
            if "story_html_alone_insufficient" in freeze_errors:
                raise ConsoleError("story_html_alone_insufficient")
            if "live_rest_sole_source" in freeze_errors:
                raise ConsoleError("live_rest_not_sole_source")
            if freeze_errors:
                raise ConsoleError(freeze_errors[0])
            kind = "boom"
        else:
            kind = classify_official_file(data, filename, content_type)
        if url and kind == "html":
            raise ConsoleError("live_url_html_not_allowed")
        digest = safe_path_token(sha256_bytes(data), pattern=STORE_DIGEST_RE)
        immutable_locator = None
        if self.immutable_source_store is not None:
            try:
                immutable_locator = self.immutable_source_store.store_verified(
                    data=data,
                    sha256=digest,
                    filename=filename,
                )
            except (G2SourceStoreError, ValueError) as exc:
                raise ConsoleError("immutable_source_storage_failed") from exc
        stored_path = safe_path_under(self.source_store, digest, filename)
        stored_path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_bytes(stored_path, data)
        locator = immutable_locator or f"g0-local:sources/private/{digest}/{filename}"
        snapshot_id = f"snap-{digest[:16]}-{uuid.uuid4().hex[:8]}"
        document_id = f"console-{_slug(family_hook)}-{_slug(title)}-{_slug(source_version)}-{digest[:8]}"
        source_id = f"src-{digest[:16]}"
        previous = None
        if ingest_kind == "new_version":
            if not replaces_snapshot_id:
                raise ConsoleError("replaces_snapshot_id_required")
            previous = self._envelope(replaces_snapshot_id)
        fragments, spec = self._fragments_and_spec(
            kind,
            stored_path,
            data=data,
            document_id=document_id,
            source_id=source_id,
            title=title.strip(),
            family=family_hook,
            class_=class_,
        )
        manifest = {
            "canonical_source": {
                "source_id": source_id,
                "title": title.strip(),
                "publisher": "V&VN",
                "source_url": live_url or url or f"urn:vvn:freeze:{digest}",
                "source_type": "interactive_tree" if kind == "boom" else kind,
                "source_level": 1,
                "canonicality": "canonical",
                "source_checksum": digest,
                "checksum_algorithm": "sha256",
                "integrity_status": "verified",
                "publication_date": source_date,
                "version": source_version,
            }
        }
        objects = transform_generic(spec, manifest, fragments)
        if kind == "boom":
            stamp_boom_flags(objects, fragments)
        else:
            objects = apply_admission_gate(
                objects,
                klasse=class_,
                fragments=fragments,
                document_version=source_version,
                source_hash=digest,
            )
        object_diff = None
        if previous:
            object_diff = self._diff_objects(self.snapshot_objects(previous["snapshot_id"]), objects)
        envelope = {
            "snapshot_id": snapshot_id,
            "source_id": source_id,
            "document_id": document_id,
            "sha256": digest,
            "locator": locator,
            "binary_path": str(stored_path.resolve()),
            "immutable_storage_locator": immutable_locator,
            "state": CAPTURED,
            "publication_eligibility": (
                "eligible_for_transform_and_review"
                if immutable_locator
                else "blocked_pending_immutable_storage"
            ),
            "content_kind": kind,
            "ingest_kind": ingest_kind,
            "title": title.strip(),
            "version": source_version,
            "date": source_date,
            "live_url": live_url or url or "",
            "class": class_,
            "family": family_hook,
            "named_reviewers": reviewers,
            "uploader_account_id": actor_id,
            "review_passes": {},
            "is_live_capture": ingest_kind == "new",
            "replaces_snapshot_id": replaces_snapshot_id,
            "object_diff": object_diff,
            "clinical_rereview_required": False,
            "acquired_at": utc_now(),
            "console_version": CONSOLE_VERSION,
        }
        self._envelopes[snapshot_id] = envelope
        self._save_objects(snapshot_id, objects)
        self._save_envelopes()
        return self._receipt(envelope)

    def reextract_unpublished(self, *, actor_id: str, snapshot_id: str) -> dict[str, Any]:
        """Replace unpublished object identities with a new extract of the same freeze.

        Source hash stays. Published objects MUST NOT be rewritten. MUST NOT hide
        stored fragments in the UI without this extract.
        """
        account = self._account(actor_id)
        if "researcher" not in account["roles"] and "reviewer" not in account["roles"]:
            raise ConsoleError("researcher_role_required")
        envelope = self._envelope(snapshot_id)
        rows = self._load_objects(snapshot_id)
        if any((row.get("governance") or {}).get("publication_status") == "published" for row in rows):
            raise ConsoleError("published_objects_must_not_be_rewritten")
        freeze_path = Path(envelope["binary_path"])
        if not freeze_path.is_file():
            raise ConsoleError("freeze_bytes_missing")
        freeze_bytes = freeze_path.read_bytes()
        digest = sha256_bytes(freeze_bytes)
        if digest != envelope["sha256"]:
            raise ConsoleError("freeze_bytes_missing")
        fragments, spec = self._fragments_and_spec(
            envelope["content_kind"],
            freeze_path,
            data=freeze_bytes,
            document_id=envelope["document_id"],
            source_id=envelope["source_id"],
            title=envelope["title"],
            family=envelope["family"],
            class_=envelope["class"],
        )
        manifest = {
            "canonical_source": {
                "source_id": envelope["source_id"],
                "title": envelope["title"],
                "publisher": "V&VN",
                "source_url": envelope.get("live_url") or "",
                "source_type": "interactive_tree" if envelope["content_kind"] == "boom" else envelope["content_kind"],
                "source_level": 1,
                "canonicality": "canonical",
                "source_checksum": envelope["sha256"],
                "checksum_algorithm": "sha256",
                "integrity_status": "verified",
                "publication_date": envelope["date"],
                "version": envelope["version"],
            }
        }
        objects = transform_generic(spec, manifest, fragments)
        if envelope["content_kind"] == "boom":
            stamp_boom_flags(objects, fragments)
        else:
            objects = apply_admission_gate(
                objects,
                klasse=envelope["class"],
                fragments=fragments,
                document_version=envelope["version"],
                source_hash=envelope["sha256"],
            )
        envelope["review_passes"] = {}
        envelope["state"] = CAPTURED
        self._envelopes[snapshot_id] = envelope
        self._save_objects(snapshot_id, objects)
        self._save_envelopes()
        self._bindings[snapshot_id] = []
        self._save_bindings()
        return self._receipt(envelope)

    def snapshot_is_published(self, snapshot_id: str) -> bool:
        """True when this snapshot is a published projection or has been published."""
        envelope = self._envelope(snapshot_id)
        if envelope.get("state") in PUBLISHED_ENVELOPE_STATES:
            return True
        if envelope.get("published") is True:
            return True
        rows = self._load_objects(snapshot_id)
        if any((row.get("governance") or {}).get("publication_status") == "published" for row in rows):
            return True
        return self._snapshot_in_published_projection(snapshot_id)

    def _published_projection_path(self) -> Path:
        return self.runtime / PUBLISHED_PROJECTION_FILENAME

    def _snapshot_in_published_projection(self, snapshot_id: str) -> bool:
        path = self._published_projection_path()
        if not path.is_file():
            return False
        token = safe_snapshot_id(snapshot_id)
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("snapshot_id") == token:
                return True
            meta = row.get("metadata") or {}
            if meta.get("snapshot_id") == token:
                return True
        return False

    def _maybe_remove_unpublished_freeze_bytes(self, envelope: dict[str, Any]) -> bool:
        """Remove freeze bytes of this unpublished source when no other snapshot uses them.

        MUST NOT walk ``/home/data``. MUST NOT rmtree the source store.
        """
        digest = safe_path_token(str(envelope["sha256"]), pattern=STORE_DIGEST_RE)
        still_used = any(
            row.get("sha256") == digest and row.get("snapshot_id") != envelope["snapshot_id"]
            for row in self._envelopes.values()
        )
        if still_used:
            return False
        filename = safe_store_filename(Path(str(envelope.get("binary_path") or "")).name)
        stored = safe_path_under(self.source_store, digest, filename)
        removed = False
        if stored.is_file():
            stored.unlink()
            removed = True
        parent = stored.parent
        try:
            if parent.is_dir() and parent != self.source_store.resolve() and not any(parent.iterdir()):
                parent.rmdir()
        except OSError:
            pass
        return removed

    def delete_unpublished_snapshot(
        self,
        *,
        actor_id: str,
        snapshot_id: str,
        confirmed: bool = False,
        confirm_title: str = "",
    ) -> dict[str, Any]:
        """Remove one unpublished captured snapshot from the operations console.

        Whole snapshot only. MUST confirm. MUST type-to-confirm the exact title.
        MUST NOT delete a published projection.
        MUST NOT hide selected objects inside a freeze that stays in Review.
        Four-eyes is not required. Capture is not publication.
        """
        if ".." in snapshot_id or "/" in snapshot_id or "\\" in snapshot_id:
            raise ConsoleError("unknown_snapshot")
        token = safe_snapshot_id(snapshot_id)
        account = self._account(actor_id)
        if _is_forbidden_identity(account["username"]) or _is_forbidden_identity(account["display_name"]):
            raise ConsoleError("forbidden_reviewer_identity")
        roles = set(account["roles"])
        if "researcher" not in roles and "reviewer" not in roles:
            raise ConsoleError("unpublished_delete_role_required")
        if not confirmed:
            raise ConsoleError("delete_confirmation_required")
        envelope = self._envelope(token)
        if self.snapshot_is_published(token):
            raise ConsoleError("published_projection_must_not_be_deleted")
        title = str(envelope["title"])
        if confirm_title != title:
            raise ConsoleError("delete_title_confirmation_required")
        digest = str(envelope["sha256"])
        objects_path = self._objects_path(token)
        if objects_path.is_file():
            objects_path.unlink()
        self._envelopes.pop(token, None)
        self._bindings.pop(token, None)
        self._save_envelopes()
        self._save_bindings()
        freeze_removed = self._maybe_remove_unpublished_freeze_bytes(envelope)
        append_event(
            self._ledger_path,
            event_type=UNPUBLISHED_DELETE_EVENT,
            object_id=token,
            object_version=str(envelope.get("version") or ""),
            actor=account["username"],
            details={
                "snapshot_id": token,
                "sha256": digest,
                "title": title,
                "actor_id": actor_id,
                "display_name": account["display_name"],
            },
        )
        return {
            "deleted": True,
            "snapshot_id": token,
            "sha256": digest,
            "title": title,
            "actor": account["username"],
            "freeze_bytes_removed": freeze_removed,
            "four_eyes_required": False,
            "second_named_reviewer_required": False,
            "capture_is_publication": False,
            "g2": "BLOCKED",
        }

    def _extract(self, kind: str, path: Path, *, document_id: str, source_id: str) -> list[dict[str, Any]]:
        if kind == "html":
            return extract_html(path, document_id=document_id, source_id=source_id)
        if kind == "boom":
            return extract_boom_fragments(path.read_bytes(), document_id=document_id, source_id=source_id)
        return extract_pdf(path, document_id=document_id, source_id=source_id)

    def _fragments_and_spec(
        self,
        kind: str,
        path: Path,
        *,
        data: bytes,
        document_id: str,
        source_id: str,
        title: str,
        family: str,
        class_: str,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        if kind == "boom":
            try:
                fragments = extract_boom_fragments(data, document_id=document_id, source_id=source_id)
            except ValueError as exc:
                raise ConsoleError("invalid_boom_freeze") from exc
            spec = boom_spec_from_fragments(
                document_id=document_id,
                title=title,
                family=family,
                class_=class_,
                fragments=fragments,
            )
            return fragments, spec
        fragments = self._extract(kind, path, document_id=document_id, source_id=source_id)
        spec = _spec_from_fragments(
            document_id=document_id,
            title=title,
            family=family,
            class_=class_,
            fragments=fragments,
            content_kind=kind,
        )
        return fragments, spec

    def _diff_objects(self, previous: list[dict[str, Any]], current: list[dict[str, Any]]) -> dict[str, Any]:
        def key(row: dict[str, Any]) -> str:
            return (row.get("content") or {}).get("clean_text") or ""

        prev = {key(row): compute_canonical_object_hash(row) for row in previous if row.get("object_type") != "document" and key(row)}
        curr = {key(row): compute_canonical_object_hash(row) for row in current if row.get("object_type") != "document" and key(row)}
        return {
            "added": sorted(text for text in curr if text not in prev),
            "removed": sorted(text for text in prev if text not in curr),
            "changed": sorted(text for text in curr if text in prev and curr[text] != prev[text]),
            "unchanged": sorted(text for text in curr if text in prev and curr[text] == prev[text]),
        }

    def _receipt(self, envelope: dict[str, Any]) -> dict[str, Any]:
        return deepcopy(envelope)

    def snapshot_objects(self, snapshot_id: str, include_blocked: bool = False) -> list[dict[str, Any]]:
        self._envelope(snapshot_id)
        rows = self._load_objects(snapshot_id)
        if include_blocked:
            return deepcopy(rows)
        current: dict[str, dict[str, Any]] = {}
        for row in rows:
            current[row["object_id"]] = row
        return deepcopy(list(current.values()))

    def family_tree(self) -> dict[str, Any]:
        families: dict[str, dict[str, Any]] = {}
        for envelope in self._envelopes.values():
            family = envelope["family"]
            bucket = families.setdefault(family, {"family": family, "children": []})
            bucket["children"].append(
                {
                    "snapshot_id": envelope["snapshot_id"],
                    "class": envelope["class"],
                    "title": envelope["title"],
                    "version": envelope["version"],
                    "family": family,
                    "status": envelope["state"],
                    "sha256": envelope["sha256"],
                    "parent": family,
                    "is_live_capture": envelope["is_live_capture"],
                }
            )
        for bucket in families.values():
            bucket["children"].sort(key=lambda child: (CLASS_ORDER.get(child["class"], 0) * -1, child["title"]))
        return {"axis": "family × class", "stable": True, "families": families}

    def move_family(self, *, actor_id: str, snapshot_id: str, new_family: str) -> dict[str, Any]:
        account = self._account(actor_id)
        if "researcher" not in account["roles"] and "publisher" not in account["roles"]:
            raise ConsoleError("curator_role_required")
        family = new_family.strip()
        if not family:
            raise ConsoleError("family_required")
        envelope = self._envelope(snapshot_id)
        envelope["family"] = family
        envelope["clinical_rereview_required"] = False
        self._save_envelopes()
        return self._receipt(envelope)

    def _class_change_history_dir(self) -> Path:
        path = self.runtime / CLASS_CHANGE_HISTORY_DIRNAME
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _class_change_manifest(self, envelope: dict[str, Any]) -> dict[str, Any]:
        return {
            "canonical_source": {
                "source_id": envelope["source_id"],
                "title": envelope["title"],
                "publisher": "V&VN",
                "source_url": envelope.get("live_url") or "",
                "source_type": "interactive_tree" if envelope["content_kind"] == "boom" else envelope["content_kind"],
                "source_level": 1,
                "canonicality": "canonical",
                "source_checksum": envelope["sha256"],
                "checksum_algorithm": "sha256",
                "integrity_status": "verified",
                "publication_date": envelope["date"],
                "version": envelope["version"],
            }
        }

    def _full_rereview_rows(self, rows: list[dict[str, Any]]) -> None:
        for row in rows:
            governance = row.setdefault("governance", {})
            governance["validation_status"] = "needs_review"
            governance["validated_by"] = None
            governance["validation_date"] = None
            governance["review_snapshot_hash"] = None
            governance["publication_status"] = "unpublished"

    def _invalidate_all_bindings(self, snapshot_id: str) -> None:
        self._bindings[snapshot_id] = invalidate_for_object(self._bindings.get(snapshot_id, []), "")
        self._bindings[snapshot_id] = [
            {**row, "valid": False} for row in self._bindings.get(snapshot_id, [])
        ]
        self._save_bindings()

    def _record_class_change_event(
        self,
        *,
        account: dict[str, Any],
        envelope: dict[str, Any],
        from_class: str,
        to_class: str,
        model: str,
    ) -> None:
        append_event(
            self._ledger_path,
            event_type=DOCUMENT_CLASS_CHANGED_EVENT,
            object_id=envelope["snapshot_id"],
            object_version=str(envelope.get("version") or ""),
            actor=account["username"],
            details={
                "snapshot_id": envelope["snapshot_id"],
                "sha256": envelope["sha256"],
                "title": envelope["title"],
                "from_class": from_class,
                "to_class": to_class,
                "actor_id": account["account_id"],
                "display_name": account["display_name"],
                "model": model,
            },
        )

    def _archive_prior_objects(
        self,
        *,
        snapshot_id: str,
        rows: list[dict[str, Any]],
        from_class: str,
        to_class: str,
    ) -> dict[str, Any]:
        token = safe_snapshot_id(snapshot_id)
        stamp = utc_now().replace(":", "").replace("-", "")
        filename = safe_store_filename(f"{token}-{stamp}.jsonl")
        dest = safe_path_under(self._class_change_history_dir(), filename)
        dest.write_text(
            "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
            encoding="utf-8",
        )
        return {
            "reason": DOCUMENT_CLASS_CHANGED_EVENT,
            "from_class": from_class,
            "to_class": to_class,
            "changed_at": utc_now(),
            "object_count": len(rows),
            "object_ids": [row.get("object_id") for row in rows],
            "history_file": filename,
        }

    def prior_object_audit_history(self, snapshot_id: str) -> list[dict[str, Any]]:
        envelope = self._envelope(snapshot_id)
        out: list[dict[str, Any]] = []
        for record in envelope.get("prior_processing_history") or []:
            objects: list[dict[str, Any]] = []
            filename = str(record.get("history_file") or "")
            if filename:
                path = safe_path_under(self._class_change_history_dir(), safe_store_filename(filename))
                if path.is_file():
                    objects = [
                        json.loads(line)
                        for line in path.read_text(encoding="utf-8").splitlines()
                        if line.strip()
                    ]
            out.append({**deepcopy(record), "objects": objects})
        return out

    def _reextract_objects_for_klasse(
        self,
        envelope: dict[str, Any],
        new_class: str,
        freeze_bytes: bytes,
    ) -> list[dict[str, Any]]:
        freeze_path = Path(envelope["binary_path"])
        if review_path_for_klasse(new_class) == "boom":
            errors = boom_freeze_errors(
                data=freeze_bytes,
                filename=freeze_path.name,
                live_url=envelope.get("live_url") or "",
            )
            if errors:
                raise ConsoleError(errors[0])
            fragments, spec = self._fragments_and_spec(
                "boom",
                freeze_path,
                data=freeze_bytes,
                document_id=envelope["document_id"],
                source_id=envelope["source_id"],
                title=envelope["title"],
                family=envelope["family"],
                class_=new_class,
            )
            objects = transform_generic(spec, self._class_change_manifest(envelope), fragments)
            stamp_boom_flags(objects, fragments)
            return objects
        if envelope["content_kind"] == "boom":
            try:
                fragments = extract_boom_fragments(
                    freeze_bytes,
                    document_id=envelope["document_id"],
                    source_id=envelope["source_id"],
                )
            except ValueError as exc:
                raise ConsoleError("invalid_boom_freeze") from exc
            fragments = [
                {key: value for key, value in fragment.items() if key != "boom_kind"}
                for fragment in fragments
            ]
            spec = _spec_from_fragments(
                document_id=envelope["document_id"],
                title=envelope["title"],
                family=envelope["family"],
                class_=new_class,
                fragments=fragments,
                content_kind=envelope["content_kind"],
            )
            objects = transform_generic(spec, self._class_change_manifest(envelope), fragments)
            return apply_admission_gate(
                objects,
                klasse=new_class,
                fragments=fragments,
                document_version=envelope["version"],
                source_hash=envelope["sha256"],
            )
        fragments, spec = self._fragments_and_spec(
            envelope["content_kind"],
            freeze_path,
            data=freeze_bytes,
            document_id=envelope["document_id"],
            source_id=envelope["source_id"],
            title=envelope["title"],
            family=envelope["family"],
            class_=new_class,
        )
        objects = transform_generic(spec, self._class_change_manifest(envelope), fragments)
        return apply_admission_gate(
            objects,
            klasse=new_class,
            fragments=fragments,
            document_version=envelope["version"],
            source_hash=envelope["sha256"],
        )

    def promote_class(
        self,
        *,
        actor_id: str,
        snapshot_id: str,
        new_class: str,
        reextract: bool = False,
    ) -> dict[str, Any]:
        account = self._require_role(actor_id, "reviewer")
        if new_class not in ALLOWED_CLASSES:
            raise ConsoleError("invalid_class")
        envelope = self._envelope(snapshot_id)
        if self.snapshot_is_published(snapshot_id):
            raise ConsoleError("published_class_change_blocked")
        from_class = envelope["class"]
        if new_class == from_class:
            raise ConsoleError("class_unchanged")
        identity_before = source_identity_fields(envelope)
        freeze_path = Path(envelope["binary_path"])
        if not freeze_path.is_file():
            raise ConsoleError("freeze_bytes_missing")
        freeze_bytes = freeze_path.read_bytes()
        if sha256_bytes(freeze_bytes) != envelope["sha256"]:
            raise ConsoleError("freeze_bytes_missing")
        if is_cross_model_class_change(from_class, new_class):
            if not reextract:
                raise ConsoleError("cross_model_direct_change_blocked")
            prior = self._load_objects(snapshot_id)
            new_objects = self._reextract_objects_for_klasse(envelope, new_class, freeze_bytes)
            archived = self._archive_prior_objects(
                snapshot_id=snapshot_id,
                rows=prior,
                from_class=from_class,
                to_class=new_class,
            )
            envelope["class"] = new_class
            envelope["clinical_rereview_required"] = True
            envelope["review_passes"] = {}
            envelope["state"] = CAPTURED
            history = list(envelope.get("prior_processing_history") or [])
            history.append(archived)
            envelope["prior_processing_history"] = history
            self._full_rereview_rows(new_objects)
            self._save_objects(snapshot_id, new_objects)
            self._envelopes[snapshot_id] = envelope
            self._bindings[snapshot_id] = []
            self._save_bindings()
            self._save_envelopes()
            self._record_class_change_event(
                account=account,
                envelope=envelope,
                from_class=from_class,
                to_class=new_class,
                model="cross_model",
            )
            receipt = self._receipt(envelope)
            if source_identity_fields(receipt) != identity_before:
                raise ConsoleError("source_identity_must_not_change")
            return receipt
        rows = self._load_objects(snapshot_id)
        envelope["class"] = new_class
        envelope["clinical_rereview_required"] = True
        envelope["review_passes"] = {}
        self._full_rereview_rows(rows)
        self._save_objects(snapshot_id, rows)
        self._save_envelopes()
        self._invalidate_all_bindings(snapshot_id)
        self._record_class_change_event(
            account=account,
            envelope=envelope,
            from_class=from_class,
            to_class=new_class,
            model="same_model",
        )
        receipt = self._receipt(envelope)
        if source_identity_fields(receipt) != identity_before:
            raise ConsoleError("source_identity_must_not_change")
        return receipt

    def review_object(
        self,
        *,
        actor_id: str,
        snapshot_id: str,
        object_id: str,
        decision: str,
        comment: str | None = None,
        proposed_correction: str | None = None,
        confirmed_object_type: str | None = None,
        recommendation_strength: str | None = None,
    ) -> list[dict[str, Any]]:
        reviewer = self._require_role(actor_id, "reviewer")
        if _is_forbidden_identity(reviewer["username"]) or _is_forbidden_identity(reviewer["display_name"]):
            raise ConsoleError("forbidden_reviewer_identity")
        envelope = self._envelope(snapshot_id)
        if actor_id not in envelope["named_reviewers"]:
            raise ConsoleError("reviewer_not_named_on_snapshot")
        current = self.snapshot_objects(snapshot_id)
        target = next((row for row in current if row["object_id"] == object_id), None)
        if target is None:
            raise ConsoleError("unknown_object")
        confirmed = confirmed_object_type
        if not confirmed and target.get("confirmed_object_type"):
            confirmed = target["confirmed_object_type"]
        apply_type = bool(confirmed_object_type)
        review_path = review_path_for_klasse(envelope["class"])
        if is_admission_blocked(target, review_path=review_path) and (
            decision == "approve" or apply_type
        ):
            raise ConsoleError("blocked_candidate_not_reviewable")
        if decision == "approve" or apply_type:
            self._require_open_original(snapshot_id, object_id)
        if decision == "approve":
            if not is_confirmable_type_for_path(confirmed, review_path):
                raise ConsoleError("unknown_object_type")
            apply_type = True
        if apply_type and confirmed:
            if not is_confirmable_type_for_path(confirmed, review_path):
                raise ConsoleError("unknown_object_type")
            if target.get("object_type") != "document":
                if target.get("confirmed_object_type") != confirmed:
                    target["object_version"] = bump_patch(str(target.get("object_version") or "1.0"))
                target["confirmed_object_type"] = confirmed
                target["object_type"] = confirmed
                mark_four_eyes_on_object(target, confirmed_type=confirmed)
                stamp_canonical_hashes(target)
        if decision == "approve" and confirmed == "outcome":
            errors = outcome_review_errors(target, peers=current)
            if errors:
                raise ConsoleError("outcome_review_failed", ",".join(errors))
        strength = (recommendation_strength or "").strip() or None
        stamp_type = confirmed or target.get("confirmed_object_type") or target.get("object_type")
        strength_allowed = stamp_type in {"recommendation", "outcome"}
        previous_strength = target.get("confirmed_recommendation_strength")
        if apply_type and confirmed and not strength_allowed and previous_strength:
            if target.get("confirmed_object_type") != confirmed:
                target["object_version"] = bump_patch(str(target.get("object_version") or "1.0"))
            target.pop("confirmed_recommendation_strength", None)
            strength = None
        if not strength and decision == "approve" and stamp_type == "outcome":
            text = str((target.get("content") or {}).get("clean_text") or "")
            if is_geen_actie_outcome(text):
                mapped = map_geen_actie(text)
                strength = mapped["strength"]
                target["no_action"] = True
                metadata = target.setdefault("metadata", {})
                metadata["no_action"] = True
        if decision == "approve" and stamp_type == "outcome":
            effective = strength or target.get("confirmed_recommendation_strength")
            if not effective:
                raise ConsoleError("outcome_strength_required")
        if strength:
            if stamp_type not in {"recommendation", "outcome"}:
                raise ConsoleError("recommendation_strength_requires_recommendation")
            if not is_closed_recommendation_strength(strength):
                raise ConsoleError("unknown_recommendation_strength")
            if target.get("confirmed_recommendation_strength") != strength:
                if not apply_type:
                    target["object_version"] = bump_patch(str(target.get("object_version") or "1.0"))
                target["confirmed_recommendation_strength"] = strength
                stamp_canonical_hashes(target)
        track = target["governance"]["review_track"]
        payload = {
            "object_id": object_id,
            "decision": decision,
            "reviewer": reviewer["username"],
            "review_date": date.today().isoformat(),
            "reviewed_canonical_object_hash": compute_canonical_object_hash(target),
            "comment": comment or "",
            "proposed_correction": proposed_correction or "",
        }
        updated, report = apply_reviews(
            current,
            [payload],
            track=track,
            schema_path=self.schema_path,
            ledger_path=self._ledger_path,
        )
        if report["errors"]:
            raise ConsoleError("review_failed", json.dumps(report["errors"], ensure_ascii=False))
        history = [
            row
            for row in self._load_objects(snapshot_id)
            if not (row["object_id"] == object_id and row["object_version"] == target["object_version"])
        ]
        updated_target = next(row for row in updated if row["object_id"] == object_id)
        if confirmed and updated_target.get("object_type") != "document":
            updated_target["confirmed_object_type"] = confirmed
            updated_target["object_type"] = confirmed
            mark_four_eyes_on_object(updated_target, confirmed_type=confirmed)
            stamp_canonical_hashes(updated_target)
        if target.get("confirmed_relations"):
            updated_target["confirmed_relations"] = target["confirmed_relations"]
        if strength:
            updated_target["confirmed_recommendation_strength"] = strength
            stamp_canonical_hashes(updated_target)
        elif apply_type and confirmed and confirmed not in {"recommendation", "outcome"}:
            updated_target.pop("confirmed_recommendation_strength", None)
            stamp_canonical_hashes(updated_target)
        if target.get("no_action"):
            updated_target["no_action"] = True
            metadata = updated_target.setdefault("metadata", {})
            metadata["no_action"] = True
            stamp_canonical_hashes(updated_target)
        history.append(updated_target)
        self._save_objects(snapshot_id, history)
        if decision == "approve":
            envelope["review_passes"][actor_id] = {"passed": True, "at": utc_now(), "object_id": object_id}
            self._save_envelopes()
            binding = tuple_record(
                object_id=object_id,
                object_version=updated_target["object_version"],
                canonical_object_hash=compute_canonical_object_hash(updated_target),
                confirmed_object_type=updated_target.get("confirmed_object_type"),
                reviewer=reviewer["username"],
                reviewer_id=actor_id,
                decision=decision,
            )
            rows = [
                item
                for item in self._bindings.get(snapshot_id, [])
                if not (item.get("object_id") == object_id and item.get("reviewer_id") == actor_id)
            ]
            rows.append(binding)
            self._bindings[snapshot_id] = rows
            self._save_bindings()
        else:
            self._bindings[snapshot_id] = invalidate_for_object(self._bindings.get(snapshot_id, []), object_id)
            self._save_bindings()
        return deepcopy(updated)

    def batch_confirm_headings(
        self,
        *,
        actor_id: str,
        snapshot_id: str,
        object_ids: Iterable[str],
    ) -> list[dict[str, Any]]:
        """Fast-lane confirm of proposed headings as structure, not advice.

        MUST NOT bypass four-eyes when the object is high-risk or is later
        reclassified onto a high-risk type. Serving still requires confirmed
        closed types plus the published projection and G2 locator.
        """
        self._require_role(actor_id, "reviewer")
        ids = [str(object_id).strip() for object_id in object_ids if str(object_id).strip()]
        if not ids:
            raise ConsoleError("fast_lane_heading_required")
        review_path = review_path_for_klasse(self._envelope(snapshot_id)["class"])
        structure_type = "path" if review_path == "boom" else "heading"
        current = {row["object_id"]: row for row in self.snapshot_objects(snapshot_id)}
        for object_id in ids:
            target = current.get(object_id)
            if target is None:
                raise ConsoleError("unknown_object")
            if review_lane(target, review_path=review_path) != "fast":
                raise ConsoleError("fast_lane_heading_required")
        updated: list[dict[str, Any]] = []
        for object_id in ids:
            rows = self.review_object(
                actor_id=actor_id,
                snapshot_id=snapshot_id,
                object_id=object_id,
                decision="approve",
                confirmed_object_type=structure_type,
            )
            refreshed = next(row for row in rows if row["object_id"] == object_id)
            mark_four_eyes_on_object(refreshed, confirmed_type="heading")
            stamp_canonical_hashes(refreshed)
            updated.append(deepcopy(refreshed))
        return updated

    def correct_object(
        self,
        *,
        actor_id: str,
        snapshot_id: str,
        object_id: str,
        patch: dict[str, Any],
    ) -> dict[str, Any]:
        account = self._account(actor_id)
        if "researcher" not in account["roles"] and "reviewer" not in account["roles"]:
            raise ConsoleError("correction_role_required")
        current = self.snapshot_objects(snapshot_id)
        target = next((row for row in current if row["object_id"] == object_id), None)
        if target is None:
            raise ConsoleError("unknown_object")
        revised = create_revision(
            target,
            patch,
            actor=account["username"],
            schema_path=self.schema_path,
            ledger=self._ledger_path,
        )
        if revised.get("object_type") not in {"document", "heading"}:
            revised["object_type"] = "unclassified"
        revised.pop("confirmed_object_type", None)
        stamp_canonical_hashes(revised)
        envelope = self._envelope(snapshot_id)
        if review_path_for_klasse(envelope["class"]) != "boom":
            peers = [
                revised if row.get("object_id") == object_id else row
                for row in current
            ]
            gated = apply_admission_gate(
                peers,
                klasse=envelope["class"],
                document_version=envelope["version"],
                source_hash=envelope["sha256"],
            )
            revised = next(row for row in gated if row["object_id"] == object_id)
        history = self._load_objects(snapshot_id)
        history.append(revised)
        self._save_objects(snapshot_id, history)
        envelope["review_passes"] = {}
        envelope["clinical_rereview_required"] = True
        self._save_envelopes()
        self._bindings[snapshot_id] = invalidate_for_object(self._bindings.get(snapshot_id, []), object_id)
        self._save_bindings()
        return deepcopy(revised)

    def silently_edit_object(self, snapshot_id: str, object_id: str, _patch: dict[str, Any]) -> None:
        self._envelope(snapshot_id)
        raise ConsoleError("cannot_silently_mutate")

    def consider_publish(self, *, actor_id: str, snapshot_id: str) -> dict[str, Any]:
        self._require_role(actor_id, "publisher")
        envelope = self._envelope(snapshot_id)
        bindings = [row for row in self.object_review_bindings(snapshot_id) if row.get("valid") and row.get("decision") == "approve"]
        others = [
            row
            for row in bindings
            if row.get("reviewer_id") != envelope["uploader_account_id"]
        ]
        blockers: list[str] = []
        independence = bool(others)
        if not independence:
            blockers.append("second_named_reviewer_required")
        if not bindings:
            blockers.append("object_tuple_required")
        four_eyes_needed = False
        four_eyes_ok = True
        contracts = []
        for obj in self.snapshot_objects(snapshot_id):
            if obj.get("object_type") == "document":
                continue
            contract = publish_authorization_contract(
                obj=obj,
                bindings=bindings,
                uploader_id=envelope["uploader_account_id"],
                immutable_locator=envelope.get("immutable_storage_locator"),
                envelope_review_passes=envelope.get("review_passes"),
            )
            contracts.append(contract)
            if requires_four_eyes(obj):
                four_eyes_needed = True
                if not contract["four_eyes_satisfied"]:
                    four_eyes_ok = False
        if four_eyes_needed and not four_eyes_ok:
            blockers.append("four_eyes_required")
        if not is_g2_locator(envelope.get("immutable_storage_locator")):
            blockers.append("blocked_pending_immutable_locator")
        unique = list(dict.fromkeys(blockers))
        return {
            "snapshot_id": snapshot_id,
            "independence_satisfied": independence,
            "tuple_authorization": bool(bindings),
            "four_eyes_required": four_eyes_needed,
            "four_eyes_satisfied": four_eyes_ok if four_eyes_needed else True,
            "envelope_review_passes_authorizes": False,
            "publish_allowed": False,
            "state": envelope["state"],
            "blockers": unique,
            "g2": "BLOCKED",
            "object_contracts": contracts,
        }

    def publish(self, *, actor_id: str, snapshot_id: str) -> dict[str, Any]:
        considered = self.consider_publish(actor_id=actor_id, snapshot_id=snapshot_id)
        envelope = self._envelope(snapshot_id)
        return {
            "status": "BLOCKED",
            "state": envelope["state"],
            "snapshot_id": snapshot_id,
            "blockers": considered["blockers"] or ["blocked_pending_immutable_locator"],
            "g2": "BLOCKED",
            "cutover": False,
        }

    def live_snapshot(self, *, family: str, class_: str) -> dict[str, Any] | None:
        live = [
            envelope
            for envelope in self._envelopes.values()
            if envelope["family"] == family and envelope["class"] == class_ and envelope.get("is_live_capture")
        ]
        if not live:
            return None
        live.sort(key=lambda row: row["acquired_at"])
        return self._receipt(live[-1])

    def select_for_question(self, *, family: str, asked_class: str) -> list[dict[str, Any]]:
        if asked_class not in ALLOWED_CLASSES:
            raise ConsoleError("invalid_class")
        matching = [
            envelope
            for envelope in self._envelopes.values()
            if envelope["family"] == family and envelope["class"] == asked_class
        ]
        heavier_present = any(
            CLASS_ORDER[envelope["class"]] > CLASS_ORDER[asked_class]
            for envelope in self._envelopes.values()
            if envelope["family"] == family
        )
        if not matching and heavier_present:
            return []
        # Never fill a heavier asked class with a lighter sibling.
        out: list[dict[str, Any]] = []
        for envelope in matching:
            for obj in self.snapshot_objects(envelope["snapshot_id"]):
                out.append(
                    {
                        "snapshot_id": envelope["snapshot_id"],
                        "object_id": obj["object_id"],
                        "class": envelope["class"],
                        "family": envelope["family"],
                        "sha256": envelope["sha256"],
                    }
                )
        return out

    def list_envelopes(self) -> list[dict[str, Any]]:
        return [self._receipt(row) for row in self._envelopes.values()]

    def resolve_document(self, *, title: str, version: str, family: str) -> dict[str, Any]:
        """Map researcher-visible document identity onto the kernel snapshot."""
        wanted = (title.strip(), version.strip(), family.strip())
        matches = [
            row
            for row in self._envelopes.values()
            if (row["title"], row["version"], row["family"]) == wanted
        ]
        if not matches:
            raise ConsoleError("unknown_document")
        if len(matches) > 1:
            raise ConsoleError("document_not_unique")
        return self._receipt(matches[0])

    def move_family_document(
        self,
        *,
        actor_id: str,
        title: str,
        version: str,
        family: str,
        new_family: str,
    ) -> dict[str, Any]:
        document = self.resolve_document(title=title, version=version, family=family)
        return self.move_family(
            actor_id=actor_id,
            snapshot_id=document["snapshot_id"],
            new_family=new_family,
        )

    def promote_class_document(
        self,
        *,
        actor_id: str,
        title: str,
        version: str,
        family: str,
        new_class: str,
        reextract: bool = False,
    ) -> dict[str, Any]:
        document = self.resolve_document(title=title, version=version, family=family)
        return self.promote_class(
            actor_id=actor_id,
            snapshot_id=document["snapshot_id"],
            new_class=new_class,
            reextract=reextract,
        )

    def researcher_path(self) -> dict[str, Any]:
        return {
            "surface": "operations_console",
            "room": "ingest",
            "first_envelope_family": "continentie",
            "engineer_only_parallel_path": False,
            "product_api": "separate_machine_door",
        }
