# process-executor

[![en](https://img.shields.io/badge/lang-en-red.svg)](https://github.com/ohara-lab-su/process_executor/blob/main/README.en.md)
[![ja](https://img.shields.io/badge/lang-ja-yellow.svg)](https://github.com/ohara-lab-su/process_executor/blob/main/README.md)

`process-executor` は、
通常の Python クラスを multiprocessing
の worker process 上で実行するための汎用実行基盤である。
特に、すでに単体で完成している装置制御用 Ctrl クラスを、
multiprocessing のためだけに書き直さず利用することを主目的としている。

設計・方針自体も含めて検討中コードであるが、運用しながら詰めていくことになる。

本 README は導入手順だけではなく、
現在の設計理由、
責務分離、
内部構造、
public API、
制約、および実際の example をまとめた仕様書兼チュートリアルである。

---

## Quick Start

### インストール

repository root で実行する。

```bash
pip install -e .
```

### 最小構成

既存クラスを `worker_class` に渡す。

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

`Calculator` 自体には multiprocessing 用の実装を追加しない。各 worker process 内で `Calculator()` が生成され、`submit("add", 10, 20)` によって、その worker-local instance の `add(10, 20)` が実行される。

### 非同期に job を扱う

```python
job_id = executor.submit("add", 10, 20)

print(executor.state(job_id))
print(executor.job(job_id))
print(executor.workers())
```

`result()` は待機しない。完了待機が必要な場合は `wait()` を使用する。

---

## README の読み方

この README は次の順序で読むことを想定している。

1. **Quick Start** — 最小構成で実行する。
2. **なぜこのプログラムを作ったのか** — 導入目的を理解する。
3. **設計の中心** — 既存 Ctrl と multiprocessing を分離する理由を理解する。
4. **worker / job / SharedContext** — 内部実行モデルを理解する。
5. **example** — generic class、既存制御 class、PM2CD での具体的利用方法を見る。
6. **追加設計が必要なケース / 責務境界** — ProcessExecutor が意図的に扱わない範囲を確認する。

GitHub Pages では、この手動ドキュメントに加えて `src/process_executor` を Sphinx で機械的に解析した API reference を生成する。

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

現在の実装の中心的な方針は次である。

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

現在の実装では、この重複を避けるため `worker_class` I/F を持つ。

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

worker-local instance には二つの生成方法がある。

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

親 process 側からは `ProcessExecutor.shared_context` を通して同じ `SharedContext` を取得できる。

```python
context = executor.shared_context
context.state["key"] = value
```

### `lock`

`multiprocessing.Manager().RLock()` により生成する process-safe lock である。

この lock は `motion_lock` や `communication_lock` のような装置固有名を持たない。どの handler が lock を使うかは利用側 Ctrl の責務である。

---

## 11. 例外処理

worker-local instance の handler 実行中に `Exception` が発生すると job は `failed` になる。

error metadata には次を保存する。

- exception type
- message
- traceback

failed job に対して `result()` または `wait()` を呼ぶと `RuntimeError` を送出する。

現在の実装では、worker 側で発生した元 exception class を親 process 側で再構築して再送出する仕様にはしていない。

なお、この exception handling の対象は handler 実行部分である。`worker_factory(context)` または `worker_class(...)` による worker-local instance の生成時に例外が発生した場合、その例外を job failure として親 process に通知する機構は現在の実装にはない。instance 生成に失敗した worker process は終了する。

また、handler 実行中に process 自体が強制終了するなど、Python の通常の `Exception` handling を通らず worker が消滅した場合、その時点で `running` であった job を自動的に `failed` へ変更する機構も現在の実装にはない。そのため、このような job に対して timeout を指定せず `wait()` すると待機が継続する可能性がある。

---

## 12. worker lifecycle

constructor では指定数の worker process を起動する。

現在の実装は、constructor が各 worker の `idle` 登録完了まで待機する仕様ではない。worker process は起動後に worker-local instance を生成し、その後 `idle` として metadata を登録する。

job は worker の準備前でも queue に投入でき、worker が利用可能になった時点で処理される。

`close()` は生存 worker に stop sentinel を投入し、worker を `join()` した後、Queue と Manager を終了する。

現在の `join()` には timeout を指定していない。そのため、worker が戻らない handler を実行中である場合、`close()` も worker 終了まで待機し続ける可能性がある。

worker process は `daemon=True` で起動する。したがって、worker-local handler からさらに multiprocessing child process を生成する用途を前提にはしていない。

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

現在の実装では logger を親 process 側に保持するのみであり、ProcessExecutor core から worker process へ自動注入しない。

複数 process から同じ rotating log file を直接扱う logging aggregation は ProcessExecutor core の責務には含めていない。

---

## 14. directory 構成

source と example の主要構成は次である。`build/`、`*.egg-info`、`__pycache__/` は生成物なので省略する。

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
   `- pm2cd/
      |- single/
      |  |- client_sync.py
      |  `- server.py
      |- multi/
      |  |- client_sync.py
      |  |- pm2cd_multi.py
      |  `- server.py
      |- multi_shared/
      |  |- client_sync.py
      |  |- pm2cd_multi.py
      |  `- server.py
      |- try_move_single_local.py
      |- try_move_single_client.py
      |- try_move_multi_local.py
      |- try_move_multi_client.py
      |- try_move_multi_shared_local.py
      |- try_move_multi_shared_client.py
      `- x1_multi/
```

`tests/` は pytest の test suite である。

`example/` は install package ではない。直接実行する sample / tutorial 用 directory である。

`example/pm2cd/x1_multi/` は v0.1.1 で置き換えた旧 PM2CD multi example の保存用 directory である。現在の example は `multi/` と `multi_shared/` を使用する。

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

PM2CD example は、single、基本 multi、shared multi を段階的に確認できる構成にしている。

```text
example/pm2cd/
|- single/
|  |- client_sync.py
|  `- server.py
|- multi/
|  |- client_sync.py
|  |- pm2cd_multi.py
|  `- server.py
|- multi_shared/
|  |- client_sync.py
|  |- pm2cd_multi.py
|  `- server.py
|- try_move_single_local.py
|- try_move_single_client.py
|- try_move_multi_local.py
|- try_move_multi_client.py
|- try_move_multi_shared_local.py
`- try_move_multi_shared_client.py
```

### 16.1 single

`try_move_single_local.py` は既存の PM2CD single class を直接使用する基準実行例である。

`single/server.py` と `single/client_sync.py` は、同じ single class を FastAPI server/client 経由で確認する example である。

single class 自体を `process-executor` 用に書き換えることはしない。

### 16.2 `multi`

`multi/pm2cd_multi.py` は、複数 worker process を使用する基本 multi example である。

各 worker process では既存 `PM2CDsingle` を生成する。ただし、各 worker が独立した TCP connection を所有しないように、物理通信は manager process 側の一つの `Communication` に集約する。

概念的な構造は次である。

```text
PM2CDsingle worker 1 --+
PM2CDsingle worker 2 --+--> Communication proxy
PM2CDsingle worker N --+          |
                                  v
                           Communication x 1
                                  |
                                  v
                           TCP connection x 1
                                  |
                                  v
                                PM2CD
```

`Communication` は `PM2CDsingle(..., communication=...)` の injection point を利用して各 worker に同じ proxy を渡す。

したがって、worker process は複数でも、PM2CD との物理通信経路は一つである。

PM2CD 専用の worker class を別に作成しない。controller logic は既存 `PM2CDsingle` を利用する。

worker 数は multi facade の constructor の `worker_count` で指定する。

example script ではコマンドラインオプションを使わず、コード内の `WORKER_COUNT` を設定値として渡す。

基本 `multi` では controller-local な移動コンテキストは共有しない。そのため、sample の `wait_for_complete()` は worker-local state に依存しないよう、`backlash=False` と明示した移動に対して `target_position` を明示して確認する。

### 16.3 `multi_shared`

`multi_shared/pm2cd_multi.py` は、`multi` の単一 Communication 構成に加えて、worker process 間で PM2CD の移動コンテキストを共有する example である。

`ProcessExecutor` の `worker_factory(context)` を使用して既存 `PM2CDsingle` を生成し、worker に渡された `SharedContext.state` を `PM2CDsingle` の `_move_context` として利用する。

```text
                 SharedContext.state
                         ^
                         |
        +----------------+----------------+
        |                |                |
   PM2CDsingle      PM2CDsingle      PM2CDsingle
    worker 1         worker 2         worker N
        \                |                /
         +------ Communication proxy -----+
                         |
                         v
                  Communication x 1
                         |
                         v
                       PM2CD
```

PM2CD の移動コンテキストには、`target_position`、backlash の有効状態、backlash value など、`move()` / `move_relative()` から `wait_for_complete()` へ引き継ぐ情報が保存される。

これを共有することで、`move()` を実行した worker と `wait_for_complete()` を実行する worker が異なっても、同じ移動コンテキストを利用できる。

共有機構は multi facade 内部の実装であり、呼び出し側に shared 専用 API を追加しない。

### 16.4 local と FastAPI server/client

各構成は local と FastAPI server/client の両方で確認できる。

```text
single
  try_move_single_local.py
  try_move_single_client.py

multi
  try_move_multi_local.py
  try_move_multi_client.py

multi_shared
  try_move_multi_shared_local.py
  try_move_multi_shared_client.py
```

multi facade の `_execute()` は内部で `submit()` と `wait()` を組み合わせるため、これら PM2CD example の public API は同期的に見える。

これは `ProcessExecutor` 自体が同期 API を強制しているのではなく、PM2CD multi facade 側の設計である。

---

## 17. PM2CD で複数 worker を使用する構成

`ProcessExecutor` は worker ごとに worker-local instance を一つ生成する。

したがって `worker_count=N` なら、PM2CD example では `PM2CDsingle` も N 個生成される。

ただし v0.1.1 の PM2CD `multi` / `multi_shared` では、`PM2CDsingle` が使用する `Communication` を各 worker 内で個別生成しない。

manager process 側で一つだけ生成した `Communication` の proxy を、すべての `PM2CDsingle` に injection する。

この設計により、

```text
controller instance: N
worker process:      N
Communication:       1
TCP connection:      1
```

という構成にできる。

重要なのは、この Communication 集約機能が `ProcessExecutor` core に組み込まれたわけではない点である。

`ProcessExecutor` は引き続き装置 protocol や TCP connection を知らない。PM2CD example 側が、PM2CD の dependency injection point と `BaseManager` を利用して物理 resource の ownership を構成している。

旧 example の `worker_count=1` 構成は `example/pm2cd/x1_multi/` に保存している。

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

一つの TCP socket、serial port、USB handle 等を複数 worker process から利用する場合は、resource owner process と IPC proxy 等の装置側設計が必要である。

`ProcessExecutor` core 自体はその broker 機能を持たない。

v0.1.1 の PM2CD `multi` / `multi_shared` は、その具体例として、PM2CD example 側に manager-owned `Communication` を置き、同じ proxy を複数 worker の `PM2CDsingle` へ injection している。

### 19.2 thread のように同一 Python object を共有したい場合

process は address space が別である。通常の object reference を thread と同じように共有する実行器ではない。

共有が必要な controller state は、必要に応じて `SharedContext.state` と `SharedContext.lock` を利用する。

### 19.3 超低 latency の shared-memory parallelism が必要な場合

現在の実装は `multiprocessing.Manager` と `Queue` を利用する。高頻度数値計算向け shared-memory executor を目的にはしていない。

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
- worker-local handler 内の `Exception` が failed job になること。
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

`pyproject.toml` の version は 0.1.1 である。

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

## 24. 現在の責務境界

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
- 単一 Communication を複数 worker で共有する装置固有 broker の実装
- FastAPI / gRPC 等の外部 server
- process-safe rotating log aggregation

この責務境界を維持することが、現在の設計上の中心である。
