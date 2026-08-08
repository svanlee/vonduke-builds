# Honcho Spike — Local deriver, zero cloud API calls

**Date:** 2026-08-08
**Question:** Can a self-hosted Honcho server run its deriver against mesh-llm at
`http://localhost:9337/v1` instead of cloud APIs?
**Scope:** Episodic Memory + Inner Monologue only. Belief State and entity tracking stay
local/hand-rolled. Self-hosted only — not api.honcho.dev.

---

## Verdict

**YES — proven end to end on this machine.** A self-hosted Honcho server + deriver
derived real observations from AKSUMAEL-style messages using only mesh-llm. Socket-level
capture showed the deriver's *only* TCP peers were three loopback ports.

**Architecture: call the Honcho SDK directly. Do not use Hermes as an indirection
layer.** Hermes is a standalone interactive agent application, not an embeddable library.
See [Direct vs Hermes](#architecture-direct-sdk-vs-hermes-indirection).

**Footprint: ~1.7 GB RAM, 0 additional VRAM** — provided the embedding server is pinned
to CPU. See [Resource footprint](#resource-footprint-measured).

Three things the handoff did not anticipate, all resolved:

1. The Honcho **server is not pip-installable** — AGPL-3.0, Docker/source only. Only the
   client SDK is on PyPI. ([details](#licensing-and-packaging))
2. A **local embedding endpoint is mandatory** and mesh-llm does not serve one.
   ([Blocker 1](#blocker-1-embeddings-are-not-optional))
3. **Qwen3.5 silently returns nothing** unless thinking is disabled.
   ([Blocker 2](#blocker-2-qwen35-burns-the-whole-budget-on-reasoning))

And one scope tension worth a decision from you:
**Honcho's deriver *is* a belief-state engine.** See [Scope fit](#scope-fit-what-we-actually-get).

---

## Answers to the spike questions

### Package name

`honcho-ai` (2.2.0, Apache-2.0) — **client SDK only**. Two decoys:

- `honcho` on PyPI (2.0.0) is an unrelated Procfile process manager.
- `honcho-ai` contains no server, no deriver, no LLM-calling code.

### Can the server be self-hosted?

Yes. FastAPI app + a separate deriver worker process. Verified at `127.0.0.1:8000`,
`/health` → `{"status":"ok"}`.

Requires **Postgres with pgvector** (Ubuntu's pgvector 0.6.0 was sufficient) and Python
3.11+. No Docker on this box, so the stack ran directly under `uv`.

Server version tested: `plastic-labs/honcho` @ `d191c10` (2026-08-06), v3.0.11.

### Is the LLM endpoint configurable? Can it point at :9337?

Yes to both, and it is a documented first-class feature rather than a hack. Honcho's own
docs (`docs/v3/contributing/configuration.mdx`) list **vLLM and Ollama** as supported:

> For OpenAI-compatible proxies (OpenRouter, vLLM, Ollama, etc.), use
> `transport = "openai"` and set `MODEL_CONFIG__OVERRIDES__BASE_URL`.

`base_url` is set **per module** (deriver, dialectic, summary, dream, embedding), not
globally. Corroborated independently by Hermes' own Honcho plugin, which exposes a
`baseUrl` config and auto-skips API-key auth for local URLs — self-hosting is a
well-trodden path.

### Config keys

Every setting works as TOML in `config.toml` or as an env var with a `{SECTION}_{KEY}`
pattern (`__` for nesting):

| Purpose | Env var | TOML |
|---|---|---|
| Transport | `DERIVER_MODEL_CONFIG__TRANSPORT` | `[deriver.model_config] transport` |
| Model name | `DERIVER_MODEL_CONFIG__MODEL` | `[deriver.model_config] model` |
| **Endpoint** | `DERIVER_MODEL_CONFIG__OVERRIDES__BASE_URL` | `[deriver.model_config.overrides] base_url` |
| API key (placeholder) | `LLM_OPENAI_API_KEY` | `[llm] OPENAI_API_KEY` |
| Loose JSON mode | `DERIVER_MODEL_CONFIG__STRUCTURED_OUTPUT_MODE` | `structured_output_mode` |
| Raw body passthrough | — (nested; use TOML) | `[deriver.model_config.overrides.provider_params] extra_body` |
| Embedding endpoint | `EMBEDDING_MODEL_CONFIG__OVERRIDES__BASE_URL` | `[embedding.model_config.overrides] base_url` |
| Embedding dims | `EMBEDDING_VECTOR_DIMENSIONS` | `[embedding] VECTOR_DIMENSIONS` |

Two things worth knowing:

- **A non-empty API key is required even for a local server.** `src/llm/registry.py`
  raises `Missing API key for openai model config` on an empty key. Any placeholder
  works. This is also a safety property: a leaked cloud call fails loudly with a 401
  instead of silently billing.
- `structured_output_mode = "json_object"` exists for OpenAI-compatible servers that
  reject `response_format: json_schema`. We did **not** need it — llama.cpp enforces
  json_schema natively via grammars. Keep it in reserve if the mesh-llm backend changes.
  Structured output is only used by the deriver.

---

## Scope fit: what we actually get

The handoff scopes this to **Episodic Memory + Inner Monologue**, explicitly *not* Belief
State or entity tracking. Mapping that onto Honcho's actual parts:

| We want | Honcho mechanism | Needs deriver? | Verified |
|---|---|---|---|
| Episodic memory | `session.add_messages()`, `session.search()`, `client.search()`, `session.context()` | **No** | ✅ |
| Inner monologue | bot as its own peer → `peer.representation()` | **Yes** | ✅ |
| Belief state | deriver representations/conclusions | — | out of scope |
| Entity tracking | peers-as-entities, peer cards, dream | — | out of scope |

**The tension:** Honcho's deriver *is* a belief-state engine. Its whole job is building
an evolving model of each peer. "Inner monologue" in Honcho terms is the deriver pointed
at the bot's own peer — the same machinery, aimed inward. So the scope boundary is not
"deriver on/off"; it is **which peers we let the deriver model**.

Concretely:

- **Keep the deriver**, but only create peers for the bot itself (and optionally a single
  `world` narrator peer). Do not create a peer per mob/player/entity — that is where
  Honcho would start duplicating the hand-rolled entity tracker.
- **Disable `dream`** — that is the deep cross-session synthesis pass, the most
  belief-state-flavoured feature and the most expensive.
- **Disable `peer_card`** — compact entity-summary cards, squarely out of scope.
- Episodic recall needs `EMBED_MESSAGES = true` and the embedding endpoint, but does
  **not** need the deriver at all.

Worth being blunt about: if you later decide you want *only* episodic recall and no
self-model, Honcho collapses to "Postgres + pgvector + a search endpoint," and the
value over hand-rolling drops sharply. The deriver-driven self-representation is the
main thing Honcho gives you that you don't already have.

**Verified episodic recall** — cross-session workspace search found the lava episode from
a different session:

```
client.search("lava")
 - I fell into lava at y=8 and lost my iron gear.          # session 3
 - I mined 3 diamond ore and took 2 hearts of damage...    # session 1
 - Nightfall. Three zombies spawned near the base entrance. # session 2
```

Ranking is loose at this corpus size with 384-dim MiniLM — the top hit is right, the tail
is noise. Fine for recall-with-reranking; do not treat raw ordering as precision.

**Verified inner-monologue self-model** — from two messages:

```
aksumael fell into lava at y=8 and lost their iron gear.
aksumael carries a water bucket when going below y=20.
```

That second line is the payoff: a durable behavioural rule the bot stated once, extracted
and persisted for future sessions. That is what makes Honcho worth the footprint.

---

## Architecture: direct SDK vs Hermes indirection

**Verdict: call `honcho-ai` directly from AKSUMAEL. Do not route through Hermes.**

Hermes Agent (NousResearch, MIT) does ship Honcho as a first-class memory provider, and
its plugin is genuinely well-built. But it is the wrong shape for this job:

1. **Hermes is not a library.** Its own docs describe it as "a self-contained agent
   application rather than an embeddable Python library for programmatic integration."
   You launch it as `hermes` (interactive TUI) or `hermes gateway` (Telegram/Discord/
   Slack/WhatsApp/Signal bridge). There is no supported way to call it as a memory API
   from a Minecraft bot's tick loop.
2. **It drags in a second runtime.** The installer pulls Python 3.11 **plus Node.js**,
   ripgrep, and ffmpeg via `curl | bash`. On a Victus already running YOLOv8 + mesh-llm,
   adding a Node process and a full agent framework to reach a Postgres-backed memory
   store is a bad trade.
3. **Its Honcho plugin is chat-shaped.** Two-layer context injection into *user messages*
   to preserve prompt caching, `per-directory`/`per-repo` session strategies, multi-pass
   dialectic on a refresh cadence. Every one of those assumptions is about a human typing
   in a terminal. None map to game ticks.
4. **No licensing benefit.** Hermes being MIT does not launder Honcho's AGPL, and there
   is nothing to launder — we are not distributing Honcho. `honcho-ai` is Apache-2.0 and
   is already exactly the arm's-length boundary we want.
5. **Indirection costs the thing we need most.** Direct SDK gives per-call control over
   when derivation happens — which is the main lever for managing GPU contention. A
   general-purpose agent framework's refresh cadence takes that away.

**Where Hermes is still useful: as a reference implementation.** Its plugin has already
solved problems we will hit — session-scoping strategy, how often to refresh derived
context, and modelling the AI as its own peer with a self-representation (exactly our
inner monologue). Worth reading `plugins/memory/honcho/` before designing ours. Read it;
don't run it.

---

## Blockers found (and fixes)

### Blocker 1: Embeddings are not optional

`EMBED_MESSAGES = false` is **not** enough to avoid an embedding provider. That flag only
gates *message* embeddings (`src/crud/message.py`, `src/routers/messages.py`). The deriver
separately embeds every observation it derives, unconditionally, in
`src/crud/representation.py`. With no embedding endpoint the run failed:

```
src.embedding_client - ERROR - Error processing batch after all retries
openai.AuthenticationError: Error code: 401 - Incorrect API key provided: not-needed
src.deriver.deriver - ERROR - Failed to save representation for observer aksumael: 401
```

Note the deriver's *LLM* call had already succeeded locally at that point
(`llm_call_duration=6862ms | observation_count=5`) — only the persist step died. Easy to
misread as "the local LLM didn't work."

**Fix:** mesh-llm returns `501 This server does not support embeddings. Start it with
--embeddings`, so a second local OpenAI-compatible endpoint runs on `:9338`
(all-MiniLM-L6-v2, 384 dims, ~40 LOC). Alternatives: restart mesh-llm with `--embeddings`,
or a second llama.cpp instance with an embedding GGUF.

**Embedding dimension is immutable per deployment**, machine-enforced at boot against the
pgvector column type. Going from the 1536 default to 384 required
`scripts/configure_embeddings.py --yes` against an empty DB. Pick the dimension before
ingesting anything real.

### Blocker 2: Qwen3.5 burns the whole budget on reasoning

The one that would have silently sunk the integration. mesh-llm's model is a thinking
model: by default it emits everything into `reasoning_content` and returns **empty**
`content`, and `response_format: json_schema` does not constrain the reasoning channel.

```
max_tokens=1500 → finish_reason: length, content: '', reasoning_len: 6145 chars, 34s
```

Honcho would get empty structured output and derive nothing, with no obvious error.

**Fix:** disable thinking via the chat template. Honcho forwards
`provider_params.extra_body` onto the request body (`src/llm/request_builder.py`,
`PASSTHROUGH_KEYS`), and llama.cpp forwards `chat_template_kwargs` into the template:

```toml
[deriver.model_config.overrides.provider_params]
extra_body = { chat_template_kwargs = { enable_thinking = false } }
```

Result: schema-valid JSON in **36 tokens** instead of 1500+ wasted ones.

Probed and rejected: `reasoning_budget = 0` and `chat_template_kwargs.thinking_budget = 0`
— both still emitted reasoning and returned empty content. Only `enable_thinking = false`
works.

### Blocker 3: 4096-token context window

mesh-llm reports `n_ctx: 4096`; Honcho's deriver defaults are `MAX_INPUT_TOKENS = 25000`
and `REPRESENTATION_BATCH_TARGET_INPUT_TOKENS = 1024`. Defaults will overflow on any real
conversation. The config below lowers every token budget. This caps how much history one
derivation can consider.

---

## Resource footprint (measured)

Measured on this box with the full stack running and three sessions derived.

### RAM

| Process | RSS |
|---|---|
| Honcho API (uvicorn + uv wrapper) | ~374 MB |
| Deriver worker (+ uv wrapper) | ~371 MB |
| Embedding server (**CPU mode**) | ~761 MB |
| Postgres (9 procs) | ~187 MB |
| **Total** | **~1.7 GB** |

The embedding server is the biggest single line and it is almost entirely PyTorch import
overhead, not the model (MiniLM is 22M params). If 760 MB matters, an ONNX Runtime or
llama.cpp-GGUF embedding server would cut it to well under 100 MB. Not worth doing unless
RAM gets tight.

### VRAM — the number that actually matters

GPU is a 6141 MiB RTX 4050, with mesh-llm (llama.cpp) already holding **3950 MiB**,
leaving ~2.1 GB for YOLOv8 and everything else.

| Embedding server mode | VRAM | RAM | Latency (8 texts) |
|---|---|---|---|
| CUDA (default) | **212 MiB** | 1193 MB | ~20 ms |
| **CPU (`CUDA_VISIBLE_DEVICES=""`)** | **0 MiB** | 761 MB | **10–20 ms** |

**Run embeddings on CPU.** It is no slower at this batch size, uses less RAM, and gives
back 212 MiB of a VRAM budget that YOLOv8 is already competing for. Verified: after
switching, GPU use dropped 4190 → 3973 MiB and derivation still worked end to end.

Honcho adds **zero additional VRAM** in this configuration. All GPU cost is the mesh-llm
inference it was already going to do.

### Disk

- Honcho checkout incl. its own `.venv`: **1.1 GB** (lives outside the repo)
- Postgres DB after 3 sessions / 9 messages / 11 observations: **9.7 MB**

### Latency

Measured on RTX 4050, Qwen3.5-4B Q4_K_M, thinking disabled:

| Work unit | Messages | LLM call | Observations |
|---|---|---|---|
| `minimal_deriver_4_aksumael` | 4 | 6448 ms | 5 |
| `minimal_deriver_2_world` | 2 | 1881 ms | 1 |
| `minimal_deriver_7_aksumael` | 3 | 4990 ms | 4 |
| `minimal_deriver_6_world` | 2 | 1833 ms | 1 |

**2–7 s of GPU time per derivation.** End-to-end wall clock including queue polling was
16 s for a 2-message session.

### The real cost: GPU contention

This is the finding that should drive the adoption decision. mesh-llm is a **single
llama.cpp instance** shared with AKSUMAEL's vision loop. A derivation occupies it for
2–7 s, during which vision inference stalls. The RAM and VRAM numbers are comfortable;
the serialised GPU access is not.

Mitigations, cheapest first:

1. **Derive only when the bot is idle** — batch messages, flush on safe/idle ticks. The
   deriver already has `REPRESENTATION_BATCH_MAX_AGE_SECONDS` and
   `REPRESENTATION_BATCH_WORK_UNIT_TARGET_TOKENS` for exactly this; we set them to flush
   eagerly for the spike, which is the wrong setting for production.
2. **Keep `WORKERS = 1`** so derivations never overlap.
3. **Feed the deriver sparingly** — one summarised message per episode, not every tick.
4. A second llama.cpp instance on another port only if VRAM allows, which today it does
   not.

---

## Working configuration

Lives in the Honcho server checkout as `config.toml`, outside this repo.

```toml
[app]
LOG_LEVEL = "INFO"
EMBED_MESSAGES = true              # required for episodic search
NAMESPACE = "honcho"

[embedding]
VECTOR_DIMENSIONS = 384            # immutable once data exists — choose now
MAX_INPUT_TOKENS = 512
MAX_TOKENS_PER_REQUEST = 100000

[embedding.model_config]
transport = "openai"
model = "all-MiniLM-L6-v2"
max_batch_size = 16

[embedding.model_config.overrides]
base_url = "http://127.0.0.1:9338/v1"      # local embedding shim (CPU)

[db]
CONNECTION_URI = "postgresql+psycopg://postgres:postgres@localhost:5432/honcho"
SCHEMA = "public"

[auth]
USE_AUTH = false

[llm]
OPENAI_API_KEY = "not-needed"              # must be non-empty; never reaches a cloud
DEFAULT_MAX_TOKENS = 512

[deriver]
ENABLED = true                             # needed for inner-monologue self-model
WORKERS = 1                                # never overlap GPU work
POLLING_SLEEP_INTERVAL_SECONDS = 1.0
POLLING_STARTUP_JITTER_SECONDS = 0.0
MAX_INPUT_TOKENS = 2000                    # mesh-llm n_ctx is 4096
REPRESENTATION_BATCH_WORK_UNIT_TARGET_TOKENS = 0
REPRESENTATION_BATCH_TARGET_INPUT_TOKENS = 1024
FLUSH_ENABLED = true                       # spike only — see GPU contention above
LOG_OBSERVATIONS = true

[deriver.model_config]
transport = "openai"
model = "mesh-llm"                         # llama.cpp ignores the name
temperature = 0.0
max_output_tokens = 512

[deriver.model_config.overrides]
base_url = "http://localhost:9337/v1"      # <-- mesh-llm

[deriver.model_config.overrides.provider_params]
timeout = 600.0
extra_body = { chat_template_kwargs = { enable_thinking = false } }

# Out of scope: belief-state / entity-tracking features.
[dream]
ENABLED = false

[peer_card]
ENABLED = false

[summary]
ENABLED = false

[sentry]
ENABLED = false

[telemetry]
ENABLED = false
```

> **Gotcha:** `base_url` is per-module. Any module left enabled without an override will
> quietly try `api.openai.com`. Override or disable every one.

### Self-host instructions

```bash
# 1. Postgres + pgvector (no Docker on this box)
sudo apt-get install -y postgresql postgresql-16-pgvector
sudo systemctl start postgresql
sudo -u postgres psql -c "ALTER USER postgres PASSWORD 'postgres';"
sudo -u postgres psql -c "CREATE DATABASE honcho;"
sudo -u postgres psql -d honcho -c "CREATE EXTENSION vector;"

# 2. Tooling into the AKSUMAEL venv (pip)
./venv/bin/python -m pip install uv honcho-ai fastapi uvicorn

# 3. Honcho server, OUTSIDE this repo (AGPL — never vendor it in)
git clone --depth 1 https://github.com/plastic-labs/honcho.git <external-dir>/honcho
cd <external-dir>/honcho
cp <config above> config.toml
<AKSUMAEL>/venv/bin/uv sync
<AKSUMAEL>/venv/bin/uv run alembic upgrade head
<AKSUMAEL>/venv/bin/uv run python scripts/configure_embeddings.py --yes   # 1536 -> 384

# 4. Three processes (embedding server pinned to CPU)
CUDA_VISIBLE_DEVICES="" <AKSUMAEL>/venv/bin/python local_embed_server.py     # :9338
<AKSUMAEL>/venv/bin/uv run uvicorn src.main:app --host 127.0.0.1 --port 8000
<AKSUMAEL>/venv/bin/uv run python -m src.deriver
```

`local_embed_server.py` is our own code (no AGPL contamination) — copy it into this repo
if we adopt Honcho. Currently in the session scratchpad.

Note this is **three more processes plus Postgres** in AKSUMAEL's boot sequence, each a
new failure mode for the launcher, and the deriver fails *silently* into a retry loop if
the embedding server isn't up yet.

### Client usage

```python
from honcho import Honcho

client = Honcho(base_url="http://127.0.0.1:8000", workspace_id="aksumael")
bot   = client.peer("aksumael")      # the bot — its own peer, for inner monologue
world = client.peer("world")         # single narrator peer; NOT one peer per entity
session = client.session("run-2026-08-08")

session.add_messages([
    bot.message("I equipped the iron pickaxe from hotbar slot 2 before digging."),
    world.message("You are at y=11 in a cave. Torch count: 4."),
    bot.message("I mined 3 diamond ore and took 2 hearts of damage from a skeleton."),
])

session.representation("aksumael")   # inner monologue / self-model
client.search("lava")                # episodic recall, cross-session
```

---

## Proof of zero cloud calls

**1. Socket-level capture.** `ss -tnp` sampled at 10 Hz against the deriver PID for a full
derivation. Every connection, without exception:

```
  35 127.0.0.1:9338      <- local embeddings
  35 127.0.0.1:9337      <- mesh-llm
  35 127.0.0.1:5432      <- postgres
```

Non-loopback peers: **none**.

**2. No credentials exist to reach a cloud.** The only key configured is the literal
string `not-needed`; `LLM_ANTHROPIC_API_KEY` and `LLM_GEMINI_API_KEY` are unset. Any
cloud call fails with a visible 401 — as it demonstrably did during Blocker 1, which
confirms the detection path works and is not silently swallowed.

**3. Zero 401s in the final run.** `grep -c "openai.com\|401\|invalid_api_key"` over the
deriver log → `0`.

**4. Real derived output.** Session 1, from four messages:

```
## Explicit Observations
aksumael mined 3 diamond ore blocks.
aksumael is playing a video game that includes interactive UI elements such as
  hotbars and health hearts, and features gameplay mechanics involving mining,
  combat with hostile mobs like skeletons, and health management.
aksumael prefers to retreat and eat when their health drops below 6 hearts.
aksumael took 2 hearts of damage from a skeleton.
aksumael was in a cave environment at a vertical coordinate of y=11 and had 4 torches lit.
```

Session 2:

```
aksumael blocks the base entrance with cobblestone before logging off.
aksumael crafted a diamond pickaxe.
aksumael placed the diamond pickaxe in hotbar slot 2.
```

Session 3 (run against the CPU embedding server, confirming that switch is safe):

```
aksumael fell into lava at y=8 and lost their iron gear.
aksumael carries a water bucket when going below y=20.
```

11 observation documents persisted with 384-dim embeddings.

**Quality caveat:** the 4B model editorialises ("is playing a video game...") and stamps
wall-clock time onto in-game events. Good enough for preference and episode capture; do
**not** treat derived text as ground truth for world state — which is consistent with
keeping belief state hand-rolled.

---

## Licensing and packaging

| Component | Package | License | Install |
|---|---|---|---|
| Client SDK | `honcho-ai` 2.2.0 | Apache-2.0 | ✅ pip |
| Server + deriver | not on PyPI | **AGPL-3.0** | Docker, or uv from source |

The brief said "install via pip only." Not achievable for the server — it is not
published to PyPI in any form. The *intent* behind the constraint is preserved:

**No Honcho server source was copied into this repo.** The checkout lives in the session
scratchpad, outside the repo tree, and should stay external if we adopt it:

- AKSUMAEL talks to Honcho **over HTTP** via the Apache-2.0 SDK — an arm's-length
  boundary AGPL does not reach across.
- Running an unmodified AGPL server as a separate local process for our own use triggers
  no distribution obligation.
- If adopted: pin the upstream commit, keep it in its own directory with its own venv,
  never a subtree or submodule of this repo.

Choosing the direct-SDK architecture keeps this boundary clean and obvious. Routing
through Hermes would add an MIT dependency without changing the AGPL analysis at all.

---

## Recommendation

Viable, and the inner-monologue output is genuinely good for a 4B local model. Before
adopting, decide on:

1. **Derivation cadence.** The single biggest cost is 2–7 s of exclusive GPU time per
   derivation, contending with the vision loop. Set batching to flush on idle ticks
   rather than the spike's eager `FLUSH_ENABLED = true`.
2. **Embedding dimension.** 384 is a one-way door once data lands.
3. **Peer discipline.** One peer for the bot, at most one narrator peer. A peer per
   entity turns Honcho into the entity tracker we said we didn't want.
4. **Boot-sequence complexity.** Three services + Postgres, with a silent failure mode if
   the embedding server lags the deriver at startup.

## Teardown

```bash
kill <embed_server_pid> <uvicorn_pid> <deriver_pid>
sudo -u postgres psql -c "DROP DATABASE honcho;"
sudo systemctl stop postgresql
sudo apt-get remove --purge postgresql postgresql-16-pgvector   # optional
```

Postgres, pgvector, `uv`, `honcho-ai`, `fastapi`, and `uvicorn` were installed during this
spike and are still present. Nothing in the AKSUMAEL source tree was modified.

## Sources

- [Honcho server (plastic-labs/honcho)](https://github.com/plastic-labs/honcho) — AGPL-3.0
- [Honcho self-hosting docs](https://honcho.dev/docs/v3/contributing/self-hosting)
- [Honcho × Hermes integration guide](https://honcho.dev/docs/v3/guides/integrations/hermes)
- [Hermes Agent (NousResearch)](https://github.com/NousResearch/hermes-agent) — MIT
- [Hermes Honcho memory plugin](https://github.com/NousResearch/hermes-agent/blob/main/plugins/memory/honcho/README.md)
- [Hermes memory providers](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory-providers)
