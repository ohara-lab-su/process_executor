# Quick Start

このページは `process-executor` を最短で使い始めるための導入です。内容は今回与えられた `executor.py` と `shared_context.py` の実装を根拠にしています。

## 基本形

通常の Python クラスを `worker_class` に指定します。

```python
from process_executor import ProcessExecutor


class Calculator:
    def add(self, a: int, b: int) -> int:
        return a + b


def main() -> None:
    with ProcessExecutor(
        worker_class=Calculator,
        worker_count=2,
        start_method="spawn",
    ) as executor:
        job_id = executor.submit("add", 10, 20)
        result = executor.wait(job_id, timeout_sec=5.0)
        print(result)


if __name__ == "__main__":
    main()
```

`Calculator` 自身には multiprocessing 用のコードは不要です。worker process の中で `Calculator` の instance が生成され、`submit("add", 10, 20)` で指定した `"add"` が `getattr(instance, handler_name)` によって method に解決されます。

## submit と wait

`submit()` は処理結果ではなく job ID を返します。

```python
job_id = executor.submit("add", 10, 20)
```

job は `queued` で登録され、worker が取得すると `running`、正常 return すると `succeeded`、通常の Python `Exception` が発生すると `failed` になります。

結果まで同期的に待つ場合は:

```python
result = executor.wait(job_id)
```

timeout を付ける場合は:

```python
result = executor.wait(job_id, timeout_sec=10.0)
```

`wait()` の timeout は handler を停止する execution timeout ではありません。caller 側の待機 timeout です。

## job の状態と結果

```python
state = executor.state(job_id)
metadata = executor.job(job_id)
all_jobs = executor.jobs()
```

`job()` と `jobs()` が返す dictionary は取得時点の snapshot です。

完了済み result を待たずに読む場合は:

```python
result = executor.result(job_id)
```

`result()` 自体は待機しません。未完了なら `RuntimeError` です。

## worker を確認する

```python
for worker_id, metadata in executor.workers().items():
    print(
        worker_id,
        metadata["pid"],
        metadata["state"],
        metadata["current_job_id"],
        metadata["current_handler"],
    )
```

一つだけ取得する場合は `executor.worker(worker_id)` を使います。

## constructor arguments を渡す

```python
class Controller:
    def __init__(self, host: str, port: int, timeout: float = 1.0):
        self.host = host
        self.port = port
        self.timeout = timeout

    def query(self, command: str):
        ...


with ProcessExecutor(
    worker_class=Controller,
    worker_args=("192.168.0.10", 5000),
    worker_kwargs={"timeout": 2.0},
    worker_count=2,
) as executor:
    job_id = executor.submit("query", "STATUS")
    result = executor.wait(job_id)
```

各 worker process は同じ constructor arguments を使って独立した `Controller` instance を生成します。

## SharedContext を使う

worker 間で共有する state や lock が必要なら `worker_factory` を使います。

```python
class CounterController:
    def __init__(self, state, lock):
        self._state = state
        self._lock = lock

    def increment(self):
        with self._lock:
            value = self._state.get("count", 0) + 1
            self._state["count"] = value
            return value


def create_controller(context):
    return CounterController(
        state=context.state,
        lock=context.lock,
    )


with ProcessExecutor(
    worker_factory=create_controller,
    worker_count=4,
) as executor:
    job_ids = [executor.submit("increment") for _ in range(10)]
    results = [executor.wait(job_id) for job_id in job_ids]
```

`worker_factory(context)` に渡される `SharedContext` は次を保持します。

```text
context.jobs
context.workers
context.state
context.lock
```

既存クラスをそのまま使うなら `worker_class`、SharedContext を使って instance を構築するなら `worker_factory` です。両方を同時には指定できません。

## 終了処理

通常は context manager を使います。

```python
with ProcessExecutor(
    worker_class=Controller,
    worker_count=2,
) as executor:
    job_id = executor.submit("run")
    result = executor.wait(job_id)
```

明示的に管理する場合は:

```python
executor = ProcessExecutor(worker_class=Controller)

try:
    ...
finally:
    executor.close()
```

## 最初に理解しておく制約

現在の source には job cancellation、priority、retry、persistent queue、remote worker はありません。

`wait(timeout_sec=...)` は実行中 handler を停止しません。

worker process が handler 実行中に process-level failure で死亡した場合、worker pool の不足分を補充する処理はありますが、その `running` job を自動的に `failed` に変更する処理はありません。

詳細な execution model、failure model、SharedContext、worker lifecycle、resource ownership はメインの詳細ガイドを参照してください。API class/method/signature/docstring は build 時に source から自動生成される API Reference に表示されます。
