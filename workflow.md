# Conventions for contributions

- Use American spelling
- All source files carry a GPL-3.0 header block. Preserve it when editing or copy it when adding new files.
- Avoid compound commands to avoid unnecessary permissions prompts (e.g. **Never run `cd <subdir> && git ...`**; use `git -C <subdir> ...` instead)
- DO NOT commit anything unless explicitly asked to. Instead, after completing work, summarize which files changed, and
which blocks of code to look at in which order aid the review process.
- When asked to commit, BEFORE committing, do a pass of the staged code & comments to look for typos or 
misunderstandings. This ensures that we are fully on the same page.
- Commit messages and changelog entries should observe the same conventions as comments (see below)
- When running the clockblocks test suite use `CLOCKBLOCKS_TEST_COMPRESSION=10` unless testing something where realtime 
is crucial.
- When it starts to feel like yak shaving — deep focus on something not worth the time — say so, prompt me to step 
back, and offer a couple of quick, imperfect ways to resolve or park it.

# On comments, commit messages, and changelog entries:

- A large part of the workflow is manual review of comments & other prose, so KEEP THESE BRIEF and easy for a 
human developer with limited working memory to comprehend.
- Do not reference downstream packages unless absolutely necessary. e.g. clockblocks doesn't need to know
about scamp.
- The goal is for a future human developer coming in cold to grasp the code quickly. They rarely need to hear about 
the twists and turns of the development process; save their limited bandwidth for the current code.
- User-facing text (docstrings, changelogs) should only address user-facing concerns. Implementation details should
  be saved for inline comments.

# Commit message format

- Subject in the imperative mood, sentence case, no conventional-commit prefix ("feat:", "fix:"), no trailing
  period. Keep it short; use a semicolon to pair two related changes.
- Non-trivial commits get a short body after a blank line, wrapped around 80 columns: the why and the user-visible
  effect, not a replay of the diff.
- Commits produced through an AI coding harness carry a trailer naming the model and harness, e.g.
  `Co-Authored-By: deepseek-v4.1-flash using OpenCode <noreply@opencode.ai>`.

# Other

- When running terminal commands, if there are any that I might reasonably want to run myself sometime, list them at the
end of your message as "commands run".