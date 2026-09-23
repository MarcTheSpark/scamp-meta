# Parameter-animation timing: the demand-driven tick driver

**Applied 2026-09-14.** Lives in `scamp/src/scamp/_animation.py` (`_AnimationTickDriver`, one per clock
family keyed by the master clock, reference-counted). `_ParameterChangeSegment` (instruments.py) no longer
forks/waits: `change_note_parameter` builds the segments and calls `segment.begin(...)`, which stamps the
start, registers the segment with the driver for live sampling (skipped when silent or when there's no
audible change), and schedules a beat-anchored endpoint (`clock.schedule_action(_finalize, Moment.at_beat(...))`)
that lands the exact final value and stamps the end. Sequences chain via each endpoint's `on_finish`.
`abort_if_running` and `_finalize` race for a one-time finish through `_claim_finish()` (a per-segment lock),
so a note ending mid-animation and the stale endpoint can't both fire. `playback_settings.animation_tick_interval`
(default 0.02s) is the knob. The livelock guard is the strict-forward grid in `_AnimationTickDriver._schedule_tick`.

Golden run: 32/32 after regenerating one reference — `16_start_and_end_note`'s first note went
`start_beat=0` → `0.0` (int→float, numerically identical; its Score output was unchanged). The flip is a
`round()`-preserves-int-ness artifact: the new endpoint events perturb the family's event-time stream enough
that a zero-duration start resolves as a float. Transcription is otherwise byte-identical, confirming the
"notation preserved" claim held end-to-end (the one thing the prototype couldn't check). Risks #1 (endpoint
ordering) and the rest below are now exercised by that run.

---

Validated design, **not applied**. As of writing it lives only in the `3804020b` session
transcript (Aug 25) — the agent's draft files (`_animation_proposed.py`, `proto_driver.py`,
`integ_clean.py`, `stub_check.py`) were in job scratch and are gone. Roadmap has the short
version under Playback; this is the *why*.

## The problem measured

Concurrent parameter automation degrades timing for everything else. Headless repro (OSC parts
firing UDP at a dead port), hihat onset jitter:

| Condition | hihat IOI stdev | mean IOI |
|---|---|---|
| hihat alone | 0.28 ms | 250.0 ms |
| + `fm_sines` (the real example) | **13 ms** | 256 ms (drifts slow) |
| + 4 heavy anim voices | 7.2 ms | 328 ms |

## Root cause: cost is per *wake*, not per *event*

Automation emits each update as a scheduled leaf action on the one shared scheduler thread.
Each animation runs at its own rate and its own onset phase, so timestamps almost never
coincide — ~200 updates/sec become ~200 distinct scheduler wakes/sec. The scheduler pays its
timing cost per wake: each is an OS timed-wait that can return late and a point to queue behind
other threads for the GIL. Only ~2.8% of wall time was *executing*; the pain is all in the
waiting. On top of that, `play_note` forks one clock per animated parameter (four for a note
with pitch + volume + two params), multiplying thread contention.

Key run-loop fact (clockblocks `scheduler.py`): when the next event shares the current
timestamp, `wait_duration <= 0` and the `if wait_duration > 0` guard is skipped — same-`t`
events drain in one wake, back-to-back, no `condition.wait`. So *coincidence* is the lever.

## Why the obvious fix (pre-snap onto an absolute grid) is wrong

Snapping updates to a shared global grid so they coincide is the right instinct, but you can't
*pre-schedule* each animation's series onto an absolute-time grid. A note's boundaries live in
**beat-space**; the updates would live in **absolute time**. They only agree at the tempo in
effect when scheduled:
- **Faster mid-note** → end-beat arrives earlier in wall time, but the time-expressed gridlines
  don't move (reschedule deliberately leaves absolute-time moments alone) → orphaned updates
  fire *after* note-off.
- **Slower mid-note** → end-beat arrives later, but you only pre-scheduled out to the old
  projected end → the envelope tail never gets sampled; value freezes early.

The cruel irony: the very property making the absolute grid good for coalescing (time-expressed
⇒ survives tempo changes) is what makes it wrong at note boundaries. One pre-scheduled series
can't be both tempo-stable and beat-tracking.

## The resolution: one live tick driver, demand-driven

Don't pre-schedule series at all. **One recurring session-wide tick** on the absolute-time grid
(default 20 ms). Each tick walks a registry of active `(note, param)` animations and sets each
to `envelope.value_at(that clock's current beat)` — sampling *live*, mapping tick scheduler-time
back through each clock's *current* tempo curve. Liveness is judged at tick time, never baked in:
- faster tempo → note's end-beat passes at an earlier tick → animation retired, no ghost updates.
- slower tempo → end-beat passes later → keeps sampling until then → tail covered.
- endpoints exact → schedule one beat-anchored final update at the end beat.

**Demand-driven (reference-counted), not free-running** — no churn during silence:
- registry empty → no tick scheduled; scheduler parked on its empty-queue wait, fully idle.
- first animation registers → schedule first tick.
- each tick → process, then reschedule *only if* registry still non-empty.
- last animation ends → not rescheduled → back to idle.

Because there's a *single* driver, every concurrently-active animation samples at the same
instants by construction — coalescing for free, no need to reason about the grid's absolute
phase. Coincidence only has to hold among animations that overlap, and those always share the
one driver. ~200 wakes/sec → ~50 (one per tick), one driver thread instead of N forks.

## Validation (prototype, not the real tree)

- Standalone vs real clockblocks: idle = 0 ticks; wall-frame grid steady across a child
  tempo-doubling while the sampled value tracked the new tempo; beat-anchored endpoints landed
  the exact final value at the exact end beat; a sub-tick-length note set by its endpoint alone.
- In-scamp monkeypatch: hihat + `fm_sines` load **8–13 ms jitter / 256 ms drift → 2.33 ms /
  250.14 ms**, transcribed gliss still correct. Timing win *and* notation preserved.

## The design as drafted

- One demand-driven, tempo-invariant tick per clock family, entirely in a new
  `scamp/_animation.py` — **clockblocks untouched** (reuses `master.schedule_action(...,
  Moment.at_time(t))`, no new primitive).
- `play_note` forks **one** clock instead of four (the other half of the GIL win).
- `playback_settings.animation_tick_interval` (default 0.02 s / 50 Hz) is the single rate knob,
  replacing the `max_animation_rate` idea and the per-parameter resolution helpers.
- Transcription untouched; abort/interruption and note-end re-homed onto deregister +
  beat-anchored endpoint.

## Load-bearing gotcha (must survive future edits)

A naïve version **livelocked the whole scheduler**: recomputing the next tick from the
event-quantized `master.time` returned the *same* instant (float floor), so the tick
rescheduled itself at the current time in a tight loop and starved every clock. Fix: advance a
stored grid counter with strict forward progress; only jump ahead when behind wall time.

## Risks to weigh before applying

1. **Endpoint-vs-note-end ordering not exercised end-to-end in scamp.** The integration test
   kept a lifecycle fork; fork-removal + endpoints was only proven standalone. Needs a real
   `test_examples.py` golden run before trusting — the one thing the prototype couldn't check.
2. Pitch sampling drops ~250 Hz → 50–100 Hz. Inaudible for OSC/synth (they smooth control
   input), coarser for fast wide MIDI pitch bends. The knob covers it; note the default trade.
3. Stale aborted endpoints linger (queued until their end beat, then no-op) — harmless, untidy.
4. Registry thread-safety: lock briefly held across `master.schedule_action`; lock order looked
   consistent but wants a second set of eyes.

## Next step if resumed

Apply to the working tree and run `scamp/test/test_examples.py` — that's exactly what the
prototype couldn't exercise, and it'll catch endpoint/ordering regressions. Do not trust the
prototype's word on the golden tests. Related: `2026-07-09-clock-position-api.md` (the leaf-action
rework this builds on; the 700-vs-34 thread split lives there).
