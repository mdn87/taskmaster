"""One review action per Eastern report day, with receipts for verified files only."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from . import apu_contract, apu_transport, strictjson, watch


LEDGER_SCHEMA = "taskmaster.apu-deliveries/v1"
JOURNAL_SCHEMA = "taskmaster.apu-pending/v1"
MAX_LEDGER = 2 * 1024 * 1024
MAX_JOURNAL = 32 * 1024
DIGEST = re.compile(r"[a-f0-9]{64}\Z")
PRIORITY = {"manual-handoff": 0, "pilot-evaluation": 1, "monitoring-window": 2}
CAP_REASONS = {"discovery-file-limit", "session-limit", "source-byte-limit"}
QUALIFIERS = {
    "full": "",
    "capped": " The audit reached a collection cap.",
    "overflow": " The summary reached its cap on review references.",
    "partial": " Coverage was partial because some session evidence was unavailable.",
    "empty": " No eligible sessions were found.",
}
HEALTH = {
    "missing": "The workstation review summary is missing.",
    "invalid": "The workstation review summary is invalid.",
    "unavailable": "The workstation review summary is unavailable.",
}


def publish(path: Path, value: dict) -> None:
    """Publish a validated summary or a closed observation beside the export."""
    apu_transport.write_sidecar(path, value)


def observe(path: Path) -> dict:
    return apu_transport.read_sidecar(path, absent="disabled")


@dataclass(frozen=True)
class Choice:
    notice: str = ""
    ref: str | None = None
    kind: str | None = None
    coverage: str = "full"

    def line(self) -> bytes:
        message = self.notice
        if self.ref:
            if self.kind == "manual-handoff":
                message = f"Review a possible manual test handoff. Run `apu behavior review show {self.ref}` on the workstation."
            elif self.kind == "pilot-evaluation":
                message = f"Evaluate the pilot on the workstation with `apu behavior review evaluate` ({self.ref})."
            else:
                message = ("The monitoring window passed 30 days with no qualifying outcome. "
                           f"Run `apu behavior review show {self.ref}` on the workstation.")
            message += QUALIFIERS[self.coverage]
        if not message:
            return b""
        return f"APU review (Dias) — {message}\n".encode("utf-8")


def _coverage(cover: dict) -> str:
    skipped = cover["skipped"]
    if any(skipped.get(key, 0) for key in CAP_REASONS):
        return "capped"
    if cover["overflow"]:
        return "overflow"
    if any(count for reason, count in skipped.items() if reason != "no-completed-turn"):
        return "partial"
    if not cover["codex"] and not cover["claude-code"]:
        return "empty"
    return "full"


def select(observation: dict, now: str, shown: dict) -> Choice:
    if observation.get("status") == "disabled":
        return Choice()
    if "status" in observation:
        return Choice(notice=HEALTH[observation["status"]])

    instant = apu_contract.timestamp(now)
    local = apu_contract.eastern(instant)
    if apu_contract.timestamp(observation["attempted_at"]) > instant:
        return Choice(notice=HEALTH["invalid"])
    last = observation["last_success_at"]
    old = last is not None and instant - apu_contract.timestamp(last) > timedelta(hours=30)
    if last:
        age = ", over 30 hours ago" if old else ""
        previous = f" Last successful coverage was {last}{age}."
    else:
        previous = " No successful coverage has been recorded."
    if observation["run_status"] == "failed":
        return Choice(notice="The nightly audit failed." + previous)
    due = local.date() if local.time() >= time(4, 15) else local.date() - timedelta(days=1)
    if observation["schedule_date"] < due.isoformat():
        return Choice(notice=f"The scheduled audit for {due} was missed." + previous)
    if old:
        return Choice(notice=f"Audit coverage is over 30 hours old. Last successful coverage was {last}.")

    today = local.date().isoformat()
    for ref, receipt in shown.items():
        if receipt["date"] == today:
            return Choice(ref=ref, kind=receipt["kind"], coverage=receipt["coverage"])
    coverage = _coverage(observation["coverage"])
    eligible = (action for action in observation["actions"] if action["ref"] not in shown)
    chosen = min(eligible, key=lambda item: (PRIORITY[item["kind"]], item["ref"]), default=None)
    if chosen:
        return Choice(ref=chosen["ref"], kind=chosen["kind"], coverage=coverage)
    return Choice(notice=QUALIFIERS[coverage].strip())


def without_history(observation: dict, now: str) -> Choice:
    """Keep an independent health notice even if the optional delivery state fails."""
    try:
        choice = select(observation, now, {})
        if choice.notice or observation.get("status") == "disabled":
            return choice
    except (OSError, ValueError, OverflowError):
        pass
    return Choice(notice="Delivery history is unavailable.")


def _bytes(path: Path, limit: int) -> bytes:
    watch.plain(path)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError("invalid delivery file")
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("delivery file exceeds limit")
    return raw


def _load(path: Path, limit: int) -> dict:
    return strictjson.loads(_bytes(path, limit), max_bytes=limit)


def _save(path: Path, doc: dict, limit: int) -> None:
    raw = (json.dumps(doc, separators=(",", ":"), ensure_ascii=True) + "\n").encode("ascii")
    if len(raw) > limit:
        raise ValueError("delivery state exceeds limit")
    watch.atomic_bytes(path, raw)


def _validate_receipt(ref: str, receipt: dict) -> None:
    if (not isinstance(ref, str) or not apu_contract.REF.fullmatch(ref)
            or not isinstance(receipt, dict) or set(receipt) != {"date", "kind", "coverage", "sha256"}
            or receipt["kind"] not in tuple(PRIORITY) or receipt["coverage"] not in tuple(QUALIFIERS)
            or not isinstance(receipt["sha256"], str) or not DIGEST.fullmatch(receipt["sha256"])
            or not isinstance(receipt["date"], str) or date.fromisoformat(receipt["date"]).isoformat() != receipt["date"]):
        raise ValueError("invalid delivery receipt")


def ledger_path(sidecar: Path) -> Path:
    return sidecar.with_name("apu-deliveries.json")


def journal_path(sidecar: Path) -> Path:
    return sidecar.with_name("apu-deliveries.pending.json")


def read_shown(sidecar: Path) -> dict:
    try:
        doc = _load(ledger_path(sidecar), MAX_LEDGER)
    except FileNotFoundError:
        return {}
    if (not isinstance(doc, dict) or set(doc) != {"schema", "shown"}
            or doc["schema"] != LEDGER_SCHEMA or not isinstance(doc["shown"], dict)):
        raise ValueError("invalid delivery ledger")
    seen_dates = set()
    for ref, receipt in doc["shown"].items():
        _validate_receipt(ref, receipt)
        if receipt["date"] in seen_dates:
            raise ValueError("multiple actions for one report date")
        seen_dates.add(receipt["date"])
    return doc["shown"]


def stage(sidecar: Path, output: Path, choice: Choice, now: str, document: bytes) -> dict:
    """Durably record intent before the caller writes any report bytes."""
    receipt = {"date": apu_contract.eastern(apu_contract.timestamp(now)).date().isoformat(),
               "kind": choice.kind, "coverage": choice.coverage,
               "sha256": hashlib.sha256(document).hexdigest()}
    pending = {"schema": JOURNAL_SCHEMA, "output": str(output.absolute()),
               "ref": choice.ref, "receipt": receipt}
    _save(journal_path(sidecar), pending, MAX_JOURNAL)
    return pending


def confirm(sidecar: Path, shown: dict, pending: dict) -> None:
    ref, receipt = pending["ref"], pending["receipt"]
    if shown.get(ref) != receipt:
        if ((ref in shown and shown[ref]["date"] != receipt["date"])
                or any(item["date"] == receipt["date"] for other, item in shown.items() if other != ref)):
            raise ValueError("conflicting delivery receipt")
        _save(ledger_path(sidecar), {"schema": LEDGER_SCHEMA, "shown": {**shown, ref: receipt}}, MAX_LEDGER)
        shown[ref] = receipt
    journal_path(sidecar).unlink(missing_ok=True)


def recover(sidecar: Path, shown: dict, max_report: int, verify) -> None:
    """Resolve the previous writer's intent under the same OS lock as a new delivery."""
    path = journal_path(sidecar)
    try:
        pending = _load(path, MAX_JOURNAL)
    except FileNotFoundError:
        return
    if (not isinstance(pending, dict) or set(pending) != {"schema", "output", "ref", "receipt"}
            or pending["schema"] != JOURNAL_SCHEMA or not isinstance(pending["output"], str)
            or not Path(pending["output"]).is_absolute()):
        raise ValueError("invalid pending delivery")
    ref, receipt = pending["ref"], pending["receipt"]
    _validate_receipt(ref, receipt)
    if shown.get(ref) != receipt:
        try:
            actual = _bytes(Path(pending["output"]), max_report)
        except (OSError, ValueError):
            actual = b""
        choice = Choice(ref=ref, kind=receipt["kind"], coverage=receipt["coverage"])
        if hashlib.sha256(actual).hexdigest() != receipt["sha256"] or not verify(actual, choice.line()):
            # Lack of proof leaves the ref eligible in the current summary.
            path.unlink(missing_ok=True)
            return
    confirm(sidecar, shown, pending)
