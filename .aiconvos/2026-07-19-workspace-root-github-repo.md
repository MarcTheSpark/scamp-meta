# Should the workspace root become a GitHub repo?

*Started 2026-07-19. Status: leaning yes (superproject with submodules), not yet implemented.*

## The question

The workspace root (uv workspace `pyproject.toml`, `uv.lock`, `release.sh`, `uploadDocs.sh`, `CLAUDE.md`, `.claudeConvos/`, `scamp_tutor/`) is currently not in any git repo — only Nextcloud sync backs it up. Should it become a GitHub repo containing the five package repos?

## Current state (verified 2026-07-19)

- Root: not a git repo.
- `clockblocks`, `expenvelope`, `pymusicxml`, `scamp`, `scamp_extensions`: each its own git repo with GitHub origin; most have sourcehut mirrors; pymusicxml has an external contributor remote (maciej).
- `cb2`: git repo with **no GitHub remote** (clockblocks has a relative-path remote `../cb2` pointing at it). Local-only.
- `scamp_tutor`: **not in any git repo at all** — currently homeless, version-control-wise.
- scamp's wheel CI (`build-wheels.yml`) triggers only on `v*` tags and manual dispatch — not on ordinary pushes.

## Marc's requirements

1. People plausibly want to clone the *whole* interconnected workspace, packages and all.
2. Independence is one-directional only: expenvelope must stand alone from clockblocks, clockblocks from scamp. (So separate repos stay; the workspace just aggregates them.)
3. **No chore of keeping submodule versions up-to-date.** Already annoyed that every scamp release triggers wheel CI even when fluidsynth is untouched.
4. `.claudeConvos/` maybe *should* be committed — it holds meaningful development history. (Contradicts CLAUDE.md's current "local-only" declaration.)
5. GitHub presence partly motivated by making `scamp_tutor` easily fetchable.

## Recommendation: superproject + submodules, with automated pointer bumps

- Root becomes a GitHub repo; the five packages become submodules. `git clone --recurse-submodules` hands someone the whole workspace.
- Submodule pointers are treated as *rough convenience*, not reproducibility pins — per-package releases are already tagged in the sub-repos, so nothing depends on exact pins.
- The chore (requirement 3) is eliminated by a scheduled GitHub Action in the superproject: `git submodule update --remote`, commit, push, daily. Marc never touches it. Staleness of hours is harmless.
- The superproject needs no other CI — it builds and releases nothing, so it adds zero release friction.
- `.claudeConvos/` gets committed to the superproject. **Before first push: skim the notes — they're candid and would become public.** Also update CLAUDE.md (remove "local-only" claim).
- `scamp_tutor` lives in the superproject; fetchability via GitHub Pages (it already has `index.html` + bundle regen scripts) or raw-content links; link prominently from scamp's README/docs.
- Gitignore: `cb2` (no public home yet; its relative-path remote breaks for cloners), `.venv`, `.idea`. Commit `uv.lock` (workspace reproducibility record).

## Rejected alternatives

- **Glue-only root repo, package dirs gitignored + bootstrap script** (first suggestion): fails requirement 1 — clone doesn't yield the workspace in one command. Otherwise the lightest option.
- **True monorepo** (merge all five into one repo): kills standalone repo identities, issues, stars, sourcehut mirrors; disruptive migration; pymusicxml has an outside contributor. Standalone *pip* installability would survive, but repo-level independence matters.
- **Manually pinned submodules**: exactly the version-bump chore Marc doesn't want.

## Related but separate: release-CI friction

The per-release wheel builds are inherent, not caused by workspace structure: version number is baked into each wheel, and scamp wheels bundle fluidsynth, so every version needs fresh binary wheels even if no native code changed. The only real escape would be un-bundling fluidsynth (depend on system lib, ship pure-Python wheel) — a much bigger decision, deliberately not pursued now.

## Open questions

- Public vs. private superproject? (Public needed for tutor fetching; public exposes `.claudeConvos`.)
- Should `scamp_tutor` instead fold into scamp's docs build? Deferred — better discoverability but couples tutor updates to scamp releases.
- Auto-bump cadence (daily seems fine) and whether sub-repo pushes should trigger it via `repository_dispatch` (probably overkill).
