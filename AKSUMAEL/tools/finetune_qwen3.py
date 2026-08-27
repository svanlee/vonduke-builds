#!/usr/bin/env python3
"""
tools/finetune_qwen3.py — QLoRA fine-tune Qwen3-8B on AURORA training data.

Uses unsloth for 4-bit quantized base + LoRA, fits in ~5.5GB VRAM.
After training, exports LoRA adapter + merged GGUF for llama-server.

Usage:
    cd ~/vonduke-builds/AKSUMAEL
    venv/bin/python3 tools/finetune_qwen3.py

Output:
    ~/models/qwen3-8b-ft/   — LoRA adapter (HuggingFace format)
    ~/models/qwen3-8b/Qwen3-8B-AKSUMAEL-ft.gguf  — merged GGUF
"""

import json
import os
import pathlib
import sys

# ── Paths ──────────────────────────────────────────────────────────────────────
BASE_DIR    = pathlib.Path(__file__).parent.parent
TRAIN_DATA  = BASE_DIR / "data" / "training" / "instruct.jsonl"
OUTPUT_DIR  = pathlib.Path.home() / "models" / "qwen3-4b-ft"
GGUF_DIR    = pathlib.Path.home() / "models" / "qwen3-4b"

# HuggingFace model — pre-quantized 4-bit for unsloth
# Qwen3-4B: ~2.5GB VRAM loaded, leaves room for LoRA + optimizer on 6GB GPU
# Switch back to 8B once we have >10GB VRAM available
HF_MODEL    = "unsloth/Qwen3-4B-unsloth-bnb-4bit"

# LoRA config — conservative settings to fit in 6GB VRAM
LORA_R          = 16
LORA_ALPHA      = 16
MAX_SEQ_LEN     = 512     # keep short to fit VRAM
BATCH_SIZE      = 1
GRAD_ACCUM      = 8       # effective batch = 8
LR              = 2e-4
MAX_STEPS       = 200     # ~1-2 hours on RTX 4050, tune up for longer runs
WARMUP_STEPS    = 20
SAVE_STEPS      = 50


def load_dataset(path: pathlib.Path):
    """Load JSONL training data, return list of message dicts."""
    samples = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                samples.append(json.loads(line))
    print(f"[TRAIN] loaded {len(samples)} samples from {path}")
    return samples


def format_sample(sample: dict) -> str:
    """Convert a {messages: [...]} sample to Qwen3 chat template string."""
    msgs = sample.get("messages", [])
    parts = []
    for msg in msgs:
        role    = msg["role"]
        content = msg["content"]
        if role == "system":
            parts.append(f"<|im_start|>system\n{content}<|im_end|>")
        elif role == "user":
            parts.append(f"<|im_start|>user\n{content}<|im_end|>")
        elif role == "assistant":
            parts.append(f"<|im_start|>assistant\n{content}<|im_end|>")
    return "\n".join(parts)


def main():
    print("[TRAIN] ── AKSUMAEL QLoRA fine-tune ────────────────────────")
    print(f"[TRAIN] model:   {HF_MODEL}")
    print(f"[TRAIN] data:    {TRAIN_DATA}")
    print(f"[TRAIN] output:  {OUTPUT_DIR}")
    print(f"[TRAIN] steps:   {MAX_STEPS}")

    try:
        from unsloth import FastLanguageModel
    except ImportError:
        print("[TRAIN] ERROR: unsloth not installed")
        print("[TRAIN] Run: venv/bin/pip install 'unsloth @ git+https://github.com/unslothai/unsloth.git'")
        sys.exit(1)

    import torch
    # Clear any lingering CUDA state from other processes
    torch.cuda.empty_cache()
    free_vram = torch.cuda.mem_get_info()[0] / 1e9
    print(f"[TRAIN] VRAM free: {free_vram:.1f} GB")
    if free_vram < 4.0:
        print("[TRAIN] WARNING: less than 4GB VRAM free — stop mesh-llm.service first!")
        print("[TRAIN]   systemctl --user stop mesh-llm.service")
        sys.exit(1)

    # ── Load base model ────────────────────────────────────────────────────────
    print("[TRAIN] loading base model (4-bit)...")
    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name      = HF_MODEL,
        max_seq_length  = MAX_SEQ_LEN,
        dtype           = None,           # auto-detect (bf16 on RTX 4050)
        load_in_4bit    = True,
        cache_dir       = str(pathlib.Path.home() / "models" / "hf_cache"),
    )

    # ── Add LoRA ───────────────────────────────────────────────────────────────
    model = FastLanguageModel.get_peft_model(
        model,
        r               = LORA_R,
        target_modules  = ["q_proj", "k_proj", "v_proj", "o_proj",
                           "gate_proj", "up_proj", "down_proj"],
        lora_alpha      = LORA_ALPHA,
        lora_dropout    = 0,
        bias            = "none",
        use_gradient_checkpointing = "unsloth",
        random_state    = 42,
        use_rslora      = False,
    )

    print(f"[TRAIN] trainable params: "
          f"{sum(p.numel() for p in model.parameters() if p.requires_grad):,}")

    # ── Prepare dataset ────────────────────────────────────────────────────────
    from datasets import Dataset

    raw = load_dataset(TRAIN_DATA)
    texts = [format_sample(s) + tokenizer.eos_token for s in raw]

    def tokenize(batch):
        return tokenizer(
            batch["text"],
            truncation  = True,
            max_length  = MAX_SEQ_LEN,
            padding     = False,
        )

    ds = Dataset.from_dict({"text": texts})
    ds = ds.map(tokenize, batched=True, remove_columns=["text"])
    print(f"[TRAIN] dataset: {len(ds)} tokenized samples")

    # ── Trainer ────────────────────────────────────────────────────────────────
    from trl import SFTTrainer
    from transformers import TrainingArguments, DataCollatorForSeq2Seq

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    trainer = SFTTrainer(
        model           = model,
        tokenizer       = tokenizer,
        train_dataset   = ds,
        dataset_text_field = None,
        max_seq_length  = MAX_SEQ_LEN,
        data_collator   = DataCollatorForSeq2Seq(tokenizer, pad_to_multiple_of=8),
        args            = TrainingArguments(
            output_dir                  = str(OUTPUT_DIR / "checkpoints"),
            per_device_train_batch_size = BATCH_SIZE,
            gradient_accumulation_steps = GRAD_ACCUM,
            warmup_steps                = WARMUP_STEPS,
            max_steps                   = MAX_STEPS,
            learning_rate               = LR,
            fp16                        = not torch.cuda.is_bf16_supported(),
            bf16                        = torch.cuda.is_bf16_supported(),
            logging_steps               = 10,
            save_steps                  = SAVE_STEPS,
            optim                       = "adamw_8bit",
            weight_decay                = 0.01,
            lr_scheduler_type           = "linear",
            seed                        = 42,
            report_to                   = "none",
        ),
    )

    print("[TRAIN] ── starting training ───────────────────────────────")
    import time
    t0 = time.time()
    trainer.train()
    elapsed = time.time() - t0
    print(f"[TRAIN] training done in {elapsed/60:.1f} min")

    # ── Save LoRA adapter ──────────────────────────────────────────────────────
    print(f"[TRAIN] saving LoRA adapter → {OUTPUT_DIR}")
    model.save_pretrained(str(OUTPUT_DIR))
    tokenizer.save_pretrained(str(OUTPUT_DIR))

    # ── Export GGUF ───────────────────────────────────────────────────────────
    gguf_path = GGUF_DIR / "Qwen3-4B-AKSUMAEL-ft.gguf"
    print(f"[TRAIN] exporting GGUF → {gguf_path}")
    try:
        model.save_pretrained_gguf(
            str(gguf_path.with_suffix("")),
            tokenizer,
            quantization_method = "q4_k_m",
        )
        print(f"[TRAIN] GGUF saved: {gguf_path}")
    except Exception as e:
        print(f"[TRAIN] GGUF export failed: {e}")
        print("[TRAIN] LoRA adapter saved — merge manually with llama.cpp convert scripts")

    print("[TRAIN] ── DONE ─────────────────────────────────────────────")
    print(f"[TRAIN] Next: update mesh-llm.service to load {gguf_path.name}")
    print("[TRAIN]   systemctl --user stop mesh-llm.service")
    print(f"[TRAIN]   # edit service to point at {gguf_path}")
    print("[TRAIN]   systemctl --user start mesh-llm.service")


if __name__ == "__main__":
    main()
