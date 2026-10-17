"""Command line: refresh, build, show, verify-export (plan section 7).

Exit codes: 0 ok, 2 config/arguments, 3 export or objective missing,
4 invalid/unsupported export, 5 an objective failed, 8 export unreadable.
Codes 6 (writer busy) and 7 (publication failed) describe atomic refresh errors.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import builder, config as config_mod, export, publication, readmodel, report, timefmt, watch

EXIT_OK, EXIT_CONFIG, EXIT_MISSING, EXIT_INVALID, EXIT_OBJECTIVE_FAILED = 0, 2, 3, 4, 5


def _write(data: bytes) -> None:
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


def _err(message: str) -> None:
    sys.stderr.write(f"taskmaster: {message}\n")


def _now(value: str | None) -> str:
    if value is None:
        return timefmt.now()
    if not timefmt.is_valid(value):
        raise config_mod.ConfigError("--now must be YYYY-MM-DDTHH:MM:SSZ")
    return value


def _json_bytes(value) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True) + "\n").encode("ascii")


def cmd_build(args) -> int:
    cfg = config_mod.load(args.config)
    now = _now(args.now)
    prior = export.load(Path(args.prior))[0] if args.prior else None
    entries = builder.build_entries(cfg, prior, now)
    _write(export.assemble(entries, now, cfg.max_age_seconds))
    failed = [e for e in entries if e["observation"]["attempt_status"] == "failed"]
    for e in failed:
        _err(f"objective {e['namespace']}/{e['id']} failed: {' '.join(e['observation']['error_codes'])}")
    return EXIT_OBJECTIVE_FAILED if failed else EXIT_OK


def cmd_show(args) -> int:
    doc, _ = export.load(Path(args.export))
    now = _now(args.now)
    selector = None if args.all else (args.namespace, args.id)
    view = readmodel.show(doc, now, selector)
    if view is None:
        _err("objective not found in export")
        return EXIT_MISSING
    _write(report.render(view) if args.format == "report" else _json_bytes(view))
    return EXIT_OK


def cmd_refresh(args) -> int:
    cfg = config_mod.load(args.config)
    now = _now(args.now)
    if cfg.watch is not None:
        settings = watch.load_settings(cfg.watch, cfg)
        if Path(args.export).resolve() != Path(settings['export']).resolve():
            raise config_mod.ConfigError('watch refresh must use its configured shared export')
        result = watch.tick(args.config, cfg.watch, now=now, recover_invalid_export=args.recover_invalid_export)
        _write(_json_bytes(result))
        return EXIT_OBJECTIVE_FAILED if result['failed_objectives'] else EXIT_OK
    doc = publication.refresh(cfg, Path(args.export), now,
                              recover_invalid_export=args.recover_invalid_export)
    failed = [e for e in doc["entries"] if e["observation"]["attempt_status"] == "failed"]
    _write(_json_bytes({"published": True, "snapshot_id": doc["snapshot_id"],
                        "published_at": doc["published_at"], "entries": len(doc["entries"]),
                        "failed_objectives": len(failed)}))
    for entry in failed:
        _err(f"objective {entry['namespace']}/{entry['id']} failed: "
             f"{' '.join(entry['observation']['error_codes'])}")
    return EXIT_OBJECTIVE_FAILED if failed else EXIT_OK


def cmd_verify(args) -> int:
    doc, data = export.load(Path(args.export))
    _write(_json_bytes({"ok": True, "sha256": export.file_sha256(data), "snapshot_id": doc["snapshot_id"],
                        "entries": len(doc["entries"]), "published_at": doc["published_at"]}))
    return EXIT_OK


def cmd_watch(args) -> int:
    cfg = config_mod.load(args.config)
    settings = args.settings or cfg.watch
    if settings is None:
        raise config_mod.ConfigError('watch settings are not configured')
    if args.once:
        result = watch.tick(args.config, settings)
        _write(_json_bytes(result))
        return EXIT_OBJECTIVE_FAILED if result['failed_objectives'] else EXIT_OK
    watch.serve(args.config, settings)
    return EXIT_OK


def cmd_slash(args) -> int:
    from .commands import execute
    value = execute(args.config, args.text)
    if isinstance(value, str):
        _write(value.encode('utf-8'))
        return EXIT_OK
    _write(_json_bytes(value))
    return EXIT_OBJECTIVE_FAILED if value.get('failed_objectives', 0) else EXIT_OK


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="taskmaster", description="Read-only objective trail index.")
    sub = p.add_subparsers(dest="command", required=True)

    w = sub.add_parser('watch', help='continuously capture verified runs and refresh consumers')
    w.add_argument('--config', required=True)
    w.add_argument('--settings')
    w.add_argument('--once', action='store_true')
    w.set_defaults(func=cmd_watch)

    slash = sub.add_parser('slash', help='run a /taskmaster status, sessions, use, clear, auto or refresh command')
    slash.add_argument('--config', required=True)
    slash.add_argument('text')
    slash.set_defaults(func=cmd_slash)

    r = sub.add_parser("refresh", help="observe and atomically publish the configured objectives")
    r.add_argument("--config", required=True)
    r.add_argument("--export", required=True)
    r.add_argument("--now")
    r.add_argument("--recover-invalid-export", action="store_true")
    r.set_defaults(func=cmd_refresh)

    b = sub.add_parser("build", help="observe configured objectives and write an export to stdout")
    b.add_argument("--config", required=True)
    b.add_argument("--now", help="evaluation time, YYYY-MM-DDTHH:MM:SSZ (default: system clock)")
    b.add_argument("--prior", help="previous export to merge against")
    b.set_defaults(func=cmd_build)

    s = sub.add_parser("show", help="read model or report block for an export")
    s.add_argument("--export", required=True)
    target = s.add_mutually_exclusive_group(required=True)
    target.add_argument("--all", action="store_true")
    target.add_argument("--namespace")
    s.add_argument("--id")
    fmt = s.add_mutually_exclusive_group()
    fmt.add_argument("--json", dest="format", action="store_const", const="json")
    fmt.add_argument("--format", choices=("json", "report"))
    s.add_argument("--now")
    s.set_defaults(func=cmd_show, format="json")

    v = sub.add_parser("verify-export", help="run the reader acceptance rules on an export")
    v.add_argument("--export", required=True)
    v.set_defaults(func=cmd_verify)
    return p


def main(argv: list[str] | None = None) -> int:
    p = parser()
    args = p.parse_args(argv)
    if args.command == "show" and not args.all and not args.id:
        p.error("--namespace requires --id")
    if args.command == "show" and args.all and args.id:
        p.error("--all excludes --id")
    try:
        return args.func(args)
    except (config_mod.ConfigError, watch.WatchError) as exc:
        _err(f"config: {exc}")
        return EXIT_CONFIG
    except export.ExportError as exc:
        _err(str(exc))
        return exc.exit_code
    except publication.PublicationError as exc:
        _err(str(exc))
        return exc.exit_code
    except OSError as exc:
        # Configuration reads must never leak a traceback or local path.
        _err(f"config: {type(exc).__name__}")
        return EXIT_CONFIG


if __name__ == "__main__":
    sys.exit(main())
