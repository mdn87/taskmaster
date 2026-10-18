"""Render once, splice exact bytes, and verify the delivered morning report.

This module never reads or archives inbox items. Its body is the ordinary report
produced by the existing inbox workflow. Unconfigured use preserves it verbatim.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from . import timefmt

BEGIN = b"<!-- BEGIN TASKMASTER V1 -->\n"
END = b"<!-- END TASKMASTER V1 -->\n"
DIGEST = b"<!-- TASKMASTER SHA256 "
MAX_BLOCK = 1024 * 1024
MAX_BODY = 2 * 1024 * 1024


@dataclass(frozen=True)
class Prepared:
    block: bytes | None = None
    error: str | None = None


def render_once(export_path: Path, now: str) -> tuple[int, bytes]:
    """Bound memory use and execution time; renderer diagnostics stay private."""
    with tempfile.TemporaryFile() as out:
        result = subprocess.run(
            [sys.executable, "-m", "taskmaster", "show", "--export", str(export_path),
             "--all", "--format", "report", "--now", now],
            stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.DEVNULL,
            timeout=30, check=False,
        )
        out.seek(0)
        return result.returncode, out.read(MAX_BLOCK + 1)


def handoff_context(export_path: Path, now: str) -> bytes:
    """Read optional context once, without changing execution evidence."""
    path = export_path.with_name('handoffs.json')
    try:
        path.lstat()
    except FileNotFoundError:
        return b''
    except OSError:
        return b'\nTaskmaster handoff: unavailable\n'
    from . import handoffs
    try:
        return b'\n' + handoffs.render(handoffs.read_projection(path, now)).encode('utf-8')
    except (OSError, ValueError):
        return b'\nTaskmaster handoff: unavailable\n'


def prepare(export_path: Path | None, now: str) -> Prepared:
    if export_path is None:
        return Prepared()
    if not timefmt.is_valid(now):
        raise ValueError("invalid evaluation time")
    try:
        code, block = render_once(export_path, now)
    except subprocess.TimeoutExpired:
        return Prepared(error="renderer_timeout")
    except OSError:
        return Prepared(error="renderer_unavailable")
    if code:
        return Prepared(error={3: "export_missing", 4: "export_invalid_or_unsupported",
                               8: "export_unreadable"}.get(code, "renderer_failed"))
    if (len(block) > MAX_BLOCK or not block.startswith(b"taskmaster trail report v1\n")
            or not block.endswith(b"\n") or b"\r" in block
            or b"<!-- BEGIN TASKMASTER" in block or b"<!-- END TASKMASTER" in block):
        return Prepared(error="renderer_invalid_output")
    try:
        block.decode("utf-8", errors="strict")
    except UnicodeError:
        return Prepared(error="renderer_invalid_output")
    context = handoff_context(export_path, now)
    if (len(block) + len(context) > MAX_BLOCK or b'\r' in context
            or (context and not context.endswith(b'\n'))
            or b'<!-- BEGIN TASKMASTER' in context or b'<!-- END TASKMASTER' in context
            or DIGEST in context):
        return Prepared(error="renderer_invalid_output")
    block += context
    return Prepared(block=block)


def _status(code: str, body: bytes) -> bytes:
    return f"Taskmaster: {code}\n".encode("ascii") + body


def compose(body: bytes, prepared: Prepared) -> bytes:
    if len(body) > MAX_BODY:
        raise ValueError("report body over bounds")
    if prepared.block is None and prepared.error is None:
        return body
    # The body is narrative only. Reserved framing must come from this helper.
    if b"<!-- BEGIN TASKMASTER" in body or b"<!-- END TASKMASTER" in body or DIGEST in body:
        raise ValueError("report body contains reserved Taskmaster framing")
    if prepared.error:
        return _status(prepared.error, body)
    if prepared.block is None:
        return body
    digest = hashlib.sha256(prepared.block).hexdigest().encode("ascii")
    return BEGIN + prepared.block + END + DIGEST + digest + b" -->\n" + body


def verify_document(data: bytes, prepared: Prepared) -> bool:
    if prepared.block is None:
        return False
    if data.count(BEGIN) != 1 or data.count(END) != 1 or data.count(DIGEST) != 1:
        return False
    # Exact prefix comparison includes the immutable buffer, markers and digest.
    # It deliberately rejects CRLF conversion and never rereads the source export.
    expected = compose(b"", prepared)
    return data.startswith(expected)


def deliver(body: bytes, output: Path, prepared: Prepared) -> bool:
    expected = compose(body, prepared)
    output.write_bytes(expected)
    with output.open("rb") as handle:
        actual = handle.read(MAX_BODY + MAX_BLOCK + 1024)
    valid = actual == expected and (prepared.block is None or verify_document(actual, prepared))
    if not valid:
        fallback = _status("report_verification_failed", body)
        output.write_bytes(fallback)
        if output.read_bytes() != fallback:
            raise OSError("report verification fallback could not be delivered")
    return valid


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export", default=os.environ.get("TASKMASTER_EXPORT"))
    parser.add_argument("--now")
    parser.add_argument("--body-file", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        # Observe configured trails before consulting the inbox report body,
        # including an empty-inbox body. Unconfigured runs launch nothing.
        prepared = prepare(Path(args.export) if args.export else None, args.now or timefmt.now())
        with Path(args.body_file).open("rb") as handle:
            body = handle.read(MAX_BODY + 1)
        deliver(body, Path(args.output), prepared)
    except (OSError, ValueError):
        sys.stderr.write("Taskmaster: report_delivery_failed\n")
        return 7
    # Trail errors are bounded lines; the inbox workflow still succeeds.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
