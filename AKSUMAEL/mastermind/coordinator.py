#!/usr/bin/env python3
# ╔══════════════════════════════════════════════════════╗
# ║  Mastermind Coordinator — hive orchestrator            ║
# ║  Standalone process. Talks to every AKSUMAEL instance  ║
# ║  over MQTT, keeps a live agent registry, assigns       ║
# ║  goals, and aggregates world-model knowledge.          ║
# ╚══════════════════════════════════════════════════════╝
#
# Run directly: python3 mastermind/coordinator.py
# (or via tools/start_mastermind.sh, which also brings up mosquitto)
#
# Topic layout:
#   aksumael/{agent_id}/status       agent -> coordinator   {env, status, current_goal, ts}
#   aksumael/{agent_id}/goal         coordinator -> agent    {goal, reason, ts}
#   aksumael/{agent_id}/observation  agent -> coordinator   {world_model snapshot, ts}
#   aksumael/broadcast               coordinator -> all      {type, payload, ts}

import argparse
import json
import threading
import time

try:
    import paho.mqtt.client as mqtt
except ImportError:
    mqtt = None

from mastermind.world_model import MasterWorldModel
from mastermind.drone_registry import default_type_for_env, agents_for_goal

STATUS_TOPIC      = "aksumael/{agent_id}/status"
GOAL_TOPIC         = "aksumael/{agent_id}/goal"
OBSERVATION_TOPIC = "aksumael/{agent_id}/observation"
BROADCAST_TOPIC    = "aksumael/broadcast"
STATUS_WILDCARD    = "aksumael/+/status"
OBSERVATION_WILDCARD = "aksumael/+/observation"

AGENT_TIMEOUT_SEC = 60   # mark an agent 'offline' if no status seen in this long

# Goal types the coordinator can hand out when an agent is idle, keyed by
# environment. Real assignment logic can get arbitrarily smart later; this
# is deliberately simple so the transport layer can be exercised end to end.
DEFAULT_GOAL_POOL = {
    "minecraft": ["mine_ore", "explore", "gather"],
    "fallout76": ["explore", "gather"],
    "driving":   ["patrol", "follow_waypoint"],
    "robocar":   ["patrol", "scout_area"],
}


class Coordinator:
    def __init__(self, host: str = "127.0.0.1", port: int = 1883, client_id: str = "mastermind-coordinator"):
        if mqtt is None:
            raise RuntimeError(
                "paho-mqtt is not installed — run: pip install paho-mqtt "
                "(or `venv/bin/python3 -m pip install paho-mqtt`)"
            )
        self.host = host
        self.port = port
        self._lock = threading.Lock()

        # agent_id -> {env, status, current_goal, last_seen}
        self.registry: dict = {}
        self.world = MasterWorldModel()

        self.client = mqtt.Client(client_id=client_id, callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message

    # ── MQTT lifecycle ─────────────────────────────────────────
    def connect(self):
        print(f"[MASTERMIND] connecting to MQTT broker at {self.host}:{self.port} ...")
        self.client.connect(self.host, self.port, keepalive=30)

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        print(f"[MASTERMIND] connected (reason={reason_code})")
        client.subscribe(STATUS_WILDCARD)
        client.subscribe(OBSERVATION_WILDCARD)
        client.subscribe(BROADCAST_TOPIC)
        print(f"[MASTERMIND] subscribed: {STATUS_WILDCARD}, {OBSERVATION_WILDCARD}, {BROADCAST_TOPIC}")

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            print(f"[MASTERMIND] bad payload on {msg.topic}: {e}")
            return

        parts = msg.topic.split("/")
        if len(parts) != 3 or parts[0] != "aksumael":
            return
        agent_id, kind = parts[1], parts[2]

        if kind == "status":
            self._handle_status(agent_id, payload)
        elif kind == "observation":
            self._handle_observation(agent_id, payload)

    # ── Inbound handlers ───────────────────────────────────────
    def _handle_status(self, agent_id: str, payload: dict):
        with self._lock:
            entry = self.registry.setdefault(agent_id, {})
            entry["env"]          = payload.get("env", entry.get("env"))
            entry["status"]       = payload.get("status", "unknown")
            entry["current_goal"] = payload.get("current_goal")
            entry["last_seen"]    = time.time()
        print(f"[MASTERMIND] status <- {agent_id}: env={entry.get('env')} "
              f"status={entry.get('status')} goal={entry.get('current_goal')}")

    def _handle_observation(self, agent_id: str, payload: dict):
        env_name = payload.get("env")
        snapshot = payload.get("world_model", {})
        if not env_name:
            print(f"[MASTERMIND] observation from {agent_id} missing 'env', dropped")
            return
        self.world.merge_agent_update(agent_id, env_name, snapshot)
        print(f"[MASTERMIND] observation <- {agent_id} ({env_name}), "
              f"{len(snapshot.get('chunks', {}))} chunks merged")

    # ── Outbound: goal assignment ──────────────────────────────
    def assign_goal(self, agent_id: str, goal: str, reason: str = "coordinator assignment"):
        topic = GOAL_TOPIC.format(agent_id=agent_id)
        payload = json.dumps({"goal": goal, "reason": reason, "ts": time.time()})
        self.client.publish(topic, payload, qos=1)
        print(f"[MASTERMIND] goal -> {agent_id}: {goal} ({reason})")

    def broadcast(self, msg_type: str, payload: dict):
        body = json.dumps({"type": msg_type, "payload": payload, "ts": time.time()})
        self.client.publish(BROADCAST_TOPIC, body, qos=1)
        print(f"[MASTERMIND] broadcast: {msg_type}")

    def _pick_goal_for(self, env_name: str) -> str:
        pool = DEFAULT_GOAL_POOL.get(env_name, ["explore"])
        # naive round-robin-by-load would go here; for now just the first
        # goal type whose preferred agent-type list is non-empty.
        for g in pool:
            if agents_for_goal(g) or True:
                return g
        return "explore"

    def rebalance(self):
        """Assign a goal to every agent currently sitting idle/explore.
        Deliberately simple: this is the seam future load-aware logic hangs
        off of, not the logic itself."""
        with self._lock:
            snapshot = dict(self.registry)
        now = time.time()
        for agent_id, info in snapshot.items():
            if now - info.get("last_seen", 0) > AGENT_TIMEOUT_SEC:
                continue  # stale — don't hand goals to an agent that's gone dark
            if info.get("current_goal") in (None, "explore", "idle"):
                env_name = info.get("env") or "minecraft"
                goal = self._pick_goal_for(env_name)
                self.assign_goal(agent_id, goal, reason="idle rebalance")

    # ── Registry introspection ─────────────────────────────────
    def active_agents(self) -> dict:
        now = time.time()
        with self._lock:
            return {
                aid: {**info, "online": (now - info.get("last_seen", 0)) <= AGENT_TIMEOUT_SEC}
                for aid, info in self.registry.items()
            }

    # ── Main loop ──────────────────────────────────────────────
    def run_forever(self, rebalance_interval_sec: int = 30):
        self.connect()
        self.client.loop_start()
        try:
            while True:
                time.sleep(rebalance_interval_sec)
                self.rebalance()
                agents = self.active_agents()
                online = sum(1 for a in agents.values() if a["online"])
                print(f"[MASTERMIND] tick — {online}/{len(agents)} agents online")
        except KeyboardInterrupt:
            print("\n[MASTERMIND] shutting down...")
        finally:
            self.client.loop_stop()
            self.client.disconnect()


def main():
    parser = argparse.ArgumentParser(description="AKSUMAEL Mastermind hive coordinator")
    parser.add_argument("--host", default="127.0.0.1", help="MQTT broker host")
    parser.add_argument("--port", type=int, default=1883, help="MQTT broker port")
    parser.add_argument("--rebalance-interval", type=int, default=30,
                         help="seconds between idle-agent goal rebalancing passes")
    args = parser.parse_args()

    coordinator = Coordinator(host=args.host, port=args.port)
    coordinator.run_forever(rebalance_interval_sec=args.rebalance_interval)


if __name__ == "__main__":
    main()
