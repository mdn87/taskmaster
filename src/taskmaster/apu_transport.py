"""Transport the bounded APU summary separately from the workstation context."""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
from pathlib import Path
import queue
import stat
import subprocess
import sys
import threading
import time
from zoneinfo import ZoneInfoNotFoundError

from . import apu_contract, publication, watch
from .strictjson import loads


CAPTURE = "apu-review/v1"
OBSERVATION_SCHEMA = "taskmaster.apu-observation/v1"
STATUSES = ("missing", "invalid", "unavailable", "disabled")
DEFAULT_INTERVAL = 900
CAPTURE_TIMEOUT = 8
MAX_BUNDLE_CHARS = 4 * ((apu_contract.MAX_BYTES + 2) // 3)


def observation(status: str) -> dict:
    if status not in STATUSES:
        raise ValueError("invalid APU observation status")
    return {"schema": OBSERVATION_SCHEMA, "status": status}


def _validate(value: object, *, allow_observation: bool) -> dict:
    if allow_observation and isinstance(value, dict) and value.get("schema") == OBSERVATION_SCHEMA:
        if set(value) != {"schema", "status"}:
            raise ValueError("invalid APU observation fields")
        return observation(value["status"])
    try:
        return apu_contract.validate(value)
    except ZoneInfoNotFoundError:
        raise OSError("review timezone unavailable") from None


def encode(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode("ascii")


def _decode(raw: bytes, *, allow_observation: bool) -> dict:
    return _validate(loads(raw, max_bytes=apu_contract.MAX_BYTES), allow_observation=allow_observation)


def parse_wire(raw: bytes) -> dict:
    """Accept only a bounded summary or an exact closed observation from a peer."""
    try:
        return _decode(raw, allow_observation=True)
    except (ValueError, OverflowError):
        return observation("invalid")
    except OSError:
        return observation("unavailable")


def _read_bytes(path: Path) -> bytes:
    watch.plain(path)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("invalid APU file")
        return stream.read(apu_contract.MAX_BYTES + 1)


def _state_home() -> Path:
    # Match APU's resolve_state_home without installing APU on the collector.
    override = os.environ.get("APU_HOME")
    if override:
        value = Path(override).expanduser()
    elif sys.platform.startswith("win"):
        value = Path(os.environ.get("LOCALAPPDATA", "")).expanduser() / "apu"
    elif os.environ.get("XDG_STATE_HOME"):
        value = Path(os.environ["XDG_STATE_HOME"]).expanduser() / "apu"
    else:
        value = Path.home() / ".local" / "state" / "apu"
    if not value.is_absolute():
        raise ValueError("APU state directory must be absolute")
    return value


def capture(request: dict) -> dict:
    """Read the single private APU summary; the peer cannot choose a path."""
    if request != {"capture": CAPTURE}:
        return observation("invalid")
    try:
        root = _state_home()
    except (OSError, ValueError, RuntimeError):
        return observation("unavailable")
    try:
        review = root / "behavior" / "review"
        for directory in (root, review.parent, review):
            watch.plain(directory, directory=True)
        return _decode(_read_bytes(review / "summary.json"), allow_observation=False)
    except FileNotFoundError:
        return observation("missing")
    except (ValueError, OverflowError):
        return observation("invalid")
    except OSError:
        return observation("unavailable")


def _mirror_path(value) -> Path:
    if not isinstance(value, (str, Path)):
        raise ValueError("invalid APU mirror path")
    path = Path(value)
    if not path.is_absolute() or path.name != "apu-review.json":
        raise ValueError("invalid APU mirror path")
    return path


def read_sidecar(path: Path, *, absent: str = "missing") -> dict:
    """Return the local observation; renderers may treat physical absence as disabled."""
    path = _mirror_path(path)
    if absent not in ("missing", "disabled"):
        raise ValueError("invalid missing-file status")
    try:
        return parse_wire(_read_bytes(path))
    except FileNotFoundError:
        return observation(absent)
    except ValueError:
        return observation("invalid")
    except OSError:
        return observation("unavailable")


def write_sidecar(path: Path, value: dict) -> dict:
    """Validate again before atomically replacing the published mirror."""
    path = _mirror_path(path)
    try:
        value = _validate(value, allow_observation=True)
    except (ValueError, OverflowError):
        value = observation("invalid")
    except OSError:
        value = observation("unavailable")
    watch.atomic_bytes(path, encode(value))
    return value


def bundle_field(path: Path) -> str:
    """Encode a validated optional sidecar for the host-export bundle."""
    try:
        value = read_sidecar(path, absent="disabled")
    except ValueError:
        value = observation("invalid")
    return base64.b64encode(encode(value)).decode("ascii")


def parse_bundle_field(value: object) -> dict:
    """Decode a bounded Base64 field without affecting the ordinary export."""
    if not isinstance(value, str) or not 1 <= len(value) <= MAX_BUNDLE_CHARS:
        return observation("invalid")
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        return observation("invalid")
    if base64.b64encode(raw).decode("ascii") != value:
        return observation("invalid")
    return parse_wire(raw)


def _request(command: list[str]) -> dict:
    # Drain at most 8 KiB plus one sentinel byte even if the peer never closes
    # stdout. The worker owns the pipe handles, including on a timed-out pipe.
    bound = apu_contract.MAX_BYTES + 1
    payload = encode({"capture": CAPTURE})
    replies = queue.Queue(maxsize=1)
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    def exchange():
        try:
            with process.stdout as output:
                with process.stdin as request_stream:
                    request_stream.write(payload)
                raw = output.read(bound)
        except OSError:
            raw = None
        replies.put(raw)

    try:
        deadline = time.monotonic() + CAPTURE_TIMEOUT
        threading.Thread(target=exchange, daemon=True).start()
        raw = replies.get(timeout=CAPTURE_TIMEOUT)
        if raw is None:
            return observation("unavailable")
        if len(raw) == bound:
            return observation("invalid")
        if process.wait(timeout=max(0, deadline - time.monotonic())):
            return observation("unavailable")
        return parse_wire(raw)
    except (queue.Empty, subprocess.TimeoutExpired):
        return observation("unavailable")
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()


def _due(path: Path, interval: int, config_path: Path) -> bool:
    try:
        written = path.lstat()
        age = time.time() - written.st_mtime
        if 0 <= age < interval:
            value = _decode(_read_bytes(path), allow_observation=True)
            # Re-enabling or correcting local settings takes effect immediately.
            # A disabled/invalid response from a peer still observes the interval.
            return (value.get("status") in ("disabled", "invalid")
                    and config_path.stat().st_mtime_ns > written.st_mtime_ns)
    except (OSError, ValueError, OverflowError):
        pass
    return True


def _mark(path: Path, status: str) -> None:
    value = observation(status)
    try:
        if _read_bytes(path) == encode(value):
            return
    except (OSError, ValueError):
        pass
    write_sidecar(path, value)


def poll(options, command: list[str], context_path: Path, config_path: Path) -> None:
    """Sample under a separate lock and cadence after publishing the ordinary context."""
    if not isinstance(options, dict):
        return
    try:
        output = _mirror_path(options.get("output"))
        if output.resolve() == context_path.resolve():
            return
        output.parent.mkdir(parents=True, exist_ok=True)
        with publication.writer_lock(output):
            enabled = options.get("enabled", True)
            interval = options.get("interval_seconds", DEFAULT_INTERVAL)
            if (set(options) - {"output", "enabled", "interval_seconds"} or type(enabled) is not bool
                    or type(interval) is not int or not 60 <= interval <= 86400):
                _mark(output, "invalid")
            elif not enabled:
                _mark(output, "disabled")
            elif _due(output, interval, config_path):
                try:
                    value = _request(command)
                except OSError:
                    value = observation("unavailable")
                write_sidecar(output, value)
    except (OSError, ValueError, RuntimeError, publication.PublicationError):
        # A competing sampler owns the refresh, or this optional output cannot
        # be written. Neither condition changes the primary observation/status.
        return


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Read or update a bounded Taskmaster APU mirror")
    parser.add_argument("mode", choices=("read", "write"))
    parser.add_argument("--file", required=True)
    args = parser.parse_args(argv)
    try:
        path = _mirror_path(args.file)
        if args.mode == "read":
            value = read_sidecar(path)
        else:
            value = write_sidecar(path, parse_wire(sys.stdin.buffer.read(apu_contract.MAX_BYTES + 1)))
        code = 0
    except ValueError:
        value, code = observation("invalid"), 1
    except OSError:
        value, code = observation("unavailable"), 1
    sys.stdout.buffer.write(encode(value))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
