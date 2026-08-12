"""
memory/local_trainer.py — Local reward model training from AKSUMAEL preference data.

Uses the DPO preference pairs from data/learning/preferences.jsonl to train
a lightweight local model that scores (belief_state, action) pairs.

This is NOT fine-tuning claude-fable-5 (we can't do that locally). Instead it
trains a small local reward model that:
1. Takes (belief, action) as input
2. Outputs a scalar reward estimate
3. Gets used by the planner to pre-score candidate goals before calling Claude

Architecture: sentence-transformer embedding → 2-layer MLP reward head
Training: Bradley-Terry preference model from DPO pairs

Requirements:
  pip install sentence-transformers torch scikit-learn

Usage:
  python3 -m memory.local_trainer              # train + save model
  python3 -m memory.local_trainer --eval       # evaluate on held-out pairs
  python3 -m memory.local_trainer --score "mine_diamonds"  # score a goal string
"""

from __future__ import annotations

import json
import pathlib
import sys
import time
from typing import Optional

BASE_DIR = pathlib.Path(__file__).parent.parent
PREF_PATH = BASE_DIR / "data" / "learning" / "preferences.jsonl"
MODEL_PATH = BASE_DIR / "data" / "learning" / "reward_model.pt"
STATS_PATH = BASE_DIR / "data" / "learning" / "trainer_stats.json"


def _load_pairs() -> list[dict]:
    if not PREF_PATH.exists():
        return []
    pairs = []
    for line in PREF_PATH.read_text().strip().splitlines():
        try:
            pairs.append(json.loads(line))
        except Exception:
            pass
    return pairs


def _pair_to_text(entry: dict) -> str:
    """Convert a tick entry to a text representation for embedding."""
    goal = entry.get("goal", "unknown")
    action = entry.get("action", {})
    belief = entry.get("belief", {})
    action_str = action.get("type", str(action))[:80] if isinstance(action, dict) else str(action)[:80]
    belief_str = str(belief)[:120] if belief else ""
    return f"goal:{goal} action:{action_str} state:{belief_str}"


def train(min_pairs: int = 20, epochs: int = 30, lr: float = 1e-3) -> dict:
    """Train the local reward model. Returns training stats."""
    pairs = _load_pairs()
    if len(pairs) < min_pairs:
        return {"error": f"need at least {min_pairs} preference pairs, have {len(pairs)}"}

    print(f"[TRAINER] {len(pairs)} preference pairs loaded")

    try:
        import torch
        import torch.nn as nn
        from sentence_transformers import SentenceTransformer
    except ImportError as e:
        return {"error": f"missing dependency: {e} — run: pip install sentence-transformers torch"}

    # Encode chosen and rejected as text
    chosen_texts = [_pair_to_text(p["chosen"]) for p in pairs]
    rejected_texts = [_pair_to_text(p["rejected"]) for p in pairs]

    print("[TRAINER] loading sentence transformer (all-MiniLM-L6-v2)...")
    encoder = SentenceTransformer("all-MiniLM-L6-v2")
    encoder.eval()

    print("[TRAINER] encoding pairs...")
    with torch.no_grad():
        chosen_emb = torch.tensor(encoder.encode(chosen_texts, show_progress_bar=False))
        rejected_emb = torch.tensor(encoder.encode(rejected_texts, show_progress_bar=False))

    emb_dim = chosen_emb.shape[1]

    # Reward MLP: embedding → scalar score
    reward_model = nn.Sequential(
        nn.Linear(emb_dim, 128),
        nn.ReLU(),
        nn.Dropout(0.1),
        nn.Linear(128, 32),
        nn.ReLU(),
        nn.Linear(32, 1),
    )

    optimizer = torch.optim.Adam(reward_model.parameters(), lr=lr)

    # Bradley-Terry loss: P(chosen > rejected) = sigmoid(r_chosen - r_rejected)
    # Loss = -log P(chosen > rejected) = -log sigmoid(r_c - r_r)
    best_loss = float("inf")
    history = []

    for epoch in range(epochs):
        reward_model.train()
        optimizer.zero_grad()

        r_chosen = reward_model(chosen_emb).squeeze(-1)
        r_rejected = reward_model(rejected_emb).squeeze(-1)

        loss = -torch.log(torch.sigmoid(r_chosen - r_rejected) + 1e-8).mean()
        loss.backward()
        optimizer.step()

        loss_val = loss.item()
        history.append(loss_val)
        if loss_val < best_loss:
            best_loss = loss_val

        if (epoch + 1) % 10 == 0:
            acc = (r_chosen > r_rejected).float().mean().item()
            print(f"[TRAINER] epoch {epoch+1}/{epochs} loss={loss_val:.4f} acc={acc:.3f}")

    # Save model + encoder name
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    torch.save({
        "model_state": reward_model.state_dict(),
        "emb_dim": emb_dim,
        "encoder_name": "all-MiniLM-L6-v2",
        "trained_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "n_pairs": len(pairs),
        "final_loss": best_loss,
    }, MODEL_PATH)

    stats = {
        "trained_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "n_pairs": len(pairs),
        "epochs": epochs,
        "final_loss": round(best_loss, 5),
        "loss_history": [round(h, 5) for h in history],
    }
    STATS_PATH.write_text(json.dumps(stats, indent=2))
    print(f"[TRAINER] saved → {MODEL_PATH}")
    return stats


def score_goal(goal: str, action: str = "", belief: str = "") -> Optional[float]:
    """Score a goal string using the saved reward model. Returns scalar or None."""
    if not MODEL_PATH.exists():
        return None
    try:
        import torch
        import torch.nn as nn
        from sentence_transformers import SentenceTransformer

        ckpt = torch.load(MODEL_PATH, map_location="cpu")
        emb_dim = ckpt["emb_dim"]
        encoder = SentenceTransformer(ckpt["encoder_name"])
        encoder.eval()

        reward_model = nn.Sequential(
            nn.Linear(emb_dim, 128), nn.ReLU(), nn.Dropout(0.1),
            nn.Linear(128, 32), nn.ReLU(), nn.Linear(32, 1),
        )
        reward_model.load_state_dict(ckpt["model_state"])
        reward_model.eval()

        text = f"goal:{goal} action:{action[:80]} state:{belief[:120]}"
        with torch.no_grad():
            emb = torch.tensor(encoder.encode([text], show_progress_bar=False))
            score = reward_model(emb).item()
        return round(score, 4)
    except Exception as e:
        print(f"[TRAINER] score error: {e}")
        return None


def _run_periodic():
    """Train if we have enough pairs and model is stale (>1h old or missing)."""
    pairs = _load_pairs()
    if len(pairs) < 20:
        print(f"[TRAINER] only {len(pairs)} pairs, skipping (need 20)")
        return {"skipped": True, "pairs": len(pairs)}

    if MODEL_PATH.exists():
        age_h = (time.time() - MODEL_PATH.stat().st_mtime) / 3600
        if age_h < 1.0:
            print(f"[TRAINER] model fresh ({age_h:.1f}h old), skipping retrain")
            return {"skipped": True, "age_hours": round(age_h, 2)}

    return train()


if __name__ == "__main__":
    if "--score" in sys.argv:
        idx = sys.argv.index("--score")
        goal = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else "explore"
        s = score_goal(goal)
        print(f"reward score for '{goal}': {s}")
    elif "--eval" in sys.argv:
        pairs = _load_pairs()
        print(f"pairs available: {len(pairs)}")
        if pairs:
            # Show top 5 by reward_gap
            top = sorted(pairs, key=lambda p: p.get("reward_gap", 0), reverse=True)[:5]
            for p in top:
                print(f"  goal={p['goal']} gap={p.get('reward_gap')} "
                      f"chosen_r={p['chosen']['reward']} rejected_r={p['rejected']['reward']}")
    else:
        result = _run_periodic()
        print(json.dumps(result, indent=2))
