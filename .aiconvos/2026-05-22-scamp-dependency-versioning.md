# scamp dependency versioning strategy (2026-05-22)

Decided while discussing the cb2 → clockblocks 1.0 redesign: how scamp should
pin its sibling-package dependencies (clockblocks, expenvelope, pymusicxml).
**Not yet implemented** — this is a plan to execute around the clockblocks 1.0
release.

## Current state (the problem)

scamp 0.9.5 declares lower-bounds-only, set to the then-current versions:

```
pymusicxml>=0.5.7
expenvelope>=0.7.3
clockblocks>=0.6.10
```

With no upper bound, the day clockblocks 1.0 (cb2) hits PyPI, a fresh
`pip install scamp==0.9.5` will resolve clockblocks to 1.0 and break — and we
*know* 1.0 breaks scamp (Step 4 made cross-thread `wait()` raise
`WrongThreadError`, killing `play_note(clock=..., blocking=True)`).

## Decision

1. **No exact `==` pins.** scamp is a published library, not an app; `==` pins
   cause resolver conflicts and block bugfix uptake. Reproducibility, if wanted,
   belongs in a lockfile/constraints file, not in published dependency metadata.

2. **Add upper bounds capped at the next major** on the three co-developed core
   libs: `clockblocks>=0.6.10,<1`, `expenvelope>=0.7.3,<1`, `pymusicxml>=0.5.7,<1`.
   The usual "don't cap your deps" advice assumes you can't predict breakage —
   here we're *authoring* the breakage and scamp re-exports clockblocks symbols,
   so the coupling justifies the cap.

3. **From 1.0 on, honor semver in clockblocks:** break compatibility *only* at
   major bumps, never within a 1.x.y. The `<2` cap is scamp trusting
   clockblocks's own versioning discipline. So "is this change breaking?" becomes
   a deliberate question at each clockblocks release.

## Rollout around clockblocks 1.0

- **0.9.5 is permanently exposed** — PyPI won't let us rewrite its metadata.
  Unfixable. But exposure is small: only people who *explicitly* install
  `scamp==0.9.5` AND let clockblocks float get bitten.
- **Mitigation:** when clockblocks 1.0 ships, simultaneously ship a **scamp 0.9.6**
  identical to 0.9.5 except it adds `clockblocks<1`. Now "latest scamp" is safe;
  only the pin-old-scamp-and-float-clockblocks crowd can hit the bad combo.
- When scamp is adapted to clockblocks 1.0, that scamp release moves to
  `clockblocks>=1,<2`. Next intentional break → clockblocks 2.0 → scamp
  `>=2,<3` in a release adapted to it.

## Note

Lower bounds are currently set to the *newest* sibling version, not the *oldest
that works*. Strictly a floor means "minimum compatible," but for a
tightly-coupled set co-released by one author, floor = current-on-release is a
defensible choice (nudges users onto matching versions). Just be aware that's
what it does.

Related: cb2 redesign tracked in `cb2/PLAN.md`; Step 10 (SCAMP integration) is
where the `play_note(clock=...)` breakage gets resolved.
