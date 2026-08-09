# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL — "my own code changed" self-awareness      ║
# ╚══════════════════════════════════════════════════════╝
#
# A git post-commit hook drops a one-shot JSON summary of the commit at
# CODE_CHANGE_FILE; the tick loop calls check() and, if the file is there,
# pushes "MY CODE WAS UPDATED — ..." into the inner monologue and deletes
# the file. Tagged SOURCE_CODE_CHANGE and persisted to Honcho, so the
# deriver folds it into episodic memory and the bot accumulates a history
# of its own evolution across sessions.
#
# Why a file and not an import-time check or a git call per tick: the hook
# fires in whatever shell Scott commits from, which is a different process
# from the bot and usually a different moment entirely (the bot is often
# not even running). A dropped file is the only handoff that survives
# both, and it survives a restart too — a commit made while the bot was
# down is read on its next boot, which is the honest thing for it to
# notice.
#
# The hook lives at .git/hooks/post-commit and is NOT committed (git hooks
# are per-clone). Its contract is exactly this file's _parse(): a single
# JSON object {"timestamp", "commit": {"hash","short","message","author",
# "date"}, "files": [...]}. Nothing here requires the hook to exist —
# without it check() is one os.path.exists() per tick and nothing else.
#
# Known limit: the hook overwrites, so several commits landing between two
# ticks collapse to the newest one. Left as is on purpose — the alternative
# (an append log) buys a burst-of-rebase-commits case that would read as
# noise in the monologue anyway.

import json
import os

from core.cognitive import SOURCE_CODE_CHANGE

CODE_CHANGE_FILE = os.environ.get('AKSUMAEL_CODE_CHANGE_FILE',
                                  '/tmp/aksumael_code_change.json')

# How many changed paths make it into the sentence. The monologue is one
# line on a display strip and one message to a deriver; a 40-file commit
# rendered in full is neither.
MAX_FILES_NAMED = 5


def _format(data: dict) -> str | None:
    """The monologue sentence for one commit payload, or None if the
    payload carries nothing worth saying."""
    commit = data.get('commit') or {}
    files  = [f for f in (data.get('files') or []) if f]
    short  = (commit.get('short') or '').strip()
    msg    = (commit.get('message') or '').strip()
    if not short and not msg:
        return None
    named = ', '.join(files[:MAX_FILES_NAMED]) or 'none listed'
    if len(files) > MAX_FILES_NAMED:
        named += f' (+{len(files) - MAX_FILES_NAMED} more)'
    return (f'MY CODE WAS UPDATED — commit {short or "?"}: '
            f'"{msg or "?"}" — files changed: {named}')


def check(monologue=None) -> str | None:
    """Consume a pending code-change drop, if any. Returns the sentence
    that was pushed, or None when there was nothing to report.

    Safe to call every tick: the common case is a single os.path.exists()
    on a file that isn't there.

    The drop file is deleted whatever happens — including on a parse
    failure. It is a one-shot signal, and a malformed one left in place
    would re-fire on every tick forever."""
    if not os.path.exists(CODE_CHANGE_FILE):
        return None
    line = None
    try:
        with open(CODE_CHANGE_FILE) as f:
            line = _format(json.load(f))
    except Exception as e:
        print(f'[CODE] unreadable {CODE_CHANGE_FILE}: {e}')
    finally:
        try:
            os.remove(CODE_CHANGE_FILE)
        except OSError as e:
            print(f'[CODE] could not remove {CODE_CHANGE_FILE}: {e}')
    if not line:
        return None
    print(f'[CODE] {line}')
    if monologue is not None:
        try:
            # persist=True: this is the whole point of the feature. The
            # monologue file keeps only MAX_THOUGHTS entries and is wiped
            # by nothing but time, so without the Honcho write the bot
            # would know its code changed for about five minutes rather
            # than building the history across sessions.
            monologue.push_external(line, source=SOURCE_CODE_CHANGE,
                                    persist=True)
        except Exception as e:
            print(f'[CODE] monologue push error: {e}')
    return line
