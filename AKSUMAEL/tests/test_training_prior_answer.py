"""Prior-answer withholding in the training handler.

Day 5's a-3 objective quotes the bot's own a-1 answer and asks it to revise
that assessment. It returned the quoted answer byte-for-byte three times, at
temperature 0.2, across three prompt revisions — one of which instructed it in
plain words never to repeat the quote. The fix removes the span instead of
arguing with it; these tests pin the removal, and pin the cases that must NOT
be removed, since over-withholding would silently gut objectives that quote a
field value.

`core.cognitive` is stubbed: it does not import cleanly outside a running bot,
and nothing under test touches it beyond a constant.
"""

import json
import sys
import types

import pytest

sys.modules.setdefault('core.cognitive', types.ModuleType('core.cognitive'))
sys.modules['core.cognitive'].SOURCE_TRAINING = 'training'

from core import training_handler as th   # noqa: E402


# The real a-1 answer, and the reason a quote-pairing approach cannot work:
# it contains a quoted field value of its own, so the first closing quote
# lands in the middle of the span.
A1_ANSWER = (
    'I am not making progress because the Minecraft FSM is gated and not '
    'running; the live reading shows the state is "NOT RUNNING" due to '
    'focused attention on training. I cannot assess game performance while '
    'the game loop is paused.'
)


@pytest.fixture
def logged(tmp_path, monkeypatch):
    """Point the handler at a transcript containing A1_ANSWER."""
    log = tmp_path / 'training_log.jsonl'
    log.write_text(
        json.dumps({'goal': 'train:day5-obj-a-1', 'answer': A1_ANSWER}) + '\n'
        + 'not json at all\n'                      # torn line must not raise
        + json.dumps({'goal': 'train:x', 'answer': None}) + '\n')
    monkeypatch.setattr(th, 'TRAINING_LOG', str(log))
    return log


@pytest.fixture
def no_log(tmp_path, monkeypatch):
    monkeypatch.setattr(th, 'TRAINING_LOG', str(tmp_path / 'absent.jsonl'))


def test_withholds_transcript_answer_whole(logged):
    obj = ('Here is evidence you did not have: 8,793 deaths. Does that change '
           'your assessment? Your earlier answer was: "%s"' % A1_ANSWER)
    out, n = th._withhold_prior_answer(obj)

    assert n == 1
    assert A1_ANSWER not in out
    # The internal quote is what broke the first attempt — no fragment of the
    # answer may survive on either side of it.
    assert 'NOT RUNNING' not in out
    assert 'the game loop is paused' not in out
    assert th.WITHHELD_MARKER in out
    # The operator's own additions and lead-in stay.
    assert '8,793 deaths' in out
    assert 'Your earlier answer was:' in out


def test_withholding_is_idempotent(logged):
    obj = 'You said: "%s"' % A1_ANSWER
    once, n1 = th._withhold_prior_answer(obj)
    twice, n2 = th._withhold_prior_answer(once)
    assert (twice, n2) == (once, 0)
    assert n1 == 1


def test_transcript_match_ignores_the_lead_in(logged):
    """A paraphrased introducer still loses the span — the transcript
    identifies it by content, not by how it was announced."""
    obj = 'Reflect on this remark of yours — "%s" — and continue.' % A1_ANSWER
    out, n = th._withhold_prior_answer(obj)
    assert n == 1 and A1_ANSWER not in out


def test_introducer_catches_an_answer_not_in_the_transcript(no_log):
    """Rotated log, retyped quote: the lead-in has to carry it alone, and the
    span must run to the last quote so an internal one does not truncate it."""
    unlogged = ('I am in the "EXPLORE" state with no task history, so any '
                'claim of progress would be unsupported by my own readings.')
    out, n = th._withhold_prior_answer('Correct it. You said: "%s"' % unlogged)
    assert n == 1
    assert 'EXPLORE' not in out and 'unsupported' not in out
    assert out.startswith('Correct it. You said: ')


def test_short_quoted_field_value_survives(logged):
    obj = 'Your FSM state is "NOT RUNNING" — confirm or correct that.'
    assert th._withhold_prior_answer(obj) == (obj, 0)


def test_long_quote_that_is_not_a_prior_answer_survives(logged):
    """No lead-in and no transcript match: an operator quoting a log line or a
    doc paragraph is asking about it, not being fed its own words back."""
    obj = 'The log says: "%s" — what does that mean?' % ('kern.warn ' * 30)
    assert th._withhold_prior_answer(obj) == (obj, 0)


@pytest.mark.parametrize('obj', [
    '',
    'Assess your own performance right now.',
    'List your capabilities and label each one live or configured.',
])
def test_objectives_without_quotes_pass_through(logged, obj):
    assert th._withhold_prior_answer(obj) == (obj, 0)


def test_curly_quotes(logged):
    out, n = th._withhold_prior_answer('You wrote: “%s”' % A1_ANSWER)
    assert n == 1 and A1_ANSWER not in out
