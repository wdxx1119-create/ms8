from __future__ import annotations

import runpy
from pathlib import Path

from ms8 import ask, demo
from ms8.connect.local_llm_adapter import adapter_llm
from ms8.connect.scripts import install_env, scan_register


def test_main_module_exit_code(monkeypatch):
    monkeypatch.setattr("ms8.cli.main", lambda: 7)
    try:
        runpy.run_module("ms8.__main__", run_name="__main__")
    except SystemExit as exc:
        assert int(exc.code) == 7
    else:
        raise AssertionError("expected SystemExit from __main__")


def test_main_module_flush_oserror_is_ignored(monkeypatch):
    class _Broken:
        @staticmethod
        def flush():
            raise OSError("broken")

    monkeypatch.setattr("ms8.cli.main", lambda: 0)
    monkeypatch.setattr("sys.stdout", _Broken())
    monkeypatch.setattr("sys.stderr", _Broken())
    try:
        runpy.run_module("ms8.__main__", run_name="__main__")
    except SystemExit as exc:
        assert int(exc.code) == 0
    else:
        raise AssertionError("expected SystemExit from __main__")


def test_ask_empty_query(monkeypatch, capsys):
    # Avoid touching real runtime dirs in sandboxed test env.
    monkeypatch.setattr(
        ask,
        "consume_llm_degraded_notice_runtime",
        lambda: {"emit": False, "message": ""},
    )
    rc = ask.run_ask("   ")
    out = capsys.readouterr().out
    assert rc == 2
    assert "query cannot be empty" in out


def test_ask_write_and_search(monkeypatch, capsys):
    monkeypatch.setattr(
        ask,
        "consume_llm_degraded_notice_runtime",
        lambda: {"emit": True, "message": "LLM degraded"},
    )
    monkeypatch.setattr(ask, "write_memory", lambda text, source="ask": {"id": "m1", "text": text, "source": source})
    monkeypatch.setattr(
        ask,
        "search_memories_detailed",
        lambda q, limit=5: {"items": [{"id": "m2", "source": "ask", "text": f"hit:{q}"}], "trace": {"backend": "test"}},
    )

    rc_write = ask.run_ask("记住: hello world")
    rc_search = ask.run_ask("hello")
    out = capsys.readouterr().out

    assert rc_write == 0
    assert rc_search == 0
    assert "[ms8] LLM degraded" in out
    assert "saved memory: m1" in out
    assert "matches: 1" in out


def test_ask_search_no_matches_and_silent_notice(monkeypatch, capsys):
    monkeypatch.setattr(
        ask,
        "consume_llm_degraded_notice_runtime",
        lambda: {"emit": True, "message": "   "},
    )
    monkeypatch.setattr(ask, "search_memories_detailed", lambda q, limit=5: {"items": [], "trace": {"backend": "test"}})

    rc = ask.run_ask("missing-topic")
    out = capsys.readouterr().out
    assert rc == 0
    assert "[ms8]" not in out
    assert "matches: 0" in out
    assert "no match found" in out


def test_demo_success_path(monkeypatch, capsys, tmp_path: Path):
    monkeypatch.setattr(demo, "ensure_runtime_dirs", lambda: {"root": str(tmp_path)})
    monkeypatch.setattr(demo, "write_memory", lambda text, source="demo": {"id": "d1", "text": text, "source": source})
    monkeypatch.setattr(
        demo,
        "search_memories",
        lambda q: [{"id": "d1", "text": demo.DEMO_TEXT}] if q in (demo.DEMO_QUERY, demo.DEMO_TEXT) else [],
    )
    monkeypatch.setattr(demo, "read_memories", lambda: [{"id": "d1", "text": demo.DEMO_TEXT}])

    rc = demo.run_demo("test")
    out = capsys.readouterr().out
    assert rc == 0
    assert "demo completed" in out


def test_demo_failure_path(monkeypatch, capsys, tmp_path: Path):
    monkeypatch.setattr(demo, "ensure_runtime_dirs", lambda: {"root": str(tmp_path)})
    monkeypatch.setattr(demo, "write_memory", lambda text, source="demo": {"id": "d2", "text": text, "source": source})
    monkeypatch.setattr(demo, "search_memories", lambda q: [])
    monkeypatch.setattr(demo, "read_memories", lambda: [])

    rc = demo.run_demo("test")
    out = capsys.readouterr().out
    assert rc == 2
    assert "demo failed" in out


def test_adapter_llm_flow(monkeypatch):
    monkeypatch.setattr(adapter_llm, "load_yaml", lambda p: {"mock": True})

    class _Svc:
        def submit(self, payload):
            return {"ok": True, "payload": payload}

        def status(self):
            return {"ok": True}

    monkeypatch.setattr(
        adapter_llm.MemoryServiceInterface,
        "from_config",
        staticmethod(lambda cfg: _Svc()),
    )

    payload = adapter_llm.parse_event_rule_layer({"text": "hello", "metadata": {"k": 1}})
    assert payload["content"] == "hello"
    assert payload["metadata"]["k"] == 1

    submitted = adapter_llm.submit_memory_candidate({"content": "abc"})
    processed = adapter_llm.process_event({"content": "abc"})
    adapter = adapter_llm.get_adapter()

    assert submitted["ok"] is True
    assert processed["ok"] is True
    assert adapter["ok"] is True
    assert "submit" in adapter["capabilities"]


def test_install_env_and_scan_register(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(install_env.shutil, "which", lambda name: f"/bin/{name}" if name == "python3" else "")
    report = install_env.run()
    assert report["ok"] is True
    assert report["deps"]["python3"].endswith("python3")

    captured = {}
    monkeypatch.setattr(scan_register, "scan_local_tools", lambda: {"tools": ["a", "b"]})
    monkeypatch.setattr(
        scan_register,
        "save_registry",
        lambda payload, path: captured.update({"payload": payload, "path": str(path)}),
    )
    monkeypatch.setattr(scan_register, "connect_package_root", lambda: tmp_path)

    result = scan_register.run()
    assert result["ok"] is True
    assert result["registry_entries"] == 1
    assert "ms8_default_adapter" in captured["payload"]


def test_install_env_main_and_module(monkeypatch, capsys):
    monkeypatch.setattr(
        install_env.shutil,
        "which",
        lambda name: "/usr/bin/python3" if name == "python3" else "/usr/local/bin/ms8",
    )
    out = install_env.main()
    assert out["ok"] is True
    runpy.run_module("ms8.connect.scripts.install_env", run_name="__main__")
    printed = capsys.readouterr().out
    assert "'ok': True" in printed


def test_scan_register_main_and_module(monkeypatch, tmp_path: Path, capsys):
    monkeypatch.setattr(scan_register, "scan_local_tools", lambda: {"tools": []})
    monkeypatch.setattr(scan_register, "connect_package_root", lambda: tmp_path)
    monkeypatch.setattr(scan_register, "save_registry", lambda payload, path: None)
    out = scan_register.main()
    assert out["ok"] is True
    runpy.run_module("ms8.connect.scripts.scan_register", run_name="__main__")
    printed = capsys.readouterr().out
    assert "'registry_entries': 1" in printed
