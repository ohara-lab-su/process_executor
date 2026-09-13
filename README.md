# process-executor

Controller クラスから任意で利用する、最小限の multiprocessing 実行基盤です。

version: `0.0.0`

継承は使用しません。Controller が `ProcessExecutor` を所有し、委譲して利用します。

## 構成

```text
process-executor/
├── pyproject.toml
├── README.md
├── CHANGELOG.md
├── src/
│   └── process_executor/
│       ├── __init__.py
│       ├── executor.py
│       └── shared_context.py
├── example/
│   ├── __init__.py
│   ├── dummy_device.py
│   ├── dummy_ctrl.py
│   └── example.py
└── tests/
    ├── test_process_executor.py
    └── test_example.py
```

## ProcessExecutor の責務

- 汎用 worker process の事前起動
- job Queue
- 非同期 `submit()`
- job state 管理
- worker state 管理
- `multiprocessing.Manager()` による共有状態
- 全 worker から利用できる単一の共有 `lock`
- worker の確認と補充
- 明示的な `result()` / `wait()`
- shutdown

以下は扱いません。

- TCP / serial / COM などの通信
- 装置固有 protocol
- 装置固有 handler
- FastAPI
- gRPC
- 単一 Communication instance を複数 worker から使うための broker / proxy

## worker

worker は handler 専用ではありません。

各 worker は1個の worker-local controller instance を保持し、job Queue から受け取った handler 名を実行します。

```text
worker A
    └── controller instance A

worker B
    └── controller instance B
```

同じ worker が job ごとに異なる handler を実行できます。

## 非同期 public I/F

`submit()` は実処理の完了を待ちません。

```python
job_id = executor.submit("method", 123)
```

状態や結果が必要な場合だけ明示的に取得します。

```python
state = executor.state(job_id)
result = executor.result(job_id)
result = executor.wait(job_id)
```

`result()` は待機しません。未完了なら例外になります。
`wait()` だけが明示的に待機します。

## state と lock

`SharedContext` は以下を保持します。

```text
jobs
workers
state
lock
```

`state` は共有状態です。

`lock` は排他保証です。state の読み書きだけでは race condition を防げないため、排他が必要な handler は明示的に共有 `lock` を利用します。

排他規則は利用側 Controller の責務です。`ProcessExecutor` は `move` や `motion` といった装置固有概念を持ちません。

## example

`example/` は、実質的に動作確認可能な最小 Controller 構成です。

`dummy_device.py` の `DummyControl` は multiprocessing を知らない通常の制御クラスです。

`dummy_ctrl.py` の `DummyCtrl` が `ProcessExecutor` を所有し、`DummyControl` の public method を multiprocessing job として実行します。

```text
DummyCtrl
   |
   └── ProcessExecutor
          |
          +── worker A ── DummyControl A
          |
          +── worker B ── DummyControl B
```

インストール後、プロジェクト root から実行できます。

```bash
python -m example.example
```

## pytest

test dependency を含めてインストールします。

```bash
python -m pip install -e ".[test]"
```

テストは pytest で実行します。

```bash
pytest
```

`tests/` では、単なる呼び出し例ではなく、以下を実際に検証します。

- `submit()` が job 完了を待たずに返ること
- 複数 worker が job を並列実行すること
- shared state が parent process から見えること
- `result()` が暗黙に待機しないこと
- worker 例外が `failed` state として保存されること
- 引数と戻り値が process 境界を越えること
- worker が指定数起動すること
- example の `DummyCtrl` が実際に multiprocessing Ctrl として動作すること
