"""YAML -> validated RuleSet, plus derived rule sets for overrides."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from app.rules.models import RuleSet

DEFAULT_PATH = Path(__file__).parent / "rule_sets" / "demo_buyer_framework_v1.yaml"


class RuleSetError(ValueError):
    """Invalid rule set. At startup this aborts the app with a clear message."""


def parse_content(content: dict[str, Any]) -> RuleSet:
    try:
        return RuleSet.model_validate(content)
    except ValidationError as e:
        problems = "; ".join(f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors())
        raise RuleSetError(f"Invalid rule set: {problems}") from e


def load_yaml(path: Path | str = DEFAULT_PATH) -> RuleSet:
    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as e:
        raise RuleSetError(f"Cannot read rule set {path}: {e}") from e
    if not isinstance(raw, dict):
        raise RuleSetError(f"Rule set {path} must be a YAML mapping")
    return parse_content(raw)


def to_content(rs: RuleSet) -> dict[str, Any]:
    return json.loads(rs.model_dump_json())


def base_id(rs: RuleSet) -> str:
    slug = "".join(ch if ch.isalnum() else "_" for ch in rs.framework.lower()).strip("_")
    return f"{slug}@{rs.version.split('+')[0]}"


def derive_id(root_id: str, content: dict[str, Any]) -> str:
    digest = hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()[:8]
    return f"{root_id.split('+')[0]}+ovr-{digest}"


LABEL_MARK = " Derived with overrides:"


def apply_overrides(base: RuleSet, overrides: dict[str, dict[str, Any]], root: RuleSet | None = None) -> RuleSet:
    """Return a NEW rule set. Rule ids/types cannot be changed; unknown rules/fields are rejected.

    The label lists the differences against the ROOT rule set (not just this call), so the
    result is a pure function of the effective rules and ids stay content-addressed.
    """
    content = to_content(base)
    by_id = {r["id"]: r for r in content["rules"]}
    for rule_id, changes in overrides.items():
        if rule_id not in by_id:
            raise RuleSetError(f"Unknown rule id in overrides: {rule_id}")
        for k in changes:
            if k in {"id", "type"}:
                raise RuleSetError(f"Override of '{k}' on {rule_id} is not allowed")
            if k not in by_id[rule_id]:
                raise RuleSetError(f"Rule {rule_id} has no field '{k}'")
        by_id[rule_id].update(changes)
    root_content = to_content(root or base)
    root_by_id = {r["id"]: r for r in root_content["rules"]}
    diffs = [f"{rid}.{k}={v}" for rid, r in sorted(by_id.items()) for k, v in sorted(r.items()) if root_by_id[rid].get(k) != v]
    content["version"] = f"{base.version.split('+')[0]}+override"
    root_label = root_content["label"].split(LABEL_MARK)[0]
    content["label"] = root_label + (f"{LABEL_MARK} {', '.join(diffs)}." if diffs else "")
    return parse_content(content)
