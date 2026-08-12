"""
memory/lora_trainer.py — LoRA fine-tuning from AKSUMAEL preference data.

Fine-tunes a small local language model (phi-2 or tinyllama) using the DPO
preference pairs from data/learning/preferences.jsonl. This produces an
action-selection model trained specifically on AKSUMAEL's own experience.

The resulting model is NOT a replacement for claude-fable-5 (which handles
complex reasoning). It's a fast local policy model that:
- Runs on the RTX 4050 Laptop GPU (6GB VRAM)
- Predicts the best next action given goal + belief state
- Runs at ~50ms per inference (vs 500ms-2s for API calls)
- Gets better the more AKSUMAEL plays

Architecture:
    Base: microsoft/phi-2 (2.7B) or TinyLlama/TinyLlama-1.1B-Chat-v1.0
    Adapter: LoRA r=16, alpha=32, target_modules=[q_proj, v_proj]
    Training: DPO on preference pairs from data/learning/preferences.jsonl

Requirements:
    pip install transformers peft trl datasets accelerate bitsandbytes

Usage:
    python3 -m memory.lora_trainer                    # fine-tune (GPU)
    python3 -m memory.lora_trainer --check            # check readiness
    python3 -m memory.lora_trainer --infer "explore"  # test inference
"""

from __future__ import annotations

import json
import pathlib
import sys
import time
from typing import Optional

BASE_DIR = pathlib.Path(__file__).parent.parent
PREF_PATH = BASE_DIR / "data" / "learning" / "preferences.jsonl"
LORA_PATH = BASE_DIR / "data" / "learning" / "lora_adapter"
STATS_PATH = BASE_DIR / "data" / "learning" / "lora_stats.json"

# Model to fine-tune (fits in 6GB VRAM with 4-bit quantization)
BASE_MODEL = "TinyLlama/TinyLlama-1.1B-Chat-v1.0"
MIN_PAIRS = 20  # minimum pairs before fine-tuning makes sense


def _pair_to_dpo_sample(pair: dict) -> dict:
    """Convert a preference pair to DPO format for TRL."""
    goal = pair.get("goal", "unknown")
    chosen = pair.get("chosen", {})
    rejected = pair.get("rejected", {})

    def _format(entry: dict) -> str:
        action = entry.get("action", {})
        belief = entry.get("belief", {})
        action_str = action.get("type", str(action))[:100] if isinstance(action, dict) else str(action)[:100]
        belief_str = str(belief)[:200] if belief else "unknown"
        reward = entry.get("reward", 0)
        return f"Goal: {goal}\nState: {belief_str}\nAction: {action_str}\nReward: {reward:.3f}"

    return {
        "prompt": f"[AKSUMAEL] Goal: {goal}\nChoose the best action:",
        "chosen": _format(chosen),
        "rejected": _format(rejected),
    }


def check_readiness() -> dict:
    """Check whether fine-tuning is possible on this machine."""
    result = {"base_model": BASE_MODEL, "min_pairs": MIN_PAIRS}

    # Check pairs
    pairs = _load_pairs()
    result["preference_pairs"] = len(pairs)
    result["ready_to_train"] = len(pairs) >= MIN_PAIRS

    # Check GPU
    try:
        import torch
        result["cuda_available"] = torch.cuda.is_available()
        if torch.cuda.is_available():
            result["gpu"] = torch.cuda.get_device_name(0)
            result["vram_gb"] = round(torch.cuda.get_device_properties(0).total_memory / 1e9, 1)
    except ImportError:
        result["cuda_available"] = False

    # Check packages
    missing = []
    for pkg in ["transformers", "peft", "trl", "datasets", "accelerate"]:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    result["missing_packages"] = missing
    result["packages_ok"] = len(missing) == 0

    if missing:
        result["install_cmd"] = f"pip install {' '.join(missing)} bitsandbytes"

    return result


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


def train(max_steps: int = 200, batch_size: int = 2) -> dict:
    """
    Fine-tune the base model with LoRA + DPO on preference pairs.
    Runs on GPU (RTX 4050, 6GB VRAM) with 4-bit quantization.
    """
    readiness = check_readiness()
    if readiness.get("missing_packages"):
        return {"error": f"missing packages: {readiness['missing_packages']}", "install": readiness.get("install_cmd")}
    if not readiness.get("cuda_available"):
        return {"error": "CUDA not available — GPU required for LoRA training"}

    pairs = _load_pairs()
    if len(pairs) < MIN_PAIRS:
        return {"error": f"need {MIN_PAIRS} preference pairs, have {len(pairs)}. Keep running the bot to collect more."}

    print(f"[LORA] Starting DPO fine-tune: {len(pairs)} pairs, base={BASE_MODEL}")
    start = time.time()

    try:
        import torch
        from datasets import Dataset
        from peft import LoraConfig, get_peft_model, TaskType
        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
        from trl import DPOTrainer, DPOConfig

        # 4-bit quantization to fit in 6GB VRAM
        bnb_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_use_double_quant=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.bfloat16,
        )

        print(f"[LORA] Loading {BASE_MODEL}...")
        tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL)
        tokenizer.pad_token = tokenizer.eos_token

        model = AutoModelForCausalLM.from_pretrained(
            BASE_MODEL,
            quantization_config=bnb_config,
            device_map="auto",
            trust_remote_code=True,
        )

        # LoRA adapter: lightweight, trainable attention projections
        lora_config = LoraConfig(
            task_type=TaskType.CAUSAL_LM,
            r=16,
            lora_alpha=32,
            lora_dropout=0.05,
            target_modules=["q_proj", "v_proj", "k_proj", "o_proj"],
            bias="none",
        )
        model = get_peft_model(model, lora_config)
        model.print_trainable_parameters()

        # Build dataset
        samples = [_pair_to_dpo_sample(p) for p in pairs]
        dataset = Dataset.from_list(samples)

        # DPO training config
        dpo_config = DPOConfig(
            output_dir=str(LORA_PATH),
            num_train_epochs=1,
            max_steps=max_steps,
            per_device_train_batch_size=batch_size,
            gradient_accumulation_steps=4,
            learning_rate=2e-4,
            lr_scheduler_type="cosine",
            warmup_ratio=0.1,
            logging_steps=20,
            save_steps=100,
            bf16=True,
            remove_unused_columns=False,
            report_to="none",
        )

        trainer = DPOTrainer(
            model=model,
            args=dpo_config,
            train_dataset=dataset,
            tokenizer=tokenizer,
        )

        print(f"[LORA] Training for {max_steps} steps...")
        trainer.train()

        # Save adapter
        LORA_PATH.mkdir(parents=True, exist_ok=True)
        model.save_pretrained(str(LORA_PATH))
        tokenizer.save_pretrained(str(LORA_PATH))

        elapsed = round(time.time() - start, 1)
        stats = {
            "trained_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "base_model": BASE_MODEL,
            "n_pairs": len(pairs),
            "max_steps": max_steps,
            "elapsed_s": elapsed,
            "adapter_path": str(LORA_PATH),
        }
        STATS_PATH.write_text(json.dumps(stats, indent=2))
        print(f"[LORA] Done in {elapsed}s. Adapter → {LORA_PATH}")
        return stats

    except Exception as e:
        return {"error": str(e)}


def infer(goal: str, belief: str = "", max_new_tokens: int = 80) -> Optional[str]:
    """Run inference with the fine-tuned LoRA model."""
    if not LORA_PATH.exists():
        return None
    try:
        import torch
        from peft import PeftModel
        from transformers import AutoModelForCausalLM, AutoTokenizer, pipeline

        tokenizer = AutoTokenizer.from_pretrained(str(LORA_PATH))
        base = AutoModelForCausalLM.from_pretrained(BASE_MODEL, device_map="auto", torch_dtype=torch.float16)
        model = PeftModel.from_pretrained(base, str(LORA_PATH))
        model.eval()

        prompt = f"[AKSUMAEL] Goal: {goal}\nState: {belief[:200] if belief else 'unknown'}\nChoose the best action:"
        pipe = pipeline("text-generation", model=model, tokenizer=tokenizer, max_new_tokens=max_new_tokens)
        result = pipe(prompt)[0]["generated_text"]
        return result[len(prompt):].strip()
    except Exception as e:
        return f"[infer error: {e}]"


_lora_training_active = False


def _run_lora_if_ready(max_steps: int = 200) -> dict:
    """Non-blocking: start LoRA training in background if not already running and adapter is stale."""
    global _lora_training_active
    if _lora_training_active:
        return {"skipped": True, "reason": "already_training"}
    # Check if adapter is fresh (< 2 hours old)
    if LORA_PATH.exists():
        age_h = (time.time() - LORA_PATH.stat().st_mtime) / 3600
        if age_h < 2.0:
            return {"skipped": True, "reason": "adapter_fresh", "age_hours": round(age_h, 2)}
    import threading
    def _run():
        global _lora_training_active
        _lora_training_active = True
        try:
            print("[LORA] auto-triggered LoRA training from overseer")
            result = train(max_steps=max_steps)
            print(f"[LORA] auto-training complete: {result}")
        finally:
            _lora_training_active = False
    t = threading.Thread(target=_run, daemon=True, name="LoRAAutoTrainer")
    t.start()
    return {"status": "training_started_background"}


if __name__ == "__main__":
    if "--check" in sys.argv:
        print(json.dumps(check_readiness(), indent=2))
    elif "--infer" in sys.argv:
        idx = sys.argv.index("--infer")
        goal = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else "explore"
        result = infer(goal)
        print(f"Inference for '{goal}':\n{result}")
    else:
        result = train()
        print(json.dumps(result, indent=2))
