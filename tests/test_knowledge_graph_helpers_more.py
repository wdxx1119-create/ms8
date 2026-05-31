from __future__ import annotations

from pathlib import Path

from ms8.engine_core import knowledge_graph as kg_mod


def _make_graph(tmp_path: Path, monkeypatch) -> kg_mod.KnowledgeGraph:
    cfg = {
        "workspace_dir": tmp_path,
        "memory_dir": tmp_path / "memory",
        "settings": {
            "memory": {
                "knowledge_graph": {
                    "db_path": "memory/kg_helpers.db",
                    "auto_extract": False,
                    "default_return_limit": 20,
                    "max_query_depth": 5,
                    "enabled": True,
                    "extraction_mode": "hybrid",
                },
                "knowledge_graph_quality": {},
            }
        },
    }
    cfg["memory_dir"].mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(kg_mod, "get_config", lambda: cfg)
    return kg_mod.KnowledgeGraph(llm=None)


def test_parse_utc_datetime_variants(tmp_path: Path, monkeypatch) -> None:
    g = _make_graph(tmp_path, monkeypatch)
    assert g._parse_utc_datetime(None) is None
    assert g._parse_utc_datetime("invalid-time") is None
    assert g._parse_utc_datetime("2026-01-01T00:00:00Z") is not None
    assert g._parse_utc_datetime("2026-01-01T08:00:00+08:00") is not None


def test_clean_scan_text_and_sentence_split(tmp_path: Path, monkeypatch) -> None:
    g = _make_graph(tmp_path, monkeypatch)
    raw = "## Title\n```py\nx=1\n```\n**OpenClaw** uses [link](https://x.y)\n"
    cleaned = g._clean_scan_text(raw)
    assert "OpenClaw" in cleaned
    assert "```" not in cleaned
    parts = g._split_sentences(raw + "另外一句。")
    assert parts


def test_noise_and_meaningful_name_judgement(tmp_path: Path, monkeypatch) -> None:
    g = _make_graph(tmp_path, monkeypatch)
    assert g._looks_like_noise("Tue 2026-03-03 00:04 GMT+8") is True
    assert g._looks_like_noise("1.0000000000000009") is True
    assert g._is_meaningful_name("OpenClaw") is True
    assert g._is_meaningful_name("123456") is False
    assert g._is_meaningful_name("这是一个方式") is False


def test_entity_quality_and_model_like_name(tmp_path: Path, monkeypatch) -> None:
    g = _make_graph(tmp_path, monkeypatch)
    assert g._entity_quality_score("OpenClaw") > 0
    assert g._entity_quality_score("只是") == 0
    assert g._model_like_name("qwen3.5") is True
    assert g._model_like_name("regular_name") is False


def test_description_score_merge_and_derive(tmp_path: Path, monkeypatch) -> None:
    g = _make_graph(tmp_path, monkeypatch)
    a = "OpenClaw 使用 sqlite 和 whoosh 进行检索"
    b = "OpenClaw 分类 统计 整理时间"
    assert g._entity_description_score(a, "OpenClaw") > g._entity_description_score(b, "OpenClaw")

    merged = g._merge_entity_descriptions(existing=b, new=a, entity_name="OpenClaw")
    assert "whoosh" in merged

    derived = g._derive_entity_description("OpenClaw", "我们今天讨论 OpenClaw 的配置与部署。", "Daily")
    assert derived
    fallback = g._derive_entity_description("https://example.com/docs", "none", "")
    assert "资源链接" in fallback


def test_infer_entity_type_and_candidate_select(tmp_path: Path, monkeypatch) -> None:
    g = _make_graph(tmp_path, monkeypatch)
    assert g._infer_entity_type("OpenAI", "") == "organization"
    assert g._infer_entity_type("https://example.com", "") == "resource"
    assert g._infer_entity_type("config.yaml", "") == "configuration"
    assert g._infer_entity_type("OpenClaw", "记忆系统工具") == "tool"

    best = g._select_best_entity_candidate("OpenClaw 与 Ollama 在项目中集成", prefer="longest")
    assert best
    assert len(best[0]) >= len(best[-1])


def test_parse_llm_json_and_use_llm_probe(tmp_path: Path, monkeypatch) -> None:
    g = _make_graph(tmp_path, monkeypatch)
    parsed = g._parse_llm_json('prefix {"entities":[{"name":"OpenClaw"}]} suffix')
    assert isinstance(parsed, dict)
    assert "entities" in parsed
    assert g._parse_llm_json("not-json") == {}

    assert g._should_use_llm_for_content("今天讨论 OpenClaw 和 SQLite", title="", source="daily") is True
    assert g._should_use_llm_for_content("普通聊天内容", title="", source="misc") is False

    g.settings["extraction_mode"] = "rule"
    assert g._should_use_llm_for_content("OpenClaw", title="", source="") is False
