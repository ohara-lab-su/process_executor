# CHANGELOG

## v0.1.2

### Documentation / GitHub Pages

- GitHub Pages 用の Sphinx 構成を追加。
- `src/process_executor` を `sphinx-apidoc` で走査し、`autodoc` による API reference を build 時に自動生成する構成を追加。
- 手動 Markdown documentation と source code から生成する API reference を分離。
- README に Quick Start と読み順を追加し、既存の詳細な設計・example・責務境界の説明は保持。
- GitHub Actions から `docs/build.sh` を実行し、`docs/_build/html` を GitHub Pages に deploy する workflow を追加。
- PyPI用 worker

# process-executor v0.1.1

v0.1.1 では、PM2CD example の multi 構成を見直し、複数 worker process を使用しながら、物理装置との通信を1本に集約できる構成へ変更した。

`ProcessExecutor` core の基本 API は変更していない。v0.1.0 で導入した `worker_factory`、`worker_class`、`SharedContext` を利用し、PM2CD 側の example を再構成したバージョンである。

## PM2CD multi

`example/pm2cd/multi/` を、複数 worker process を使用する基本 multi sample として更新した。

構成は以下の通りである。

```text
example/pm2cd/
├── multi/
│   ├── client_sync.py
│   ├── pm2cd_multi.py
│   └── server.py
├── try_move_multi_client.py
└── try_move_multi_local.py
```

従来の PM2CD multi example では、worker ごとに `PM2CDsingle` を生成すると、それぞれが独立した `Communication` と TCP connection を持つため、同一装置に対して複数の通信経路が生成される問題があった。

v0.1.1 の `multi` では、`Communication` を manager process 側で1インスタンスだけ生成し、各 worker process の `PM2CDsingle` へ同じ Communication proxy を渡す構成へ変更した。

```text
PM2CDsingle worker 1 ─┐
PM2CDsingle worker 2 ─┼─> Communication proxy
PM2CDsingle worker 3 ─┘
                           |
                           v
                    Communication
                           |
                           v
                      TCP connection
                           |
                           v
                         PM2CD
```

これにより、

- `ProcessExecutor` 側では複数 worker process を利用する。
- PM2CD の controller logic は各 worker の既存 `PM2CDsingle` をそのまま利用する。
- 物理通信は1個の `Communication` に集約する。
- PM2CD 専用の worker class を新たに作成しない。

という構成とした。

worker 数は `PM2CD(..., worker_count=...)` で指定する。

example では `WORKER_COUNT` をコード側の設定値として使用する。コマンドラインオプションによる worker 数指定は行わない。

## PM2CD multi_shared

`example/pm2cd/multi_shared/` を追加した。

構成は以下の通りである。

```text
example/pm2cd/
├── multi_shared/
│   ├── client_sync.py
│   ├── pm2cd_multi.py
│   └── server.py
├── try_move_multi_shared_client.py
└── try_move_multi_shared_local.py
```

`multi_shared` は、`multi` の通信構成に加えて、worker process 間で controller の移動コンテキストを共有する sample である。

PM2CD の `move()` / `move_relative()` と `wait_for_complete()` は、直前の位置決め条件として以下の情報を利用する。

- target position
- backlash 指定
- backlash 有効状態
- backlash value

worker ごとに独立した `PM2CDsingle` を持つだけでは、`move()` を実行した worker と `wait_for_complete()` を実行した worker が異なる場合、この移動コンテキストを引き継げない。

v0.1.1 の `multi_shared` では、`worker_factory(context)` を使用して `PM2CDsingle` を生成し、`SharedContext.state` を `PM2CDsingle` の移動コンテキストとして利用する。

```text
                    SharedContext.state
                           ^
                           |
          +----------------+----------------+
          |                |                |
     worker 1         worker 2         worker 3
    PM2CDsingle      PM2CDsingle      PM2CDsingle
          \                |               /
           \               |              /
            +------ Communication proxy ---+
                           |
                           v
                    Communication
                           |
                           v
                         PM2CD
```

これにより、異なる worker process 間でも、

```text
move()
  |
  v
shared move context
  |
  v
wait_for_complete()
```

という状態引き継ぎが可能になった。

共有機構は multi class 内部で処理するため、呼び出し側に shared 専用 API は追加していない。

## multi と multi_shared の違い

`multi` は、複数 worker と単一 Communication による基本構成を示す。

controller 固有の状態共有は行わないため、worker-local な状態に依存しない呼び出し方法を使用する。

`multi_shared` は、単一 Communication に加えて `SharedContext` を利用し、PM2CD の移動コンテキストも worker 間で共有する。

そのため、`move()` と `wait_for_complete()` が別 worker で実行された場合でも、位置決め条件を引き継ぐことができる。

## 旧 example

v0.1.1 で置き換えた旧 multi example は、

```text
example/pm2cd/x1_multi/
```

へ移動した。

`x1_multi` は旧構成の保存用であり、v0.1.1 の PM2CD multi example は、

```text
example/pm2cd/multi/
example/pm2cd/multi_shared/
```

を使用する。

## v0.1.1 の位置づけ

v0.1.1 での主な変更は PM2CD example の multi-process 構成の更新である。

`ProcessExecutor` 自体は装置 protocol や PM2CD 固有の通信仕様を持たない。

物理通信の共有は PM2CD example 側で `Communication` を1個に集約し、controller 固有状態の共有が必要な場合のみ `SharedContext` を利用する。

この構成により、

```text
ProcessExecutor
    |
    +-- generic worker execution
    +-- SharedContext

PM2CD multi
    |
    +-- PM2CDsingle worker × N
    +-- Communication × 1

PM2CD multi_shared
    |
    +-- PM2CDsingle worker × N
    +-- Communication × 1
    +-- shared move context
```

という責務分離を維持したまま、PM2CD を複数 worker process で扱える sample とした。


## v0.1.0

### Shared Context

- `ProcessExecutor` の `SharedContext` を利用した handler 間のデータ共有に対応。
- `ProcessExecutor.shared_context` を通して、複数 handler から共通の `state` を参照できる構成を追加。
- 既存の single class を `worker_class` として使用する構造は維持し、共有処理は multi 側で管理する構成とした。
- 共有機能を利用する場合も、single class の公開 API および multi class の基本構造を変更せずに利用可能。

### Example

- `example/pm2cd/multi_share/` を追加。
- PM2CD を用いて `SharedContext` による handler 間のデータ共有を確認するサンプルを追加。
- `move()` で設定した target position を共有状態へ保存し、後続の `wait_for_complete()` が共有状態から取得する構成とした。
- 共有処理は multi class 内部で行い、呼び出し側から共有機構を意識するための専用 API は不要。
- `multi_share` 用に以下の構成を追加。
  - `example/pm2cd/multi_share/pm2cd_multi.py`
  - `example/pm2cd/multi_share/server.py`
  - `example/pm2cd/multi_share/client_sync.py`
  - `example/pm2cd/try_move_multi_share_local.py`
  - `example/pm2cd/try_move_multi_share_client.py`
- local および FastAPI server/client の双方で `multi_share` を確認できるテスト構成とした。


## v0.0.1

### Test / Example

- `process_executor` の動作確認用テストコードおよび example を整理。
- `example/pm2cd/` に、PM2CD を用いた実機テストを追加・整理。
- PM2CD のテストを以下の4構成に分離。
  - `try_move_single_local.py`
    - single 構成でのローカル実行テスト。
  - `try_move_single_client.py`
    - single 構成での FastAPI server/client 経由の実行テスト。
  - `try_move_multi_local.py`
    - `process_executor` を使用した multi 構成でのローカル実行テスト。
  - `try_move_multi_client.py`
    - `process_executor` を使用した multi 構成での FastAPI server/client 経由の実行テスト。
- FastAPI server/client 用コードを構成ごとに整理。
  - `example/pm2cd/single/`
    - `server.py`
    - `client_sync.py`
  - `example/pm2cd/multi/`
    - `pm2cd_multi.py`
    - `server.py`
    - `client_sync.py`
- single → single server/client → multi local → multi server/client の各段階を独立して確認できるテスト構成とした。

### Dependency

- PM2CD の server/client テストで dataclass を戻り値として扱うため、更新された `ese774_frame` が必要。
- `ese774_frame` の FastAPI auto-dispatch における dataclass のシリアライズ／復元対応を前提とする。
- このため、従来版の `ese774_frame` では PM2CD の `MotorStatus` などの dataclass を返す API のテストは実行できない。


## 0.0.0 - 2026-09-13

`process-executor` の初期版である。

本バージョンでは、通常の Python クラス、特に既存の装置制御 Ctrl クラスを、multiprocessing のためだけに書き直さず worker process 上で実行するための汎用 execution layer を実装した。

### ProcessExecutor

`ProcessExecutor` を実装した。

以下の機能を持つ。

- 指定数の worker process を起動する。
- worker process を job ごとに作り直さず再利用する。
- handler 名、位置引数、キーワード引数を job として queue に投入する。
- worker-local instance の public method を handler 名から取得して実行する。
- job ID を生成する。
- job state を `queued`、`running`、`succeeded`、`failed` として管理する。
- job metadata を共有 mapping に保持する。
- worker metadata を共有 mapping に保持する。
- worker-local handler 内で発生した `Exception` の type、message、traceback を job metadata に保持する。
- `state()` を実装する。
- `job()` を実装する。
- `jobs()` を実装する。
- `result()` による非待機の結果取得を実装する。
- `wait()` による明示的な完了待機を実装する。
- `worker()`、`workers()` による worker metadata 取得を実装する。
- dead worker を検出し、設定した `worker_count` まで補充する。
- `close()` により worker、Queue、Manager を終了する。
- context manager protocol を実装する。

終了 API は `close()` である。

### worker_factory

`worker_factory(context)` による worker-local instance 生成を実装した。

factory には `SharedContext` を渡す。

共有 state や共有 lock を worker instance の生成時に利用したい場合の I/F である。

### worker_class

既存クラスを worker 専用クラスへ書き直さず利用するため、`worker_class` を実装した。

次の引数を利用できる。

```python
ProcessExecutor(
    worker_class=ExistingCtrl,
    worker_args=(...),
    worker_kwargs={...},
)
```

各 worker process 内で `ExistingCtrl(*worker_args, **worker_kwargs)` を生成する。

`worker_class` の追加により、装置固有側に既存 Ctrl と同じ constructor / delegation を持つ worker class や factory を繰り返し実装する必要をなくした。

`worker_factory` と `worker_class` は同時に指定できない。

`worker_args` と `worker_kwargs` は `worker_class` 専用である。

### SharedContext

`SharedContext` を実装した。

次の共有 object を保持する。

- `jobs`
- `workers`
- `state`
- `lock`

`state` は利用側の共有状態用 mapping である。

`lock` は process-safe な generic `RLock` であり、装置固有の意味は持たない。

### worker model

handler ごとの専用 worker process は作らず、generic worker が worker-local instance を一つ保持し、job ごとに異なる method を実行する構成とした。

worker state として次を使用する。

- `idle`
- `running`
- `stopped`
- `dead`

constructor では指定数の worker process を起動する。

0.0.0 は constructor が全 worker の `idle` 登録完了まで待機する仕様ではない。worker process は起動後に worker-local instance を生成し、準備完了後に metadata を `idle` として登録する。

### job model

`submit()` は handler の終了を待たず job ID を返す。

job state として次を使用する。

- `queued`
- `running`
- `succeeded`
- `failed`

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

### exception

worker-local instance の handler 実行中に発生した `Exception` を job failure として捕捉する。

error metadata に次を保存する。

- exception type
- message
- traceback

failed job に対する `result()` / `wait()` は `RuntimeError` とする。

### logger

`ProcessExecutor(..., logger=...)` を受け取る I/F を実装した。

0.0.0 では logger は親 process 側に保持する。worker process への logger object の自動注入は行わない。

process-safe rotating log aggregation は ProcessExecutor core の責務には含めない。

### generic example

以下を収録する。

```text
example/dummy_single.py
example/dummy_ctrl.py
example/example.py
```

`DummySingle` は multiprocessing を知らない通常の同期クラスである。

`DummyCtrl` は `ProcessExecutor` を composition し、`worker_factory` を利用して `DummySingle` を worker process 上で実行する。

`example.py` は二つの worker に `get` と `query` を job として投入し、state と result を確認する実行例である。

### PM2CD example

以下を収録する。

```text
example/pmcd/try_move.py
example/pmcd/pm2cd_multi.py
example/pmcd/try_move_multi.py
```

`try_move.py` は既存 `pm2cd.pm2cd_ctrl.PM2CD` を直接利用する single 側の実行例である。

`pm2cd_multi.py` は既存 `pm2cd.pm2cd_ctrl.PM2CD` を `worker_class` として `ProcessExecutor` に渡す限定 multi facade である。

PM2CD 専用 worker class、Communication、Device の再実装は行わない。

`try_move_multi.py` は multi facade を利用する実機操作例である。

PM2CD example は `worker_count=1` とする。0.0.0 は一つの Communication / TCP connection を複数 worker process で共有する broker を実装していないためである。

### tests

pytest test directory を `tests/` とした。

`tests/test_process_executor.py` では、job model、parallel execution、SharedContext、例外、`close()`、`worker_class`、`worker_factory` の条件を検証する。

`tests/test_example.py` では generic example を検証する。

PM2CD example は実機依存であるため pytest から実機操作は行わない。

### 設計上の決定

- ProcessExecutor は装置 protocol を知らない。
- ProcessExecutor は Ctrl の public API を規定しない。
- multiprocessing のために既存 Ctrl を継承させない。
- reusable architecture は composition / delegation で構成する。
- handler と worker process を1対1に対応させない。
- worker 専用 Ctrl のコピーを要求しない。
- 通常の既存 Ctrl は `worker_class` で直接利用する。
- `SharedContext` が必要な場合は `worker_factory` を利用する。
- job submission と completion waiting を分離する。
- generic process lock と装置固有の排他 semantics を分離する。
- 外部 server、Web API、gRPC は ProcessExecutor の責務に含めない。
- process-safe rotating log aggregation は ProcessExecutor の責務に含めない。

### 既知の制約

- worker ごとに独立した worker-local instance を生成する。
- constructor は全 worker の初期化完了を待機しない。
- 一つの TCP socket、serial port、device handle 等を複数 worker process から共有する broker 機能は持たない。
- worker process へ logger object を自動配布しない。
- worker-local handler 内の `Exception` は元 exception class のまま親 process に再送出せず、error metadata を基に `RuntimeError` として通知する。
- worker-local instance の constructor / factory 生成失敗を job failure として親 process に通知する機構は持たない。
- worker process が handler 実行中に process-level で終了した場合、その job を自動的に `failed` へ変更する機構は持たない。
- `wait()` を timeout なしで使用した場合、process-level failure では待機が継続する可能性がある。
- `close()` の worker `join()` には timeout がない。
- worker process は daemon process である。
- `Manager` と `Queue` を利用するため、超低 latency の shared-memory execution engine を目的とした実装ではない。
- job の `succeeded` は Python handler が正常 return したことを表し、物理装置の動作完了を意味しない。
