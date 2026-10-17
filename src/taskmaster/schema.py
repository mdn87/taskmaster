"""Closed v1 schemas, frozen enum tables, bounds and validators (plan sections 3-4).

Every reader goes through `validate_entry`/`validate_content`, so the agent
CLI, the report renderer and (in Batch 2) Dias apply one acceptance set.
"""

from __future__ import annotations

from typing import Any

from . import timefmt
from .canonical import CanonicalError, canonical, objective_key, sha256_hex, sort_key
from .identity import (
    IdentityError,
    is_sha256_hex,
    validate_id,
    validate_label,
    validate_pointer,
    validate_token,
)

EXPORT_SCHEMA = "taskmaster.export/v1"
SHOW_SCHEMA = "taskmaster.show/v1"
ADAPTER_VERSION = "1"

# Bounds (section 3).
MAX_EXPORT_BYTES = 8 * 1024 * 1024
MAX_OBJECTIVES = 64
MAX_CONTENT_BYTES = 64 * 1024
MAX_REFS = 2048
MAX_LINKS = 4096
MAX_GAPS = 4096
MAX_SOURCES = 32
MAX_COVERAGE = 32
MAX_INPUTS_PER_SOURCE = 2048
MAX_EVIDENCE_PER_LINK = 64
MAX_ENTRY_BYTES = 96 * 1024
MAX_ENTRY_CONTENT_FRAME = 80 * 1024
OBSERVATION_RESERVE = MAX_ENTRY_BYTES - MAX_ENTRY_CONTENT_FRAME
MAX_ENVELOPE_OVERHEAD = 16 * 1024
MAX_ENTRY_CODES = 32
MAX_SOURCE_CODES = 8

OWNERS = ("autowork", "dias", "git", "lugos-audit", "operator", "policy", "taskmaster")
KINDS = ("assignment", "audit_receipt", "chip", "objective", "policy", "revision", "run")
RELATIONS = ("annotates_chip", "has_assignment", "has_audit_candidate", "has_revision", "has_run", "uses_policy")
LINK_STATUSES = ("candidate", "conflict", "missing", "resolved", "unavailable", "unsupported")
PROVENANCE = ("operator_annotated", "owner_recorded")
SOURCE_MODES = ("live", "saved_snapshot")
COVERAGE_STATUSES = ("checked", "unavailable", "unsupported")
ATTEMPT_STATUSES = ("failed", "ok", "partial")
SOURCE_HEALTH = ("changed", "failed", "invalid", "ok", "unavailable", "unsupported")

COVERAGE_ADAPTERS = (
    "agent-mail.board",
    "autowork.outcome",
    "dias.chip_annotation",
    "lugos-audit.receipt",
    "policy.current",
    "roundtable.session",
    "torc.lineage",
)

# Frozen gap-code table: code -> what the affected reference is.
GAP_CODES = {
    "assignment_unrecorded": "run",
    "audit_subject_incomplete": "run",
    "chip_existence_unchecked": "chip",
    "membership_conflict": "run",
    "not_in_configured_store": "run",
    "objective_binding_unrecorded": "run",
    "policy_binding_absent": "assignment",
    "policy_historical_lookup_unsupported": "policy",
    "repository_identity_unrecorded": "run",
    "revision_unrecorded": "run",
    "validator_unavailable": "run",
}

# Frozen observation error-code table (entry and per-source).
ERROR_CODES = (
    "ambiguous_run_identity",
    "annotation_identity_mismatch",
    "config_changed",
    "evidence_time_unknown",
    "optional_source_invalid",
    "optional_source_unavailable",
    "optional_source_unsupported",
    "over_bounds",
    "owner_version_mismatch",
    "required_input_invalid",
    "required_schema_unsupported",
    "required_source_failed",
    "source_changed_during_read",
)
STATUS_ONLY_CODES = ("clock_skew",)

# Relation -> (from kind, to kind, allowed asserted fields and their types).
_TOKEN = "token"
_TOKEN_OR_NULL = "token?"
_BOOL_OR_NULL = "bool?"
_HEX_OR_NULL = "hex?"
RELATION_RULES: dict[str, tuple[str, str, dict[str, str]]] = {
    "has_run": ("objective", "run", {
        "dispatch_status": _TOKEN,
        "launch": _TOKEN_OR_NULL,
        "last_proven_stage": _TOKEN_OR_NULL,
        "delivered": _BOOL_OR_NULL,
        "acceptance_outcome": _TOKEN_OR_NULL,
        "task_sha256": _HEX_OR_NULL,
    }),
    "has_assignment": ("run", "assignment", {
        "task_class": _TOKEN_OR_NULL,
        "route": _TOKEN_OR_NULL,
        "model": _TOKEN_OR_NULL,
        "sandbox": _TOKEN_OR_NULL,
    }),
    "uses_policy": ("assignment", "policy", {
        "recorded_sha256": _HEX_OR_NULL,
        "current_sha256": _HEX_OR_NULL,
    }),
    "has_revision": ("run", "revision", {}),
    "has_audit_candidate": ("run", "audit_receipt", {}),
    "annotates_chip": ("objective", "chip", {}),
}


class SchemaError(ValueError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _fail(reason: str) -> None:
    raise SchemaError(reason)


def _keys(obj: Any, required: set[str], what: str, optional: set[str] = frozenset()) -> dict:
    if not isinstance(obj, dict):
        _fail(f"{what}: not an object")
    keys = set(obj)
    if not required <= keys or not keys <= required | optional:
        _fail(f"{what}: unexpected or missing fields")
    return obj


def _list(obj: Any, what: str, limit: int) -> list:
    if not isinstance(obj, list):
        _fail(f"{what}: not an array")
    if len(obj) > limit:
        _fail(f"{what}: over bounds")
    return obj


def _int(value: Any, what: str, low: int, high: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
        _fail(f"{what}: invalid integer")
    return value


def _enum(value: Any, allowed: tuple[str, ...] | dict, what: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        _fail(f"{what}: value outside the closed enum")
    return value


def _time(value: Any, what: str, nullable: bool = True) -> str | None:
    if value is None and nullable:
        return None
    if not timefmt.is_valid(value):
        _fail(f"{what}: invalid time")
    return value


def _hex(value: Any, what: str, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not is_sha256_hex(value):
        _fail(f"{what}: invalid sha256")
    return value


def _ident(value: Any, what: str) -> str:
    try:
        return validate_id(value, what)
    except IdentityError as exc:
        _fail(str(exc))


def _tok(value: Any, what: str) -> str:
    try:
        return validate_token(value, what)
    except IdentityError as exc:
        _fail(str(exc))


def _sorted_unique(items: list, key_fn, what: str) -> None:
    keys = [sort_key(key_fn(item)) for item in items]
    for a, b in zip(keys, keys[1:]):
        if a >= b:
            _fail(f"{what}: not sorted by semantic key or has duplicates")


def _codes(value: Any, what: str, limit: int, allowed: tuple[str, ...] = ERROR_CODES) -> list[str]:
    codes = _list(value, what, limit)
    for code in codes:
        _enum(code, allowed, what)
    if codes != sorted(set(codes)):
        _fail(f"{what}: codes must be a sorted duplicate-free set")
    return codes


# --- references --------------------------------------------------------------

def ref_tuple(ref: dict) -> list[str]:
    return [ref["owner"], ref["store_id"], ref["kind"], ref["id"]]


def validate_ref(ref: Any, what: str) -> dict:
    _keys(ref, {"owner", "store_id", "kind", "id"}, what)
    _enum(ref["owner"], OWNERS, f"{what}.owner")
    _ident(ref["store_id"], f"{what}.store_id")
    _enum(ref["kind"], KINDS, f"{what}.kind")
    _ident(ref["id"], f"{what}.id")
    return ref


def _source_ref_tuple(ref: dict) -> list[str]:
    return [ref["owner"], ref["store_id"], ref["record_type"], ref["record_id"]]


def evidence_key(ev: dict) -> list:
    return [_source_ref_tuple(ev["source_ref"]), ev["json_pointer"], ev["raw_sha256"]]


def link_key(link: dict) -> list:
    return [link["relation"], ref_tuple(link["from"]), ref_tuple(link["to"]) if link["to"] else None]


def gap_key(gap: dict) -> list:
    return [gap["code"], ref_tuple(gap["affected_ref"])]


def _validate_evidence(ev: Any, what: str) -> None:
    _keys(ev, {"source_ref", "json_pointer", "raw_sha256"}, what)
    sr = _keys(ev["source_ref"], {"owner", "store_id", "record_type", "record_id"}, f"{what}.source_ref")
    _enum(sr["owner"], OWNERS, f"{what}.source_ref.owner")
    _ident(sr["store_id"], f"{what}.source_ref.store_id")
    _tok(sr["record_type"], f"{what}.source_ref.record_type")
    _ident(sr["record_id"], f"{what}.source_ref.record_id")
    try:
        validate_pointer(ev["json_pointer"], f"{what}.json_pointer")
    except IdentityError as exc:
        _fail(str(exc))
    _hex(ev["raw_sha256"], f"{what}.raw_sha256")


def _validate_asserted(asserted: Any, allowed: dict[str, str], what: str) -> None:
    if not isinstance(asserted, dict) or not set(asserted) <= set(allowed):
        _fail(f"{what}: fields outside the relation allowlist")
    for name, value in asserted.items():
        rule = allowed[name]
        if rule.endswith("?") and value is None:
            continue
        if rule.startswith("token"):
            _tok(value, f"{what}.{name}")
        elif rule.startswith("bool"):
            if not isinstance(value, bool):
                _fail(f"{what}.{name}: not a boolean")
        elif rule.startswith("hex"):
            _hex(value, f"{what}.{name}")


# --- trail content -------------------------------------------------------------

def validate_content(content: Any, namespace: str, objective_id: str) -> dict:
    """Closed schema plus ordering, uniqueness and endpoint invariants."""
    _keys(content, {"schema_version", "objective", "sources", "refs", "links", "gaps", "coverage"}, "content")
    if content["schema_version"] != 1 or isinstance(content["schema_version"], bool):
        _fail("content.schema_version must be 1")
    obj = _keys(content["objective"], {"namespace", "id", "label"}, "content.objective")
    if obj["namespace"] != namespace or obj["id"] != objective_id:
        _fail("content objective disagrees with the entry tuple")
    if obj["label"] is not None:
        try:
            validate_label(obj["label"], "content.objective.label")
        except IdentityError as exc:
            _fail(str(exc))

    sources = _list(content["sources"], "content.sources", MAX_SOURCES)
    for i, s in enumerate(sources):
        w = f"content.sources[{i}]"
        _keys(s, {"owner", "store_id", "required", "source_mode", "adapter_version", "owner_version",
                  "owner_schema_version", "captured_at", "inputs"}, w)
        _enum(s["owner"], OWNERS, f"{w}.owner")
        _ident(s["store_id"], f"{w}.store_id")
        if not isinstance(s["required"], bool):
            _fail(f"{w}.required: not a boolean")
        _enum(s["source_mode"], SOURCE_MODES, f"{w}.source_mode")
        _tok(s["adapter_version"], f"{w}.adapter_version")
        if s["owner_version"] is not None:
            _tok(s["owner_version"], f"{w}.owner_version")
        if s["owner_schema_version"] is not None:
            _int(s["owner_schema_version"], f"{w}.owner_schema_version", 1, 1_000_000)
        _time(s["captured_at"], f"{w}.captured_at")
        if s["source_mode"] == "live" and s["captured_at"] is not None:
            _fail(f"{w}: captured_at applies only to saved_snapshot sources")
        inputs = _list(s["inputs"], f"{w}.inputs", MAX_INPUTS_PER_SOURCE)
        for j, item in enumerate(inputs):
            _keys(item, {"record_type", "record_id", "raw_sha256"}, f"{w}.inputs[{j}]")
            _tok(item["record_type"], f"{w}.inputs[{j}].record_type")
            _ident(item["record_id"], f"{w}.inputs[{j}].record_id")
            _hex(item["raw_sha256"], f"{w}.inputs[{j}].raw_sha256")
        _sorted_unique(inputs, lambda x: [x["record_type"], x["record_id"]], f"{w}.inputs")
    _sorted_unique(sources, lambda s: [s["owner"], s["store_id"]], "content.sources")

    refs = _list(content["refs"], "content.refs", MAX_REFS)
    for i, r in enumerate(refs):
        validate_ref(r, f"content.refs[{i}]")
    _sorted_unique(refs, ref_tuple, "content.refs")
    ref_set = {canonical(ref_tuple(r)) for r in refs}
    objective_ref = ["operator", namespace, "objective", objective_id]
    if canonical(objective_ref) not in ref_set:
        _fail("content.refs: objective reference missing")

    def endpoint(ref: Any, what: str, kind: str) -> None:
        validate_ref(ref, what)
        if ref["kind"] != kind:
            _fail(f"{what}: wrong endpoint kind for relation")
        if canonical(ref_tuple(ref)) not in ref_set:
            _fail(f"{what}: endpoint not in refs")

    links = _list(content["links"], "content.links", MAX_LINKS)
    for i, link in enumerate(links):
        w = f"content.links[{i}]"
        _keys(link, {"relation", "from", "to", "status", "provenance", "asserted", "evidence"}, w)
        relation = _enum(link["relation"], RELATIONS, f"{w}.relation")
        from_kind, to_kind, allowed = RELATION_RULES[relation]
        endpoint(link["from"], f"{w}.from", from_kind)
        if link["to"] is not None:
            endpoint(link["to"], f"{w}.to", to_kind)
        status = _enum(link["status"], LINK_STATUSES, f"{w}.status")
        if status in ("resolved", "candidate", "conflict") and link["to"] is None:
            _fail(f"{w}: {status} link needs a target")
        if link["provenance"] is not None:
            _enum(link["provenance"], PROVENANCE, f"{w}.provenance")
        _validate_asserted(link["asserted"], allowed, f"{w}.asserted")
        evidence = _list(link["evidence"], f"{w}.evidence", MAX_EVIDENCE_PER_LINK)
        for j, ev in enumerate(evidence):
            _validate_evidence(ev, f"{w}.evidence[{j}]")
        _sorted_unique(evidence, evidence_key, f"{w}.evidence")
        if status == "resolved" and not evidence:
            _fail(f"{w}: resolved link without evidence")
    _sorted_unique(links, link_key, "content.links")

    gaps = _list(content["gaps"], "content.gaps", MAX_GAPS)
    for i, gap in enumerate(gaps):
        w = f"content.gaps[{i}]"
        _keys(gap, {"code", "affected_ref"}, w)
        code = _enum(gap["code"], GAP_CODES, f"{w}.code")
        endpoint(gap["affected_ref"], f"{w}.affected_ref", GAP_CODES[code])
    _sorted_unique(gaps, gap_key, "content.gaps")

    coverage = _list(content["coverage"], "content.coverage", MAX_COVERAGE)
    for i, cov in enumerate(coverage):
        _keys(cov, {"adapter", "status"}, f"content.coverage[{i}]")
        _enum(cov["adapter"], COVERAGE_ADAPTERS, f"content.coverage[{i}].adapter")
        _enum(cov["status"], COVERAGE_STATUSES, f"content.coverage[{i}].status")
    _sorted_unique(coverage, lambda c: [c["adapter"]], "content.coverage")
    if [c["adapter"] for c in coverage] != sorted(COVERAGE_ADAPTERS):
        _fail("content.coverage: must list every v1 adapter exactly once")
    return content


# --- observation ---------------------------------------------------------------

def validate_observation(obs: Any, has_content: bool) -> dict:
    _keys(obs, {"content_generated_at", "last_attempt_at", "last_success_at", "has_successful_observation",
                "attempt_status", "error_codes", "binding_sha256", "sources"}, "observation")
    _time(obs["content_generated_at"], "observation.content_generated_at")
    _time(obs["last_attempt_at"], "observation.last_attempt_at", nullable=False)
    _time(obs["last_success_at"], "observation.last_success_at")
    if not isinstance(obs["has_successful_observation"], bool):
        _fail("observation.has_successful_observation: not a boolean")
    status = _enum(obs["attempt_status"], ATTEMPT_STATUSES, "observation.attempt_status")
    _codes(obs["error_codes"], "observation.error_codes", MAX_ENTRY_CODES)
    _hex(obs["binding_sha256"], "observation.binding_sha256")
    sources = _list(obs["sources"], "observation.sources", MAX_SOURCES)
    for i, s in enumerate(sources):
        w = f"observation.sources[{i}]"
        _keys(s, {"owner", "store_id", "required", "source_mode", "last_checked_at", "health", "codes"}, w)
        _enum(s["owner"], OWNERS, f"{w}.owner")
        _ident(s["store_id"], f"{w}.store_id")
        if not isinstance(s["required"], bool):
            _fail(f"{w}.required: not a boolean")
        _enum(s["source_mode"], SOURCE_MODES, f"{w}.source_mode")
        _time(s["last_checked_at"], f"{w}.last_checked_at")
        _enum(s["health"], SOURCE_HEALTH, f"{w}.health")
        _codes(s["codes"], f"{w}.codes", MAX_SOURCE_CODES)
    _sorted_unique(sources, lambda s: [s["owner"], s["store_id"]], "observation.sources")

    if has_content:
        if not obs["has_successful_observation"] or obs["content_generated_at"] is None:
            _fail("observation: content requires a successful observation and generation time")
        required_times = [s["last_checked_at"] for s in sources if s["required"]]
        expected = None if (not required_times or None in required_times) else min(required_times)
        if obs["last_success_at"] != expected:
            _fail("observation.last_success_at must be the minimum required checked time")
        unknown = expected is None
        if unknown != ("evidence_time_unknown" in obs["error_codes"]):
            _fail("observation: evidence_time_unknown must track an unknown required evidence time")
    else:
        if obs["has_successful_observation"] or obs["content_generated_at"] is not None \
                or obs["last_success_at"] is not None or status != "failed":
            _fail("observation: null content requires a failed attempt and no success metadata")
    if status == "ok" and any(c.startswith("optional_") for c in obs["error_codes"]):
        _fail("observation: optional-source codes require a partial or failed attempt")
    return obs


# --- entries and export --------------------------------------------------------

def entry_size(entry: dict) -> int:
    return len(canonical(entry))


def validate_entry(entry: Any, index: int = 0) -> dict:
    w = f"entries[{index}]"
    _keys(entry, {"namespace", "id", "objective_key", "content_json", "trail_sha256", "observation"}, w)
    namespace = _ident(entry["namespace"], f"{w}.namespace")
    objective_id = _ident(entry["id"], f"{w}.id")
    if entry["objective_key"] != objective_key(namespace, objective_id):
        _fail(f"{w}.objective_key does not match the objective tuple")
    content_json = entry["content_json"]
    if content_json is None:
        if entry["trail_sha256"] is not None:
            _fail(f"{w}: null content with a trail hash")
    else:
        if not isinstance(content_json, str):
            _fail(f"{w}.content_json: not a string")
        raw = content_json.encode("utf-8", "surrogatepass")
        if len(raw) > MAX_CONTENT_BYTES:
            _fail(f"{w}.content_json: over bounds")
        if any(b < 0x20 or b > 0x7E for b in raw):
            _fail(f"{w}.content_json: not printable ASCII")
        if entry["trail_sha256"] != sha256_hex(raw):
            _fail(f"{w}.trail_sha256 does not match content bytes")
    validate_observation(entry["observation"], content_json is not None)
    if entry_size(entry) > MAX_ENTRY_BYTES:
        _fail(f"{w}: encoded entry over bounds")
    return entry


def parse_content(entry: dict) -> dict | None:
    """Strictly parse and validate an entry's embedded content."""
    from .strictjson import StrictJSONError, loads

    if entry["content_json"] is None:
        return None
    try:
        content = loads(entry["content_json"].encode("ascii"), max_bytes=MAX_CONTENT_BYTES)
    except StrictJSONError as exc:
        _fail(f"content_json: {exc.reason}")
    return validate_content(content, entry["namespace"], entry["id"])


def assert_canonical_content(content: dict) -> str:
    """Writer obligation: the stored string is exactly C(content)."""
    try:
        return canonical(content).decode("ascii")
    except CanonicalError as exc:
        _fail(f"content outside canonical domain: {exc}")
