"""ProcessExecutor の example 用ダミー単独制御クラス。"""

import time


class DummySingle:
    """通常の同期型制御クラスを模擬する。"""

    def query(self, name: str) -> str:
        """query 処理を模擬する。"""
        return "dummy:{}".format(name)

    def get(self, value: int, delay_sec: float = 0.5) -> int:
        """時間のかかる get 処理を模擬する。"""
        time.sleep(delay_sec)
        return value
