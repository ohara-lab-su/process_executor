"""ProcessExecutor を装置制御から使う形を示す example。"""

import time

from x_logger import XLogger

from dummy_ctrl import DummyCtrl


def main() -> None:
    """get 実行中に別 worker へ query を投入する。"""
    logger = XLogger(
        log_level="INFO",
        logger_name="process_executor.example",
    )

    with DummyCtrl(
        worker_count=2,
        start_method="spawn",
        logger=logger,
    ) as ctrl:
        get_job_id = ctrl.get(123, delay_sec=0.5)
        query_job_id = ctrl.query("controller_status")

        while True:
            get_state = ctrl.state(get_job_id)
            query_state = ctrl.state(query_job_id)

            logger.info(
                "job state: get=%s query=%s",
                get_state,
                query_state,
            )

            get_finished = get_state == "succeeded"
            query_finished = query_state == "succeeded"

            if get_finished:
                if query_finished:
                    break

            time.sleep(0.05)

        get_result = ctrl.result(get_job_id)
        query_result = ctrl.result(query_job_id)

        logger.info(
            "example result: get=%s query=%s",
            get_result,
            query_result,
        )


if __name__ == "__main__":
    main()
