Big day on the Aksūmal project.

We've been building AKSUMAEL — a BDI + HTN cognitive agent that plays Minecraft autonomously, training itself in real-time via a YOLOv8 vision pipeline on a 6GB laptop GPU.

**Vision model is live.** mesh-llm 0.73.0 had a CLIP/CUDA bug (SIGSEGV on any multimodal call) that nobody upstream had fixed. We bypassed it by building llama.cpp directly from source with CUDA 12.6 — installed user-locally with no root access. Qwen3.5-4B-Vision is now running, serving multimodal completions on the same GPU as our game-playing agent. 6GB total. No VRAM left on the table.

**Autotrain loop is self-healing.** The agent was silently deadlocking — it called `systemctl stop aksumael` from inside its own process, systemd nuked the cgroup, and nothing ever restarted. Fixed with `start_new_session=True` subprocess isolation so the training script detaches before the parent dies. The agent now stops itself, retrains, and relaunches without any human intervention.

**Multi-environment HAL is committed.** AKSUMAEL now has a Hardware Abstraction Layer with adapters for Minecraft, Fallout 76, a driving sim, and the AK-01 robocar. Same planner, same skill system, four environments. That's the architecture we've been working toward.

The self-improvement loop is now complete: play → observe → label → train → redeploy → repeat.

Next up: wire vision into live gameplay decisions and start the wood-gathering benchmark.

#robotics #AI #computervision #selfimproving #minecraft #autonomousagents #llm #aksumael
