# process-executor

`process-executor` は、通常の Python クラスを multiprocessing の worker process 上で実行するための汎用実行基盤である。特に、すでに単体で完成している装置制御用 Ctrl クラスを、multiprocessing のためだけに書き直さず利用することを主目的としている。

現在のバージョンは **0.0.0** である。

本 README は導入手順だけではなく、0.0.0 の設計理由、責務分離、内部構造、public API、制約、および実際の example をまとめた仕様書兼チュートリアルである。

---

## 1. なぜこのプログラムを作ったのか

装置制御コードは、まず通常の同期クラスとして実装するのが自然である。

```python
class MotorCtrl:
    def move(
        self,
        motor: int,
        pulse: int,
    ) -> bool:
        ...

    def get_position(
        self,
        motor: int,
    ) -> int:
        ...
```

このクラスの責務は、装置 protocol、command、状態取得、移動、停止などである。

一方、制御システムを大きくすると、装置 protocol とは別に次の要求が発生する。

- 長時間処理を呼び出し元から切り離したい。
- 処理を job として投入し、job ID を受け取りたい。
- job の `queued`、`running`、`succeeded`、`failed` を管理したい。
- 複数 worker process を事前起動し、再利用したい。
- worker の状態を親 process から確認したい。
- worker 内で発生した例外を job の失敗として保持したい。
- FastAPI 等の上位 server から、job 投入と結果取得を分離したい。
- 装置ごとに `Process`、`Queue`、`Manager`、job 管理を再実装したくない。

これらは「装置をどう動かすか」という Ctrl 本来の責務ではない。

各 Ctrl に multiprocessing の実装を直接書き込むと、装置固有コードと process 管理コードが密結合する。また、装置が増えるたびに worker loop、job queue、状態管理をコピーすることになる。

`process-executor` は、この multiprocessing 側の共通部分を独立させるために作ったものである。

---

## 2. 設計の中心

0.0.0 の中心的な方針は次である。

> Ctrl を multiprocessing 用に作り直すのではなく、既存 Ctrl を worker process 内で生成し、その public method を job として実行する。

概念的な構造は次である。

```text
Application / Server
        |
        v
Multi Ctrl / Facade
        |
        v
ProcessExecutor
        |
        +-- job queue
        +-- job metadata
        +-- worker metadata
        +-- shared state
        +-- shared lock
        |
        +-- Worker Process 1
        |       |
        |       +-- Ctrl instance
        |
        +-- Worker Process 2
                |
                +-- Ctrl instance
```

`ProcessExecutor` は装置 protocol、TCP、serial、モーター、ステージ等を知らない。

worker process 内では概念的に次を実行するだけである。

```python
handler = getattr(instance, handler_name)
result = handler(*args, **kwargs)
```

したがって、`ProcessExecutor` は装置非依存である。

---

## 3. なぜ worker 専用 Ctrl を作らないのか

既存 Ctrl がすでに次のように存在するとする。

```python
class ExistingCtrl:
    def __init__(
        self,
        host: str,
    ) -> None:
        ...

    def move(
        self,
        position: int,
    ) -> bool:
        ...
```

multiprocessing のためだけに、

```text
ExistingCtrl
    ↓
ExistingWorkerCtrl
    ↓
ExistingWorkerFactory
```

という専用クラス群を作ると、既存 Ctrl とほぼ同じ constructor や delegation を繰り返すことになる。

0.0.0 では、この重複を避けるため `worker_class` I/F を持つ。

```python
executor = ProcessExecutor(
    worker_class=ExistingCtrl,
    worker_kwargs={
        "host": "192.168.0.10",
    },
    worker_count=2,
    start_method="spawn",
)
```

各 worker process 内で `ExistingCtrl(...)` が生成される。

既存 Ctrl は multiprocessing を意識する必要がない。

---

## 4. 継承ではなく composition / delegation を使う理由

装置側 Ctrl が `ProcessExecutor` を継承する設計にはしていない。

関係は次である。

```text
Multi Ctrl
    has-a
ProcessExecutor
```

すなわち composition / delegation である。

この設計により、次を維持できる。

- 装置 Ctrl の class hierarchy を multiprocessing に支配されない。
- 既存 Ctrl を変更せず利用できる。
- ProcessExecutor を装置非依存に保てる。
- multi 側 public API を装置ごとに自由に設計できる。
- ProcessExecutor を別の制御系にも再利用できる。

---

## 5. worker の考え方

worker は handler 専用 process ではない。

`move()` 用 process、`get_position()` 用 process、`query()` 用 processのような構成にはしない。

一つの worker は一つの worker-local instance を保持し、queue から受け取った job に応じて異なる method を実行する。

```text
Worker 1
  Ctrl instance
    |- query()
    |- get()
    |- move()
    `- ...

Worker 2
  Ctrl instance
    |- query()
    |- get()
    |- move()
    `- ...
```

handler の種類と worker の寿命を分離する設計である。

---

## 6. worker instance の生成方法

0.0.0 には二つの生成方法がある。

### 6.1 `worker_class`

通常の既存クラスをそのまま worker にする場合に使用する。

```python
executor = ProcessExecutor(
    worker_class=MyCtrl,
    worker_args=(arg1, arg2),
    worker_kwargs={
        "option": value,
    },
    worker_count=2,
    start_method="spawn",
)
```

各 worker process 内で概念的に次が実行される。

```python
instance = MyCtrl(
    *worker_args,
    **worker_kwargs,
)
```

既存 Ctrl をそのまま worker-local instance として使用する場合はこちらが基本である。

### 6.2 `worker_factory`

worker の生成時に `SharedContext` が必要な場合に使用する。

```python
def create_ctrl(
    context: SharedContext,
) -> MyCtrl:
    return MyCtrl(
        shared_state=context.state,
        lock=context.lock,
    )


executor = ProcessExecutor(
    worker_factory=create_ctrl,
    worker_count=2,
    start_method="spawn",
)
```

`worker_factory(context)` は、共有 state や process-safe lock を worker instance に渡したい場合の I/F である。

### 6.3 両者の関係

`worker_factory` と `worker_class` は同時に指定できない。

`worker_args` と `worker_kwargs` は `worker_class` 専用である。

---

## 7. job の実行モデル

job は `submit()` で投入する。

```python
job_id = executor.submit(
    "move",
    motor,
    pulse,
)
```

`submit()` は handler の完了を待たない。返すのは handler の戻り値ではなく job ID である。

状態遷移は次である。

```text
queued
  |
  v
running
  |
  +--> succeeded
  |
  `--> failed
```

job metadata には次を保持する。

- `job_id`
- `handler`
- `args`
- `kwargs`
- `state`
- `worker_id`
- `result`
- `error`
- `created_at`
- `started_at`
- `finished_at`

一つの job は `job(job_id)`、全 job は `jobs()` で取得する。

---

## 8. `submit()`、`result()`、`wait()` の役割

### `submit(handler, *args, **kwargs) -> str`

job を queue に投入し、job ID を返す。

### `result(job_id)`

完了済み job の結果を取得する。

この method は待機しない。まだ `succeeded` していない場合は `RuntimeError` になる。

### `wait(job_id, timeout_sec=None, interval_sec=0.01)`

job の完了を明示的に待つ。

`succeeded` なら handler の戻り値を返す。

`failed` なら `RuntimeError` を送出する。

`timeout_sec` を超えると `TimeoutError` を送出する。

この分離により、上位 Ctrl は用途に応じて非同期 API と同期 API のどちらも構成できる。

---

## 9. worker metadata

worker metadata は次を持つ。

- `worker_id`
- `pid`
- `state`
- `current_job_id`
- `current_handler`

worker state は次である。

- `idle`
- `running`
- `stopped`
- `dead`

一つの worker は `worker(worker_id)`、全 worker は `workers()` で取得する。

`submit()`、`worker()`、`workers()` の実行時には dead worker を確認し、設定した `worker_count` を下回っていれば補充する。

---

## 10. SharedContext

`SharedContext` は次の共有 object をまとめた container である。

```python
context.jobs
context.workers
context.state
context.lock
```

### `jobs`

ProcessExecutor が管理する job metadata の共有 mapping である。

### `workers`

ProcessExecutor が管理する worker metadata の共有 mapping である。

### `state`

利用側が必要に応じて使用する共有 mapping である。

### `lock`

`multiprocessing.Manager().RLock()` により生成する process-safe lock である。

この lock は `motion_lock` や `communication_lock` のような装置固有名を持たない。どの handler が lock を使うかは利用側 Ctrl の責務である。

---

## 11. 例外処理

worker method 内で例外が発生すると job は `failed` になる。

error metadata には次を保存する。

- exception type
- message
- traceback

failed job に対して `result()` または `wait()` を呼ぶと `RuntimeError` を送出する。

0.0.0 では、worker 側で発生した元 exception class を親 process 側で再構築して再送出する仕様にはしていない。

---

## 12. worker lifecycle

constructor では指定数の worker process を起動する。

現在の 0.0.0 は、constructor が各 worker の `idle` 登録完了まで待機する仕様ではない。worker process は起動後に worker-local instance を生成し、その後 `idle` として metadata を登録する。

job は worker の準備前でも queue に投入でき、worker が利用可能になった時点で処理される。

`close()` は生存 worker に stop sentinel を投入し、worker を `join()` した後、Queue と Manager を終了する。

`close()` は複数回呼んでも二回目以降は何もしない。

context manager としても使用できる。

```python
with ProcessExecutor(
    worker_class=MyCtrl,
) as executor:
    ...
```

終了 API は `close()` であり、`shutdown()` ではない。

---

## 13. logger

`ProcessExecutor` constructor は `logger` を受け取る。

```python
executor = ProcessExecutor(
    worker_class=MyCtrl,
    logger=logger,
)
```

0.0.0 では logger を親 process 側に保持するのみであり、ProcessExecutor core から worker process へ自動注入しない。

複数 process から同じ rotating log file を直接扱う logging aggregation は 0.0.0 の責務には含めていない。

---

## 14. directory 構成

```text
process-executor/
|- pyproject.toml
|- README.md
|- CHANGELOG.md
|- src/
|  `- process_executor/
|     |- __init__.py
|     |- executor.py
|     `- shared_context.py
|- tests/
|  |- test_process_executor.py
|  `- test_example.py
`- example/
   |- dummy_single.py
   |- dummy_ctrl.py
   |- example.py
   `- pmcd/
      |- try_move.py
      |- pm2cd_multi.py
      `- try_move_multi.py
```

`tests/` は pytest の test suite である。

`example/` は install package ではない。直接実行する sample / tutorial 用 directory である。

---

## 15. example 1: 自分で作ったクラスを multi 化する

対象は次である。

```text
example/dummy_single.py
example/dummy_ctrl.py
example/example.py
```

### 15.1 `dummy_single.py`

`DummySingle` は multiprocessing を知らない通常の同期クラスである。

```python
class DummySingle:
    def query(
        self,
        name: str,
    ) -> str:
        return "dummy:{}".format(name)

    def get(
        self,
        value: int,
        delay_sec: float = 0.5,
    ) -> int:
        time.sleep(delay_sec)
        return value
```

### 15.2 `dummy_ctrl.py`

`DummyCtrl` は `ProcessExecutor` を composition する。

この example では `worker_factory` I/F を示す。

```python
def create_dummy_single(
    context: SharedContext,
) -> DummySingle:
    _ = context
    return DummySingle()
```

`DummyCtrl.get()` や `DummyCtrl.query()` は結果ではなく job ID を返す。

```python
job_id = self._executor.submit(
    "get",
    value,
    delay_sec,
)
```

### 15.3 `example.py`

二つの worker を生成し、時間のかかる `get()` と `query()` を別 job として投入する。

```bash
cd example
python example.py
```

この example は、single class と multiprocessing facade を分離する基本形を示すものである。

---

## 16. example 2: 既存の制御クラスを multi 化する

PM2CD の example は次である。

```text
example/pmcd/try_move.py
example/pmcd/pm2cd_multi.py
example/pmcd/try_move_multi.py
```

### 16.1 `try_move.py`

single 側の基準となる実行例である。

```python
from pm2cd.pm2cd_ctrl import PM2CD
```

既存 PM2CD Ctrl を直接使用する。

### 16.2 `pm2cd_multi.py`

multi 側では PM2CD protocol、Communication、Device を再実装しない。

既存 Ctrl class を読み込む。

```python
from pm2cd.pm2cd_ctrl import PM2CD as PM2CDsingle
```

その class 自体を `ProcessExecutor` に渡す。

```python
self._executor = ProcessExecutor(
    worker_class=PM2CDsingle,
    worker_kwargs={
        "ip_address": ip_address,
        "port": port,
        "timeout_sec": timeout_sec,
        "channel_count": channel_count,
        "log_level": worker_log_level,
    },
    worker_count=1,
    start_method="spawn",
    logger=self._logger,
)
```

この構造では PM2CD 専用 worker class や PM2CD 専用 factory を作らない。

worker process 内で生成されるのは既存 `pm2cd.pm2cd_ctrl.PM2CD` そのものである。

### 16.3 `try_move_multi.py`

multi facade の利用例である。

```python
from pm2cd_multi import PM2CD
```

外側の呼び出し形は single 側に近い。

`pm2cd_multi.py` の `_execute()` は内部で `submit()` と `wait()` を組み合わせているため、この限定 example の public API は同期的に見える。

これは `ProcessExecutor` 自体が同期 API を強制しているのではなく、multi facade 側の設計である。

---

## 17. PM2CD example が worker_count=1 である理由

`ProcessExecutor` は worker ごとに worker-local instance を一つ生成する。

したがって、

```python
worker_count=4
```

なら原則として Ctrl instance も4個生成される。

既存 PM2CD Ctrl は constructor 内で実 TCP connection を所有するため、worker 数を増やせば複数 Ctrl / 複数 connection となる。

0.0.0 は、一つの TCP socket / Communication object を複数 worker process から共有する broker ではない。

そのため現在の PM2CD example は `worker_count=1` としている。

この example の目的は、既存 Ctrl を worker 専用に書き直さず worker process 内で実行できることを示す点にある。

---

## 18. どのようなケースに有益か

### 18.1 既存 Ctrl を変更したくない場合

すでに単体で動作している class を維持し、実行場所だけを worker process に移したい場合である。

### 18.2 multiprocessing の共通処理を装置ごとに再実装したくない場合

job queue、worker loop、job metadata、worker metadata、結果管理を共通化したい場合である。

### 18.3 上位 server と実処理を分離したい場合

FastAPI 等の上位層から job を投入し、実処理を worker process に任せる構成の基盤として利用できる。

### 18.4 job ID を中心に状態管理したい場合

```text
submit
  ↓
job ID
  ↓
state
  ↓
result / wait
```

という I/F にしたい場合である。

### 18.5 handler ごとに process を生成したくない場合

事前起動した generic worker を再利用したい場合である。

---

## 19. 追加設計が必要なケース

### 19.1 一つの物理 resource を複数 worker で共有する場合

一つの TCP socket、serial port、USB handle 等を複数 worker process から共有する場合は、resource owner process と IPC proxy 等の追加設計が必要である。

0.0.0 はその broker 機能を持たない。

### 19.2 thread のように同一 Python object を共有したい場合

process は address space が別である。通常の object reference を thread と同じように共有する実行器ではない。

### 19.3 超低 latency の shared-memory parallelism が必要な場合

0.0.0 は `multiprocessing.Manager` と `Queue` を利用する。高頻度数値計算向け shared-memory executor を目的にはしていない。

---

## 20. `spawn` 使用時の条件

macOS 等で `spawn` を使用する場合、実行 script は次の形式にする。

```python
def main() -> None:
    ...


if __name__ == "__main__":
    main()
```

worker class、worker factory、constructor 引数等は process 境界を越えて利用可能な形である必要がある。

本 repository の example はこの形式にしている。

---

## 21. tests

pytest test は `tests/` に置く。

```bash
pytest
```

`tests/test_process_executor.py` では次を検証する。

- `submit()` が非 blocking であること。
- 複数 worker が独立 job を並列実行すること。
- `SharedContext.state` と `lock` が worker から利用できること。
- `result()` が暗黙に待機しないこと。
- worker 内 exception が failed job になること。
- 引数と戻り値が process 境界を越えること。
- worker 数が維持されること。
- `close()` 後の `submit()` が拒否されること。
- `worker_class` で既存 class を直接生成できること。
- `worker_factory` と `worker_class` の排他条件。

`tests/test_example.py` では generic example を検証する。

PM2CD example は実機依存であるため pytest から実機操作は行わない。

---

## 22. install

repository root で次を実行する。

```bash
pip install -e .
```

`pyproject.toml` の version は 0.0.0 である。

Python は 3.10 以上を対象としている。

---

## 23. 最小使用例

既存 class が次であるとする。

```python
class MyCtrl:
    def __init__(
        self,
        base: int,
    ) -> None:
        self._base = base

    def run(
        self,
        value: int,
    ) -> int:
        return self._base + value
```

この class 自体を変更せず次のように実行できる。

```python
from process_executor import ProcessExecutor


def main() -> None:
    executor = ProcessExecutor(
        worker_class=MyCtrl,
        worker_kwargs={
            "base": 10,
        },
        worker_count=2,
        start_method="spawn",
    )

    try:
        job_id = executor.submit(
            "run",
            5,
        )

        result = executor.wait(
            job_id,
            timeout_sec=5.0,
        )

        print(result)
    finally:
        executor.close()


if __name__ == "__main__":
    main()
```

結果は `15` である。

---

## 24. 0.0.0 の責務境界

ProcessExecutor が担当するものは次である。

- worker process の生成
- worker-local instance の生成
- job queue
- job ID
- job state
- job result / error
- worker metadata
- shared state
- process-safe generic lock
- dead worker の検出と補充
- worker lifecycle
- Queue / Manager の終了

担当しないものは次である。

- 装置 protocol
- TCP / serial command
- 装置固有の排他規則
- 物理装置の動作完了判定
- 装置固有の error recovery
- 単一 Communication を複数 worker で共有する broker
- FastAPI / gRPC 等の外部 server
- process-safe rotating log aggregation

この責務境界を維持することが、0.0.0 の設計上の中心である。
