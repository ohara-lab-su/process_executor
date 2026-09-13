"""example/ の generic example を検証する pytest。"""

import sys
from pathlib import Path

import pytest


EXAMPLE_DIR = Path(__file__).resolve().parents[1] / "example"

if str(EXAMPLE_DIR) not in sys.path:
    sys.path.insert(
        0,
        str(EXAMPLE_DIR),
    )

from dummy_ctrl import DummyCtrl  # noqa: E402
from dummy_single import DummySingle  # noqa: E402


def test_dummy_single_is_normal_synchronous_class() -> None:
    """DummySingle が ProcessExecutor 非依存で動作することを検証する。"""
    ctrl = DummySingle()

    assert ctrl.query("status") == "dummy:status"
    assert ctrl.get(
        123,
        delay_sec=0.01,
    ) == 123


def test_dummy_ctrl_runs_multiple_jobs() -> None:
    """DummyCtrl が DummySingle を worker_factory 経由で実行することを検証する。"""
    with DummyCtrl(
        worker_count=2,
        start_method="spawn",
    ) as ctrl:
        first_job_id = ctrl.get(
            10,
            delay_sec=0.10,
        )
        second_job_id = ctrl.query("controller_status")

        assert isinstance(first_job_id, str)
        assert isinstance(second_job_id, str)
        assert first_job_id != second_job_id

        first_result = ctrl.wait(
            first_job_id,
            timeout_sec=2.0,
        )
        second_result = ctrl.wait(
            second_job_id,
            timeout_sec=2.0,
        )

        assert first_result == 10
        assert second_result == "dummy:controller_status"


def test_pm2cd_example_files_are_present() -> None:
    """実機 example を自動実行せず、必要な3ファイルが存在することを検証する。"""
    pmcd_dir = EXAMPLE_DIR / "pmcd"

    assert (pmcd_dir / "try_move.py").is_file()
    assert (pmcd_dir / "pm2cd_multi.py").is_file()
    assert (pmcd_dir / "try_move_multi.py").is_file()
