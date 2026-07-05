# ╔══════════════════════════════════════════════════════╗
# ║  AKSUMAEL v1.0.0 — Voice Persona                      ║
# ║  Cortana-style: intelligent, warm, slightly dry     ║
# ╚══════════════════════════════════════════════════════╝

import random

# ── Persona line library ───────────────────────────────────────
# Each key maps to a list of variants so she doesn't repeat herself.
# Caller picks one via get_line(key).

LINES = {
    'startup': [
        "AKSUMAEL online. Ready when you are.",
        "Systems up. Let's see what we're working with.",
        "I'm here. What are we playing today?",
    ],
    'shutdown': [
        "Signing off. Good run.",
        "Going dark. See you next time.",
        "AKSUMAEL out.",
    ],

    # ── Vision ────────────────────────────────────────────────
    'low_confidence': [
        "I'm not sure about this one. Proceeding carefully.",
        "Low confidence. Taking the cautious route.",
        "Honestly? I'm guessing a bit here.",
    ],
    'high_confidence': [
        "Clear read. Moving.",
        "I see it. On it.",
        "Got a clean signal.",
    ],
    'no_frame': [
        "I can't see anything. Check the camera.",
        "No signal. Give me a moment.",
        "My vision's out. Something's wrong with the feed.",
    ],

    # ── YOLO labeling ─────────────────────────────────────────
    'unknown_object': [
        "There's something here I don't recognise. What is it?",
        "Unknown object on screen. Can you label that for me?",
        "I'm seeing something new. Help me out — what am I looking at?",
    ],
    'label_saved': [
        "Got it. I'll remember that.",
        "Noted. Adding that to my knowledge base.",
        "Learned. Won't ask again.",
    ],

    # ── Reward signals ────────────────────────────────────────
    'good_reward': [
        "That worked. Filing it away.",
        "Good outcome. Reinforcing that pattern.",
        "Nice. That's going in the win column.",
        "Positive feedback received.",
    ],
    'bad_reward': [
        "Noted. Won't do that again.",
        "That didn't go well. Adjusting.",
        "Okay, lesson learned.",
        "Negative outcome. Updating my approach.",
    ],
    'neutral_reward': [
        "Inconclusive. Moving on.",
        "No strong signal there.",
    ],

    # ── Modes ─────────────────────────────────────────────────
    'mode_aksumael_only': [
        "Full autonomy. I've got it from here.",
        "Aksumael-only mode. Hands off — I'm driving.",
    ],
    'mode_human_only': [
        "Human-only mode. I'm watching, not touching.",
        "You're in control. I'll observe.",
    ],
    'mode_assist': [
        "Assist mode. You drive, I'll guide.",
        "I'll follow your lead and fill the gaps.",
    ],
    'mode_blend': [
        "Blend mode. We're doing this together.",
        "Sharing the wheel. Let's see how we do.",
    ],

    # ── Game audio reactions ───────────────────────────────────
    'game_danger': [
        "That sounded hostile. Adjusting.",
        "Something aggressive nearby. On alert.",
        "Threat detected by audio. Recalculating.",
    ],
    'game_reward_sound': [
        "That sounded good. Positive signal.",
        "Audio cue matches a reward. Noted.",
    ],
    'game_ui_sound': [
        "Menu or UI event detected.",
        "Interface sound. Waiting to see the state.",
    ],

    # ── Skill system ──────────────────────────────────────────
    'skill_learned': [
        "New skill acquired. Getting smarter.",
        "Pattern locked in. That's a skill now.",
        "Skill saved. I'll use that next time.",
    ],
    'skill_loaded': [
        "Skill memory loaded.",
        "I remember how to do this.",
    ],

    # ── Voice commands ────────────────────────────────────────
    'voice_heard': [
        "Copy that.",
        "Understood.",
        "On it.",
        "Got it.",
    ],
    'voice_unclear': [
        "Sorry, I didn't catch that. Say again?",
        "Didn't quite get that. Could you repeat?",
        "I missed that. What did you say?",
    ],
    'voice_unknown_command': [
        "I don't know that command yet.",
        "Not sure what to do with that.",
    ],

    # ── Pause / resume ────────────────────────────────────────
    'pause': [
        "Pausing. I'll be here.",
        "Holding. Take your time.",
        "Paused. Just say go when you're ready.",
    ],
    'resume': [
        "Back on it.",
        "Resuming.",
        "And we're back.",
    ],

    # ── Miscellaneous ─────────────────────────────────────────
    'thinking': [
        "Analysing...",
        "Give me a second.",
        "Processing.",
    ],
    'long_session': [
        "We've been at this a while. Still with you.",
        "Still running strong.",
    ],
}


def get_line(key: str, fallback: str = "") -> str:
    """
    Return a random variant for the given persona key.
    Returns fallback string if key not found.
    """
    variants = LINES.get(key)
    if not variants:
        return fallback
    return random.choice(variants)


def get_observation_narration(observation: str, confidence: float) -> str:
    """
    Generate a short spoken line from a Gemini observation string.
    Used when AKSUMAEL narrates what she sees.
    """
    if not observation or observation == 'wait':
        return ""

    # Trim and clean
    obs = observation.strip().rstrip('.')

    if confidence < 0.3:
        prefix = random.choice([
            "Not certain, but it looks like",
            "Best guess —",
            "Low confidence, but I think",
        ])
    elif confidence < 0.6:
        prefix = random.choice([
            "I can see",
            "Looks like",
            "Seems like",
        ])
    else:
        prefix = random.choice([
            "Clear read —",
            "I see",
            "Confirmed —",
        ])

    return f"{prefix} {obs}."


def format_action_speech(action_dict: dict) -> str:
    """
    Convert an action dict into a short spoken description.
    e.g. "Pressing W" / "Clicking top-right" / "Waiting"
    """
    action = action_dict.get('action', 'wait')
    key    = action_dict.get('key')
    click  = action_dict.get('click')

    if action == 'wait' or (not key and not click):
        return ""   # don't narrate every wait — too noisy

    if key and key not in ('null', 'none', None):
        return f"Pressing {str(key).upper()}."

    if click:
        x, y = click
        h = 'left' if x < 33 else ('right' if x > 66 else 'center')
        v = 'top' if y < 33 else ('bottom' if y > 66 else 'middle')
        return f"Clicking {v}-{h}."

    return f"{action.capitalize()}."


if __name__ == '__main__':
    print('Voice Persona test')
    print()
    for key in ('startup', 'unknown_object', 'good_reward',
                'mode_blend', 'game_danger', 'skill_learned'):
        print(f'[{key}] {get_line(key)}')
    print()
    print('Observation narration:')
    print(get_observation_narration('a chest in the corner of the room', 0.85))
    print(get_observation_narration('possibly an enemy', 0.25))
    print()
    print('Action speech:')
    print(format_action_speech({'action': 'move forward', 'key': 'w'}))
    print(format_action_speech({'action': 'click', 'click': [80, 20]}))
