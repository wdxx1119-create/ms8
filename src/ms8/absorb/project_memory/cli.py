"""CLI dispatch for absorb project-memory."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .generator import build_outputs
from .health import project_doctor, project_status
from .scanner import scan_project
from .scope import (
    get_project,
    init_project,
    list_projects,
    load_index_state,
    project_dir_paths,
    set_auto_write_main_memory,
    update_project_stats,
)
from .search import rebuild_search_index, search_chunks
from .submit import submit_project_summary
from .watch import watch_project


def _print(payload: dict[str, Any]) -> int:
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if bool(payload.get("ok", True)) else 1


def _pretty_search(project_name: str, query: str, matches: list[dict[str, Any]]) -> int:
    print(f'PROJECT_MEMORY_SEARCH  project={project_name}  query="{query}"')
    print(f"matches={len(matches)}")
    print("---")
    for idx, item in enumerate(matches, 1):
        print(f"{idx}. {item.get('relative_path', item.get('chunk_id', ''))}  {item.get('file_type', '')}")
        print(f"   chunk={item.get('chunk_index', 0)} backend={item.get('search_backend', '')} score={item.get('score', '')}")
        preview = str(item.get("text_preview", "") or "").replace("\n", " ").strip()
        if preview:
            print(f"   preview={preview[:220]}")
        print("")
    return 0


def run_project_memory_cli(args) -> int:
    cmd = str(getattr(args, "pm_cmd", "") or "")
    if cmd == "init":
        return _print(init_project(args.project_dir, getattr(args, "name", None)))
    if cmd == "list":
        projects = list_projects()
        return _print({"ok": True, "projects": projects, "count": len(projects)})
    if cmd == "service-install-all":
        from ...service import install_all_project_memory_services

        return _print(
            install_all_project_memory_services(
                auto_build=not bool(getattr(args, "no_build", False)),
                submit_summary=not bool(getattr(args, "no_submit_summary", False)),
                auto_index=not bool(getattr(args, "no_index", False)),
            )
        )
    if cmd == "service-remove-all":
        from ...service import remove_all_project_memory_services

        return _print(remove_all_project_memory_services())
    if cmd == "service-status-all":
        from ...service import project_memory_services_status_all

        return _print(project_memory_services_status_all())

    project_lookup = get_project(getattr(args, "name", None))
    if not bool(project_lookup.get("ok", False)):
        return _print(project_lookup)
    project = dict(project_lookup["project"])
    name = str(project["name"])
    root = Path(str(project["root"])).expanduser().resolve()
    paths = project_dir_paths(name)

    if cmd == "scan":
        out = scan_project(project_name=name, project_root=root, db_path=paths["db_path"], index_state_path=paths["index_state_path"])
        current = out.get("current_stats", {})
        update_project_stats(
            name,
            file_count=int(current.get("file_count", 0) or 0),
            chunk_count=int(current.get("chunk_count", 0) or 0),
            last_scan_at=str(current.get("last_scan_at", "")),
        )
        return _print(out)
    if cmd == "index":
        force_full = bool(getattr(args, "full", False))
        return _print(
            rebuild_search_index(
                paths["db_path"],
                paths["whoosh_dir"],
                paths["index_state_path"],
                full_rebuild=force_full,
            )
        )
    if cmd == "build":
        index_state = load_index_state(paths["index_state_path"])
        changed_paths = list(index_state.get("changed_paths", []) or [])
        changed_paths.extend(list(index_state.get("deleted_paths", []) or []))
        return _print(
            build_outputs(
                project_name=name,
                project_root=root,
                db_path=paths["db_path"],
                output_dir=paths["output_dir"],
                build_state_path=paths["build_state_path"],
                changed_paths=changed_paths,
                force=bool(getattr(args, "force", False)),
            )
        )
    if cmd == "submit":
        return _print(
            submit_project_summary(
                project_name=name,
                project_root=root,
                output_dir=paths["output_dir"],
                previous_hash=str(project.get("last_summary_hash", "") or ""),
                force=bool(getattr(args, "force", False)),
            )
        )
    if cmd == "search":
        matches = search_chunks(
            paths["db_path"],
            paths["whoosh_dir"],
            args.query,
            limit=int(getattr(args, "limit", 10)),
            index_state_path=paths["index_state_path"],
        )
        if bool(getattr(args, "pretty", False)):
            return _pretty_search(name, args.query, matches)
        return _print({"ok": True, "name": name, "query": args.query, "matches": matches})
    if cmd == "status":
        return _print(
            project_status(
                name=name,
                root=str(root),
                db_path=paths["db_path"],
                whoosh_dir=paths["whoosh_dir"],
                output_dir=paths["output_dir"],
                index_state_path=paths["index_state_path"],
                build_state_path=paths["build_state_path"],
            )
        )
    if cmd == "doctor":
        return _print(
            project_doctor(
                name=name,
                root=str(root),
                db_path=paths["db_path"],
                whoosh_dir=paths["whoosh_dir"],
                output_dir=paths["output_dir"],
                index_state_path=paths["index_state_path"],
                build_state_path=paths["build_state_path"],
            )
        )
    if cmd == "watch":
        return _print(
            watch_project(
                project_name=name,
                project_root=root,
                db_path=paths["db_path"],
                whoosh_dir=paths["whoosh_dir"],
                output_dir=paths["output_dir"],
                index_state_path=paths["index_state_path"],
                watch_state_path=paths["watch_state_path"],
                duration=getattr(args, "duration", None),
                auto_index=not bool(getattr(args, "no_index", False)),
                auto_build=bool(getattr(args, "build", False)),
                auto_submit_main_memory=bool(getattr(args, "submit_summary", False) or project.get("auto_write_main_memory", False)),
                previous_summary_hash=str(project.get("last_summary_hash", "") or ""),
                build_state_path=paths["build_state_path"],
            )
        )
    if cmd == "service-install":
        from ...service import install_project_memory_service

        return _print(
            install_project_memory_service(
                name,
                auto_build=not bool(getattr(args, "no_build", False)),
                submit_summary=not bool(getattr(args, "no_submit_summary", False)),
                auto_index=not bool(getattr(args, "no_index", False)),
            )
        )
    if cmd == "service-remove":
        from ...service import remove_project_memory_service

        return _print(remove_project_memory_service(name))
    if cmd == "service-status":
        from ...service import project_memory_service_status

        return _print(project_memory_service_status(name))
    if cmd == "enable-auto-write":
        return _print(set_auto_write_main_memory(name, True))
    if cmd == "disable-auto-write":
        return _print(set_auto_write_main_memory(name, False))
    return _print(
        {
            "ok": False,
            "error": "choose init|list|scan|index|build|submit|search|status|doctor|watch|service-install|service-remove|service-status|service-install-all|service-remove-all|service-status-all|enable-auto-write|disable-auto-write",
            "next_actions": ["ms8 absorb project-memory init <project_dir> [--name <name>]"],
        }
    )
