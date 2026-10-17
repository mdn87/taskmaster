"""TOML configuration: declared objectives, stores, policies and chip mappings.

Paths are relative to the configuration file. Resolved paths take part only
in each objective's binding digest; they never appear in exported data.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .canonical import canonical, sha256_hex
from .identity import IdentityError, validate_id, validate_label, validate_token

RESERVED_ALIASES = {"config"}
DEFAULT_MAX_AGE = 86400
MAX_OBJECTIVES = 64


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Store:
    alias: str
    owner: str
    path: Path
    mode: str
    manifest: Path | None


@dataclass(frozen=True)
class Policy:
    alias: str
    path: Path
    mode: str


@dataclass(frozen=True)
class Chip:
    project_id: str
    chip_id: str


@dataclass(frozen=True)
class Objective:
    namespace: str
    id: str
    annotation: Path
    run_store: Store
    policy: Policy | None
    label: str | None
    chips: tuple[Chip, ...]
    binding_sha256: str = ""


@dataclass(frozen=True)
class OwnerCommand:
    argv: tuple[str, ...]
    version: str
    timeout_seconds: int


@dataclass
class Config:
    path: Path
    max_age_seconds: int
    autowork: OwnerCommand
    objectives: list[Objective] = field(default_factory=list)
    watch: Path | None = None


def _private(path: Path) -> str:
    return os.path.normcase(os.path.abspath(path))


def _resolve(base: Path, value: Any, what: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{what} must be a non-empty path string")
    path = Path(value)
    return path if path.is_absolute() else (base / path)


def _table(raw: dict, key: str, allowed: set[str], required: set[str]) -> None:
    extra = set(raw) - allowed
    if extra:
        raise ConfigError(f"{key}: unknown keys {sorted(extra)}")
    missing = required - set(raw)
    if missing:
        raise ConfigError(f"{key}: missing keys {sorted(missing)}")


def binding_digest(objective: Objective) -> str:
    """Hash of the non-secret configuration that binds one objective.

    Computed without reading the annotation or any owner evidence.
    """
    store = objective.run_store
    policy = objective.policy
    value = {
        "objective": [objective.namespace, objective.id],
        "annotation": _private(objective.annotation),
        "run_store": {
            "alias": store.alias,
            "owner": store.owner,
            "path": _private(store.path),
            "mode": store.mode,
            "manifest": _private(store.manifest) if store.manifest else None,
        },
        "policy": None
        if policy is None
        else {"alias": policy.alias, "path": _private(policy.path), "mode": policy.mode, "required": False},
        "chips": sorted([c.project_id, c.chip_id] for c in objective.chips),
        "label": objective.label,
    }
    return sha256_hex(canonical(value))


def load(path: str | os.PathLike) -> Config:
    config_path = Path(path)
    try:
        raw = tomllib.loads(config_path.read_bytes().decode("utf-8"))
    except FileNotFoundError:
        raise ConfigError("configuration file not found") from None
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"configuration is not valid TOML: {exc}") from None
    try:
        return _build(config_path, raw)
    except IdentityError as exc:
        raise ConfigError(str(exc)) from None


def _build(config_path: Path, raw: dict) -> Config:
    base = config_path.parent
    _table(raw, "root", {"schema_version", "max_age_seconds", "autowork", "stores", "policies", "objectives", "chips", "watch"},
           {"schema_version", "autowork", "stores", "objectives"})
    if raw["schema_version"] != 1 or isinstance(raw["schema_version"], bool):
        raise ConfigError("schema_version must be 1")
    max_age = raw.get("max_age_seconds", DEFAULT_MAX_AGE)
    if not isinstance(max_age, int) or isinstance(max_age, bool) or not 1 <= max_age <= 31_536_000:
        raise ConfigError("max_age_seconds must be an integer from 1 to 31536000")

    aw = raw["autowork"]
    _table(aw, "autowork", {"command", "version", "timeout_seconds"}, {"command", "version"})
    command = aw["command"]
    if not isinstance(command, list) or not command or not all(isinstance(a, str) and a for a in command):
        raise ConfigError("autowork.command must be a non-empty list of strings")
    first = command[0]
    if "/" in first or "\\" in first:
        first = str(_resolve(base, first, "autowork.command[0]"))
    timeout = aw.get("timeout_seconds", 120)
    if not isinstance(timeout, int) or isinstance(timeout, bool) or not 1 <= timeout <= 3600:
        raise ConfigError("autowork.timeout_seconds must be 1..3600")
    owner = OwnerCommand((first, *command[1:]), validate_token(aw["version"], "autowork.version"), timeout)

    aliases: set[str] = set()

    def claim(alias: Any, what: str) -> str:
        alias = validate_token(alias, what)
        if alias in RESERVED_ALIASES or alias in aliases:
            raise ConfigError(f"{what}: alias {alias!r} is reserved or already used")
        aliases.add(alias)
        return alias

    stores: dict[str, Store] = {}
    for i, s in enumerate(raw["stores"]):
        _table(s, f"stores[{i}]", {"alias", "owner", "path", "mode", "manifest"}, {"alias", "owner", "path", "mode"})
        alias = claim(s["alias"], f"stores[{i}].alias")
        if s["owner"] != "autowork":
            raise ConfigError(f"stores[{i}].owner must be 'autowork' in v1")
        if s["mode"] not in ("live", "saved_snapshot"):
            raise ConfigError(f"stores[{i}].mode must be live or saved_snapshot")
        manifest = s.get("manifest")
        if s["mode"] == "saved_snapshot" and manifest is None:
            raise ConfigError(f"stores[{i}]: a saved_snapshot store needs a manifest")
        stores[alias] = Store(alias, "autowork", _resolve(base, s["path"], f"stores[{i}].path"), s["mode"],
                              _resolve(base, manifest, f"stores[{i}].manifest") if manifest else None)

    policies: dict[str, Policy] = {}
    for i, p in enumerate(raw.get("policies", [])):
        _table(p, f"policies[{i}]", {"alias", "path", "required"}, {"alias", "path"})
        if p.get("required", False) is not False:
            raise ConfigError(f"policies[{i}]: policy evidence is optional in v1")
        alias = claim(p["alias"], f"policies[{i}].alias")
        policies[alias] = Policy(alias, _resolve(base, p["path"], f"policies[{i}].path"), "live")

    chip_rows: dict[tuple[str, str], list[Chip]] = {}
    seen_chips: dict[tuple[str, str], tuple[str, str]] = {}
    for i, c in enumerate(raw.get("chips", [])):
        _table(c, f"chips[{i}]", {"project_id", "chip_id", "namespace", "objective"},
               {"project_id", "chip_id", "namespace", "objective"})
        chip = Chip(validate_id(c["project_id"], f"chips[{i}].project_id"), validate_id(c["chip_id"], f"chips[{i}].chip_id"))
        target = (validate_id(c["namespace"], f"chips[{i}].namespace"), validate_id(c["objective"], f"chips[{i}].objective"))
        key = (chip.project_id, chip.chip_id)
        if key in seen_chips:
            raise ConfigError(f"chips[{i}]: chip is already mapped")
        seen_chips[key] = target
        chip_rows.setdefault(target, []).append(chip)

    objectives: list[Objective] = []
    seen: set[tuple[str, str]] = set()
    namespaces: set[str] = set()
    for i, o in enumerate(raw["objectives"]):
        _table(o, f"objectives[{i}]", {"namespace", "id", "annotation", "run_store", "policy", "label"},
               {"namespace", "id", "annotation", "run_store"})
        namespace = validate_id(o["namespace"], f"objectives[{i}].namespace")
        validate_token(namespace, f"objectives[{i}].namespace")
        objective_id = validate_id(o["id"], f"objectives[{i}].id")
        if (namespace, objective_id) in seen:
            raise ConfigError(f"objectives[{i}]: duplicate objective")
        seen.add((namespace, objective_id))
        namespaces.add(namespace)
        store = stores.get(o["run_store"])
        if store is None:
            raise ConfigError(f"objectives[{i}].run_store names no configured store")
        policy = None
        if "policy" in o:
            policy = policies.get(o["policy"])
            if policy is None:
                raise ConfigError(f"objectives[{i}].policy names no configured policy")
        label = validate_label(o["label"], f"objectives[{i}].label") if "label" in o else None
        chips = tuple(sorted(chip_rows.pop((namespace, objective_id), []), key=lambda c: (c.project_id, c.chip_id)))
        objective = Objective(namespace, objective_id, _resolve(base, o["annotation"], f"objectives[{i}].annotation"),
                              store, policy, label, chips)
        objectives.append(Objective(**{**objective.__dict__, "binding_sha256": binding_digest(objective)}))

    if chip_rows:
        raise ConfigError("a chip mapping names an objective that is not configured")
    if len(objectives) > MAX_OBJECTIVES:
        raise ConfigError("more than 64 objectives configured")
    clash = namespaces & aliases
    if clash:
        raise ConfigError(f"objective namespaces collide with store aliases: {sorted(clash)}")
    from . import merge, schema

    if merge.worst_case_observation_size() > schema.OBSERVATION_RESERVE:
        raise ConfigError("worst-case observation exceeds the entry reserve")
    return Config(config_path, max_age, owner, objectives,
                  _resolve(base, raw['watch'], 'watch') if 'watch' in raw else None)
