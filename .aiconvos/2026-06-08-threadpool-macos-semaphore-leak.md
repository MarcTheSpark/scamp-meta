# cb2 fork thread pool + the macOS "leaked semaphore objects" wart

Context: implementing Step 9 (fork thread pool) in cb2, and while doing so, tracked down the
long-standing macOS warning students hit:

    resource_tracker: There appear to be 2 leaked semaphore objects to clean up at shutdown

(See the scampsters thread "leaked semaphore objects" — Mac-only, triggered by `fork()`, never
definitively diagnosed, declared harmless-but-annoying.)

## Root cause (finally pinned down)

The original clockblocks pool is `multiprocessing.pool.ThreadPool(processes=pool_size)`. `Pool.__init__`
builds a `_change_notifier = self._ctx.SimpleQueue()`. Under the **spawn** start method (macOS default
since 3.8; Linux defaults to fork), that multiprocessing `SimpleQueue` allocates `_rlock` + `_wlock`,
each a `SemLock` = a real **named POSIX semaphore**. That's exactly **2** semaphores → the "2 leaked
semaphore objects" message.

Why Mac-only and why "leaked":
- The semaphores are created on every platform, but under spawn the `resource_tracker` daemon actively
  tracks them and complains about any not unlinked before shutdown. Under Linux/fork it doesn't police
  them the same way.
- The pool registers an atexit finalizer that unlinks them on a *clean* exit (so a plain script that
  ends normally shows nothing — I verified this). The warning fires on an **abrupt** exit — i.e. the
  student hits Ctrl-C to stop a playing piece before the finalizer runs.

Verified empirically (Linux, forcing `set_start_method("spawn")` + tracing `SemLock.__init__`):
`ThreadPool(4)` → 2 SemLocks; `ThreadPoolExecutor(200)` → 0 SemLocks. NB: a *clean* normal exit shows
no warning either way — the abrupt-exit path is what surfaces it.

It is NOT the `threading.BoundedSemaphore` (that's a pure-Python counter, no OS resource, no tracker),
and NOT the earlier under-release bug (that manifests as "ran out of threads", a different symptom).
Also confirmed nothing else in scamp/clockblocks/scamp_extensions or their runtime deps creates mp
semaphores — `ThreadPool` was the sole `multiprocessing` user.

## The fix

Use `concurrent.futures.ThreadPoolExecutor` instead. Pure-threading, zero multiprocessing/semaphores,
and spawns workers **lazily** up to `max_workers` — bonus win: an idle `Session` no longer eagerly
creates 200 OS threads (`ThreadPool` pre-spawns all of them).

API swap in `_run_in_pool`: `apply_async(callback=, error_callback=)` → `submit(...).add_done_callback`.
A single done-callback releases the semaphore and surfaces `future.exception()` (guarded by
`future.cancelled()`, since `kill()` now uses `shutdown(wait=False, cancel_futures=True)`).

## Design subtlety worth remembering

Keep the `BoundedSemaphore` cap — it's **load-bearing**, not an optimization. Forked clocks occupy their
worker for their whole lifetime (they park in waits), and the executor's task queue is unbounded. Without
the cap, a fork submitted once all workers are busy would queue forever and never start → musical-time
deadlock. The cap (submit only if a slot is free, else raw Thread) keeps the executor queue empty.

Considered and rejected: just calling `pool.terminate()` reliably / via the context manager. Doesn't
help the Ctrl-C path, and doesn't address the eager-200-threads cost. Switching executors fixes both at
the root.
