from __future__ import annotations

import time

import pytest

from ms8.engine_core.security.shadow.shadow_locking import Lease, ShadowLocking


def test_lease_expired_property() -> None:
    past = Lease(op_name="x", lease_id="l1", owner="u", expires_at=time.monotonic() - 0.1)
    future = Lease(op_name="x", lease_id="l2", owner="u", expires_at=time.monotonic() + 10)
    assert past.expired() is True
    assert future.expired() is False


def test_acquire_validate_release_and_cooldown() -> None:
    lock = ShadowLocking()
    with lock.acquire("recover", "tester", ttl_s=5, cooldown_s=1) as lease:
        assert lease.lease_id.startswith("lease-")
        assert lock.validate_lease(lease.lease_id) is True
        assert lock.current_lease() is not None
    # released
    assert lock.current_lease() is None
    # cooldown active immediately
    with pytest.raises(RuntimeError):
        with lock.acquire("recover", "tester", ttl_s=5, cooldown_s=1):
            pass
    # wait cooldown to pass
    time.sleep(1.1)
    with lock.acquire("recover", "tester", ttl_s=5, cooldown_s=1):
        pass


def test_acquire_blocked_by_active_lease() -> None:
    lock = ShadowLocking()
    with lock.acquire("state", "owner-a", ttl_s=30):
        with pytest.raises(RuntimeError) as exc:
            with lock.acquire("backup", "owner-b", ttl_s=30):
                pass
        assert "operation_locked:" in str(exc.value)


def test_current_lease_clears_expired() -> None:
    lock = ShadowLocking()
    lock._active = Lease(op_name="x", lease_id="l3", owner="u", expires_at=time.monotonic() - 0.01)  # noqa: SLF001
    assert lock.current_lease() is None
    assert lock.validate_lease("l3") is False

