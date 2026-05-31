from __future__ import annotations

import json
from datetime import datetime, timezone

from ms8.engine_core.core import MemoryCore


class _SynthOK:
    def __init__(self, count: int = 2):
        self.count = count

    def generate_candidates(self, limit: int = 5):
        return [{"candidate_id": f"c{i}"} for i in range(min(limit, self.count))]

    def list_reasoning_candidates(self, limit: int = 2):
        return [
            {"candidate_id": "cid-1", "statement": "alpha\nbeta"},
            {"candidate_id": "cid-2", "statement": "gamma"},
        ][:limit]


class _SynthErr:
    def generate_candidates(self, limit: int = 5):
        raise RuntimeError("boom-generate")

    def list_reasoning_candidates(self, limit: int = 2):
        raise RuntimeError("boom-list")


def _mk_core(tmp_path):
    c = MemoryCore.__new__(MemoryCore)
    c.config = {
        "memory_dir": tmp_path / "memory",
        "settings": {"memory": {"synthetic_memory": {"auto_generate_on_interaction": True, "auto_generate_interval_hours": 6, "auto_generate_limit": 5}}},
    }
    c.config["memory_dir"].mkdir(parents=True, exist_ok=True)
    c._utc_now = lambda: datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc)  # type: ignore[method-assign]
    return c


def test_maybe_generate_synthetic_candidates_disabled_and_interval_skip(tmp_path):
    c = _mk_core(tmp_path)
    c.synthesizer = None
    assert c._maybe_generate_synthetic_candidates()["status"] == "disabled"

    c.synthesizer = _SynthOK()
    state = c.config["memory_dir"] / "synthetic_runtime_state.json"
    # Same timestamp -> not due yet.
    state.write_text(json.dumps({"last_run": "2026-05-20T12:00:00+00:00", "last_count": 1}), encoding="utf-8")
    out = c._maybe_generate_synthetic_candidates()
    assert out["status"] == "skipped"
    assert out["reason"] == "interval_not_due"


def test_maybe_generate_synthetic_candidates_success_and_bad_state_and_error(tmp_path):
    c = _mk_core(tmp_path)
    c.synthesizer = _SynthOK(count=3)
    state = c.config["memory_dir"] / "synthetic_runtime_state.json"
    state.write_text("{bad-json", encoding="utf-8")

    out_ok = c._maybe_generate_synthetic_candidates()
    assert out_ok["status"] == "success"
    assert out_ok["generated"] == 3
    saved = json.loads(state.read_text(encoding="utf-8"))
    assert saved["last_count"] == 3

    c.synthesizer = _SynthErr()
    state.write_text(json.dumps({"last_run": "not-a-date", "last_count": 3}), encoding="utf-8")
    out_err = c._maybe_generate_synthetic_candidates()
    assert out_err["status"] == "error"
    assert "boom-generate" in out_err["error"]


def test_get_synthetic_context_bundle_variants(tmp_path):
    c = _mk_core(tmp_path)

    c.synthesizer = None
    assert c._get_synthetic_context_bundle() == {"text": "", "candidate_ids": []}

    c.synthesizer = _SynthErr()
    assert c._get_synthetic_context_bundle() == {"text": "", "candidate_ids": []}

    c.synthesizer = _SynthOK()
    bundle = c._get_synthetic_context_bundle(limit=2)
    assert bundle["candidate_ids"] == ["cid-1", "cid-2"]
    assert "## Synthesized Insights" in bundle["text"]
    assert "alpha beta" in bundle["text"]
