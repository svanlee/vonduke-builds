"""Minimal OpenAI-compatible /v1/embeddings server, fully local.

Honcho's deriver embeds every observation it derives, unconditionally
(src/crud/representation.py) — EMBED_MESSAGES=false only gates *message*
embeddings. mesh-llm on :9337 is started without --embeddings, so this fills
the gap so the stack makes zero cloud calls.

all-MiniLM-L6-v2, 384 dims, runs on CPU or CUDA.

Our own code, deliberately NOT part of the AGPL Honcho checkout (which stays
outside this repo — see docs/HONCHO_SPIKE.md, "Licensing and packaging").
Honcho reaches it via [embedding.model_config.overrides] base_url in its
config.toml.

**Launch it CPU-only.** The RTX 4050's 6 GB is already ~4 GB of mesh-llm with
YOLOv8 competing for the rest; CUDA mode costs 212 MiB of VRAM and is no
faster than CPU at this batch size:

    CUDA_VISIBLE_DEVICES="" venv/bin/python tools/local_embed_server.py

Honcho's deriver fails *silently* into a retry loop if this isn't up yet, so
start it before the deriver.
"""

import torch
import torch.nn.functional as F
import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel
from transformers import AutoModel, AutoTokenizer

MODEL = "sentence-transformers/all-MiniLM-L6-v2"
DIMS = 384

device = "cuda" if torch.cuda.is_available() else "cpu"
tokenizer = AutoTokenizer.from_pretrained(MODEL)
model = AutoModel.from_pretrained(MODEL).to(device).eval()

app = FastAPI()


class EmbeddingRequest(BaseModel):
    input: str | list[str]
    model: str | None = None
    dimensions: int | None = None
    encoding_format: str | None = None
    user: str | None = None


@app.get("/v1/models")
def models():
    return {"object": "list", "data": [{"id": MODEL, "object": "model", "owned_by": "local"}]}


@app.post("/v1/embeddings")
def embeddings(req: EmbeddingRequest):
    texts = [req.input] if isinstance(req.input, str) else list(req.input)
    batch = tokenizer(
        texts, padding=True, truncation=True, max_length=512, return_tensors="pt"
    ).to(device)
    with torch.no_grad():
        out = model(**batch).last_hidden_state
    mask = batch["attention_mask"].unsqueeze(-1).expand(out.size()).float()
    pooled = (out * mask).sum(1) / mask.sum(1).clamp(min=1e-9)
    vecs = F.normalize(pooled, p=2, dim=1).cpu().tolist()
    return {
        "object": "list",
        "model": req.model or MODEL,
        "data": [
            {"object": "embedding", "index": i, "embedding": v} for i, v in enumerate(vecs)
        ],
        "usage": {"prompt_tokens": int(batch["attention_mask"].sum()), "total_tokens": 0},
    }


if __name__ == "__main__":
    print(f"local embedding server on :9338 ({MODEL}, {DIMS} dims, {device})")
    uvicorn.run(app, host="127.0.0.1", port=9338, log_level="warning")
