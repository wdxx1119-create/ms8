"""CLI dispatch for ms8 absorb."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .health import absorb_health_summary
from .incremental_processor import process_pending
from .kg import extract_absorb_knowledge_graph
from .repository import init_repository
from .reviewer import (
    approve_all,
    approve_chunk,
    auto_submit_by_tier,
    export_review_items,
    list_review_chunks,
    reject_all,
    reject_chunk,
    restore_rejected_chunk,
    rollback_auto_writes,
    submit_chunk,
)
from .scope import (
    add_allowed_root,
    add_exclude_pattern,
    list_allowed_roots,
    load_absorb_config,
    remove_allowed_root,
    set_auto_submit_summaries,
    set_auto_write_tier,
)
from .search import search_chunks
from .spotlight_bootstrap import bootstrap_authorized_roots


def _print(payload: dict) -> int:
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if bool(payload.get("ok", True)) else 1


def _privacy_note() -> str:
    return "Absorb indexes authorized local files only; main memory writes require explicit submit/autosubmit opt-in."


def _status_next_actions(summary: dict[str, Any]) -> list[str]:
    roots = int(summary.get("authorized_roots", 0) or 0)
    pending = int(summary.get("pending_review", 0) or 0)
    quarantine = int(summary.get("quarantine", 0) or 0)
    actions: list[str] = []
    if roots <= 0:
        actions.append("ms8 absorb add <directory>")
        return actions
    if pending:
        actions.append("ms8 absorb review list")
    if quarantine:
        actions.append("ms8 absorb review export --include-quarantine")
    actions.extend(["ms8 absorb rescan", "ms8 absorb ingest", "ms8 absorb search <query> --pretty"])
    return actions


def _review_next_actions(items: list[dict[str, Any]]) -> list[str]:
    if not items:
        return ["ms8 absorb status"]
    first = str(items[0].get("chunk_id", "") or "<chunk_id>")
    status = str(items[0].get("status", "") or "")
    if status == "QUARANTINED":
        return ["ms8 absorb review export --include-quarantine"]
    return [f"ms8 absorb review approve {first}", f"ms8 absorb review reject {first} --reason <reason>"]


def _search_next_actions(query: str, matches: list[dict[str, Any]]) -> list[str]:
    if not matches:
        return ["ms8 absorb rescan", "ms8 absorb ingest", f'ms8 absorb search "{query}" --pretty']
    return [f'ms8 ask "{query}"', "ms8 absorb review submit <chunk_id>"]


def _with_next_actions(payload: dict[str, Any], actions: list[str]) -> dict[str, Any]:
    payload.setdefault("next_actions", actions)
    return payload


def _print_pretty_search(query: str, matches: list[dict[str, Any]]) -> int:
    print("MS8_ABSORB_SEARCH")
    print(f"query={query}")
    print(f"matches={len(matches)}")
    for idx, item in enumerate(matches, 1):
        path = str(item.get("canonical_path", "") or "")
        title = Path(path).name or path or str(item.get("chunk_id", ""))
        file_type = str(item.get("file_type", "") or "")
        status = str(item.get("status", "") or "")
        risk = str(item.get("risk_level", "") or "")
        backend = str(item.get("search_backend", "") or "")
        score = item.get("score", "")
        print("")
        print(f"{idx}. {title} {file_type}".rstrip())
        print(f"   status={status} risk={risk} backend={backend} score={score}")
        if path:
            print(f"   path={path}")
        preview = str(item.get("text_preview", "") or "").replace("\n", " ").strip()
        if preview:
            print(f"   preview={preview[:220]}")
    print("")
    print("next_actions:")
    for action in _search_next_actions(query, matches):
        print(f"- {action}")
    return 0


def run_absorb_cli(args) -> int:
    init_repository()
    cmd = str(getattr(args, "absorb_cmd", "") or "")
    if cmd == "add":
        out = add_allowed_root(args.path, confirm_high_risk=bool(getattr(args, "confirm_high_risk", False)))
        out["privacy_note"] = _privacy_note()
        return _print(_with_next_actions(out, ["ms8 absorb rescan", "ms8 absorb ingest", "ms8 absorb start"]))
    if cmd == "remove":
        return _print(_with_next_actions(remove_allowed_root(args.path), ["ms8 absorb status"]))
    if cmd == "list":
        cfg = load_absorb_config()
        roots = list_allowed_roots()
        return _print(
            {
                "ok": True,
                "allowed_roots": roots,
                "exclude_patterns": cfg.get("exclude_patterns", []),
                "next_actions": ["ms8 absorb add <directory>"] if not roots else ["ms8 absorb rescan", "ms8 absorb ingest"],
            }
        )
    if cmd == "exclude":
        if str(getattr(args, "exclude_cmd", "") or "") == "add":
            return _print(add_exclude_pattern(args.pattern))
        return _print({"ok": False, "error": "choose exclude add"})
    if cmd == "rescan":
        return _print(bootstrap_authorized_roots())
    if cmd == "ingest":
        submit = True if bool(getattr(args, "submit_summaries", False)) else None
        return _print(process_pending(submit_summaries=submit, limit=int(args.limit)))
    if cmd == "status":
        summary = absorb_health_summary()
        summary["counts"] = {"files": summary.get("files", {}), "chunks": summary.get("chunks", {})}
        summary["next_actions"] = _status_next_actions(summary)
        return _print(summary)
    if cmd == "review":
        subcmd = str(getattr(args, "review_cmd", "") or "")
        if subcmd == "approve":
            return _print(approve_chunk(args.chunk_id, submit=bool(getattr(args, "submit", False))))
        if subcmd == "reject":
            return _print(reject_chunk(args.chunk_id, reason=str(getattr(args, "reason", "") or "user_rejected")))
        if subcmd == "restore":
            return _print(restore_rejected_chunk(args.chunk_id))
        if subcmd == "submit":
            return _print(submit_chunk(args.chunk_id))
        if subcmd == "approve-all":
            return _print(
                approve_all(
                    risk=str(getattr(args, "risk", "") or ""),
                    limit=int(getattr(args, "limit", 50)),
                    apply=bool(getattr(args, "apply", False)),
                    submit=bool(getattr(args, "submit", False)),
                )
            )
        if subcmd == "reject-all":
            return _print(
                reject_all(
                    reason=str(getattr(args, "reason", "") or "bulk_rejected"),
                    risk=str(getattr(args, "risk", "") or ""),
                    limit=int(getattr(args, "limit", 50)),
                    apply=bool(getattr(args, "apply", False)),
                )
            )
        if subcmd == "export":
            return _print(export_review_items(limit=int(getattr(args, "limit", 100)), include_quarantine=bool(getattr(args, "include_quarantine", False))))
        items = list_review_chunks(limit=int(getattr(args, "limit", 50)))
        review_items = list(items.get("pending_review", []) or []) + list(items.get("quarantine", []) or [])
        return _print(_with_next_actions(items, _review_next_actions(review_items)))
    if cmd == "search":
        matches = search_chunks(args.query, limit=int(args.limit))
        if bool(getattr(args, "pretty", False)):
            return _print_pretty_search(args.query, matches)
        return _print({"ok": True, "query": args.query, "matches": matches, "next_actions": _search_next_actions(args.query, matches)})
    if cmd == "autosubmit":
        subcmd = str(getattr(args, "autosubmit_cmd", "") or "")
        if subcmd == "enable":
            return _print(set_auto_submit_summaries(True))
        if subcmd == "disable":
            return _print(set_auto_submit_summaries(False))
        if subcmd == "tier":
            return _print(set_auto_write_tier(str(getattr(args, "tier", "") or "")))
        if subcmd == "run":
            return _print(
                auto_submit_by_tier(
                    limit=int(getattr(args, "limit", 20)),
                    daily_cap=int(getattr(args, "daily_cap", 20)),
                    apply=bool(getattr(args, "apply", False)),
                )
            )
        if subcmd == "rollback":
            return _print(
                rollback_auto_writes(
                    since_hours=int(getattr(args, "since_hours", 1)),
                    limit=int(getattr(args, "limit", 100)),
                    apply=bool(getattr(args, "apply", False)),
                    source_system=str(getattr(args, "source_system", "absorb") or "absorb"),
                )
            )
        cfg = load_absorb_config()
        return _print(
            {
                "ok": True,
                "auto_submit_summaries": bool(cfg.get("auto_submit_summaries", False)),
                "auto_write_tier": str(cfg.get("auto_write_tier", "OFF")),
            }
        )
    if cmd == "kg-extract":
        return _print(
            extract_absorb_knowledge_graph(
                limit=int(getattr(args, "limit", 50)),
                apply=bool(getattr(args, "apply", False)),
                force=bool(getattr(args, "force", False)),
            )
        )
    if cmd == "start":
        from .fs_watcher import start_watch

        submit = True if bool(getattr(args, "submit_summaries", False)) else None
        out = start_watch(duration=getattr(args, "duration", None), submit_summaries=submit)
        roots = out.get("roots", [])
        out["summary"] = (
            f"watched {len(roots)} root(s); "
            f"events={out.get('events', 0)} poll_scans={out.get('poll_scans', 0)} "
            f"processed={out.get('poll_processed', 0)}"
        )
        out["next_actions"] = ["ms8 absorb status", "ms8 absorb search <query> --pretty"]
        return _print(out)
    if cmd == "stop":
        from .fs_watcher import stop_watch

        return _print(stop_watch())
    return _print({"ok": False, "error": "choose add|remove|list|exclude|rescan|ingest|status|review|search|autosubmit|kg-extract|start|stop"})
