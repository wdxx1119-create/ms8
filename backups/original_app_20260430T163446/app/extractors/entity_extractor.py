from __future__ import annotations

import re


def extract_entities(text: str) -> list[str]:
    words = re.findall(r"[A-Za-z][A-Za-z0-9_\-./]{1,}", text)
    model_or_version = re.findall(r"[A-Za-z0-9][A-Za-z0-9_\-./]*(?::[A-Za-z0-9._-]+)?", text)
    zh_chunks = re.findall(r"[\u4e00-\u9fff]{2,24}", text)
    quoted = re.findall(r"[\"'“”‘’`《〈](.{2,40}?)[\"'“”‘’`》〉]", text)
    zh_stop = {
        "这个",
        "那个",
        "我们",
        "你们",
        "他们",
        "以及",
        "然后",
        "但是",
        "如果",
        "因为",
        "所以",
        "进行",
        "已经",
        "一个",
        "一些",
        "需要",
        "可以",
        "应该",
        "就是",
        "我们",
        "他们",
        "你们",
        "今天",
        "昨天",
        "现在",
        "目前",
        "然后",
        "之后",
        "一下",
        "这里",
        "那里",
        "本次",
        "这次",
        "可以",
        "需要",
        "就是",
        "一个",
        "一些",
        "已经",
        "我们今天",
    }
    zh_noise_prefix = ("今天", "昨天", "刚刚", "目前", "现在")
    zh_noise_suffix = ("功能", "东西", "内容", "问题", "情况")
    tech_joiners = re.compile(r"(?:并且|以及|然后|但是|并|和|与|及|且|还有|或者|或|并接入|接入|改为|设置为|设为|改成)")
    zh_atomic_stop = {"接入", "改为", "设置", "配置", "系统", "模块", "模型"}
    uniq: list[str] = []
    for w in words + model_or_version:
        w = w.strip(".,;()[]{}")
        if len(w) < 2:
            continue
        if w.lower() in {"the", "and", "for", "with", "from", "that", "this"}:
            continue
        if w not in uniq:
            uniq.append(w)
    for q in quoted:
        q = q.strip()
        if len(q) >= 2 and q not in uniq:
            uniq.append(q)
    # Split by punctuation first, then split Chinese chunks by joiner verbs.
    for seg in re.split(r"[，,。；;：:\n]+", text):
        seg = seg.strip()
        if not seg:
            continue
        for chunk in re.findall(r"[\u4e00-\u9fff]{2,24}", seg):
            normalized = chunk.strip()
            if normalized.startswith(zh_noise_prefix):
                normalized = normalized[2:]
            if len(normalized) < 2:
                continue
            parts = re.split(tech_joiners, normalized)
            for part in parts:
                part = part.strip()
                if len(part) < 2 or part in zh_stop or part in zh_atomic_stop:
                    continue
                if part.endswith(zh_noise_suffix):
                    core = part[:-2]
                    if len(core) >= 2:
                        part = core
                if part in zh_stop or part in zh_atomic_stop or len(part) < 2:
                    continue
                if part not in uniq:
                    uniq.append(part)
                # Lift common "X系统/X模块/X配置" style technical entities.
                for suffix in ("系统", "模块", "配置", "接口", "服务", "数据库", "模型", "索引", "规则", "流程", "插件"):
                    if part.endswith(suffix) and len(part) > len(suffix):
                        core = part[: -len(suffix)]
                        if len(core) >= 2 and core not in zh_stop and core not in uniq:
                            uniq.append(core)
    # Keep standalone Chinese chunks as fallback.
    for chunk in zh_chunks:
        normalized = chunk.strip()
        if normalized.startswith(zh_noise_prefix):
            normalized = normalized[2:]
        if len(normalized) < 2 or normalized in zh_stop or normalized in zh_atomic_stop:
            continue
        if normalized not in uniq:
            uniq.append(normalized)
    return uniq[:12]
