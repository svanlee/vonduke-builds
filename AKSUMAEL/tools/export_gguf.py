#!/usr/bin/env python3
"""
Export fine-tuned Qwen3-4B LoRA as a GGUF and deploy to mesh-llm.service.

Strategy (no 7.5 GB full-precision download needed):
  1. Stop mesh-llm to free VRAM
  2. Download Qwen3-4B-Q4_K_M.gguf base (~2.4 GB) from HuggingFace
  3. Convert our LoRA adapter (adapter_model.safetensors) to GGUF LoRA
     using llama.cpp/convert_lora_to_gguf.py
  4. Write updated mesh-llm.service pointing to base + lora
  5. Reload systemd + restart mesh-llm

Run as:
    nohup venv/bin/python3 tools/export_gguf.py > /tmp/export_gguf.log 2>&1 &
"""
import os, sys, subprocess, time, re, shutil, textwrap

# ── Paths ─────────────────────────────────────────────────────────────────────
HOME          = os.path.expanduser("~")
AKSUMAEL_DIR  = os.path.join(HOME, "vonduke-builds", "AKSUMAEL")
VENV_PY       = os.path.join(AKSUMAEL_DIR, "venv", "bin", "python3")

LORA_HF_DIR   = os.path.join(HOME, "models", "qwen3-4b-ft")
OUT_DIR       = os.path.join(HOME, "models", "qwen3-4b")
GGUF_BASE     = os.path.join(OUT_DIR, "Qwen3-4B-Q5_K_M.gguf")
GGUF_LORA     = os.path.join(OUT_DIR, "Qwen3-4B-AKSUMAEL-lora.gguf")

LLAMA_CPP     = os.path.join(HOME, "llama.cpp")
LLAMA_SERVER  = os.path.join(LLAMA_CPP, "build", "bin", "llama-server")
CONVERT_LORA  = os.path.join(LLAMA_CPP, "convert_lora_to_gguf.py")

HF_BASE_REPO  = "Qwen/Qwen3-4B-GGUF"
HF_FILENAME   = "Qwen3-4B-Q5_K_M.gguf"

HF_CACHE_DIR  = os.path.join(HOME, "models", "hf_cache")
UNSLOTH_BASE  = os.path.join(
    HF_CACHE_DIR,
    "models--unsloth--Qwen3-4B-unsloth-bnb-4bit",
)

SERVICE_PATH  = os.path.join(HOME, ".config", "systemd", "user", "mesh-llm.service")
SERVICE_NAME  = "mesh-llm.service"


def log(msg):
    print(f"[export_gguf] {msg}", flush=True)


def run(cmd, **kwargs):
    log(f"$ {' '.join(str(c) for c in cmd)}")
    return subprocess.run(cmd, check=True, **kwargs)


def unsloth_snapshot() -> str:
    """Return the local path of the cached unsloth 4-bit Qwen3-4B snapshot."""
    snap_root = os.path.join(UNSLOTH_BASE, "snapshots")
    snaps = sorted(os.listdir(snap_root))
    if not snaps:
        raise FileNotFoundError(f"No snapshots under {snap_root}")
    return os.path.join(snap_root, snaps[-1])


# ── Step 0: stop mesh-llm to free VRAM ────────────────────────────────────────
log("Step 0: stopping mesh-llm.service to free VRAM …")
subprocess.run(["systemctl", "--user", "stop", SERVICE_NAME])
time.sleep(3)


# ── Step 1: download base GGUF ────────────────────────────────────────────────
os.makedirs(OUT_DIR, exist_ok=True)

if os.path.exists(GGUF_BASE):
    log(f"Step 1: base GGUF already present → {GGUF_BASE}  (skipping download)")
else:
    log(f"Step 1: downloading {HF_BASE_REPO}/{HF_FILENAME} (~2.4 GB) …")
    # Use the venv's huggingface_hub so we don't pollute system Python
    sys.path.insert(0, os.path.join(AKSUMAEL_DIR, "venv", "lib", "python3.12", "site-packages"))
    from huggingface_hub import hf_hub_download
    tmp = hf_hub_download(
        repo_id=HF_BASE_REPO,
        filename=HF_FILENAME,
        cache_dir=HF_CACHE_DIR,
        local_dir=OUT_DIR,
        local_dir_use_symlinks=False,
    )
    if os.path.abspath(tmp) != os.path.abspath(GGUF_BASE):
        shutil.move(tmp, GGUF_BASE)
    log(f"Step 1: base GGUF saved → {GGUF_BASE}")


# ── Step 2: convert LoRA → GGUF LoRA ──────────────────────────────────────────
if os.path.exists(GGUF_LORA):
    log(f"Step 2: GGUF LoRA already present → {GGUF_LORA}  (skipping conversion)")
else:
    log("Step 2: converting LoRA adapter to GGUF …")
    snap = unsloth_snapshot()
    log(f"  base model dir: {snap}")
    log(f"  lora adapter:   {LORA_HF_DIR}")
    log(f"  output:         {GGUF_LORA}")

    env = os.environ.copy()
    env["PYTHONPATH"] = os.path.join(AKSUMAEL_DIR, "venv", "lib", "python3.12", "site-packages")

    # convert_lora_to_gguf.py args: base_model_dir lora_dir --outfile output.gguf
    run(
        [VENV_PY, CONVERT_LORA, snap, "--lora-path", LORA_HF_DIR, "--outfile", GGUF_LORA],
        env=env,
        cwd=LLAMA_CPP,
    )
    log(f"Step 2: GGUF LoRA → {GGUF_LORA}")


# ── Step 3: verify llama-server binary ────────────────────────────────────────
if not os.path.exists(LLAMA_SERVER):
    log(f"ERROR: llama-server not found at {LLAMA_SERVER}")
    log("Run: cd ~/llama.cpp && cmake -B build -DGGML_CUDA=ON && cmake --build build --config Release -j$(nproc)")
    sys.exit(1)
log(f"Step 3: llama-server binary OK → {LLAMA_SERVER}")


# ── Step 4: update mesh-llm.service ──────────────────────────────────────────
log("Step 4: updating mesh-llm.service …")

new_service = textwrap.dedent(f"""\
    [Unit]
    Description=LLaMA Server — Qwen3-4B-Q5_K_M + AKSUMAEL LoRA (fine-tuned, local)
    After=network.target

    [Service]
    Environment=LD_LIBRARY_PATH=/home/ros/cuda-12.6/targets/x86_64-linux/lib
    ExecStart={LLAMA_SERVER} \\
        --model {GGUF_BASE} \\
        --lora {GGUF_LORA} \\
        --host 0.0.0.0 --port 9337 \\
        --ctx-size 8192 \\
        --parallel 1 \\
        --cache-type-k q8_0 \\
        --cache-type-v q8_0 \\
        -fa on \\
        --n-gpu-layers 28 \\
        --ngl 28 \\
        --temp 0.2 \\
        --repeat-penalty 1.1
    Restart=on-failure
    RestartSec=5

    [Install]
    WantedBy=default.target
""")

# Back up original (once)
backup = SERVICE_PATH + ".bak"
if not os.path.exists(backup):
    shutil.copy2(SERVICE_PATH, backup)
    log(f"  backed up original → {backup}")

with open(SERVICE_PATH, "w") as f:
    f.write(new_service)
log("Step 4: service file written.")


# ── Step 5: reload & restart ─────────────────────────────────────────────────
log("Step 5: reloading systemd and restarting mesh-llm …")
run(["systemctl", "--user", "daemon-reload"])
run(["systemctl", "--user", "start", SERVICE_NAME])

log("Waiting 10s for model to load …")
time.sleep(10)

r = subprocess.run(
    ["systemctl", "--user", "is-active", SERVICE_NAME],
    capture_output=True, text=True
)
state = r.stdout.strip()
if state == "active":
    log(f"✓ mesh-llm.service is ACTIVE — fine-tuned Qwen3-4B deployed on port 9337")
else:
    log(f"✗ service state: {state} — check: journalctl --user -u mesh-llm.service -n 30")
    sys.exit(1)

log("Done. AKSUMAEL is now running the fine-tuned Qwen3-4B model.")
