"""OS-held single-writer refresh and atomic publication of the complete export."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from . import builder, export

REPLACE_DELAYS = (.1, .2, .4, .8)


class PublicationError(Exception):
    def __init__(self, code: str, reason: str):
        super().__init__(f"{code}: {reason}")
        self.code = code
        self.exit_code = {"busy": 6, "publication_failed": 7}[code]


def lock_path(path: Path) -> Path:
    return path.with_name(path.name + ".lock")


@contextmanager
def writer_lock(path: Path):
    """Hold one byte on Windows, or flock on POSIX; never unlink the lock file."""
    fd = os.open(lock_path(path), os.O_CREAT | os.O_RDWR, 0o600)
    held = False
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                raise PublicationError("busy", "another writer holds the export lock") from None
            raise
        held = True
        # Initialize only after acquiring ownership: competing first writers
        # must never write the byte before one has obtained the lock.
        if os.fstat(fd).st_size == 0:
            os.write(fd, b"\0")
        yield
    finally:
        try:
            if held:
                if os.name == "nt":
                    import msvcrt
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _unique(path: Path, suffix: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return path.with_name(f"{path.name}.{stamp}.{uuid.uuid4().hex}.{suffix}")


def _diagnostic(path: Path, exc: Exception) -> None:
    """Local-only detail, never copied into exports or bounded CLI errors."""
    directory = path.parent / ".taskmaster-diagnostics"
    try:
        directory.mkdir(mode=0o700, exist_ok=True)
        target = _unique(directory / "refresh", "json")
        data = json.dumps({"code": getattr(exc, "code", "publication_failed"),
                           "error_type": type(exc).__name__, "detail": str(exc),
                           "export": str(path.absolute())}, ensure_ascii=True).encode("ascii")
        fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
    except OSError:
        # An unwritable directory may also prevent the diagnostic; preserve the
        # original failure and do not make another write location implicit.
        pass


def preserve_invalid(path: Path) -> Path:
    """Preserve even oversized invalid inputs with bounded-memory exact copying."""
    target = _unique(path, "invalid")
    digest = hashlib.sha256()
    fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "wb") as destination, path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            destination.write(chunk)
            digest.update(chunk)
        destination.flush()
        os.fsync(destination.fileno())
    copied = hashlib.sha256()
    with target.open("rb") as preserved:
        while chunk := preserved.read(1024 * 1024):
            copied.update(chunk)
    if copied.digest() != digest.digest():
        raise OSError("invalid-export preservation verification failed")
    return target


def publish(path: Path, data: bytes) -> None:
    """Flush a unique sibling, then replace without truncating the live file."""
    export.verify(data)
    temporary = _unique(path, "tmp")
    fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    for attempt in range(len(REPLACE_DELAYS) + 1):
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            if attempt == len(REPLACE_DELAYS):
                raise
            time.sleep(REPLACE_DELAYS[attempt])
    # Abandoned siblings are intentionally retained; cleanup is a separate action.


def refresh(cfg, path: Path, now: str, *, recover_invalid_export: bool = False) -> dict:
    """Refresh under a lock. The CLI validates config/time before entering here."""
    path = Path(path)
    try:
        with writer_lock(path):
            prior = None
            try:
                prior, _ = export.load(path)
            except export.ExportError as exc:
                if exc.code == "export_missing":
                    pass
                elif exc.code == "invalid_export":
                    if not recover_invalid_export:
                        raise export.ExportError("existing_export_invalid", exc.reason) from None
                    preserve_invalid(path)
                else:
                    raise
            try:
                entries = builder.build_entries(cfg, prior, now)
                data = export.assemble(entries, now, cfg.max_age_seconds)
                publish(path, data)
                return export.verify(data)
            except export.ExportError as exc:
                _diagnostic(path, exc)
                raise PublicationError("publication_failed", "built export failed validation; see local diagnostics") from None
    except (export.ExportError, PublicationError) as exc:
        _diagnostic(path, exc)
        raise
    except Exception as exc:
        _diagnostic(path, exc)
        raise PublicationError("publication_failed", "refresh could not publish; see local diagnostics") from None
