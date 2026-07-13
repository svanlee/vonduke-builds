#!/usr/bin/env python3
# ╔══════════════════════════════════════════════════════╗
# ║  Mastermind Agent Client — runs inside one AKSUMAEL    ║
# ║  instance, connects it to the hive.                    ║
# ╚══════════════════════════════════════════════════════╝
#
# Opt-in only: an AKSUMAEL instance joins the hive by setting
#   MASTERMIND_ENABLED = True
#   MASTERMIND_HOST    = "<coordinator ip>"
# in config.py. core/runtime.py constructs an AgentClient when enabled and
# calls .tick(...) once per loop iteration; everything here is a no-op
# (and safe to import) when disabled or when paho-mqtt isn't installed.
#
# Goal delivery: goals received from the coordinator are NOT pushed
# directly into the live GoalStack from the MQTT thread (GoalStack isn't
# thread-safe and runtime.py owns it). Instead they're appended to
# data/injected_goals.json — a small queue file — and memory.goals.GoalStack
# .check_injected_goals() (called once per runtime tick, same as
# check_retirement) drains it on the main thread.

import json
import os
import socket
import time
import uuid

try:
    import paho.mqtt.client as mqtt
except ImportError:
    mqtt = None

STATUS_TOPIC      = "aksumael/{agent_id}/status"
GOAL_TOPIC         = "aksumael/{agent_id}/goal"
OBSERVATION_TOPIC = "aksumael/{agent_id}/observation"
BROADCAST_TOPIC    = "aksumael/broadcast"

INJECTED_GOALS_PATH = "data/injected_goals.json"

STATUS_EVERY_N_TICKS      = 20   # ~5s at LOOP_INTERVAL_SEC=0.25
OBSERVATION_EVERY_N_TICKS = 200  # ~50s — world model snapshots are heavier


def _default_agent_id() -> str:
    return f"{socket.gethostname()}-{uuid.uuid4().hex[:6]}"


class AgentClient:
    """Hive-facing sidecar for a single AKSUMAEL instance. Construct once in
    core/runtime.py when config.MASTERMIND_ENABLED, then call .tick() once
    per loop iteration with the current env name, goal stack, and world
    model. Every method degrades to a harmless no-op if MQTT isn't
    available or the client hasn't connected yet, so a hive outage never
    takes down the local agent."""

    def __init__(self, host: str, port: int = 1883, agent_id: str = None,
                 env_name: str = "minecraft"):
        self.agent_id = agent_id or _default_agent_id()
        self.env_name = env_name
        self.host = host
        self.port = port
        self.connected = False
        self.client = None

        if mqtt is None:
            print("[MASTERMIND] paho-mqtt not installed — hive mode disabled for this run "
                  "(pip install paho-mqtt to enable)")
            return

        self.client = mqtt.Client(client_id=f"aksumael-{self.agent_id}",
                                   callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message

        try:
            self.client.connect(self.host, self.port, keepalive=30)
            self.client.loop_start()
        except Exception as e:
            print(f"[MASTERMIND] could not connect to {self.host}:{self.port}: {e} "
                  "— continuing without hive coordination")
            self.client = None

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        self.connected = True
        goal_topic = GOAL_TOPIC.format(agent_id=self.agent_id)
        client.subscribe(goal_topic)
        client.subscribe(BROADCAST_TOPIC)
        print(f"[MASTERMIND] agent '{self.agent_id}' connected to hive at "
              f"{self.host}:{self.port}, subscribed {goal_topic}")

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return
        parts = msg.topic.split("/")
        if len(parts) == 3 and parts[2] == "goal":
            self._enqueue_injected_goal(payload)
        elif msg.topic == BROADCAST_TOPIC:
            print(f"[MASTERMIND] broadcast received: {payload.get('type')}")

    def _enqueue_injected_goal(self, payload: dict):
        goal = payload.get("goal")
        if not goal:
            return
        try:
            os.makedirs(os.path.dirname(INJECTED_GOALS_PATH) or ".", exist_ok=True)
            queue = []
            if os.path.exists(INJECTED_GOALS_PATH):
                with open(INJECTED_GOALS_PATH) as f:
                    queue = json.load(f).get("queue", [])
            queue.append({
                "goal": goal,
                "reason": payload.get("reason", "mastermind"),
                "received_at": time.time(),
            })
            with open(INJECTED_GOALS_PATH, "w") as f:
                json.dump({"queue": queue}, f)
            print(f"[MASTERMIND] queued injected goal '{goal}' for next tick")
        except Exception as e:
            print(f"[MASTERMIND] failed to enqueue injected goal: {e}")

    # ── Outbound ────────────────────────────────────────────────
    def _publish(self, topic: str, payload: dict):
        if not self.client or not self.connected:
            return
        try:
            self.client.publish(topic, json.dumps(payload), qos=0)
        except Exception as e:
            print(f"[MASTERMIND] publish to {topic} failed: {e}")

    def publish_status(self, status: str, current_goal: str = None):
        self._publish(STATUS_TOPIC.format(agent_id=self.agent_id), {
            "env": self.env_name,
            "status": status,
            "current_goal": current_goal,
            "ts": time.time(),
        })

    def publish_observation(self, world_model_snapshot: dict):
        self._publish(OBSERVATION_TOPIC.format(agent_id=self.agent_id), {
            "env": self.env_name,
            "world_model": world_model_snapshot,
            "ts": time.time(),
        })

    def tick(self, tick_num: int, status: str, current_goal: str, world_model=None):
        """Call once per runtime loop iteration. Cheap no-op on ticks that
        aren't due for a publish."""
        if self.client is None:
            return
        if tick_num % STATUS_EVERY_N_TICKS == 0:
            self.publish_status(status, current_goal)
        if world_model is not None and tick_num % OBSERVATION_EVERY_N_TICKS == 0:
            snapshot = {"chunks": getattr(world_model, "chunks", {})}
            self.publish_observation(snapshot)

    def shutdown(self):
        if self.client is not None:
            self.publish_status("offline")
            self.client.loop_stop()
            self.client.disconnect()
