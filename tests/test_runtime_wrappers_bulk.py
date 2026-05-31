from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

from ms8 import runtime


def _value_for_param(name: str, annotation: Any) -> Any:
    lname = name.lower()
    if "data" in lname or "mapping" in lname or "payload" in lname or "config" in lname:
        return {}
    if "ids" in lname or "list" in lname or annotation in (list[str], list[dict[str, Any]]):
        return []
    if "limit" in lname or "count" in lname or "days" in lname or "minutes" in lname or "depth" in lname:
        return 1
    if "enabled" in lname or "force" in lname or "confirm" in lname or "apply" in lname or "dry" in lname:
        return False
    if "score" in lname or "ratio" in lname:
        return 0.5
    if "mapping" in lname or annotation is dict[str, Any]:
        return {}
    return "x"


def test_bulk_runtime_wrappers_smoke(monkeypatch, tmp_path: Path) -> None:
    calls: list[str] = []
    skipped: list[str] = []

    home = tmp_path / "home"
    ms8_home = home / ".ms8"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("MS8_HOME", str(ms8_home))
    monkeypatch.setenv("MS8_DATA_DIR", str(ms8_home / "data"))
    monkeypatch.setenv("MS8_CONFIG_DIR", str(ms8_home / "config"))
    monkeypatch.setenv("MS8_LOG_DIR", str(ms8_home / "logs"))

    def _fake(name: str, *args, **kwargs):  # noqa: ANN002, ANN003
        calls.append(name)
        return {"ok": True, "method": name}

    monkeypatch.setattr(runtime, "_run_core_method", _fake)

    skip = {
        "_run_core_method",
        "prepare_reply",
        "submit_memory",
        "health_report",
        "render_dashboard",
        "run_watch_loop",
        "configure_llm_mode_runtime",
        "get_llm_mode_runtime",
        "llm_notice_state_runtime",
    }

    for name, fn in inspect.getmembers(runtime, inspect.isfunction):
        if not name.endswith("_runtime"):
            continue
        if name in skip:
            continue
        sig = inspect.signature(fn)
        kwargs: dict[str, Any] = {}
        for p in sig.parameters.values():
            if p.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
                continue
            if p.default is not inspect._empty:
                continue
            kwargs[p.name] = _value_for_param(p.name, p.annotation)
        try:
            fn(**kwargs)
        except (TypeError, ValueError):
            skipped.append(name)

    # Ensure we actually exercised a broad set of runtime wrappers.
    assert len(calls) >= 80
    # Spot-check a few high-value wrappers.
    assert "run_self_repair" in calls
    assert "get_self_check_report" in calls
    assert "backfill_auto_memory_record_ids" in calls
    assert len(skipped) <= 20
