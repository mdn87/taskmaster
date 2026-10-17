"""taskmaster.show/v1 read model and freshness evaluation (plan sections 4, 7)."""

from __future__ import annotations

from . import schema, timefmt


def evaluate(observation: dict, max_age_seconds: int, now: str) -> dict:
    """Clock-relative flags at an injected evaluation time."""
    now_t = timefmt.parse(now)
    success_at = observation["last_success_at"]
    age = None
    if success_at is not None:
        age = max(0, int((now_t - timefmt.parse(success_at)).total_seconds()))
    required_times = [s["last_checked_at"] for s in observation["sources"] if s["required"]]
    clock_skew = any(t is not None and timefmt.parse(t) > now_t for t in required_times)
    never = not observation["has_successful_observation"]
    stale = (
        never
        or success_at is None
        or observation["attempt_status"] == "failed"
        or clock_skew
        or (age is not None and age > max_age_seconds)
    )
    codes = set(observation["error_codes"])
    if clock_skew:
        codes.add("clock_skew")
    return {
        "age_seconds": age,
        "stale": stale,
        "partial": observation["attempt_status"] == "partial",
        "clock_skew": clock_skew,
        "never_successful": never,
        "status_codes": sorted(codes),
        "source_modes": sorted({s["source_mode"] for s in observation["sources"]}),
    }


def counts(content: dict | None) -> dict | None:
    if content is None:
        return None
    links = {status: 0 for status in schema.LINK_STATUSES}
    for link in content["links"]:
        links[link["status"]] += 1
    gaps: dict[str, int] = {}
    for gap in content["gaps"]:
        gaps[gap["code"]] = gaps.get(gap["code"], 0) + 1
    return {
        "refs": len(content["refs"]),
        "links": links,
        "gaps": dict(sorted(gaps.items())),
        "coverage": {c["adapter"]: c["status"] for c in content["coverage"]},
    }


def objective_view(entry: dict, max_age_seconds: int, now: str) -> dict:
    content = schema.parse_content(entry)
    return {
        "namespace": entry["namespace"],
        "id": entry["id"],
        "objective_key": entry["objective_key"],
        "label": content["objective"]["label"] if content else None,
        "trail_sha256": entry["trail_sha256"],
        "content": content,
        "observation": entry["observation"],
        "evaluation": evaluate(entry["observation"], max_age_seconds, now),
        "counts": counts(content),
    }


def show(export_doc: dict, now: str, selector: tuple[str, str] | None = None) -> dict | None:
    """Build show/v1 for all objectives or one; None when the objective is absent."""
    timefmt.parse(now)
    entries = export_doc["entries"]
    if selector is not None:
        entries = [e for e in entries if (e["namespace"], e["id"]) == selector]
        if not entries:
            return None
    return {
        "schema": schema.SHOW_SCHEMA,
        "schema_version": 1,
        "evaluated_at": now,
        "snapshot_id": export_doc["snapshot_id"],
        "published_at": export_doc["published_at"],
        "max_age_seconds": export_doc["max_age_seconds"],
        "objectives": [objective_view(e, export_doc["max_age_seconds"], now) for e in entries],
    }
