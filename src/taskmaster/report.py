"""Deterministic morning-report block renderer (plan section 7).

Output is UTF-8 with LF line endings. The delivered-report helper that splices
this block between markers and verifies it is Batch 2.
"""

from __future__ import annotations

from .identity import display
from .schema import LINK_STATUSES


def _yn(flag: bool) -> str:
    return "yes" if flag else "no"


def render(view: dict) -> bytes:
    lines = [
        "taskmaster trail report v1",
        f"evaluated_at: {view['evaluated_at']}",
        f"snapshot_id: {view['snapshot_id']}",
        f"published_at: {view['published_at']}",
        f"max_age_seconds: {view['max_age_seconds']}",
        f"objectives: {len(view['objectives'])}",
    ]
    for obj in view["objectives"]:
        ev = obj["evaluation"]
        obs = obj["observation"]
        lines.append(f"objective: namespace={display(obj['namespace'])} id={display(obj['id'])}")
        if obj["label"] is not None:
            lines.append(f"  label: {display(obj['label'])}")
        lines.append(f"  objective_key: {obj['objective_key']}")
        lines.append(f"  trail_sha256: {obj['trail_sha256'] or 'none'}")
        lines.append(
            f"  attempt: {obs['attempt_status']} stale={_yn(ev['stale'])} partial={_yn(ev['partial'])}"
            f" never_successful={_yn(ev['never_successful'])} clock_skew={_yn(ev['clock_skew'])}"
        )
        age = "unknown" if ev["age_seconds"] is None else str(ev["age_seconds"])
        lines.append(f"  last_success_at: {obs['last_success_at'] or 'unknown'} age_seconds: {age}")
        lines.append(f"  last_attempt_at: {obs['last_attempt_at']}")
        lines.append(f"  status_codes: {' '.join(ev['status_codes']) or 'none'}")
        for src in obs["sources"]:
            lines.append(
                f"  source: {src['owner']}/{display(src['store_id'])} mode={src['source_mode']}"
                f" required={_yn(src['required'])} health={src['health']}"
                f" checked={src['last_checked_at'] or 'unknown'}"
            )
        counts = obj["counts"]
        if counts is None:
            lines.append("  content: none")
            continue
        lines.append("  links: " + " ".join(f"{s}={counts['links'][s]}" for s in LINK_STATUSES))
        gaps = " ".join(f"{code}={n}" for code, n in counts["gaps"].items())
        lines.append(f"  gaps: {gaps or 'none'}")
        cov = " ".join(f"{a}={s}" for a, s in counts["coverage"].items())
        lines.append(f"  coverage: {cov}")
    return ("\n".join(lines) + "\n").encode("utf-8")
