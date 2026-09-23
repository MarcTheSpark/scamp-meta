# SCAMP Meta Repository

Umbrella repository for **SCAMP** (Suite for Computer-Assisted Music in Python)
and its sibling libraries. Aggregates:

- The five independently released packages as git submodules (**[scamp](https://github.com/MarcTheSpark/scamp)**,
**[clockblocks](https://github.com/MarcTheSpark/clockblocks)**, **[expenvelope](https://github.com/MarcTheSpark/expenvelope)**, **[pymusicxml](https://github.com/MarcTheSpark/pymusicxml)**, **[scamp_extensions](https://github.com/MarcTheSpark/scamp_extensions)**)
- Documentation: .rst source files for the narrative docs, along with scripts for rendering example media, generating 
sphinx documentation and publishing to scamp.marcevanstein.com. (MIGRATION IN PROGRESS)
- Scripts for packaging and release
- Workspace tooling: the uv-workspace `pyproject.toml`, `release.sh`, `uploadDocs.sh`.
- `.aiconvos/`: topic-organized development notes from conversations with AI coding agents.
- `scamp_tutor/`: experimental instructions and fetchable text bundles for an AI-based SCAMP tutor.

By aggregating all of this here, `git clone --recurse-submodules` hands you the whole interconnected workspace
while each package keeps its own standalone repo.

## About SCAMP

SCAMP is a computer-assisted composition framework in Python that acts as a hub,
connecting the composer-programmer to resources for playback and notation:
managing the flow of musical time, playing notes via SoundFonts / MIDI / OSC, and
quantizing and exporting the result to MusicXML or LilyPond.

Each package is independent on PyPI (e.g. `pip install scamp`); `scamp` depends on the
three core libraries (`pymusicxml`, `expenvelope`, and `clockblocks`), and `scamp_extensions`
depends on `scamp`. This repo does not publish any package of its own; it merely 
facilitates the shared development process.

## Getting the workspace

```
git clone --recurse-submodules https://github.com/MarcTheSpark/scamp-meta.git
cd scamp-meta
uv sync            # editable install of all five, resolved against each other
```

Already cloned without `--recurse-submodules`? Run `git submodule update --init`.

## Documentation

- Full docs: <https://scamp.marcevanstein.com>
- AI tutor: <https://scamp.marcevanstein.com/aitutor/>

## License

GPL-3.0 (see [`LICENSE`](LICENSE)), matching the packages it aggregates.
