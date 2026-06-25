from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from ms8.engine_core.maintenance.self_check import check_specs as cs


class _CoreMissing:
    def __init__(self, memory_dir: Path) -> None:
        self.config = {"memory_dir": str(memory_dir)}
        self.auto_memory = None
        self.whoosh_search = None
        self.monitoring = None
        self.shadow = None


class _ProbeResult:
    def __init__(self, status: str, records: list[dict] | None = None, dropped: list[str] | None = None) -> None:
        self.status = status
        self.records = records or []
        self.dropped = dropped or []


class _ProbeLogger:
    def __init__(self, log_path: Path) -> None:
        self.log_path = log_path


class _ProbeRepo:
    def cleanup(self, excluded_source_prefixes: list[str], drop_rejected: bool) -> dict[str, object]:
        return {"excluded": excluded_source_prefixes, "drop_rejected": drop_rejected}


class _ProbeIndexer:
    def __init__(self) -> None:
        self.excluded_source_prefixes: list[str] = []

    def search(self, query: str, limit: int = 5) -> list[dict[str, str]]:
        if query:
            return [{"id": "probe"}]
        return []

    def cleanup_excluded(self) -> dict[str, object]:
        return {"ok": True, "excluded": list(self.excluded_source_prefixes)}


class _ProbeIndexerNoHits(_ProbeIndexer):
    def search(self, query: str, limit: int = 5) -> list[dict[str, str]]:
        _ = (query, limit)
        return []


class _ProbePipeline:
    def __init__(self, log_path: Path) -> None:
        self.repo = _ProbeRepo()
        self.indexer = _ProbeIndexer()
        self.logger = _ProbeLogger(log_path)
        self._calls = 0

    def process(self, text: str, source: str) -> _ProbeResult:
        self._calls += 1
        # First attempt rejected, second one succeeds to cover retry path.
        if self._calls == 1:
            return _ProbeResult("rejected", records=[], dropped=["noise"])
        return _ProbeResult("success", records=[{"id": "r1", "text": text, "source": source}], dropped=[])


class _CoreProbe:
    def __init__(self, memory_dir: Path, pipeline: _ProbePipeline) -> None:
        self.config = {"memory_dir": str(memory_dir)}
        self.auto_memory = SimpleNamespace(pipeline=pipeline)
        self.whoosh_search = object()
        self.monitoring = object()
        self.shadow = object()
        self.last_retrieve: dict[str, object] | None = None

    def retrieve_memories(
        self,
        query: str,
        top_k: int = 5,
        allow_semantic: bool = True,
        allow_graph: bool = True,
    ) -> list[dict[str, str | bool]]:
        self.last_retrieve = {
            "query": query,
            "top_k": top_k,
            "allow_semantic": allow_semantic,
            "allow_graph": allow_graph,
        }
        return [
            {
                "id": "r1",
                "query": query,
                "top_k": str(top_k),
                "allow_semantic": allow_semantic,
                "allow_graph": allow_graph,
            }
        ]


class _ProbePipelineAlwaysRejected(_ProbePipeline):
    def process(self, text: str, source: str) -> _ProbeResult:
        _ = (text, source)
        return _ProbeResult("rejected", records=[], dropped=["noise"])


def test_l2_pipeline_stages_missing(tmp_path: Path) -> None:
    out = cs._check_l2_pipeline_stages(_CoreMissing(tmp_path), {})
    assert out["status"] == "fail"
    assert "missing" in out["details"]


def test_l2_admission_distribution_warn_and_ok(tmp_path: Path) -> None:
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)

    class _Core:
        def __init__(self, md: Path) -> None:
            self.config = {"memory_dir": str(md)}
            self.auto_memory = None

    core = _Core(memory_dir)

    # Missing log -> skip ok
    skipped = cs._check_l2_admission_distribution(core, {})
    assert skipped["status"] == "pass"

    logf = memory_dir / "auto_memory_pipeline.log"
    rows = [
        {"admission": {"route": "accepted"}},
        {"admission": {"route": "accepted"}},
        {"admission": {"route": "rejected"}},
        {"admission": {"route": "accepted"}},
    ]
    logf.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    elevated = cs._check_l2_admission_distribution(core, {})
    assert elevated["status"] == "warn"

    rows2 = [
        {"admission": {"route": "accepted"}},
        {"admission": {"route": "accepted"}},
        {"admission": {"route": "accepted"}},
        {"admission": {"route": "accepted"}},
    ]
    logf.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows2) + "\n", encoding="utf-8")
    healthy = cs._check_l2_admission_distribution(core, {})
    assert healthy["status"] == "pass"


def test_l2_write_then_search_probe_flow(tmp_path: Path) -> None:
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    log_path = memory_dir / "auto_memory_pipeline.log"
    log_path.write_text("probe pre-existing line\n", encoding="utf-8")

    pipeline = _ProbePipeline(log_path=log_path)
    core = _CoreProbe(memory_dir=memory_dir, pipeline=pipeline)

    out = cs._check_l2_write_then_search(core, {})
    assert out["status"] == "pass"
    assert out["details"]["index_hits"] >= 1
    assert core.last_retrieve is not None
    assert core.last_retrieve["allow_semantic"] is False
    assert core.last_retrieve["allow_graph"] is False
    assert out["details"]["pipeline_status"] in {"success", "partial_success"}


def test_l2_write_then_search_warn_when_index_has_no_hits(tmp_path: Path) -> None:
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    log_path = memory_dir / "auto_memory_pipeline.log"
    log_path.write_text("probe pre-existing line\n", encoding="utf-8")

    pipeline = _ProbePipeline(log_path=log_path)
    pipeline.indexer = _ProbeIndexerNoHits()
    core = _CoreProbe(memory_dir=memory_dir, pipeline=pipeline)
    out = cs._check_l2_write_then_search(core, {})
    assert out["status"] == "warn"
    assert "not found in index" in out["message"]


def test_l2_write_then_search_fail_when_pipeline_never_succeeds(tmp_path: Path) -> None:
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    log_path = memory_dir / "auto_memory_pipeline.log"
    log_path.write_text("probe pre-existing line\n", encoding="utf-8")

    pipeline = _ProbePipelineAlwaysRejected(log_path=log_path)
    core = _CoreProbe(memory_dir=memory_dir, pipeline=pipeline)
    out = cs._check_l2_write_then_search(core, {})
    assert out["status"] == "fail"
    assert "write probe failed" in out["message"]
