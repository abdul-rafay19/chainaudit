"""Dependency container: built once per app (and per test)."""

from __future__ import annotations

import asyncio

from app.agents.auditor.auditor import AuditorAgent
from app.agents.compliance.evaluator import ComplianceAgent
from app.agents.corrective.drafter import CorrectiveAgent
from app.agents.extraction.extractor import ExtractionAgent
from app.config import Settings
from app.core.events import EventBus
from app.core.store import Repo
from app.db import Database
from app.llm.counting import CountingProvider
from app.llm.factory import build_provider
from app.rules.loader import base_id, load_yaml, parse_content, to_content
from app.rules.models import RuleSet


class Container:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.db = Database(settings.database_url)
        self.repo = Repo(self.db)
        self.bus = EventBus(self.db)
        self.provider = CountingProvider(build_provider(settings))
        self.extractor = ExtractionAgent(self.provider, self.repo, self.bus, settings)
        self.compliance = ComplianceAgent(self.repo, self.bus)
        self.auditor = AuditorAgent(self.repo, self.bus, self.provider, self.extractor, settings)
        self.corrective = CorrectiveAgent(self.repo, self.bus, self.provider, settings.llm_timeout_seconds)
        self._default = load_yaml()  # invalid YAML aborts startup with a clear RuleSetError
        self.default_rule_set_id = base_id(self._default)
        self._locks: dict[str, asyncio.Lock] = {}
        from app.orchestrator.workflow import Orchestrator

        self.orchestrator = Orchestrator(self)

    def startup(self) -> None:
        self.settings.upload_dir.mkdir(parents=True, exist_ok=True)
        self.settings.render_dir.mkdir(parents=True, exist_ok=True)
        self.db.create_all()
        self.repo.save_rule_set(id_=self.default_rule_set_id, name=self._default.framework, version=self._default.version, content=to_content(self._default), derived_from=None)

    def lock(self, wid: str) -> asyncio.Lock:
        return self._locks.setdefault(wid, asyncio.Lock())

    def ruleset(self, rule_set_id: str) -> RuleSet:
        row = self.repo.get_rule_set(rule_set_id)
        if row is None:
            from app.core.errors import not_found

            raise not_found("Rule set", rule_set_id)
        import json

        return parse_content(json.loads(row.content_json))
