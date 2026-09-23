# 2026-05-05 — Wheel build script paths + manylinux CI failure

## What we worked on

1. Fixed `scamp/scripts/build_macos_12_wheel.sh` to be runnable from any CWD.
2. Diagnosed and fixed a cibuildwheel CI failure on `ubuntu-24.04-arm` (and, in retrospect, also x86_64) where the manylinux image couldn't be pulled.
3. Set up this `.claudeConvos/` workflow and audited CLAUDE.md for staleness against the last few days of commits.

## (1) Build script path handling

The original script was three lines mixing two different CWD assumptions:

- `python -m build --wheel` and `dist/...` paths only work from `scamp/` (where pyproject.toml lives).
- `scamp/scripts/inject_mac_dylibs.py` only resolves from the workspace root.
- `scripts/intel-mac-dylibs.tar.gz` only resolves from `scamp/`.

So no single CWD made the script work. Marc was running it from the workspace root via `uv` and feeling the brittleness.

**Pattern adopted** (worth re-using for future scripts in this workspace):

```bash
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
SCAMP_DIR=$(dirname "$SCRIPT_DIR")
cd "$SCAMP_DIR"
```

Anchor everything to the script's own location. `cd` into the package dir so `python -m build` and short relative paths like `dist/` behave naturally. Reference data files that travel with the script via `$SCRIPT_DIR/...` so they're CWD-independent.

Considered but rejected: making `dist/` land wherever the user invoked from. Decided that for build scripts, a fixed `<package>/dist/` output is least surprising; if we ever need it configurable, add a `--output` arg.

Also added `set -euo pipefail` and a shebang.

## (2) The manylinux CI pull failure

Symptom in the GH Actions log:

```
+ docker image inspect manylinux_2_34 --format '{{.Os}}/{{.Architecture}}'
Error response from daemon: pull access denied for manylinux_2_34, repository does not exist
```

Root cause: `pyproject.toml` had

```toml
manylinux-x86_64-image = "manylinux_2_34"
manylinux-aarch64-image = "manylinux_2_34"
```

cibuildwheel's config dump in the same log showed those values passing through *unexpanded* — the bare string was being fed to Docker as a literal image name (which Docker resolves as `library/manylinux_2_34` on Docker Hub, which doesn't exist).

cibuildwheel docs claim `manylinux_2_34` is a recognized alias as of 2.21+, and we're on 2.23.4, so this *should* have expanded. Didn't dig into why it didn't. Just used the fully-qualified image refs:

```toml
manylinux-x86_64-image = "quay.io/pypa/manylinux_2_34_x86_64:latest"
manylinux-aarch64-image = "quay.io/pypa/manylinux_2_34_aarch64:latest"
```

Open question / note for future-Marc: `:latest` is non-reproducible. The other entries in the auto-dumped config use dated tags like `:2025.04.19-1`. Worth pinning to a dated tag if a build six months from now needs to be byte-identical to today's.

Also worth re-running x86_64: it "succeeded" earlier despite the same misconfiguration, which means it built against *something* Docker Hub returned for the bare name — definitely not the pypa manylinux image. The wheel may need re-checking via `auditwheel show`.

## (3) Quay / manylinux explainer (for the record)

- `quay.io` is a container registry, like Docker Hub but Red Hat's. PyPA publishes the official manylinux images there because that's where they live; nothing more interesting than that.
- Only Linux uses Docker images for builds because "manylinux" is a Linux-specific compatibility standard (PEP 600). The point is to build inside an old-glibc container so the resulting `.so`s run on a wide range of distros.
- macOS: no container. Uses `MACOSX_DEPLOYMENT_TARGET` + `delocate-wheel` to forward-bundle dylibs.
- Windows: no container. MSVC runtime is stable; just bundle DLLs (and optionally `delvewheel`).

## (4) Workflow set up this session

- Created `.claudeConvos/` at workspace root (no parent git repo, no gitignore needed; folder is synced via Nextcloud).
- Audited CLAUDE.md and fixed three stale claims:
  - "The other four use the older `setup.py` + `setup.cfg` style" — false since 2026-05-02; all five now use `pyproject.toml`.
  - "On Mac/Windows the compiled library is bundled inside `scamp/src/scamp/_thirdparty/`" — those dirs are now gitignored and populated at build time.
  - Added a short paragraph pointing at the cibuildwheel pipeline so future-Claude knows it exists.
- Added a "Working notes" section to CLAUDE.md describing this `.claudeConvos/` convention and the per-commit ritual (re-check CLAUDE.md, write a convo file).

## Considered-but-rejected ideas

- **Auto-appending commit summaries to CLAUDE.md.** Marc's first phrasing sounded like this; pushed back because CLAUDE.md is loaded into every conversation, so growth = exactly the "huge context" problem he was trying to avoid. `git log` already authoritatively answers "what changed". The agreed split: CLAUDE.md = stable durable facts only; `git log` = what & when; `.claudeConvos/` = why & alternatives.
- **ADR tooling (`adr-tools`).** Same shape as what we built but adds a dependency for what is essentially a naming convention. Skipped.
- **Claude Code's per-user memory dir** (`~/.claude/projects/.../memory/`). Per-machine, doesn't travel with the project. `.claudeConvos/` is in-tree (well, in the synced workspace) so future-Marc on a different machine still has it.

## Update 2026-08-05: consolidated into scripts/wheel_building/

All the wheel-build files now live under `scripts/wheel_building/`: the four
`before_build_*` cibuildwheel hooks (previously `scripts/ci/`), `build_macos_12_wheel.sh`,
`inject_mac_dylibs.py`, `intel-mac-dylibs.tar.gz`, and `fetch_fluidsynth_libs.py`.
`publish_github_release.py` stayed in `scripts/` — it publishes release notes, doesn't
build. Committed as "Consolidate wheel-building scripts under scripts/wheel_building".

Gotchas worth remembering:

- **Flattened, no `ci/` subfolder.** Keeping the hooks directly in `wheel_building/` (rather
  than `wheel_building/ci/`) preserves `before_build_windows.py`'s `REPO_ROOT =
  parents[2]` — it's still two levels below the repo root, so that line didn't need editing
  (only its `scripts/fetch_fluidsynth_libs.py` → `scripts/wheel_building/…` reference did).
- **The dylib stash is untracked** (`*-mac-dylibs.tar.gz` gitignore), so the `mv` was
  invisible to git — the file just moved on disk. Don't expect it in `git status`.
- `build_macos_12_wheel.sh` gained a second `dirname` (`SCAMP_DIR` is now two levels up).
- Path references updated: `pyproject.toml` before-build hooks (in the repo), plus the
  workspace-root `release.sh`, `CLAUDE.md`, and each script's own usage docstring. Note the
  first three are OUTSIDE the scamp git repo (workspace root isn't a repo), so those edits
  are on-disk only, not in any commit.
