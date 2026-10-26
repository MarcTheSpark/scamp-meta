# SCAMP AI Tutor

Instructions and fetchable text bundles that turn any AI assistant into a tutor for
[SCAMP](http://scamp.marcevanstein.com). Usage: <https://marcthespark.github.io/scamp-tutor/>

- `tutor.txt`: teaching instructions (entry point for the tutor).
- `resources.txt`: map of the docs and examples (usable on its own as a SCAMP reference).
- `stumblingBlocks.txt`: SCAMP facts AIs commonly get wrong.
- `docs/`, `examples/`: generated bundles. Regenerate with `regenerate_doc_bundles.sh` and
  `regenerate_bundles.sh`, which must run from inside the
  [scamp-meta](https://github.com/MarcTheSpark/scamp-meta) workspace, where this repo is a submodule.
