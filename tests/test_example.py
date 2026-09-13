"""example の DummyCtrl を統合 test として検証する pytest。"""

from example.dummy_ctrl import DummyCtrl


def test_dummy_ctrl_runs_multiple_jobs() -> None:
    """DummyCtrl の public method が multiprocessing job として動くことを検証する。"""
    with DummyCtrl(
        worker_count=2,
        start_method="spawn",
        log_level="WARNING",
    ) as ctrl:
        first_job_id = ctrl.slow_add(
            10,
            20,
            delay_sec=0.10,
        )
        second_job_id = ctrl.slow_add(
            1,
            2,
            delay_sec=0.10,
        )

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

        assert first_result == 30
        assert second_result == 3


def test_dummy_ctrl_uses_shared_state_and_lock() -> None:
    """DummySingle が共有 state と lock を利用できることを検証する。"""
    with DummyCtrl(
        worker_count=2,
        start_method="spawn",
        log_level="WARNING",
    ) as ctrl:
        set_job_id = ctrl.set_value(456)

        set_result = ctrl.wait(
            set_job_id,
            timeout_sec=2.0,
        )

        assert set_result == 456

        get_job_id = ctrl.get_value()
        get_result = ctrl.wait(
            get_job_id,
            timeout_sec=2.0,
        )

        assert get_result == 456
