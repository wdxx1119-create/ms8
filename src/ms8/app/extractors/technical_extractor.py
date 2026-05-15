from __future__ import annotations

import re


def extract_time_info(text: str) -> dict:
    dates = re.findall(r"\b\d{4}-\d{2}-\d{2}\b", text)
    times = re.findall(r"\b\d{1,2}:\d{2}\b", text)
    return {"dates": dates[:3], "times": times[:3]}
