"""Executable example for the process-executor package."""

import time

from example.dummy_ctrl import DummyCtrl


def main() -> None:
    """Run the dummy controller example."""
    with DummyCtrl(worker_count=2) as ctrl:
        job_a = ctrl.slow_add(10, 20, delay_sec=0.5)
        job_b = ctrl.slow_add(1, 2, delay_sec=0.5)

        print("submitted:", job_a, job_b)

        while True:
            state_a = ctrl.state(job_a)
            state_b = ctrl.state(job_b)

            print("state:", state_a, state_b)

            if state_a == "succeeded" and state_b == "succeeded":
                break

            time.sleep(0.05)

        print("result:", ctrl.result(job_a))
        print("result:", ctrl.result(job_b))

        set_job = ctrl.set_value(123)
        ctrl.wait(set_job, timeout_sec=2.0)

        get_job = ctrl.get_value()
        value = ctrl.wait(get_job, timeout_sec=2.0)

        print("shared value:", value)
        print("workers:", ctrl.workers())


if __name__ == "__main__":
    main()
