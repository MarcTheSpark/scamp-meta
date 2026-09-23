# cb2 Scheduler simplification (2026-05-12)

Pass over `cb2/cb2/scheduler.py` to remove cruft and fix one latent bug. Committed as `e3e9ce2` on cb2/master.

## What changed

- **Removed `SchedulerKilledException`** — defined but never raised or caught anywhere.
- **Removed `__main__` demo block** — module-level demo, not a real test.
- **Removed commented-out debug prints** in `run()`. The information was useful, so it lives on as a guarded `_log_event_timing(event, rel_wait, abs_wait, actual_wait)` helper that early-returns unless DEBUG is enabled (so the format string isn't built in production).
- **Made `hold()` / `release()` protected** (`_hold` / `_release`). The only caller is the `held()` context manager in `clock.py`. Public surface stays just `held()`.
- **Replaced `_wait_if_held` busy-loop** (`while ... time.sleep(0.01)`) with `self._hold_event.wait()`. Required adding `self._hold_event.set()` to `kill()` so a killed-while-held scheduler unblocks and exits.
- **Inlined the timing-policy blend math.** Was three intermediate variables (`relative_target_time`, `absolute_target_time`, `target_time`) that all had `now` subtracted to get `wait_duration`. Now computed directly as `relative_wait_dur` and `absolute_wait_dur`, blended into `wait_duration`. Same math.
- **Tightened `_get_next_event` return type** to `QueueEvent | None`, dropped the redundant `if self._killed: return None` (the `while` already exits on `_killed`, and `self._queue[0] if self._queue else None` covers the kill-with-empty-queue case). `run()` now does `if next_event is None: break` instead of double-checking `_killed`.
- **`_scheduler` global typed as `Scheduler | None`.** `get_scheduler()` rewritten to use a local variable so the type checker can narrow through the `if sched is None` branch — PyCharm doesn't narrow on globals.

## The latent bug we fixed

`@dataclass(order=True)` on `QueueEvent` was ordering by **all** fields, including `action: Callable` and `metadata: Any`. When two events share the same `(t, priority)`, heapq tie-break would fall through to comparing functions → `TypeError: '<' not supported between instances of 'function' and 'function'`. Fixed with `field(compare=False)` on both.

Marc's reaction: "I didn't realize it would cause a typeerror, I just thought it would be random order which, who cares." Worth noting for the future — `order=True` always falls through to the next field unless you stop it.

## Why `_execute_event` re-acquires the lock and re-checks the head

Documented inline now. The pattern:

```python
with self._new_event:
    if self._queue and self._queue[0] == event:
        heapq.heappop(self._queue)
    else:
        return  # head changed under us; bail and let run() recompute
```

`_get_next_event` peeks at the head while holding the lock, then releases. Between the release and `_execute_event` re-acquiring, another thread can push a smaller-`t` event or `reschedule()` mutate `t`s. If we blindly `heappop` we'd consume the wrong event and stamp `_ideal_time = event.t` from the *intended* event onto bookkeeping for the popped one. The peek-then-confirm-then-pop pattern guarantees we only consume the event we computed timing for.

`with self._new_event:` here is acquiring the underlying `Lock` of the `Condition` — not using its wait/notify abilities. The condition-specific methods (`wait`, `notify_all`) are used in `_get_next_event`, `schedule_action`, `_release`, `kill`, etc., to coordinate the scheduler thread waking up when the queue changes.

## What we deferred

- **`time(wake=False)` parameter.** No internal caller passes `wake=True`. We left it in because the scheduler may have value to external library users outside clockblocks, and `wake=True` is meaningful for a non-scheduler thread that just wants to nudge the loop. Worth revisiting if it's still unused at 1.0.
- **`hold()` / `release()` made protected, not removed.** Same reasoning — external users *might* want them, but the canonical pattern is the `held()` context manager.

## PLAN.md touch-up

Step 8 in PLAN.md referenced `scheduler.hold()` directly; updated to `scheduler.held()` context manager since that's the public surface now.
