from __future__ import annotations

import re


def extract_action_object(text: str) -> tuple[str, str, str]:
    lowered = text.lower()
    # configuration changes
    m = re.search(r"(?:配置|参数)\s*(?:改为|设为|调整为|更新为)\s*([^，。\n]+)", text)
    if m:
        return "configure", m.group(1).strip(), "updated"
    m = re.search(r"(?:将|把)?\s*([^，。\n]{2,24})\s*(?:配置|参数)?\s*(?:改成|改为|设置为|设为)\s*([^，。\n]+)", text)
    if m:
        return "configure", f"{m.group(1).strip()}={m.group(2).strip()}", "updated"

    # fixing/repairing
    m = re.search(r"(?:修复了|修复|fix(?:ed)?)\s*([^，。\n]+)?", text, flags=re.IGNORECASE)
    if m:
        obj = (m.group(1) or "issue").strip() or "issue"
        return "fix", obj, "fixed"
    m = re.search(r"(?:解决了|处理了)\s*([^，。\n]+)", text)
    if m:
        return "fix", m.group(1).strip(), "resolved"

    # install / integrate
    m = re.search(r"(?:安装了|安装|install(?:ed)?)\s*([^，。\n]+)?", text, flags=re.IGNORECASE)
    if m:
        obj = (m.group(1) or "package").strip() or "package"
        return "install", obj, "installed"
    m = re.search(r"(?:接入了|集成了|对接了)\s*([^，。\n]+)", text)
    if m:
        return "integrate", m.group(1).strip(), "integrated"

    # enable/disable
    m = re.search(r"(?:启用|开启|打开|enable(?:d)?)\s*([^，。\n]+)?", text, flags=re.IGNORECASE)
    if m:
        obj = (m.group(1) or "feature").strip() or "feature"
        return "enable", obj, "enabled"
    m = re.search(r"(?:禁用|关闭|停用|disable(?:d)?)\s*([^，。\n]+)?", text, flags=re.IGNORECASE)
    if m:
        obj = (m.group(1) or "feature").strip() or "feature"
        return "disable", obj, "disabled"

    # release / deploy / test
    m = re.search(r"(?:发布|上线|deploy(?:ed)?)\s*([^，。\n]+)?", text, flags=re.IGNORECASE)
    if m:
        obj = (m.group(1) or "version").strip() or "version"
        return "release", obj, "released"
    m = re.search(r"(?:测试|验证|回归)\s*([^，。\n]+)?(?:通过|完成)?", text)
    if m:
        obj = (m.group(1) or "case").strip() or "case"
        status = "passed" if ("通过" in text or "pass" in lowered) else "tested"
        return "test", obj, status

    # completion / planning / decision
    if "完成" in text or "done" in lowered:
        return "complete", "task", "done"
    if "计划" in text or "plan" in lowered:
        return "plan", "next_step", "planned"
    m = re.search(r"(?:决定|采用|选择)\s*([^，。\n]+)", text)
    if m:
        return "decide", m.group(1).strip(), "decided"
    return "note", "context", "active"
