from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

from app.config import Settings
from app.core.events import EventBus
from app.core.store import Repo
from app.db import Database
from app.llm.mock_provider import MockProvider

DEMO = Path(__file__).resolve().parents[2] / "demo_data"


def make_settings(tmp_path: Path, **kw) -> Settings:
    base = dict(database_url=f"sqlite:///{tmp_path / 'test.db'}", upload_dir=tmp_path / "uploads", render_dir=tmp_path / "renders", llm_provider="mock", mock_faults="")
    base.update(kw)
    return Settings(_env_file=None, **base)


@dataclass
class Env:
    settings: Settings
    db: Database
    repo: Repo
    bus: EventBus
    provider: MockProvider
    tmp: Path

    def workflow(self) -> str:
        return self.repo.create_workflow(rule_set_id="rs", provider=self.provider.name, model=self.provider.model)

    def add_doc(self, wid: str, src: Path | bytes, name: str | None = None) -> str:
        import hashlib

        data = src if isinstance(src, bytes) else Path(src).read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        folder = self.settings.upload_dir / wid
        folder.mkdir(parents=True, exist_ok=True)
        ext = "pdf"
        path = folder / f"{sha}.{ext}"
        path.write_bytes(data)
        return self.repo.add_document(
            wid=wid, original_name=name or (Path(src).name if not isinstance(src, bytes) else "doc.pdf"), stored_path=str(path), sha256=sha, mime="application/pdf", size=len(data)
        )


@pytest.fixture
def env(tmp_path) -> Env:
    s = make_settings(tmp_path)
    db = Database(s.database_url)
    db.create_all()
    yield Env(s, db, Repo(db), EventBus(db), MockProvider(), tmp_path)
    db.dispose()


@pytest.fixture
def demo() -> Path:
    return DEMO


os.environ.setdefault("CHAINAUDIT_NO_AUTOAPP", "1")

SETTLED = {"COMPLETED", "AWAITING_REVIEW", "AWAITING_EVIDENCE", "FAILED"}
CLEAN = [DEMO / "clean_supplier" / n for n in ("lab_report_BT-2047.pdf", "certificate_SUP-001.pdf", "supplier_declaration_BT-2047.pdf")]
FAILED = [DEMO / "failed_supplier" / n for n in ("lab_report_BT-2047.pdf", "certificate_SUP-002.pdf", "supplier_declaration_BT-2041.pdf")]


class Api:
    """Thin helper around TestClient for integration tests."""

    def __init__(self, client, app) -> None:
        self.client, self.app = client, app
        self.c = app.state.container

    def upload(self, paths, rule_set_id=None):
        files = [("files", (p[0], p[1], "application/octet-stream") if isinstance(p, tuple) else (Path(p).name, Path(p).read_bytes(), "application/octet-stream")) for p in paths]
        data = {"rule_set_id": rule_set_id} if rule_set_id else None
        return self.client.post("/api/evidence/upload", files=files, data=data)

    def run(self, paths, timeout=20):
        r = self.upload(paths)
        assert r.status_code == 200, r.text
        wid = r.json()["workflow_id"]
        return wid, self.wait(wid, timeout)

    def wait(self, wid, timeout=20):
        end = time.time() + timeout
        while time.time() < end:
            d = self.client.get(f"/api/workflows/{wid}").json()
            if d["status"] in SETTLED:
                return d
            time.sleep(0.05)
        raise AssertionError(f"workflow did not settle: {d['status']}")

    def events(self, wid):
        return self.client.get(f"/api/workflows/{wid}/events/history").json()

    @staticmethod
    def by_rule(detail):
        return {f["rule_id"]: f for f in detail["findings"] if not f["superseded"]}


@pytest.fixture
def make_api(tmp_path):
    from fastapi.testclient import TestClient

    from app.main import create_app

    opened = []

    def factory(**kw):
        app = create_app(make_settings(tmp_path / f"s{len(opened)}", **kw))
        cm = TestClient(app)
        client = cm.__enter__()
        opened.append(cm)
        return Api(client, app)

    yield factory
    for cm in opened:
        cm.__exit__(None, None, None)


@pytest.fixture
def api(make_api) -> Api:
    return make_api()
