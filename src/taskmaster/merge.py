"""Pure per-objective entry merge (plan sections 3-4, TM-R21-05).

prior entry + attempt result + configured binding -> next entry. No I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import schema
from .canonical import objective_key, sha256_hex


@dataclass(frozen=True)
class SourceResult:
    owner: str
    store_id: str
    required: bool
    source_mode: str
    health: str
    codes: tuple[str, ...] = ()
    # Only meaningful when health == "ok": the evidence time to record
    # (attempt time for live sources, manifest capture time or None for saved).
    checked_at: str | None = None


@dataclass(frozen=True)
class AttemptResult:
    status: str  # ok | partial | failed
    content: dict | None
    sources: tuple[SourceResult, ...]
    codes: tuple[str, ...] = field(default=())


def _content_frame_size(namespace: str, objective_id: str, content_json: str) -> int:
    frame = {
        "namespace": namespace,
        "id": objective_id,
        "objective_key": objective_key(namespace, objective_id),
        "content_json": content_json,
        "trail_sha256": "0" * 64,
        "observation": None,
    }
    return schema.entry_size(frame)


def check_bounds(namespace: str, objective_id: str, content: dict) -> str | None:
    """Return the canonical content string, or None if it is over bounds."""
    if (len(content["refs"]) > schema.MAX_REFS or len(content["links"]) > schema.MAX_LINKS
            or len(content["gaps"]) > schema.MAX_GAPS or len(content["sources"]) > schema.MAX_SOURCES):
        return None
    text = schema.assert_canonical_content(content)
    if len(text) > schema.MAX_CONTENT_BYTES:
        return None
    if _content_frame_size(namespace, objective_id, text) > schema.MAX_ENTRY_CONTENT_FRAME:
        return None
    return text


def _sorted_codes(codes) -> list[str]:
    out = sorted(set(codes))
    for code in out:
        if code not in schema.ERROR_CODES:
            raise ValueError(f"unknown error code {code}")
    return out[: schema.MAX_ENTRY_CODES]


def merge_entry(
    namespace: str,
    objective_id: str,
    binding_sha256: str,
    prior: dict | None,
    attempt: AttemptResult,
    now: str,
) -> dict:
    """Build the next export entry for one objective."""
    binding_matches = prior is not None and prior["observation"]["binding_sha256"] == binding_sha256
    prior_sources = {}
    if binding_matches:
        prior_sources = {(s["owner"], s["store_id"]): s for s in prior["observation"]["sources"]}

    status = attempt.status
    codes = set(attempt.codes)
    for s in attempt.sources:
        codes.update(s.codes)

    content_json = None
    if status in ("ok", "partial"):
        if attempt.content is None:
            raise ValueError("a successful attempt needs content")
        content_json = check_bounds(namespace, objective_id, attempt.content)
        if content_json is None:
            status = "failed"
            codes.add("over_bounds")

    def source_rows(success: bool) -> list[dict]:
        rows = []
        for s in attempt.sources:
            key = (s.owner, s.store_id)
            if success and s.health == "ok":
                checked = s.checked_at
            else:
                checked = prior_sources.get(key, {}).get("last_checked_at")
            rows.append({
                "owner": s.owner,
                "store_id": s.store_id,
                "required": s.required,
                "source_mode": s.source_mode,
                "last_checked_at": checked,
                "health": s.health,
                "codes": sorted(set(s.codes))[: schema.MAX_SOURCE_CODES],
            })
        rows.sort(key=lambda r: schema.sort_key([r["owner"], r["store_id"]]))
        return rows

    def last_success(rows: list[dict]) -> str | None:
        times = [r["last_checked_at"] for r in rows if r["required"]]
        if not times or None in times:
            return None
        return min(times)

    if status in ("ok", "partial"):
        trail = sha256_hex(content_json.encode("ascii"))
        if binding_matches and prior["trail_sha256"] == trail:
            content_json = prior["content_json"]  # keep exact prior bytes
            generated = prior["observation"]["content_generated_at"]
        else:
            generated = now
        rows = source_rows(success=True)
        success_at = last_success(rows)
        if success_at is None:
            codes.add("evidence_time_unknown")
        observation = {
            "content_generated_at": generated,
            "last_attempt_at": now,
            "last_success_at": success_at,
            "has_successful_observation": True,
            "attempt_status": status,
            "error_codes": _sorted_codes(codes),
            "binding_sha256": binding_sha256,
            "sources": rows,
        }
        return _entry(namespace, objective_id, content_json, trail, observation)

    # Failed attempt: retain prior content only under the same binding.
    if prior is not None and not binding_matches:
        codes.add("config_changed")
    rows = source_rows(success=False)
    if binding_matches and prior["content_json"] is not None:
        success_at = prior["observation"]["last_success_at"]
        if success_at is None:
            codes.add("evidence_time_unknown")
        observation = {
            "content_generated_at": prior["observation"]["content_generated_at"],
            "last_attempt_at": now,
            "last_success_at": success_at,
            "has_successful_observation": True,
            "attempt_status": "failed",
            "error_codes": _sorted_codes(codes),
            "binding_sha256": binding_sha256,
            "sources": rows,
        }
        return _entry(namespace, objective_id, prior["content_json"], prior["trail_sha256"], observation)

    codes.discard("evidence_time_unknown")
    observation = {
        "content_generated_at": None,
        "last_attempt_at": now,
        "last_success_at": None,
        "has_successful_observation": False,
        "attempt_status": "failed",
        "error_codes": _sorted_codes(codes),
        "binding_sha256": binding_sha256,
        "sources": [{**r, "last_checked_at": None} for r in rows],
    }
    return _entry(namespace, objective_id, None, None, observation)


def _entry(namespace: str, objective_id: str, content_json: str | None, trail: str | None, observation: dict) -> dict:
    entry = {
        "namespace": namespace,
        "id": objective_id,
        "objective_key": objective_key(namespace, objective_id),
        "content_json": content_json,
        "trail_sha256": trail,
        "observation": observation,
    }
    schema.validate_entry(entry)
    return entry


def worst_case_observation_size() -> int:
    """Largest observation frame the closed schema allows for v1 sources."""
    long_id = "x" * 128
    observation = {
        "content_generated_at": "2026-01-01T00:00:00Z",
        "last_attempt_at": "2026-01-01T00:00:00Z",
        "last_success_at": "2026-01-01T00:00:00Z",
        "has_successful_observation": True,
        "attempt_status": "partial",
        "error_codes": sorted(schema.ERROR_CODES),
        "binding_sha256": "0" * 64,
        "sources": [
            {
                "owner": "lugos-audit",
                "store_id": long_id + str(i),
                "required": True,
                "source_mode": "saved_snapshot",
                "last_checked_at": "2026-01-01T00:00:00Z",
                "health": "unavailable",
                "codes": sorted(schema.ERROR_CODES)[: schema.MAX_SOURCE_CODES],
            }
            for i in range(3)
        ],
    }
    from .canonical import canonical

    return len(canonical({"observation": observation}))
