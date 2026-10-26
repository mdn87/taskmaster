"""Start-of-session brief for one objective: facts the saved export already holds.

`taskmaster.brief/v1` is derived from one export read at an explicit clock. It
carries no disposition and no recommended next action (decision D1), and no
host path: the local CLI adds handoff note paths only when it renders text
(decision D3), so they never enter the brief or the export. Candidate links are
named as candidates, never as confirmed membership (decision D2).
"""

from __future__ import annotations

from . import readmodel, schema

SCHEMA = "taskmaster.brief/v1"
MAX_RUNS = 10
OPEN_COMPARISONS = ("new", "open")
CANDIDATE_NOTE = ("candidate means joined by revision only; no owner record confirms "
                  "the membership")
LAG_NOTE = ("This brief reads the saved export. A run that finished in the last few "
            "minutes may not be in it yet.")


def _links(content, relation):
    return [l for l in content["links"] if l["relation"] == relation]


def build(export_doc: dict, namespace: str, objective_id: str, now: str) -> dict | None:
    """The brief for one objective, or None when the export has no such objective."""
    view = readmodel.show(export_doc, now, (namespace, objective_id))
    if view is None:
        return None
    o = view["objectives"][0]
    obs, ev = o["observation"], o["evaluation"]
    content = o["content"] or {"links": [], "gaps": []}

    runs = []
    for l in _links(content, "has_run"):
        a = l["asserted"]
        runs.append({"run_id": l["to"]["id"], "membership": l["status"],
                     "dispatch_status": a.get("dispatch_status"), "delivered": a.get("delivered"),
                     "acceptance_outcome": a.get("acceptance_outcome")})
    # Autowork run ids start with their UTC start time, so the newest sort last.
    runs.sort(key=lambda r: r["run_id"], reverse=True)

    attempt_membership = {l["to"]["id"]: l["status"] for l in _links(content, "has_production_audit") if l["to"]}
    items: dict[str, dict] = {}
    for l in _links(content, "observes_finding"):
        a = l["asserted"]
        item = items.setdefault(a.get("item") or l["to"]["id"], {
            "item": a.get("item") or l["to"]["id"], "finding_key": a.get("finding_key"),
            "severity": a.get("severity"), "comparisons": set(), "attempts": set()})
        if a.get("comparison"):
            item["comparisons"].add(a["comparison"])
        item["attempts"].add(l["from"]["id"])
        item["severity"] = item["severity"] or a.get("severity")
    findings = []
    for item in items.values():
        # A baseline-only observation has no comparison and is still open.
        if item["comparisons"] and not item["comparisons"] & set(OPEN_COMPARISONS):
            continue
        memberships = {attempt_membership.get(a, "missing") for a in item["attempts"]}
        findings.append({"item": item["item"], "finding_key": item["finding_key"], "severity": item["severity"],
                         "comparisons": sorted(item["comparisons"]), "attempts": len(item["attempts"]),
                         "membership": "resolved" if "resolved" in memberships else
                         "candidate" if "candidate" in memberships else "missing"})
    findings.sort(key=lambda f: (f["severity"] or "", f["finding_key"] or "", f["item"]))

    gates = []
    for l in _links(content, "has_audit_gate"):
        if l["to"] is None:
            continue
        gates.append({"attempt": l["from"]["id"], "audit_id": l["to"]["id"],
                      "membership": attempt_membership.get(l["from"]["id"], "missing"), **l["asserted"]})
    gates.sort(key=lambda g: g["attempt"])

    counts = o["counts"] or {"links": {s: 0 for s in schema.LINK_STATUSES}, "gaps": {}}
    candidates: dict[str, int] = {}
    for l in content["links"]:
        if l["status"] == "candidate":
            candidates[l["relation"]] = candidates.get(l["relation"], 0) + 1
    return {
        "schema": SCHEMA,
        "schema_version": 1,
        "evaluated_at": now,
        "objective": {"namespace": o["namespace"], "id": o["id"], "label": o["label"]},
        "evidence": {
            "snapshot_id": view["snapshot_id"], "published_at": view["published_at"],
            "content_generated_at": obs["content_generated_at"], "last_success_at": obs["last_success_at"],
            "attempt_status": obs["attempt_status"], "max_age_seconds": view["max_age_seconds"],
            "age_seconds": ev["age_seconds"], "stale": ev["stale"], "partial": ev["partial"],
            "never_successful": ev["never_successful"], "status_codes": ev["status_codes"],
        },
        "runs": {"total": len(runs), "recent": runs[:MAX_RUNS]},
        "open_findings": findings,
        "audit_gates": gates,
        "membership": {"links": dict(counts["links"]), "candidate_links": dict(sorted(candidates.items())),
                       "gaps": dict(sorted(counts["gaps"].items()))},
    }


def _flag(membership):
    return "" if membership == "resolved" else f" [{membership}]"


def render(brief: dict, handoff_notes=()) -> str:
    """Plain text for an agent. `handoff_notes` are local (provider, path) pairs added here only."""
    o, e = brief["objective"], brief["evidence"]
    name = f"{o['namespace']}/{o['id']}"
    lines = [f"Taskmaster brief: {o['label'] or name} ({name})"]
    state = [f"attempt {e['attempt_status']}"]
    if e["stale"]:
        state.append("STALE")
    if e["partial"]:
        state.append("partial coverage")
    checked = e["last_success_at"] or "never"
    age = f", {e['age_seconds']}s ago" if e["age_seconds"] is not None else ""
    lines.append(f"Evidence: last checked {checked}{age} (max age {e['max_age_seconds']}s); "
                 f"{', '.join(state)}.")
    lines.append(f"Export published {e['published_at']}, snapshot {e['snapshot_id'][:12]}, "
                 f"evaluated {brief['evaluated_at']}.")
    lines.append(LAG_NOTE)
    if e["status_codes"]:
        lines.append("Status codes: " + ", ".join(e["status_codes"]))

    runs = brief["runs"]
    lines.append("")
    lines.append(f"Runs: {runs['total']} total" + (f", newest {len(runs['recent'])}:" if runs["recent"] else "."))
    for r in runs["recent"]:
        delivered = {True: "delivered", False: "not delivered", None: "delivery unrecorded"}[r["delivered"]]
        detail = [r["dispatch_status"] or "status unrecorded", delivered]
        if r["acceptance_outcome"]:
            detail.append(f"acceptance {r['acceptance_outcome']}")
        lines.append(f"- {r['run_id']}: {', '.join(detail)}{_flag(r['membership'])}")

    lines.append("")
    lines.append(f"Open findings: {len(brief['open_findings'])}" + (":" if brief["open_findings"] else "."))
    for f in brief["open_findings"]:
        seen = ", ".join(f["comparisons"]) or "baseline"
        lines.append(f"- {f['severity'] or 'unrated'} {f['finding_key'] or f['item']} "
                     f"({seen}; {f['attempts']} audit(s)){_flag(f['membership'])}")
    if brief["audit_gates"]:
        lines.append("Audit gates:")
        for g in brief["audit_gates"]:
            text = f"- {g['attempt'][:12]}: {g.get('record')} gate={g.get('gate')}"
            if g.get("conclusion"):
                text += f" conclusion={g['conclusion']}"
            if g.get("blocking") is not None:
                text += f" blocking={g['blocking']}"
            if g.get("waiver_reason"):
                text += f" waived: {g['waiver_reason']}"
            lines.append(text + _flag(g["membership"]))

    m = brief["membership"]
    lines.append("")
    lines.append("Links: " + " ".join(f"{s}={m['links'].get(s, 0)}" for s in schema.LINK_STATUSES))
    if m["candidate_links"]:
        lines.append("Candidate links: " + ", ".join(f"{k}={v}" for k, v in m["candidate_links"].items())
                     + f". {CANDIDATE_NOTE}.")
    lines.append("Gaps: " + (", ".join(f"{k}={v}" for k, v in m["gaps"].items()) or "none"))

    if handoff_notes:
        lines.append("")
        lines.append("Handoff notes (local):")
        for provider, path in handoff_notes:
            lines.append(f"- {provider}: {path}")
    return "\n".join(lines) + "\n"
