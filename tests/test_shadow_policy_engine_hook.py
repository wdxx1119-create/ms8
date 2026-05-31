from __future__ import annotations

from ms8.engine_core.security.shadow.shadow_guard import ShadowSystem


class _Seal:
    def __init__(self, *, sealed: bool, level: str) -> None:
        self._sealed = sealed
        self._level = level

    def is_sealed(self) -> bool:
        return self._sealed

    def seal_level(self) -> str:
        return self._level


class _PolicyYes:
    def shadow_decide(self, payload):
        return {
            "ok": True,
            "code": "OK",
            "reason": "t",
            "trace_id": "a",
            "data": {"takeover": True},
        }


class _PolicyNo:
    def shadow_decide(self, payload):
        return {
            "ok": True,
            "code": "OK",
            "reason": "t",
            "trace_id": "b",
            "data": {"takeover": False},
        }


def _make_shadow(*, sealed: bool = True, level: str = "soft", enabled: bool = True) -> ShadowSystem:
    s = object.__new__(ShadowSystem)
    s.enabled = enabled
    s._seal = _Seal(sealed=sealed, level=level)
    s._policy_engine = None
    return s


def test_should_takeover_write_respects_policy_engine_override() -> None:
    s = _make_shadow(sealed=True, level="soft", enabled=True)
    s._policy_engine = _PolicyYes()
    assert s.should_takeover_write("low") is True

    s2 = _make_shadow(sealed=True, level="hard", enabled=True)
    s2._policy_engine = _PolicyNo()
    assert s2.should_takeover_write("critical") is False


def test_should_takeover_write_fallback_when_policy_unavailable() -> None:
    s = _make_shadow(sealed=True, level="soft", enabled=True)
    assert s.should_takeover_write("low") is False
    assert s.should_takeover_write("high") is True

