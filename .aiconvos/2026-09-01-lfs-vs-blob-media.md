# LFS vs raw blob for docs media (svg / mp3) — RESOLVED 2026-09-14

## Resolution (2026-09-14)

Decided: both svg and mp3 → LFS, and purge raw binary blobs committed since the last push.
State found: svg/png/sf2 already LFS; the 6 tracked mp3s were raw blobs, all added in the
56 unpushed commits (nothing else binary was raw in that range). Added `*.mp3` to
`.gitattributes` and ran:

    git lfs migrate import --include="*.mp3" --include-ref=main --exclude-ref=origin/main

Scoping to `main` minus `origin/main` rewrote only the unpushed commits, so `origin/main`
stays an ancestor and the push is a normal fast-forward (no force). Gotcha: migrate rewrites
*every* ref pointing into the range, so the `backup/pre-lfs-migrate` branch got rewritten too —
it was never a real rollback point; the reflog was the actual fallback. After verifying the
result (pointers in HEAD, real audio smudged into the working tree via `git lfs checkout`,
fast-forwardable), deleted the backup, expired reflogs, and `git gc --prune=now` to evict the
raw blobs (.git 201M→133M). README note for cloners added (soundfont + docs media are LFS);
that README edit is left uncommitted pending review.

The rest below is the original (pre-decision) reasoning, kept for context.

---

# LFS vs raw blob for docs media (svg / mp3) — unsettled (original note)

Came up while committing the octave-line example renders. Not a decision so
much as a parked question with a leaning.

## Current state (not chosen for these reasons — inherited)

- `docs/_static/media` is **gitignored**; the few tracked media files are
  force-added past it.
- `.gitattributes` has `*.svg filter=lfs` (also sf2/tar.gz/dll/jpg/png). Added
  2020-01-18 in `7961d84` as a blanket "images → LFS" batch, back when the only
  svg was `ScampLogo.svg`. So the render svgs inherit LFS by accident, not intent.
- Result today: render **svgs → LFS**, **mp3s → raw blob** (no `*.mp3` rule).

## The heuristic

LFS pays off when a file is **big AND churns**. Blob history keeps every version
in full forever and every clone pulls them all, so churn × size is what bloats
the repo. LFS keeps only a pointer in history; the store holds the bytes, and it
costs indirection + GitHub LFS quota/bandwidth.

## Leaning: leave it as-is — but genuinely unsure

The tidy story is "churny svgs in LFS, static mp3s as blobs, and blobs keep mp3s
off the LFS quota." It's plausible but shaky both ways:

- **mp3s could churn** if playback / soundfont output changes — probably not
  *these* example renders, but the category isn't frozen.
- **svgs may not churn much either** — they're only ~100–200 KB, and it's not
  obvious any one render gets regenerated ~10 times over the project's life.

So the current split isn't clearly right; it's just not worth disturbing now.
Revisit only if media churn actually shows up in the history or LFS quota bites.
No `*.mp3` rule added; nothing migrated.
