#!/usr/bin/env python3
"""
Direct HF LoRA safetensors → GGUF LoRA converter for Qwen3-4B.

Bypasses llama.cpp convert_lora_to_gguf.py which fails on BNB 4-bit base models
because it unconditionally calls dequant_model() on the base.

This script reads adapter_model.safetensors directly and writes a GGUF LoRA
file using the known Qwen3 tensor name mapping. No base model loading required.

Usage:
    cd ~/vonduke-builds/AKSUMAEL
    venv/bin/python3 tools/lora_to_gguf_direct.py

Output: ~/models/qwen3-4b/Qwen3-4B-AKSUMAEL-lora.gguf
Load with llama-server: --model base.gguf --lora adapter.gguf
"""
import json
import sys
import os
import numpy as np
from pathlib import Path

# Add llama.cpp gguf-py to path
sys.path.insert(0, os.path.expanduser("~/llama.cpp/gguf-py"))
import gguf

from safetensors import safe_open

# ── Paths ──────────────────────────────────────────────────────────────────────
LORA_DIR = Path(os.path.expanduser("~/models/qwen3-4b-ft"))
ST_FILE  = LORA_DIR / "adapter_model.safetensors"
CFG_FILE = LORA_DIR / "adapter_config.json"
OUT_FILE = Path(os.path.expanduser("~/models/qwen3-4b/Qwen3-4B-AKSUMAEL-lora.gguf"))

# ── Qwen3-4B architecture constants (from config.json) ─────────────────────────
ARCH             = "qwen3"
N_LAYERS         = 36
HIDDEN_SIZE      = 2560
N_HEADS          = 32
N_KV_HEADS       = 8
HEAD_DIM         = 128
INTERMEDIATE_SIZE = 9728
VOCAB_SIZE       = 151936
ROPE_FREQ_BASE   = 1_000_000.0
MAX_SEQ_LEN      = 40960

# ── HF layer name → GGUF tensor prefix ────────────────────────────────────────
# HF pattern:  base_model.model.model.layers.{L}.{hf_mod}.lora_{A/B}.weight
# GGUF pattern: blk.{L}.{gguf_mod}.weight.lora_{a/b}
MODULE_MAP = {
    "self_attn.q_proj":  "attn_q",
    "self_attn.k_proj":  "attn_k",
    "self_attn.v_proj":  "attn_v",
    "self_attn.o_proj":  "attn_output",
    "mlp.gate_proj":     "ffn_gate",
    "mlp.up_proj":       "ffn_up",
    "mlp.down_proj":     "ffn_down",
}


def hf_to_gguf_name(hf_name: str) -> str | None:
    """Map a HF LoRA tensor name to its GGUF equivalent, or None to skip."""
    parts = hf_name.split(".")
    # Must contain "layers" with an integer index after it
    if "layers" not in parts:
        return None
    try:
        layer_idx = int(parts[parts.index("layers") + 1])
    except (ValueError, IndexError):
        return None

    for hf_mod, gguf_mod in MODULE_MAP.items():
        # hf_mod has dots — check both parts match
        mod_parts = hf_mod.split(".")
        if all(p in parts for p in mod_parts):
            if "lora_A" in parts:
                ab = "lora_a"
            elif "lora_B" in parts:
                ab = "lora_b"
            else:
                return None
            return f"blk.{layer_idx}.{gguf_mod}.weight.{ab}"
    return None


def main() -> None:
    # ── Load adapter config ──────────────────────────────────────────────────
    with open(CFG_FILE) as f:
        cfg = json.load(f)

    lora_alpha: float = float(cfg.get("lora_alpha", 16))
    lora_rank:  int   = int(cfg.get("r", 16))
    target_mods       = cfg.get("target_modules", [])

    print(f"[lora→gguf] rank={lora_rank}, alpha={lora_alpha}")
    print(f"[lora→gguf] targets: {target_mods}")

    # ── Read safetensors ─────────────────────────────────────────────────────
    print(f"[lora→gguf] reading {ST_FILE} …")
    st = safe_open(str(ST_FILE), framework="pt", device="cpu")

    tensors: dict[str, np.ndarray] = {}
    skipped = []
    for hf_key in sorted(st.keys()):
        gguf_name = hf_to_gguf_name(hf_key)
        if gguf_name is not None:
            tensors[gguf_name] = st.get_tensor(hf_key).float().numpy()
        else:
            skipped.append(hf_key)

    if skipped:
        print(f"[lora→gguf] skipped {len(skipped)} tensors: {skipped[:5]}")
    print(f"[lora→gguf] mapped {len(tensors)} tensors")

    if len(tensors) == 0:
        print("[lora→gguf] ERROR: no tensors mapped — check name mapping")
        sys.exit(1)

    # ── Write GGUF LoRA ──────────────────────────────────────────────────────
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    print(f"[lora→gguf] writing {OUT_FILE} …")

    writer = gguf.GGUFWriter(str(OUT_FILE), arch=ARCH)

    # Adapter metadata
    writer.add_type(gguf.GGUFType.ADAPTER)
    writer.add_string(gguf.Keys.Adapter.TYPE, "lora")
    writer.add_float32(gguf.Keys.Adapter.LORA_ALPHA, lora_alpha)

    # Architecture KV (llama-server may validate tensor dims against these)
    writer.add_uint32("qwen3.block_count",               N_LAYERS)
    writer.add_uint32("qwen3.context_length",            MAX_SEQ_LEN)
    writer.add_uint32("qwen3.embedding_length",          HIDDEN_SIZE)
    writer.add_uint32("qwen3.feed_forward_length",       INTERMEDIATE_SIZE)
    writer.add_uint32("qwen3.attention.head_count",      N_HEADS)
    writer.add_uint32("qwen3.attention.head_count_kv",   N_KV_HEADS)
    writer.add_uint32("qwen3.attention.key_length",      HEAD_DIM)
    writer.add_uint32("qwen3.attention.value_length",    HEAD_DIM)
    writer.add_float32("qwen3.rope.freq_base",           ROPE_FREQ_BASE)
    writer.add_uint32("qwen3.vocab_size",                VOCAB_SIZE)

    # Add tensors BEFORE writing header (GGUFWriter needs counts at header time)
    for name in sorted(tensors):
        t = tensors[name]
        writer.add_tensor(name, t)

    # Write header, KV data, then tensor data
    writer.write_header_to_file()
    writer.write_kv_data_to_file()
    writer.write_tensors_to_file()
    writer.close()

    size_mb = OUT_FILE.stat().st_size / (1024 * 1024)
    print(f"[lora→gguf] ✓  {OUT_FILE}  ({size_mb:.1f} MB, {len(tensors)} tensors)")


if __name__ == "__main__":
    main()
