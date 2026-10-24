# process-executor


```{toctree}
:maxdepth: 3
:caption: Documentation

quickstart
api/modules
```


`process-executor` は、通常の Python クラスのメソッド呼び出しを、multiprocessing の worker process 上で job として実行するための execution layer です。

この Pages 本文は、今回与えられた `executor.py` と `shared_context.py` の実装だけを根拠として新規に記述しています。既存 README / CHANGELOG の文章は根拠として使用していません。

このライブラリの中心は、既存クラスを multiprocessing 専用クラスへ作り替えることではありません。worker process の中に通常のクラス instance を一つ作り、Queue から届いた handler 名を `getattr()` で method に解決し、その method を job として実行します。

```text
caller / application
        |
        | submit("move", ...)
        v
+-------------------------+
| ProcessExecutor         |
|                         |
| jobs / workers / state  |
| multiprocessing.Queue   |
+------------+------------+
             |
       +-----+-----+
       |           |
       v           v
   Worker A     Worker B
       |           |
       v           v
  Ctrl instance  Ctrl instance
       |           |
       v           v
   handler(...) handler(...)
```

つまり責務は次のように分離されます。

```text
既存クラス
    └─ 実際の処理、装置制御、計算、protocol、domain logic

ProcessExecutor
    └─ process、Queue、job ID、状態、結果、例外、worker lifecycle

SharedContext
    └─ process 間で明示的に共有する metadata / state / lock
```

---

# Quick Start

## 既存クラスをそのまま worker にする

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

`Calculator` には `multiprocessing.Process` も `Queue` もありません。

worker process の中では概念的に次の処理になります。

```python
instance = Calculator()

job = job_queue.get()
handler = getattr(instance, job["handler"])
result = handler(*job["args"], **job["kwargs"])
```

一つの worker-local instance は、一つの job だけのために作られるのではありません。worker process の起動時に一度作られ、同じ worker が取得した複数の job で再利用されます。

---

# 1. ProcessExecutor が抽象化しているもの

Python のクラスを multiprocessing で実行しようとすると、処理本体とは別に多くの infrastructure が必要になります。

たとえば、処理本体が次だけだったとします。

```python
class Controller:
    def move(self, position):
        ...
```

multiprocessing 化を Controller 自身へ直接書き込むと、Controller が次まで持つことになります。

```text
Controller
├─ move()
├─ Process の生成
├─ Queue の生成
├─ worker loop
├─ job ID
├─ job state
├─ result
├─ exception transport
├─ worker state
├─ worker restart
└─ shutdown
```

これは `move()` の意味と process management の意味を一つのクラスへ混在させます。

ProcessExecutor は後半を generic execution infrastructure として切り出します。

```text
Controller
└─ move()

ProcessExecutor
├─ Process
├─ Queue
├─ Manager
├─ jobs
├─ workers
├─ state
├─ lock
├─ submit
├─ result
├─ wait
└─ close
```

この分離によって、既存クラスを multiprocessing なしでも使える状態に保ちながら、必要な場所だけ上位から multi-process execution を組み合わせられます。

---

# 2. constructor の全体像

`ProcessExecutor.__init__()` は、worker の作り方を確定した後、multiprocessing infrastructure を構築します。

実装上の順序は次です。

```text
1. worker_count を検証
2. worker_factory / worker_class の指定を検証
3. worker factory を確定
4. multiprocessing context を取得
5. Manager を生成
6. jobs dictionary を生成
7. workers dictionary を生成
8. state dictionary を生成
9. RLock を生成
10. Queue を生成
11. 親 process 側の Process table を生成
12. closed=False
13. worker_count 個まで worker を起動
```

multiprocessing context は次で取得されます。

```python
multiprocessing.get_context(start_method)
```

したがって Manager、Queue、Process は同じ multiprocessing context から作られます。

`start_method=None` なら Python がその platform で選ぶ default が使われます。ProcessExecutor 自身が platform ごとに start method を強制するコードはありません。

---

# 3. worker_count

`worker_count` は 1 以上でなければなりません。

```python
if worker_count < 1:
    raise ValueError("worker_count must be 1 or greater")
```

設定値は executor の lifetime 中に維持すべき worker process 数として使われます。

constructor は最初にその数まで worker を起動します。

その後も dead worker を検出した場合、同じ `worker_count` に戻るまで replacement worker を起動します。

現在の source には、生成後に worker_count を変更する public API はありません。

したがってこれは dynamic autoscaling pool ではなく、constructor で決めた固定 target size を維持する worker pool です。

---

# 4. worker_class と worker_factory

worker-local instance の生成方法は二つあります。

## worker_class

既存クラスをそのまま使う場合です。

```python
executor = ProcessExecutor(
    worker_class=Controller,
    worker_args=("COM3",),
    worker_kwargs={"timeout": 1.0},
)
```

内部では `_WorkerClassFactory` が作られます。

この factory は worker process 内で呼ばれ、次を実行します。

```python
Controller(
    *worker_args,
    **worker_kwargs,
)
```

`SharedContext` は `_WorkerClassFactory.__call__()` に渡されますが、既存 controller class には渡しません。

このため既存クラスは ProcessExecutor 用の constructor signature を持つ必要がありません。

## worker_factory

worker-local instance の生成に SharedContext が必要な場合です。

```python
def create_controller(context):
    return Controller(
        shared_state=context.state,
        lock=context.lock,
    )


executor = ProcessExecutor(
    worker_factory=create_controller,
)
```

factory は worker process の中で:

```python
instance = worker_factory(context)
```

として呼ばれます。

`worker_factory` と `worker_class` は同時には指定できません。

`worker_factory` を使う場合、`worker_args` と `worker_kwargs` も指定できません。

この区別によって、

```text
既存クラスを無変更で使う
        -> worker_class

process-shared dependency を注入して生成する
        -> worker_factory
```

という二つの利用形を分けています。

---

# 5. worker process が保持する instance

worker main の最初で SharedContext を作ります。

```python
context = SharedContext(
    jobs=jobs,
    workers=workers,
    state=state,
    lock=lock,
)
```

次に:

```python
instance = worker_factory(context)
```

を一度だけ実行します。

その後に `while True` の job loop に入ります。

したがって worker-local instance の寿命は:

```text
worker process start
        |
        v
instance creation
        |
        v
job 1
        |
        v
job 2
        |
        v
job 3
        |
        v
STOP
        |
        v
worker process end
```

です。

job ごとに instance を作り直す構造ではありません。

これは stateful な controller を worker 内で保持できることを意味します。

ただし worker A の instance と worker B の instance は別 process にあります。

```text
Worker A                     Worker B
---------                    ---------
Controller A                 Controller B
self.x = 10                  self.x = 20
```

通常の instance attribute は共有されません。

---

# 6. SharedContext

`SharedContext` は非常に小さいクラスです。

保持するものは四つだけです。

```text
jobs
workers
state
lock
```

constructor は渡された object をそのまま instance attribute に保存します。

`SharedContext` 自体が IPC を実装するのではありません。

実際に process 間共有を成立させているのは、ProcessExecutor が Manager から生成した proxy object です。

```text
SharedContext
    |
    +-- jobs    -> Manager.dict()
    +-- workers -> Manager.dict()
    +-- state   -> Manager.dict()
    `-- lock    -> Manager.RLock()
```

SharedContext はこれらを一つに束ね、worker factory へ渡す interface です。

---

# 7. jobs と workers と state は同じ dictionary ではない

三つとも Manager dictionary ですが、役割は異なります。

`jobs` は ProcessExecutor が job execution metadata を管理するために使います。

`workers` は ProcessExecutor が worker execution metadata を管理するために使います。

`state` は executor core が schema を決めない shared mapping です。

```text
jobs
    executor が意味を定義

workers
    executor が意味を定義

state
    controller / application が意味を定義
```

この分離は重要です。

controller-specific state を jobs metadata の中へ混ぜる必要はありません。

逆に ProcessExecutor の worker state を application の generic state key として管理する必要もありません。

---

# 8. lock

lock は Manager の `RLock` です。

```python
self._lock = self._manager.RLock()
```

この lock は worker へ SharedContext 経由で渡されます。

しかし ProcessExecutor は handler 実行前後で自動的に lock を取りません。

つまり:

```python
handler = getattr(instance, handler_name)
result = handler(*args, **kwargs)
```

の外側に generic lock はありません。

そのため `worker_count > 1` なら複数 worker の handler は同時に実行され得ます。

shared state や単一 resource に対して排他が必要なら、controller 側が SharedContext.lock を使う設計にできます。

この形にしているため、すべての handler を無条件に serialise することなく、必要な critical section だけを利用側で定義できます。

---

# 9. submit

`submit()` は handler call を直接実行しません。

最初に closed flag を確認します。

closed なら:

```python
RuntimeError("ProcessExecutor is already closed")
```

です。

handler は文字列でなければなりません。

文字列でなければ `TypeError`、空文字なら `ValueError` です。

次に `_replenish_workers()` を呼びます。

その後 UUID4 hex から job ID を生成します。

```python
job_id = uuid.uuid4().hex
```

job metadata を `queued` で作ります。

```text
job_id
handler
args
kwargs
state = queued
worker_id = None
result = None
error = None
created_at
started_at = None
finished_at = None
```

そして Queue に execution payload を入れます。

Queue payload は metadata 全体ではなく:

```text
job_id
handler
args
kwargs
```

です。

最後に job ID を返します。

したがって `submit()` の return は handler result ではありません。

---

# 10. job ID の意味

job ID は一回の submit を識別する UUID4 hex です。

同じ handler を同じ arguments で二回 submit しても job ID は別になります。

```text
submit("move", 100) -> job A
submit("move", 100) -> job B
```

job ID を使って:

```python
executor.state(job_id)
executor.job(job_id)
executor.result(job_id)
executor.wait(job_id)
```

を呼びます。

job ID は worker ID とは別です。

job は worker に割り当てられる前に作られるため、queued の時点では `worker_id=None` です。

---

# 11. queued

`queued` は submit が metadata を作成した直後の state です。

この段階では:

```text
worker_id = None
started_at = None
finished_at = None
result = None
error = None
```

です。

Queue には job payload が入っていますが、どの worker が取得するかはまだ決まっていません。

ProcessExecutor には caller が worker ID を指定して submit する API はありません。

したがって job routing は generic shared Queue に任されています。

---

# 12. worker startup

`_start_worker()` は新しい worker ID を UUID4 hex で作ります。

次に multiprocessing Process を生成します。

target は `_worker_main` です。

渡されるものは:

```text
worker_id
worker_factory
job_queue
jobs
workers
state
lock
```

です。

その Process を daemon にします。

```python
process.daemon = True
```

そして `start()` します。

最後に親 process の `_processes` dictionary へ:

```text
worker_id -> Process object
```

として保存します。

---

# 13. 親側 _processes と共有 workers の違い

`_processes` は通常の Python dictionary です。

これは parent process が `Process` object を保持するために使います。

```text
_processes
worker_id -> multiprocessing.Process
```

一方 `workers` は Manager dictionary です。

こちらには:

```text
worker_id
pid
state
current_job_id
current_handler
```

が入ります。

つまり:

```text
_processes
    process lifecycle を操作するため

workers
    worker の状態を観測するため
```

です。

この二つを同一視しないことが内部構造を理解する上で重要です。

---

# 14. worker readiness

worker metadata は `_start_worker()` の親 process 側では登録されません。

worker process が実際に開始し、SharedContext を作り、worker factory で instance を生成した後に:

```python
workers[worker_id] = {
    ...
    "state": "idle",
}
```

と登録されます。

したがって:

```text
Process.start()
        |
        | 時間差があり得る
        v
factory(context)
        |
        v
workers[worker_id] = idle
```

です。

constructor は全 worker が `idle` になるまで待つ barrier を持ちません。

よって constructor が return した瞬間に、全 worker-local controller の initialization が完了しているとまでは source から保証できません。

---

# 15. idle

worker-local instance の生成が完了すると worker metadata は `idle` になります。

```text
worker_id
pid
state = idle
current_job_id = None
current_handler = None
```

idle worker は Queue.get() で次の job を待っています。

Queue は全 worker に共通です。

したがって複数 idle worker があれば、Queue から job を取得した worker がその job を実行します。

特定 worker への affinity はありません。

---

# 16. worker が job を取る

worker は:

```python
job = job_queue.get()
```

で block します。

通常 job を受け取ると payload から:

```text
job_id
handler
args
kwargs
```

を取り出します。

次に worker metadata を `running` に更新します。

```text
state = running
current_job_id = job_id
current_handler = handler_name
```

PID と worker ID も metadata に保持されます。

この時点で「どの worker が、どの job の、どの handler を実行しているか」が観測できます。

---

# 17. job を running にする

worker metadata の更新に続いて、jobs dictionary から対象 job metadata の snapshot を取ります。

そして:

```text
state = running
worker_id = current worker
started_at = current UNIX time
```

へ更新します。

このため queued から running への transition は worker が job を実際に取得した時点で起きます。

submit 時点では started_at は設定されません。

created_at と started_at の差は、job が Queue 上で待った時間を含みます。

---

# 18. handler dispatch

実行対象は:

```python
handler = getattr(instance, handler_name)
```

で取得します。

その後:

```python
result = handler(*args, **kwargs)
```

です。

ProcessExecutor は handler ごとの signature を事前に登録しません。

handler name に対応する attribute が存在するか、arguments が signature と一致するかは実行時に Python が判定します。

存在しない handler 名なら `getattr()` が例外になります。

arguments が不正なら method call が例外になります。

どちらも worker の `except Exception` に入るため job failure として記録されます。

---

# 19. handler 名と安全境界

`submit()` が検証する handler は:

```text
str であること
空文字でないこと
```

だけです。

`_` で始まる method を禁止する code や allowlist はありません。

つまり source の dispatch mechanism 自体は:

```python
getattr(instance, handler_name)
```

で取得できる attribute を対象にします。

外部 HTTP request 等から handler 名をそのまま受け取る場合、公開可能 handler を制限する responsibility は上位 layer にあります。

ProcessExecutor 自体を remote procedure call security boundary と考えるべきではありません。

---

# 20. succeeded

handler が正常 return すると result を job metadata に保存します。

```text
state = succeeded
result = handler return value
error = None
finished_at = current UNIX time
```

ここで重要なのは `succeeded` の意味です。

ProcessExecutor が確認しているのは Python handler call が exception を送出せず return したことです。

例えば handler が:

```python
return False
```

と返しても exception ではありません。

その場合 executor-level job state は succeeded です。

したがって physical device success や domain validation は handler/controller の責務です。

---

# 21. failed

handler execution が `Exception` を送出すると job state は failed になります。

保存する error は:

```text
type
message
traceback
```

です。

type は:

```python
exc.__class__.__name__
```

message は:

```python
str(exc)
```

traceback は:

```python
traceback.format_exc()
```

です。

result は None に設定されます。

finished_at も設定されます。

---

# 22. handler failure 後の worker

handler が failed になっても worker process は終了しません。

exception handling の後で worker metadata は:

```text
state = idle
current_job_id = None
current_handler = None
```

に戻ります。

したがって:

```text
job failure
    !=
worker failure
```

です。

通常の Python Exception は一つの job の失敗として隔離され、同じ worker-local instance は次の job を処理できます。

ただし handler が instance state を途中まで変更してから exception になった場合、その instance state を自動 rollback する機能はありません。

---

# 23. state(job_id)

`state()` は内部で `job(job_id)` を呼びます。

job が存在しなければ None です。

存在すれば metadata の `"state"` を返します。

したがって unknown job ID は exception ではなく None です。

これは `result()` / `wait()` の unknown job ID が KeyError になる挙動とは異なります。

---

# 24. job(job_id)

`job()` は shared jobs dictionary から metadata を取得します。

存在しなければ None。

存在すれば:

```python
dict(metadata)
```

として通常の dictionary に変換して返します。

Manager proxy をそのまま公開していません。

返された dictionary は snapshot です。

後から worker が shared metadata を更新しても、すでに取得した local dictionary が自動更新されるわけではありません。

---

# 25. jobs()

`jobs()` は全 jobs を走査し、各 metadata を通常の dictionary に変換します。

返り値は:

```text
job_id -> metadata snapshot
```

の dictionary です。

completed job を削除する code は source にありません。

したがって executor の lifetime 中に submit された job metadata は jobs dictionary に蓄積します。

大量の job を長時間投入する運用では、この retention は考慮対象になります。

---

# 26. worker(worker_id)

`worker()` は metadata を読む前に `_replenish_workers()` を呼びます。

したがって純粋な read-only operation ではありません。

dead worker が存在すれば、この呼び出しを契機に replacement worker が起動する可能性があります。

指定 worker ID が workers dictionary に存在しなければ None。

存在すれば metadata snapshot を返します。

---

# 27. workers()

`workers()` も最初に `_replenish_workers()` を呼びます。

その後、workers dictionary の全 metadata を snapshot にして返します。

dead worker metadata は replenish 時に `dead` に更新されますが、workers dictionary から削除されません。

replacement worker は新しい UUID の worker ID で追加されます。

そのため workers() は現在 alive な worker だけではなく、過去に dead になった worker metadata を含む可能性があります。

---

# 28. result(job_id)

`result()` は completed result の読み出しです。

wait ではありません。

unknown job ID:

```text
KeyError(job_id)
```

failed:

```text
RuntimeError(str(error_metadata))
```

succeeded 以外:

```text
RuntimeError("job is not completed")
```

succeeded:

```text
metadata["result"]
```

です。

元 worker で発生した exception class を parent process で同じ class として再送出する仕組みではありません。

---

# 29. wait(job_id)

`wait()` は polling loop です。

開始時刻は `time.monotonic()` で取ります。

loop 内で `job(job_id)` を繰り返し呼びます。

succeeded なら result。

failed なら RuntimeError。

timeout を超えたら TimeoutError。

それ以外なら:

```python
time.sleep(interval_sec)
```

して再確認します。

Condition や Event を使った通知型 wait ではありません。

---

# 30. timeout は execution cancellation ではない

`wait(timeout_sec=...)` が timeout しても Queue から job を削除しません。

running handler を停止しません。

job state を failed に変更しません。

したがって timeout の意味は:

```text
caller がこの wait を続けない
```

です。

execution deadline ではありません。

timeout 後も worker が job を実行し続け、後から succeeded になる可能性があります。

---

# 31. cancellation

source には `cancel(job_id)` がありません。

queued job を Queue から選択的に取り除く API もありません。

running handler に cancellation token を渡す仕組みもありません。

job 単位で worker process を terminate する API もありません。

したがって cancellation は現在の ProcessExecutor の機能ではありません。

---

# 32. priority

job queue は通常の multiprocessing Queue です。

priority field は job payload にありません。

priority queue でもありません。

したがって source には high/normal/low priority scheduling はありません。

---

# 33. ordering

submit は Queue.put を順に行います。

しかし worker_count が複数なら job は並行実行されます。

そのため completion order は submit order と一致する保証がありません。

```text
submit A
submit B

Worker 1: A ----長い----> complete
Worker 2: B -> complete
```

のように B が先に完了できます。

ProcessExecutor は completion order を submit order に並べ直す gather API を持ちません。

---

# 34. job metadata の timestamp

job には三つの timestamp があります。

`created_at` は submit 時。

`started_at` は worker が job を running にした時。

`finished_at` は succeeded/failed にした時。

したがって:

```text
queue wait
    = started_at - created_at

handler execution
    = finished_at - started_at

total latency
    = finished_at - created_at
```

を計算できます。

ProcessExecutor 自身に duration property はありませんが、metadata から導けます。

---

# 35. worker metadata

worker metadata は次です。

```text
worker_id
pid
state
current_job_id
current_handler
```

idle では current job/handler は None。

running では実行中の job ID と handler 名が入ります。

stopped では再び None。

dead は parent process の replenish が既存 metadata の state を変更します。

この metadata は monitoring や status endpoint の材料にできます。

---

# 36. worker ID

worker ID は worker process 起動ごとに UUID4 hex で生成されます。

これは pool slot index ではありません。

worker が死んで replacement が起動すると別 ID になります。

したがって worker ID は process instance identity として扱うのが実装に合っています。

---

# 37. stop sentinel

worker loop の停止には特別な文字列:

```text
__PROCESS_EXECUTOR_STOP__
```

を使います。

worker が Queue からこの値を受け取ると job payload として処理せず loop を抜けます。

loop を抜けた後、worker metadata を `stopped` に更新します。

stop は job ID を持つ通常 job ではありません。

---

# 38. close()

`close()` は二回目以降を無視する idempotent guard を持ちます。

最初に `_closed=True` にします。

次に `_processes` から alive process を集めます。

alive worker 数だけ stop sentinel を Queue に put します。

その後 alive process を順に join します。

最後に Queue を close し Manager を shutdown します。

この順序により、通常 shutdown では worker が Queue から STOP を受け取って loop を終了することを期待しています。

---

# 39. close() と running job

STOP sentinel は Queue の item です。

worker が現在 handler を実行中なら、その handler が return/exception して Queue.get() に戻るまで STOP を読みません。

したがって close は running handler を強制中断しません。

現在の `process.join()` に timeout はありません。

戻らない handler が存在すれば close が待ち続ける可能性があります。

---

# 40. context manager

`__enter__()` は executor 自身を返します。

`__exit__()` は `close()` を呼びます。

したがって:

```python
with ProcessExecutor(...) as executor:
    ...
```

で lifecycle を scope に結び付けられます。

ただし context manager だから running handler を強制終了できるわけではありません。

shutdown semantics は close() と同じです。

---

# 41. dead worker detection

`_replenish_workers()` は parent の `_processes` table を走査します。

各 Process に:

```python
process.is_alive()
```

を呼びます。

False の worker ID を dead list に入れます。

その後 dead process を table から pop して join します。

workers metadata が存在すれば snapshot を取り:

```text
state = dead
```

に更新します。

最後に `_processes` の要素数が worker_count に戻るまで新 worker を起動します。

---

# 42. replenish が呼ばれるタイミング

replenish は常時 background thread で監視しているわけではありません。

source では少なくとも:

```text
submit()
worker()
workers()
```

から呼ばれます。

したがって worker が死んだ瞬間に独立 watchdog が即座に replacement を作る architecture ではありません。

次に replenish を呼ぶ public operation が実行されたときに検出されます。

---

# 43. worker recovery と job recovery は別

worker が handler 実行中に process-level failure で死んだ場合を考えます。

worker metadata には current_job_id が残っている可能性があります。

job metadata は running の可能性があります。

replenish は dead worker を検出して replacement を起動します。

しかし source には:

```text
dead worker の current_job_id を取得
        |
        v
その job を failed にする
```

という code はありません。

したがって pool size は回復しても running job の state は自動修復されません。

これは重要な failure boundary です。

---

# 44. process-level failure

handler 内の Python Exception は try/except で捕捉できます。

しかし次は同じではありません。

```text
OS から process kill
native extension crash
segmentation fault
process の異常終了
```

これらでは worker loop の exception handling が実行されない場合があります。

その場合 job を failed に更新する code も実行できません。

ProcessExecutor の現在の recovery は worker process の補充までです。

---

# 45. factory failure

worker main は job loop に入る前に:

```python
instance = worker_factory(context)
```

を実行します。

この call は handler execution 用の try/except の外です。

したがって factory または worker_class constructor が失敗した場合、その failure を job metadata の failed として記録する path はありません。

まだ job を取得していないため、特定 job と対応付けることもしていません。

---

# 46. startup failure が継続する場合

worker factory が常に失敗する設定を考えます。

parent は Process.start() 自体には成功できます。

child が factory で終了します。

後で replenish がその dead process を検出すると replacement worker を作ります。

しかし configuration が同じなら replacement も同じ factory failure を起こす可能性があります。

source には worker startup retry backoff、startup failure counter、circuit breaker はありません。

---

# 47. daemon worker

worker Process は daemon です。

daemon process は親 process に従属します。

また Python multiprocessing の daemon process は child process を生成できません。

そのため handler の中からさらに multiprocessing.Process を起動する nested worker architecture には制約があります。

ProcessExecutor は一段の worker pool を構成するものと考えるのが実装に合っています。

---

# 48. Manager

Manager は次の共有 object を作るために使われます。

```text
jobs
workers
state
lock
```

Manager proxy を使うことで、別 process から Python dictionary に近い interface で metadata を読み書きできます。

一方で shared-memory array の直接アクセスとは異なり、proxy communication の overhead があります。

この executor は metadata と coarse-grained state を共有する設計です。

---

# 49. Queue

Queue は job dispatch の channel です。

job metadata の正本そのものを Queue の中だけに置くのではありません。

Queue item は worker が consume すると取り出されますが、jobs dictionary の metadata は残ります。

したがって parent は job 完了後にも job ID から state/result/error を参照できます。

```text
Queue
    execution request の配送

jobs
    execution record の保持
```

という役割分担です。

---

# 50. args / kwargs

submit の args と kwargs は二箇所に使われます。

まず jobs metadata に保存されます。

次に Queue payload にも入ります。

worker は Queue payload の args/kwargs を handler call に展開します。

このため非常に大きな object を arguments にすると、IPC と metadata retention の両方にコストが発生し得ます。

source に arguments の size limit はありません。

---

# 51. result の保存

handler return value は jobs Manager dictionary の metadata に保存されます。

したがって result も process 間で Manager が扱える object である必要があります。

非常に大きな numerical array を大量に返す用途では、この設計の IPC overhead を考える必要があります。

ProcessExecutor は shared-memory ndarray transport を特別実装していません。

---

# 52. job retention

completed job は jobs dictionary から削除されません。

source には:

```text
delete_job
purge_jobs
TTL
max_history
```

がありません。

したがって executor lifetime が長く submit 数が増え続ける場合、metadata と result の retention が増えます。

これは後から job ID で結果を取得できる設計と表裏一体です。

---

# 53. dead worker metadata retention

dead worker の Process object は `_processes` から削除されます。

しかし workers Manager dictionary の metadata は削除されません。

state を `dead` にして残します。

replacement worker は新 ID で追加されます。

したがって workers() は historical dead workers を含み得ます。

current live process table と worker history metadata は別です。

---

# 54. logger

constructor は `logger` argument を受け取り:

```python
self._logger = logger
```

として保持します。

しかし今回の source 内では `_logger` を使った logging call はありません。

worker main に logger を渡す code もありません。

したがって現在の実装から保証できるのは「parent executor が logger reference を保持する」ことまでです。

worker-safe logging system が実装されているとは言えません。

---

# 55. snapshot helper

`_snapshot()` は Manager proxy value を通常 dictionary に変換する helper です。

None なら空 dictionary。

それ以外なら `dict(value)`。

worker は job metadata を更新するとき、現在の metadata snapshot を取り、field を変更して jobs dictionary に再代入します。

つまり nested field を直接 mutate するのではなく、dictionary snapshot を作って置き換える pattern です。

---

# 56. timestamp helper

`_now()` は単純に:

```python
time.time()
```

を返します。

job metadata の wall-clock timestamp に使われます。

一方 wait timeout の測定には `time.monotonic()` を使います。

これは用途が異なります。

```text
time.time()
    metadata として記録する時刻

time.monotonic()
    timeout の経過時間測定
```

です。

---

# 57. parent から SharedContext を取得する

`shared_context` property は parent controller 用に新しい SharedContext object を返します。

中身は worker と同じ:

```text
jobs
workers
state
lock
```

の proxy reference です。

したがって worker factory だけでなく parent 側も shared state/lock にアクセスできます。

これは parent と worker が同じ controller-specific shared state を参照する設計に利用できます。

---

# 58. SharedContext は domain schema を持たない

`state` に何を保存するかは source で規定されていません。

例えば:

```text
target_position
last_direction
sequence
mode
```

のような key を利用側が定義できます。

しかしこれらは ProcessExecutor 標準 field ではありません。

そのため application ごとに state schema を明確にする必要があります。

generic dictionary であることは柔軟ですが、型や key の意味は利用側の責務になります。

---

# 59. shared state を使うべき場合

worker A で更新した情報を worker B が次の job で必要とする場合、通常の instance attribute だけでは足りません。

```text
Worker A
Controller A
self.target = 100

Worker B
Controller B
self.target は別 object
```

この target が controller 全体の logical state なら SharedContext.state に置く候補です。

一方、worker A の内部 cache のように worker A だけで完結する state は instance attribute のままで構いません。

shared state は「multiprocessing だから全部共有する」のではなく、意味的に共有すべきものに限定するのが自然です。

---

# 60. shared lock を使うべき場合

複数 worker が同じ state を read-modify-write する場合、複数 operation を atomic な critical section として扱う必要があることがあります。

その場合:

```python
with context.lock:
    current = context.state.get("counter", 0)
    context.state["counter"] = current + 1
```

のようにできます。

ProcessExecutor は state key ごとの lock を提供しません。

一つの generic RLock を提供します。

より細粒度の lock が必要なら利用側 architecture で追加する必要があります。

---

# 61. 一つの physical resource と複数 worker

worker-local controller instance が複数あっても、physical resource が一つとは限りませんし、逆も同様です。

例えば単一 TCP connection を一つの device に対して維持する必要があるなら:

```text
Worker Ctrl A ----+
Worker Ctrl B ----+--> resource owner / proxy --> TCP connection x1
Worker Ctrl C ----+
```

のような構造が必要になる場合があります。

ProcessExecutor は TCP connection ownership を実装していません。

worker process の execution と physical resource の ownership は別問題です。

---

# 62. worker_count を増やす意味

worker_count を増やすと、同時に handler を実行できる worker process 数が増えます。

しかしそれが安全な parallelism かどうかは handler が使う resource に依存します。

CPU calculation なら CPU core 数や IPC overhead が関係します。

device control なら device protocol が concurrent command を許すかが関係します。

single connection なら serialization layer が必要かもしれません。

ProcessExecutor は worker_count の適正値を自動判定しません。

---

# 63. CPU-bound job

multiprocessing process を使うため、Python thread とは異なる execution process になります。

CPU-bound Python code を複数 worker で実行する用途にも構造上は利用できます。

ただし job arguments/result を Queue/Manager 経由で転送する overhead があります。

非常に小さな calculation を大量に投げる場合、process IPC cost が支配的になる可能性があります。

source は batching を実装していません。

---

# 64. I/O-bound job

handler が blocking I/O を行う場合も worker process 上で実行できます。

caller process は submit 後すぐ job ID を受け取れます。

一方、同じ physical I/O resource を複数 worker が共有する場合の排他や connection ownership は executor core では決めません。

そのため I/O-bound だから worker_count を単純に増やせばよいとは限りません。

---

# 65. 上位同期 API

ProcessExecutor の内部は job-based ですが、上位 public API を job-based にする必要はありません。

```python
class MultiController:
    def __init__(self):
        self._executor = ProcessExecutor(
            worker_class=Controller,
            worker_count=2,
        )

    def move(self, position):
        job_id = self._executor.submit("move", position)
        return self._executor.wait(job_id)
```

とすれば caller からは通常の同期 method に見えます。

この形では multiprocessing infrastructure を facade の内部へ隠せます。

---

# 66. 上位非同期 API

job ID を public API に出したい場合は:

```python
def start_move(self, position):
    return self._executor.submit("move", position)

def move_state(self, job_id):
    return self._executor.state(job_id)
```

のようにできます。

HTTP server なら request と long-running job の lifetime を分離できます。

ProcessExecutor 自体は HTTP を知りません。

job model だけを提供します。

---

# 67. FastAPI 等との境界

ProcessExecutor は web framework ではありません。

request parsing、authentication、response schema、HTTP status code は扱いません。

しかし:

```text
POST /jobs
    -> submit()
    -> job_id

GET /jobs/{id}
    -> job()

GET /jobs/{id}/result
    -> result()
```

のような上位設計を構築する材料になります。

handler 名を外部入力から直接渡す場合は、前述のとおり allowlist を上位 layer で設けるべきです。

---

# 68. unknown job ID

API ごとに unknown ID の挙動が違います。

```text
state(id)  -> None
job(id)    -> None
result(id) -> KeyError
wait(id)   -> KeyError
```

この差は上位 API wrapper を作るときに意識する必要があります。

例えば HTTP 404 へ変換するなら、None と KeyError の両方を同じ application-level not-found に正規化できます。

---

# 69. failed result の exception

failed job に `result()` または `wait()` を呼ぶと:

```python
RuntimeError(str(metadata.get("error")))
```

です。

error dictionary 全体が string 化されます。

元 exception の traceback は job metadata に残っています。

詳細 diagnostics が必要なら:

```python
metadata = executor.job(job_id)
metadata["error"]["traceback"]
```

を参照できます。

---

# 70. result object と snapshot

job() が返す metadata は snapshot ですが、その中の result object 自体の mutability について ProcessExecutor は追加 copy を行いません。

Manager dictionary から取り出した metadata を dict 化して返すだけです。

deep copy API ではありません。

複雑な mutable result の semantics は multiprocessing/Manager serialization と object type に依存します。

---

# 71. submit arguments の検証範囲

submit は handler の type と空文字だけを検証します。

args/kwargs の type validation はしません。

handler signature との整合性も submit 時には検証しません。

そのため invalid arguments は worker process で handler call を行った時点で failed job になります。

「投入前に即時 validation error を返す」executor ではありません。

---

# 72. worker class constructor arguments

`worker_args=None` は空 tuple に変換されます。

`worker_kwargs=None` は空 dictionary に変換されます。

worker_class path では `_WorkerClassFactory` がそれらを保持します。

各 worker process は同じ configured args/kwargs を使って独立 instance を生成します。

worker ごとに異なる constructor arguments を自動割当する mechanism はありません。

必要なら worker_factory 側で process context や外部 configuration を使う設計が必要です。

---

# 73. factory に渡る worker ID

worker_factory の signature は:

```python
worker_factory(context)
```

です。

worker ID 自体は factory argument に含まれません。

SharedContext.workers に worker metadata が登録されるのは factory call の後です。

したがって factory が「自分の worker ID」を直接受け取る contract ではありません。

worker-specific identity が constructor に必要な場合は現在の factory interface だけでは直接提供されません。

---

# 74. worker metadata 登録前の failure

factory が成功して初めて worker metadata の idle record が作られます。

factory が失敗した場合、workers dictionary にその worker ID の metadata が一度も作られない可能性があります。

replenish は parent `_processes` から dead process を検出します。

metadata が None なら dead state update は行いません。

したがって startup failure worker が workers() の historical metadata に必ず残るとは限りません。

---

# 75. worker stop metadata

STOP を受け取ると while loop を break します。

その後 worker metadata を `stopped` にします。

これは normal shutdown path です。

dead は parent が abnormal/non-alive process を検出したときの state です。

```text
normal STOP -> stopped

unexpected death detected by parent -> dead
```

という区別があります。

---

# 76. close 後の metadata

close は Manager.shutdown() を行います。

そのため close 後に shared Manager proxy を通常どおり利用し続ける lifecycle は想定できません。

submit は `_closed` を明示的に検査します。

他の read API すべてが `_closed` を検査するわけではありませんが、Manager 自体が終了しているため close は executor の利用終了点として扱うべきです。

---

# 77. close の idempotency

close の先頭は:

```python
if self._closed:
    return
```

です。

そのため context manager の exit と明示 close が重なっても二重 shutdown を避けられます。

```python
executor.close()
executor.close()
```

の二回目は何もしません。

---

# 78. result の None

handler が正常に `None` を return した場合、job state は succeeded で result は None です。

failed job も metadata result は None です。

したがって result field だけでは成功/失敗を区別できません。

必ず state と組み合わせて解釈します。

`result()` は state を確認してから result を返すため、この区別を内部で行います。

---

# 79. error の None

queued/running/succeeded の正常 path では error は None です。

failed では error dictionary です。

handler が succeeded した場合、result 保存時に error=None を明示的に設定します。

job metadata の error field は exception transport 用です。

domain-level warning や validation message 用 field ではありません。

---

# 80. metadata update pattern

worker は jobs dictionary から current metadata を snapshot し、field を変更して全 dictionary を再代入します。

```python
metadata = _snapshot(jobs.get(job_id))
metadata["state"] = "running"
...
jobs[job_id] = metadata
```

同様に succeeded/failed update も行います。

この pattern では一つの metadata update は dictionary replacement として Manager に送られます。

複数主体が同じ job metadata を同時更新する拡張を将来行う場合、競合 semantics を検討する必要があります。

現在は主にその job を取得した worker が execution fields を更新します。

---

# 81. jobs dictionary と lock

ProcessExecutor 自身の job metadata update は SharedContext.lock を明示的には使っていません。

Manager dictionary operation と snapshot replacement を利用しています。

SharedContext.lock は generic controller-side shared state 用として提供されています。

将来複数 process が同じ job metadata を複雑に read-modify-write する機能を追加するなら、atomicity を別途検討する必要があります。

---

# 82. workers dictionary と lock

worker metadata update も lock を明示的には使いません。

worker 自身が自分の worker ID の record を置き換えます。

parent replenish は dead worker の record を state=dead に更新します。

worker death と parent detection の timing によって metadata の最終状態がどう見えるかは process scheduling に依存し得ます。

---

# 83. Queue の STOP 数

close は alive process 数を数え、その数だけ STOP を put します。

これは各 alive worker が一つずつ STOP を consume することを意図しています。

dead process 数ではなく alive process 数です。

ただし STOP は通常 job と同じ Queue に入るため、すでに Queue に通常 job が残っていれば FIFO 的にそれらの後で処理される可能性があります。

close は queued job を purge して即停止する実装ではありません。

---

# 84. close と queued jobs

close 時に Queue に残っている通常 job を削除する code はありません。

STOP を alive worker 数だけ追加します。

したがって Queue ordering により、worker が STOP より前に queued job を取得して処理する可能性があります。

「close を呼んだ瞬間に未開始 job を cancel する」という semantics ではありません。

---

# 85. submit と close の concurrency

source は `_closed` flag に対する thread lock を持ちません。

submit と close を同一 parent process の複数 thread から同時に呼ぶ場合の厳密な thread-safety contract は source に明記されていません。

ProcessExecutor の主たる concurrency model は worker process 側です。

parent-side multi-thread concurrent API calls を保証する library と解釈するには追加検証が必要です。

---

# 86. multiprocessing start method と platform

`get_context(start_method)` を使うため、明示 start method を一つの executor 内で一貫して利用できます。

macOS など spawn が一般的な環境では、worker class/factory が import 可能であることや main guard が重要になります。

```python
if __name__ == "__main__":
    main()
```

ProcessExecutor はこの guard を自動生成できません。

利用 application 側の責務です。

---

# 87. pickle と worker factory

spawn では worker process を起動するために target arguments を serialization する必要があります。

worker factory や `_WorkerClassFactory` が process boundary を越えます。

module top-level に定義された class/factory の方が扱いやすいのは Python multiprocessing の一般的な性質です。

ProcessExecutor source 自体は cloudpickle 等の追加 serializer を使っていません。

---

# 88. physical device object を直接渡す問題

すでに open 済みの device object、socket、serial object 等を parent から worker_args に入れれば常に安全に共有できる、とは source から言えません。

process boundary と start method に依存します。

また複数 worker で同じ physical connection をどう serialize するかも別問題です。

そのため resource は worker 内で生成するか、明示的な IPC owner/proxy architecture を設計する方が責務を明確にできます。

---

# 89. worker-local connection

worker_class constructor が connection を開く設計なら、worker_count 個の worker がそれぞれ constructor を実行します。

つまり connection も worker ごとに作られる可能性があります。

```text
worker_count = 4
Controller constructor opens TCP
        ->
TCP connection が最大4本
```

これが正しいかどうかは device semantics に依存します。

ProcessExecutor は connection cardinality を制御しません。

---

# 90. single resource broker

physical resource を一つにしたい場合、resource owner を一つの process/service に置き、worker-local controller は proxy を使う architecture が考えられます。

```text
worker 1 --+
worker 2 --+--> proxy --> owner --> device
worker 3 --+
```

これは ProcessExecutor source の機能そのものではありません。

ただし executor と resource ownership を別 layer にすることで、generic worker scheduling と device-specific serialization を分離できます。

---

# 91. job state machine

source から直接確認できる job state transition は:

```text
submit
  |
  v
queued
  |
  | worker receives
  v
running
  |
  +-- handler returns -----> succeeded
  |
  `-- Exception ----------> failed
```

cancelled、retrying、timeout、lost といった state はありません。

worker process death で running job が自動的に別 state へ移る transition もありません。

---

# 92. worker state machine

worker state は概念的に:

```text
factory success
      |
      v
    idle
      |
      | job
      v
   running
      |
      +-- job complete --> idle
      |
      `-- STOP is read only after returning to queue loop

idle -- STOP --> stopped
```

parent が process death を検出した場合 metadata は dead になります。

dead は worker 自身が正常 loop で設定する state ではありません。

---

# 93. state machine にない状態を作らない

上位 application が job state を UI に表示するとき、ProcessExecutor の state と application 独自 state を混ぜない方が明確です。

例えば physical device が moving であることと executor job が running であることは別概念です。

```text
executor state: running

device state: moving
```

handler が device command を発行してすぐ return する設計なら executor job は succeeded でも device は moving の可能性があります。

domain state は controller 側で別に表現する必要があります。

---

# 94. handler の粒度

ProcessExecutor は method call 一回を job とします。

しかしどの粒度の method を submit するかは利用側次第です。

```text
send_one_byte()
```

のような極端に細かい operation を大量 submit すると IPC overhead が大きくなります。

```text
measure_sample()
```

のような意味のある coarse-grained operation を一 job にする方が execution layer の価値が出る場合があります。

source は粒度を強制しません。

---

# 95. long-running handler

long-running handler は一つの worker をその間占有します。

worker_count=4 で4個の long-running handler が走れば、5個目の job は Queue で待ちます。

ProcessExecutor は preemption を行いません。

running handler を途中で別 worker へ移動することもありません。

---

# 96. starvation と fairness

Queue は generic multiprocessing Queue です。

source に handler priority や per-client quota はありません。

長時間 job と短時間 job を同じ Queue に入れると、worker availability によって短時間 job が待つ可能性があります。

厳密な scheduling fairness policy は ProcessExecutor が提供する機能ではありません。

---

# 97. backpressure

submit は Queue.put を行います。

Queue 作成時に maxsize を指定していません。

source に outstanding job 数を制限する backpressure API はありません。

producer が worker processing rate より速く submit し続ける場合、queued work と jobs metadata が増えます。

長時間 server で利用する場合は上位 layer で admission control を考える余地があります。

---

# 98. persistence

jobs/workers/state は Manager process の memory 上にあります。

close で Manager.shutdown() します。

persistent database ではありません。

parent process が終了した後も job history を保持する機能はありません。

durable task queue として利用する設計ではありません。

---

# 99. remote execution

worker は同一 Python multiprocessing environment の Process です。

network remote worker registration はありません。

message broker もありません。

複数 host に分散する executor ではありません。

一台の host 内で process isolation を使う execution layer です。

---

# 100. ProcessExecutor の適用範囲

source から見た適用範囲は、既存 Python class の method を local multiprocessing worker で実行し、その job と worker の状態を parent から追跡したいケースです。

特に有効なのは:

```text
既存 class を変更したくない
worker process を再利用したい
job ID が必要
state/result/error を後から取得したい
複数 worker が必要
一部 state を process 間共有したい
```

という要求が組み合わさる場合です。

---

# 101. ProcessExecutor が現在持たないもの

source に存在しない機能を明確にすると境界が分かります。

```text
cancel
retry policy
priority
job TTL
job purge
persistent storage
remote worker
dynamic resize
worker affinity
handler allowlist
execution deadline
startup readiness barrier
running-job recovery
metrics exporter
automatic resource broker
```

これらが必要なら追加設計が必要です。

---

# 102. 実例: 単純な計算

```python
class MathWorker:
    def square(self, value):
        return value * value
```

```python
with ProcessExecutor(
    worker_class=MathWorker,
    worker_count=4,
) as executor:
    ids = [
        executor.submit("square", value)
        for value in range(10)
    ]

    values = [
        executor.wait(job_id)
        for job_id in ids
    ]
```

job ID と submit order は保持できますが、実際の completion order は worker scheduling に依存します。

---

# 103. 実例: state を観測する

```python
job_id = executor.submit("run", argument)

print(executor.state(job_id))
print(executor.job(job_id))
```

job metadata を定期的に取得すれば上位 UI で status を表示できます。

ただし `job()` が返すのは snapshot なので、同じ dictionary を保持していても自動更新されません。

再取得が必要です。

---

# 104. 実例: worker を観測する

```python
workers = executor.workers()

for worker_id, metadata in workers.items():
    print(
        worker_id,
        metadata["pid"],
        metadata["state"],
        metadata["current_handler"],
    )
```

これにより現在 running handler を確認できます。

workers() 呼び出し自体が replenish を起こす可能性がある点には注意が必要です。

---

# 105. 実例: shared counter

```python
class CounterWorker:
    def __init__(self, state, lock):
        self._state = state
        self._lock = lock

    def increment(self):
        with self._lock:
            value = self._state.get("count", 0)
            value += 1
            self._state["count"] = value
            return value


def factory(context):
    return CounterWorker(
        state=context.state,
        lock=context.lock,
    )
```

この例では worker-local attribute ではなく、全 worker が同じ `state["count"]` を参照します。

read-modify-write を lock で囲みます。

---

# 106. 実例: parent から shared state を読む

```python
context = executor.shared_context

with context.lock:
    current = context.state.get("count", 0)
```

`shared_context` property により parent 側も同じ Manager proxy にアクセスできます。

worker と parent の双方が同じ state schema を使う場合に利用できます。

---

# 107. 実例: 同期 facade

```python
class MultiController:
    def __init__(self):
        self._executor = ProcessExecutor(
            worker_class=Controller,
            worker_count=2,
        )

    def query(self, command):
        job_id = self._executor.submit("query", command)
        return self._executor.wait(job_id)

    def close(self):
        self._executor.close()
```

caller は ProcessExecutor を直接知る必要がありません。

既存の同期 interface を保ったまま実処理を worker process へ移せます。

---

# 108. 実例: job-oriented facade

```python
class JobController:
    def __init__(self):
        self._executor = ProcessExecutor(
            worker_class=Controller,
            worker_count=2,
        )

    def submit_query(self, command):
        return self._executor.submit("query", command)

    def job(self, job_id):
        return self._executor.job(job_id)

    def result(self, job_id):
        return self._executor.result(job_id)
```

こちらは job ID を application API に露出する形です。

server や GUI の background operation と相性があります。

---

# 109. 実例: timeout 後に再確認する

```python
job_id = executor.submit("long_operation")

try:
    result = executor.wait(job_id, timeout_sec=1.0)
except TimeoutError:
    print(executor.state(job_id))
```

timeout は execution cancellation ではないため、後で:

```python
print(executor.state(job_id))
```

と再確認できます。

handler が完了していれば succeeded/failed に変わっています。

---

# 110. 実例: failed job の traceback

```python
job_id = executor.submit("operation")

try:
    executor.wait(job_id)
except RuntimeError:
    metadata = executor.job(job_id)
    print(metadata["error"]["type"])
    print(metadata["error"]["message"])
    print(metadata["error"]["traceback"])
```

worker traceback は error metadata に保存されます。

parent で元 exception object を再構築するわけではありません。

---

# 111. 実例: close

```python
executor = ProcessExecutor(
    worker_class=Controller,
    worker_count=2,
)

try:
    ...
finally:
    executor.close()
```

または:

```python
with ProcessExecutor(
    worker_class=Controller,
    worker_count=2,
) as executor:
    ...
```

です。

context manager の方が lifecycle を明示しやすい構造です。

---

# 112. API Reference

この Pages の API Reference は手書きしません。

`docs/build.sh` が build ごとに:

```bash
sphinx-apidoc \
    -o docs/api \
    src/process_executor \
    -f \
    --module-first
```

を実行します。

生成された `.rst` を Sphinx `autodoc` が処理します。

これにより source code の:

```text
module
class
method
signature
type annotation
docstring
inheritance
source view
```

を機械的に Pages に反映します。

---

# 113. 手動本文と自動 API の役割

自動 API Reference が得意なのは「何という class/method があり、signature と docstring が何か」です。

一方、次は source の個々の docstring だけでは把握しにくい内容です。

```text
worker-local instance の寿命
jobs と Queue の役割の違い
_processes と workers metadata の違い
worker recovery と job recovery の違い
wait timeout と execution cancellation の違い
SharedContext.state の意味
single physical resource と worker_count の関係
```

これらをこの手動本文で説明します。

したがって手動 Pages は API 一覧のコピーではありません。

---

# 114. documentation build

ローカルでは repository root から:

```bash
bash docs/build.sh
```

を実行します。

script は:

```text
docs/api を削除
docs/_build を削除
sphinx-apidoc で API stub 生成
sphinx-build で HTML 生成
.nojekyll 作成
```

を行います。

HTML 出力先は:

```text
docs/_build/html/
```

です。

---

# 115. GitHub Pages workflow

成果物では指定どおり:

```text
github/workflows/pages.yml
```

として出力しています。

実際の GitHub repository では GitHub Actions の規則に従って:

```text
.github/workflows/pages.yml
```

に配置する内容です。

workflow は main push と手動実行を trigger にし、Python と documentation dependency を install し、`docs/build.sh` を実行し、生成 HTML を Pages artifact として deploy します。

---

# 116. source と documentation の同期

API reference は build 時に source を解析するため、public API の追加・削除は自動生成部分へ反映されます。

一方、この本文は execution semantics を説明するため、内部挙動を変更した場合には人間が更新する必要があります。

例えば:

```text
job cancellation を追加
worker crash recovery を追加
readiness barrier を追加
job purge を追加
```

した場合、自動 API には method が現れますが、その設計上の意味は本文へ追記する必要があります。

---

# 117. 設計変更時の確認軸

job metadata を変える場合:

```text
submit
worker running update
success/failure update
state
job/jobs
result/wait
```

を一緒に確認します。

worker metadata を変える場合:

```text
worker startup
running
idle
stopped
dead
worker/workers
replenish
```

を確認します。

SharedContext を変える場合:

```text
parent shared_context property
worker main
worker_factory
controller-side usage
```

を確認します。

lifecycle を変える場合:

```text
STOP
Queue
join
Manager shutdown
dead worker
queued/running job
```

を確認します。

---

# 118. 現在の実装を一枚で読む

```text
ProcessExecutor.__init__
        |
        +--> get_context
        +--> Manager
        |     +--> jobs
        |     +--> workers
        |     +--> state
        |     `--> RLock
        |
        +--> Queue
        |
        `--> start workers
                |
                v
          _worker_main
                |
                +--> SharedContext
                +--> factory(context)
                +--> worker = idle
                |
                `--> Queue.get loop
                        |
                        +--> STOP -> stopped
                        |
                        `--> job
                             |
                             +--> worker running
                             +--> job running
                             |
                             +--> getattr(instance, handler)
                             +--> handler(*args, **kwargs)
                             |
                             +--> succeeded + result
                             |        or
                             `--> failed + error
                                      |
                                      v
                                  worker idle
```

これが現在の source code の中心構造です。

---

# 119. まとめ

ProcessExecutor は、既存 Python class を multiprocessing worker 上で再利用するための generic execution layer です。

最も重要な設計上の分離は次です。

```text
method の意味
    -> worker-local class

method をどの process で実行するか
    -> ProcessExecutor

job の execution record
    -> jobs

worker の execution record
    -> workers

controller 全体で共有する state
    -> SharedContext.state

process-safe critical section
    -> SharedContext.lock
```

この分離によって、既存クラスへ multiprocessing infrastructure を埋め込まず、worker pool、job ID、state/result/error、worker observation、shared context、shutdown を共通化できます。

同時に、現在の source が持たない cancellation、priority、persistent queue、remote execution、automatic job recovery 等を機能として誤認しないことも重要です。

API の表面的な一覧は source から機械生成し、Pages 本文ではこの execution model と責務境界を説明する構成にしています。

# 120. Source-derived API inspection

## `__init__`
Source line: `182`

実装中に現れる call:

- `ValueError`
- `_WorkerClassFactory`
- `multiprocessing.get_context`
- `self._manager.RLock`
- `self._manager.dict`
- `self._mp_context.Manager`
- `self._mp_context.Queue`
- `self._start_workers`

明示的な raise:

- `ValueError('worker_count must be 1 or greater')`
- `ValueError('worker_factory or worker_class must be specified')`
- `ValueError('worker_factory and worker_class cannot be used together')`
- `ValueError('worker_args is only valid with worker_class')`
- `ValueError('worker_kwargs is only valid with worker_class')`

この一覧は supplied source の AST から抽出した補助情報です。API の正式表示は Sphinx autodoc が build 時に source から生成します。

## `shared_context`
Source line: `248`

Docstring:

> Return the shared context for the parent controller.

実装中に現れる call:

- `SharedContext`

この一覧は supplied source の AST から抽出した補助情報です。API の正式表示は Sphinx autodoc が build 時に source から生成します。

## `submit`
Source line: `257`

Docstring:

> Submit a handler job and immediately return its job ID.

実装中に現れる call:

- `RuntimeError`
- `TypeError`
- `ValueError`
- `_now`
- `isinstance`
- `self._job_queue.put`
- `self._replenish_workers`
- `uuid.uuid4`

明示的な raise:

- `RuntimeError('ProcessExecutor is already closed')`
- `TypeError('handler must be str')`
- `ValueError('handler must not be empty')`

この一覧は supplied source の AST から抽出した補助情報です。API の正式表示は Sphinx autodoc が build 時に source から生成します。

## `state`
Source line: `296`

Docstring:

> Return a job state.

実装中に現れる call:

- `metadata.get`
- `self.job`

この一覧は supplied source の AST から抽出した補助情報です。API の正式表示は Sphinx autodoc が build 時に source から生成します。

## `job`
Source line: `305`

Docstring:

> Return a snapshot of one job.

実装中に現れる call:

- `dict`
- `self._jobs.get`

この一覧は supplied source の AST から抽出した補助情報です。API の正式表示は Sphinx autodoc が build 時に source から生成します。

## `jobs`
Source line: `314`

Docstring:

> Return snapshots of all jobs.

実装中に現れる call:

- `dict`
- `self._jobs.items`

この一覧は supplied source の AST から抽出した補助情報です。API の正式表示は Sphinx autodoc が build 時に source から生成します。

## `worker`
Source line: `323`

Docstring:

> Return a snapshot of one worker.

実装中に現れる call:

- `dict`
- `self._replenish_workers`
- `self._workers.get`

この一覧は supplied source の AST から抽出した補助情報です。API の正式表示は Sphinx autodoc が build 時に source から生成します。

## `workers`
Source line: `333`

Docstring:

> Return snapshots of all workers.

実装中に現れる call:

- `dict`
- `self._replenish_workers`
- `self._workers.items`

この一覧は supplied source の AST から抽出した補助情報です。API の正式表示は Sphinx autodoc が build 時に source から生成します。

## `result`
Source line: `343`

Docstring:

> Return the result of a completed job.

実装中に現れる call:

- `KeyError`
- `RuntimeError`
- `metadata.get`
- `self.job`
- `str`

明示的な raise:

- `KeyError(job_id)`
- `RuntimeError(str(metadata.get('error')))`
- `RuntimeError('job is not completed')`

この一覧は supplied source の AST から抽出した補助情報です。API の正式表示は Sphinx autodoc が build 時に source から生成します。

## `wait`
Source line: `360`

Docstring:

> Explicitly wait for one job and return its result.

実装中に現れる call:

- `KeyError`
- `RuntimeError`
- `TimeoutError`
- `metadata.get`
- `self.job`
- `str`
- `time.monotonic`
- `time.sleep`

明示的な raise:

- `KeyError(job_id)`
- `RuntimeError(str(metadata.get('error')))`
- `TimeoutError('waiting for job timed out')`

この一覧は supplied source の AST から抽出した補助情報です。API の正式表示は Sphinx autodoc が build 時に source から生成します。

## `close`
Source line: `391`

Docstring:

> Stop workers and shut down shared resources.

実装中に現れる call:

- `alive_processes.append`
- `process.is_alive`
- `process.join`
- `self._job_queue.close`
- `self._job_queue.put`
- `self._manager.shutdown`
- `self._processes.values`

この一覧は supplied source の AST から抽出した補助情報です。API の正式表示は Sphinx autodoc が build 時に source から生成します。

## `__enter__`
Source line: `412`

この一覧は supplied source の AST から抽出した補助情報です。API の正式表示は Sphinx autodoc が build 時に source から生成します。

## `__exit__`
Source line: `415`

実装中に現れる call:

- `self.close`

この一覧は supplied source の AST から抽出した補助情報です。API の正式表示は Sphinx autodoc が build 時に source から生成します。

## `_start_workers`
Source line: `418`

Docstring:

> Start the requested number of workers.

実装中に現れる call:

- `len`
- `self._start_worker`

この一覧は supplied source の AST から抽出した補助情報です。API の正式表示は Sphinx autodoc が build 時に source から生成します。

## `_start_worker`
Source line: `423`

Docstring:

> Start one generic worker.

実装中に現れる call:

- `process.start`
- `self._mp_context.Process`
- `uuid.uuid4`

この一覧は supplied source の AST から抽出した補助情報です。API の正式表示は Sphinx autodoc が build 時に source から生成します。

## `_replenish_workers`
Source line: `443`

Docstring:

> Remove dead workers and restore the configured worker count.

実装中に現れる call:

- `dead_worker_ids.append`
- `dict`
- `len`
- `process.is_alive`
- `process.join`
- `self._processes.items`
- `self._processes.pop`
- `self._start_worker`
- `self._workers.get`

この一覧は supplied source の AST から抽出した補助情報です。API の正式表示は Sphinx autodoc が build 時に source から生成します。

## `SharedContext`

`SharedContext` は四つの constructor argument を同名 attribute として保持します。

- `jobs`
- `workers`
- `state`
- `lock`

追加の method や validation は supplied source にはありません。
