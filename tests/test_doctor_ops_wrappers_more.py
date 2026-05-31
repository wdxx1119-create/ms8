from __future__ import annotations

from pathlib import Path

from ms8 import doctor


def test_run_doctor_with_hint_permission_and_oserror(monkeypatch, capsys) -> None:
    monkeypatch.setattr(doctor, "run_doctor", lambda: (_ for _ in ()).throw(PermissionError("denied")))
    code1 = doctor.run_doctor_with_hint()
    out1 = capsys.readouterr().out
    assert code1 == 1
    assert "hint: check runtime permissions" in out1

    monkeypatch.setattr(doctor, "run_doctor", lambda: (_ for _ in ()).throw(OSError("disk err")))
    code2 = doctor.run_doctor_with_hint()
    out2 = capsys.readouterr().out
    assert code2 == 1
    assert "ms8 backup" in out2
    assert "ms8 cleanup" in out2


def test_run_backup_and_cleanup_and_set_risk(monkeypatch, capsys) -> None:
    monkeypatch.setattr(doctor, "backup_memories", lambda tag="manual": {"path": "/tmp/b.json"})
    monkeypatch.setattr(doctor, "cleanup_old_backups", lambda max_keep=20: {"removed_count": 3})
    code = doctor.run_backup_and_cleanup(max_keep=7)
    out = capsys.readouterr().out
    assert code == 0
    assert "backup: /tmp/b.json" in out
    assert "cleanup removed: 3" in out

    monkeypatch.setattr(doctor, "update_governance_risk_config", lambda **kw: {"ok": True, **kw})
    code2 = doctor.run_set_risk_thresholds(
        red_schema_invalid_gt=1,
        red_fallback_write_gt=2,
        red_noncanonical_gt=3,
        yellow_fallback_write_gt=4,
        yellow_pending_review_gt=5,
        yellow_duplicate_groups_gt=6,
    )
    out2 = capsys.readouterr().out
    assert code2 == 0
    assert "updated governance risk thresholds:" in out2
    assert "red_schema_invalid_gt" in out2


def test_format_trend_delta_empty_and_filled() -> None:
    assert doctor._format_trend_delta({}) == "risk=green delta=n/a"
    line = doctor._format_trend_delta(
        {
            "risk": "yellow",
            "delta": {
                "noncanonical_records": 1,
                "schema_invalid_count": 2,
                "fallback_write_count": 3,
                "fallback_total_count": 4,
                "fallback_error_code_spike": 5,
                "duplicate_groups": 6,
                "pending_review": 7,
            },
        }
    )
    assert "risk=yellow" in line
    assert "schema_invalid=2" in line
