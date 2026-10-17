"""One read-only attempt per configured objective, then the pure merge.

The trail content is derived only from owner records. Absent reverse
bindings stay gaps; nothing is inferred from timing or proximity.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json

from . import autowork, schema
from .canonical import canonical, sha256_hex
from .config import Config, Objective
from .identity import IdentityError, is_sha256_hex, validate_id, validate_token
from .merge import AttemptResult, SourceResult, merge_entry
from .sources import SourceError, footprint, load_manifest, parse_record, read_small
from .strictjson import StrictJSONError, loads

ANNOTATION_RECORD = "objective-annotation"
POLICY_RECORD = "policy"
EVIDENCE_RECORDS = ("request-summary.json", "result.json", "state.json", "subject-manifest.json")


def _verify_review_manifest(records, result, run_id):
    """Check the owner's v1 subject digest without claiming a dispatch outcome.

    Autowork hashes the captured file metadata without its derived roles or
    reviewer metadata (cross_review_subject._manifest_payload, owner 0.1.0).
    The filenames remain private; only the raw manifest digest is exported.
    """
    try:
        manifest = parse_record(records['subject-manifest.json'][1])
        if (not isinstance(manifest, dict) or type(manifest.get('version')) is not int
                or manifest['version'] != 1 or result.get('run_id') != run_id):
            raise ValueError('unsupported review manifest')
        base = {k: manifest[k] for k in ('kind', 'anchor_kind', 'anchor_paths',
                                        'target_selectors', 'exclusions')}
        if base['kind'] not in ('plan', 'implementation'):
            raise ValueError('unsupported review kind')
        for key in ('anchor_paths', 'target_selectors', 'exclusions'):
            if not isinstance(base[key], list) or not all(isinstance(v, str) for v in base[key]):
                raise ValueError('invalid subject paths')
        if not isinstance(base['anchor_kind'], str) or not isinstance(manifest['files'], list):
            raise ValueError('invalid subject metadata')
        base['files'] = []
        seen = set()
        for row in manifest['files']:
            value = {k: row[k] for k in ('path', 'byte_count', 'sha256', 'selectors')}
            if (not isinstance(value['path'], str) or not value['path'] or value['path'] in seen
                    or type(value['byte_count']) is not int or value['byte_count'] < 0
                    or not is_sha256_hex(value['sha256']) or not isinstance(value['selectors'], list)
                    or not all(isinstance(s, str) for s in value['selectors'])):
                raise ValueError('invalid captured file')
            seen.add(value['path'])
            base['files'].append(value)
        digest = sha256_hex(json.dumps(base, sort_keys=True, ensure_ascii=False,
                                       allow_nan=False, separators=(',', ':')).encode('utf-8'))
        if digest != manifest.get('manifest_sha256') or digest != result.get('manifest_sha256'):
            raise ValueError('review subject digest mismatch')
    except (KeyError, TypeError, ValueError):
        raise _Failure('required_input_invalid', 'autowork', 'invalid') from None


def _token_or_none(value) -> str | None:
    try:
        return validate_token(value, "value")
    except IdentityError:
        return None


def _hex_or_none(value) -> str | None:
    return value if is_sha256_hex(value) else None


@dataclass
class _Failure(Exception):
    code: str
    source: str  # "operator" | "autowork"
    health: str


@dataclass
class _Trail:
    refs: dict = field(default_factory=dict)
    links: list = field(default_factory=list)
    gaps: list = field(default_factory=list)

    def ref(self, owner: str, store_id: str, kind: str, ref_id: str) -> dict:
        value = {"owner": owner, "store_id": store_id, "kind": kind, "id": ref_id}
        self.refs[canonical(schema.ref_tuple(value))] = value
        return value

    def link(self, relation, src, dst, status, provenance, asserted, evidence) -> None:
        evidence = sorted({canonical(schema.evidence_key(e)): e for e in evidence}.values(),
                          key=lambda e: schema.sort_key(schema.evidence_key(e)))
        self.links.append({"relation": relation, "from": src, "to": dst, "status": status,
                           "provenance": provenance, "asserted": asserted, "evidence": evidence})

    def gap(self, code: str, affected: dict) -> None:
        self.gaps.append({"code": code, "affected_ref": affected})


def _evidence(owner, store_id, record_type, record_id, pointer, raw_sha):
    return {"source_ref": {"owner": owner, "store_id": store_id, "record_type": record_type, "record_id": record_id},
            "json_pointer": pointer, "raw_sha256": raw_sha}


def _stem(basename: str) -> str:
    return basename.rsplit(".", 1)[0]


def _record_identity(run_id: str, name: str) -> tuple[str, str]:
    if name.startswith("owner-attempts/"):
        # Slots repeat across runs. Hash a structured tuple instead of inventing
        # a delimiter encoding or exporting the owner filesystem path.
        kind = name.rsplit(".", 2)[-2]
        return "owner-attempt-" + kind, sha256_hex(canonical([run_id, name]))
    return _stem(name), run_id


def _read_annotation(objective: Objective) -> tuple[bytes, list[str]]:
    try:
        raw = read_small(objective.annotation)
    except SourceError as exc:
        raise _Failure(exc.code, "operator", "failed") from None
    try:
        doc = loads(raw, max_bytes=len(raw) or 1)
    except StrictJSONError:
        raise _Failure("required_input_invalid", "operator", "invalid") from None
    run_ids = doc.get("run_ids") if isinstance(doc, dict) else None
    if not isinstance(doc, dict) or not isinstance(doc.get("objective_id"), str) \
            or set(doc) - {"objective_id", "run_ids", "interventions"} \
            or not isinstance(run_ids, list) or not run_ids:
        raise _Failure("required_input_invalid", "operator", "invalid")
    try:
        for run_id in run_ids:
            validate_id(run_id, "run_id")
    except IdentityError:
        raise _Failure("required_input_invalid", "operator", "invalid") from None
    if len(run_ids) != len(set(run_ids)):
        raise _Failure("required_input_invalid", "operator", "invalid")
    if doc["objective_id"] != objective.id:
        raise _Failure("annotation_identity_mismatch", "operator", "invalid")
    return raw, run_ids


def _read_policy(objective: Objective) -> bytes | None:
    if objective.policy is None:
        return None
    try:
        return read_small(objective.policy.path, missing_code="optional_source_unavailable")
    except SourceError:
        return None


def _observe(config: Config, objective: Objective, run_ids: list[str]):
    """One owner observation bracketed by footprint reads. Returns (before, after, located, owner_doc, version)."""
    store = objective.run_store
    before = footprint(store.path)
    if before.skipped_dirs:
        raise _Failure("required_input_invalid", "autowork", "invalid")
    located: dict[str, str | None] = {}
    for run_id in run_ids:
        matches = before.run_dirs(run_id)
        if len(matches) > 1:
            raise _Failure("ambiguous_run_identity", "autowork", "invalid")
        located[run_id] = matches[0] if matches else None
    present = [r for r in run_ids if located[r] is not None]
    owner_doc, version = None, None
    if present:
        version = autowork.check_version(config.autowork)
        owner_doc = autowork.outcome(config.autowork, store.path, objective.id, present)
    after = footprint(store.path)
    if after.skipped_dirs:
        raise _Failure("source_changed_during_read", "autowork", "changed")
    return before, after, located, owner_doc, version


def _content(config, objective, annotation_raw, run_ids, fp, located, owner_doc, version,
             manifest_captured, policy_raw, policy_state):
    store = objective.run_store
    alias = store.alias
    trail = _Trail()
    obj_ref = trail.ref("operator", objective.namespace, "objective", objective.id)
    annotation_sha = sha256_hex(annotation_raw)
    attempts = {a["run_id"]: a for a in (owner_doc or {}).get("attempts", [])}
    policy_sha = sha256_hex(policy_raw) if policy_raw is not None else None

    autowork_inputs: dict[tuple[str, str], dict] = {}
    has_review = False

    def add_inputs(run_dir: str, run_id: str) -> dict[str, tuple[str, bytes]]:
        records = {}
        for basename, rel in fp.records_in(run_dir).items():
            sha = fp.files[rel]
            record_type, record_id = _record_identity(run_id, basename)
            autowork_inputs[(record_type, record_id)] = {"record_type": record_type, "record_id": record_id,
                                                          "raw_sha256": sha}
            records[basename] = (sha, fp.data[rel])
        return records

    for index, run_id in enumerate(run_ids):
        run_ref = trail.ref("autowork", alias, "run", run_id)
        annotation_ev = _evidence("operator", objective.namespace, ANNOTATION_RECORD, objective.id,
                                  f"/run_ids/{index}", annotation_sha)
        run_dir = located[run_id]
        if run_dir is None:
            trail.link("has_run", obj_ref, run_ref, "missing", "operator_annotated", {}, [annotation_ev])
            trail.gap("not_in_configured_store", run_ref)
            continue
        records = add_inputs(run_dir, run_id)
        attempt = attempts[run_id]
        status = _token_or_none(attempt.get("status"))
        if status is None:
            raise _Failure("required_input_invalid", "autowork", "invalid")
        acceptance = attempt.get("acceptance") if isinstance(attempt.get("acceptance"), dict) else {}
        delivered = attempt.get("delivered")
        asserted = {
            "dispatch_status": status,
            "launch": _token_or_none(attempt.get("launch")),
            "last_proven_stage": _token_or_none(attempt.get("last_proven_stage")),
            "delivered": delivered if isinstance(delivered, bool) else None,
            "acceptance_outcome": _token_or_none(acceptance.get("outcome")),
            "task_sha256": _hex_or_none(attempt.get("task_sha256")),
        }
        evidence = [annotation_ev] + [_evidence("autowork", alias, *_record_identity(run_id, name), "", raw[0])
                                      for name, raw in records.items()
                                      if name in EVIDENCE_RECORDS or name.startswith("owner-attempts/")]
        result = parse_record(records["result.json"][1]) if "result.json" in records else None
        if result and result.get('command_kind') == 'cross-review':
            _verify_review_manifest(records, result, run_id)
            has_review = True
            # A completed review is not a completed dispatch. The current owner
            # outcome reader has no cross-review delivery/acceptance contract.
            asserted = {}
        recorded_objective = result.get("objective_id") if result else None
        if isinstance(recorded_objective, str) and recorded_objective != objective.id:
            trail.link("has_run", obj_ref, run_ref, "conflict", "operator_annotated", asserted, evidence)
            trail.gap("membership_conflict", run_ref)
        else:
            trail.link("has_run", obj_ref, run_ref, "resolved", "operator_annotated", asserted, evidence)
            if not isinstance(recorded_objective, str):
                trail.gap("objective_binding_unrecorded", run_ref)

        state = parse_record(records["state.json"][1]) if "state.json" in records else None
        trail.link("has_revision", run_ref, None, "missing", None, {}, [])
        if state is not None and isinstance(state.get("source_commit"), str):
            trail.gap("repository_identity_unrecorded", run_ref)
        else:
            trail.gap("revision_unrecorded", run_ref)

        assignment = parse_record(records["agent-assignment.json"][1]) if "agent-assignment.json" in records else None
        assignment_id = assignment.get("assignment_id") if assignment else None
        try:
            assignment_id = validate_id(assignment_id, "assignment_id") if assignment_id is not None else None
        except IdentityError:
            assignment_id = None
        if assignment_id is None:
            trail.link("has_assignment", run_ref, None, "missing", None, {}, [])
            trail.gap("assignment_unrecorded", run_ref)
            continue
        assignment_ref = trail.ref("autowork", alias, "assignment", assignment_id)
        assignment_sha = records["agent-assignment.json"][0]
        trail.link("has_assignment", run_ref, assignment_ref, "resolved", "owner_recorded",
                   {name: _token_or_none(assignment.get(name)) for name in ("task_class", "route", "model", "sandbox")},
                   [_evidence("autowork", alias, "agent-assignment", run_id, "/assignment_id", assignment_sha)])
        if objective.policy is None:
            continue
        binding = assignment.get("scope_binding")
        recorded = _hex_or_none(binding.get("scope_policy_sha256")) if isinstance(binding, dict) else None
        if recorded is None:
            trail.link("uses_policy", assignment_ref, None, "missing", None, {}, [])
            trail.gap("policy_binding_absent", assignment_ref)
            continue
        policy_ref = trail.ref("policy", objective.policy.alias, "policy", recorded)
        binding_ev = _evidence("autowork", alias, "agent-assignment", run_id,
                               "/scope_binding/scope_policy_sha256", assignment_sha)
        if policy_state != "ok":
            trail.link("uses_policy", assignment_ref, policy_ref, "unavailable", "owner_recorded",
                       {"recorded_sha256": recorded, "current_sha256": None}, [binding_ev])
        elif recorded == policy_sha:
            trail.link("uses_policy", assignment_ref, policy_ref, "resolved", "owner_recorded",
                       {"recorded_sha256": recorded, "current_sha256": policy_sha},
                       [binding_ev, _evidence("policy", objective.policy.alias, POLICY_RECORD,
                                              objective.policy.alias, "", policy_sha)])
        else:
            trail.link("uses_policy", assignment_ref, policy_ref, "unsupported", "owner_recorded",
                       {"recorded_sha256": recorded, "current_sha256": policy_sha}, [binding_ev])
            trail.gap("policy_historical_lookup_unsupported", policy_ref)

    # Probe-linked runs the owner read count as inputs even outside the objective.
    for attempt in attempts.values():
        probe = attempt.get("probe") if isinstance(attempt.get("probe"), dict) else {}
        for linked in probe.get("linked_run_ids") or []:
            dirs = fp.run_dirs(linked) if isinstance(linked, str) else []
            if len(dirs) == 1:
                add_inputs(dirs[0], linked)

    for chip in objective.chips:
        chip_ref = trail.ref("dias", chip.project_id, "chip", chip.chip_id)
        raw = sha256_hex(canonical([chip.project_id, chip.chip_id, objective.namespace, objective.id]))
        trail.link("annotates_chip", obj_ref, chip_ref, "resolved", "operator_annotated", {},
                   [_evidence("taskmaster", "config", "chip_mapping", chip.chip_id, "", raw)])
        trail.gap("chip_existence_unchecked", chip_ref)

    sources = [
        {"owner": "operator", "store_id": objective.namespace, "required": True, "source_mode": "live",
         "adapter_version": schema.ADAPTER_VERSION, "owner_version": None, "owner_schema_version": None,
         "captured_at": None,
         "inputs": [{"record_type": ANNOTATION_RECORD, "record_id": objective.id, "raw_sha256": annotation_sha}]},
        {"owner": "autowork", "store_id": alias, "required": True, "source_mode": store.mode,
         "adapter_version": schema.ADAPTER_VERSION, "owner_version": version,
         "owner_schema_version": owner_doc["schema_version"] if owner_doc else None,
         "captured_at": manifest_captured if store.mode == "saved_snapshot" else None,
         "inputs": sorted(autowork_inputs.values(), key=lambda i: schema.sort_key([i["record_type"], i["record_id"]]))},
    ]
    if objective.policy is not None:
        sources.append({
            "owner": "policy", "store_id": objective.policy.alias, "required": False, "source_mode": "live",
            "adapter_version": schema.ADAPTER_VERSION, "owner_version": None, "owner_schema_version": None,
            "captured_at": None,
            "inputs": [] if policy_sha is None or policy_state != "ok" else
            [{"record_type": POLICY_RECORD, "record_id": objective.policy.alias, "raw_sha256": policy_sha}]})
    sources.sort(key=lambda s: schema.sort_key([s["owner"], s["store_id"]]))

    coverage_status = {adapter: "unsupported" for adapter in schema.COVERAGE_ADAPTERS}
    coverage_status["autowork.outcome"] = "unsupported" if has_review else "checked"
    if objective.chips:
        coverage_status["dias.chip_annotation"] = "checked"
    if objective.policy is not None:
        coverage_status["policy.current"] = "checked" if policy_state == "ok" else "unavailable"

    return {
        "schema_version": 1,
        "objective": {"namespace": objective.namespace, "id": objective.id, "label": objective.label},
        "sources": sources,
        "refs": sorted(trail.refs.values(), key=lambda r: schema.sort_key(schema.ref_tuple(r))),
        "links": sorted(trail.links, key=lambda l: schema.sort_key(schema.link_key(l))),
        "gaps": sorted({canonical(schema.gap_key(g)): g for g in trail.gaps}.values(),
                       key=lambda g: schema.sort_key(schema.gap_key(g))),
        "coverage": [{"adapter": a, "status": coverage_status[a]} for a in sorted(schema.COVERAGE_ADAPTERS)],
    }


def _over_count_bounds(content: dict) -> bool:
    limits = {"refs": schema.MAX_REFS, "links": schema.MAX_LINKS, "gaps": schema.MAX_GAPS,
              "sources": schema.MAX_SOURCES, "coverage": schema.MAX_COVERAGE}
    return any(len(content[key]) > maximum for key, maximum in limits.items()) \
        or any(len(s["inputs"]) > schema.MAX_INPUTS_PER_SOURCE for s in content["sources"]) \
        or any(len(l["evidence"]) > schema.MAX_EVIDENCE_PER_LINK for l in content["links"])


def attempt_objective(config: Config, objective: Objective, now: str) -> AttemptResult:
    store = objective.run_store
    policy = objective.policy

    def sources(operator=("ok", ()), autowork_state=("unavailable", ()), policy_state=("unavailable", ()),
                autowork_time=None):
        rows = [SourceResult("operator", objective.namespace, True, "live", operator[0], operator[1],
                             now if operator[0] == "ok" else None),
                SourceResult("autowork", store.alias, True, store.mode, autowork_state[0], autowork_state[1],
                             autowork_time)]
        if policy is not None:
            rows.append(SourceResult("policy", policy.alias, False, "live", policy_state[0], policy_state[1],
                                     now if policy_state[0] == "ok" else None))
        return tuple(rows)

    manifest_captured = None
    try:
        for round_number in range(2):
            annotation_raw, run_ids = _read_annotation(objective)
            policy_before = _read_policy(objective)
            manifest_before = load_manifest(store.manifest) if store.mode == "saved_snapshot" else None
            before, after, located, owner_doc, version = _observe(config, objective, run_ids)
            annotation_after, _ = _read_annotation(objective)
            policy_after = _read_policy(objective)
            manifest_after = load_manifest(store.manifest) if store.mode == "saved_snapshot" else None
            annotation_changed = annotation_raw != annotation_after
            required_changed = (before.signature() != after.signature() or annotation_changed
                                or manifest_before != manifest_after)
            if not required_changed and (policy_before == policy_after or round_number == 1):
                break
            if round_number == 1:
                raise _Failure("source_changed_during_read", "operator" if annotation_changed else "autowork", "changed")
        if store.mode == "saved_snapshot":
            manifest_captured, manifest_files = manifest_after
            if manifest_files != after.files:
                raise _Failure("required_input_invalid", "autowork", "invalid")
    except SourceError as exc:
        return AttemptResult("failed", None, sources(autowork_state=("failed" if exc.code == "required_source_failed"
                                                                     else "invalid", (exc.code,))))
    except _Failure as f:
        if f.source == "operator":
            return AttemptResult("failed", None, sources(operator=(f.health, (f.code,))))
        return AttemptResult("failed", None, sources(autowork_state=(f.health, (f.code,))))

    autowork_time = now if store.mode == "live" else manifest_captured
    status, policy_row, policy_state = "ok", ("ok", ()), "ok"
    if policy is not None:
        if policy_before is None or policy_after is None:
            status, policy_row, policy_state = "partial", ("unavailable", ("optional_source_unavailable",)), "unavailable"
        elif policy_before != policy_after:
            status, policy_row, policy_state = "partial", ("changed", ("source_changed_during_read",)), "changed"
    try:
        content = _content(config, objective, annotation_raw, run_ids, after, located, owner_doc, version,
                           manifest_captured, policy_after, policy_state)
    except _Failure as f:
        return AttemptResult("failed", None, sources(autowork_state=(f.health, (f.code,))))
    if _over_count_bounds(content):
        return AttemptResult("failed", None, sources(autowork_state=("ok", ())), ("over_bounds",))
    try:
        schema.validate_content(content, objective.namespace, objective.id)
    except schema.SchemaError:
        return AttemptResult("failed", None, sources(autowork_state=("invalid", ("required_input_invalid",))))
    return AttemptResult(status, content, sources(autowork_state=("ok", ()), policy_state=policy_row,
                                                  autowork_time=autowork_time))


def build_entries(config: Config, prior_doc: dict | None, now: str) -> list[dict]:
    prior = {}
    if prior_doc is not None:
        prior = {(e["namespace"], e["id"]): e for e in prior_doc["entries"]}
    entries = []
    for objective in config.objectives:
        attempt = attempt_objective(config, objective, now)
        entries.append(merge_entry(objective.namespace, objective.id, objective.binding_sha256,
                                   prior.get((objective.namespace, objective.id)), attempt, now))
    return entries
