# CHANGELOG

## 0.0.0

- 初版。
- `pyproject.toml` による src-layout package として構成。
- 装置非依存の `ProcessExecutor` を追加。
- 継承を使用せず、Controller からの委譲で利用する構成とした。
- 汎用 worker process の事前起動を実装。
- handler 専用 process を作らず、worker が任意 handler job を実行する構成とした。
- job Queue を実装。
- 非同期 `submit()` を実装。
- `state()`, `job()`, `jobs()`, `result()`, `wait()` を実装。
- worker state の参照を実装。
- `multiprocessing.Manager()` による `jobs`, `workers`, `state` の共有を実装。
- 全 worker から利用可能な単一の共有 `lock` を追加。
- dead worker の確認と補充を実装。
- `example/` に multiprocessing を知らない `DummyControl` を追加。
- `example/` に `ProcessExecutor` を利用する `DummyCtrl` を追加。
- `example/example.py` に実行可能なサンプルを追加。
- pytest による実テストを追加。
- 通信方式、装置 protocol、FastAPI、gRPC に依存しない構成とした。
