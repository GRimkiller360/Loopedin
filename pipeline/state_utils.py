"""Shared helpers for reading/writing the routine's JSON state files."""
import json
from pathlib import Path


def load_json(path, default):
    path = Path(path)
    if not path.exists():
        return default
    return json.loads(path.read_text())


def save_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


def is_paused(health_path):
    health = load_json(health_path, {"paused": False})
    return health.get("paused", False), health.get("pause_reason")


def record_success(health_path):
    health = load_json(health_path, {})
    health.update({"paused": False, "pause_reason": None, "consecutive_failures": 0, "last_run_status": "ok"})
    save_json(health_path, health)


def record_failure(health_path, reason, threshold):
    health = load_json(health_path, {"consecutive_failures": 0})
    failures = health.get("consecutive_failures", 0) + 1
    health["consecutive_failures"] = failures
    health["last_run_status"] = "error"
    health["last_error"] = reason
    if failures >= threshold:
        health["paused"] = True
        health["pause_reason"] = f"{failures} consecutive failures: {reason}"
    save_json(health_path, health)
    return health.get("paused", False)


def record_used_topic(used_topics_path, topic, video_id):
    used = load_json(used_topics_path, {"topics": []})
    used["topics"].append({"topic": topic, "video_id": video_id})
    used["topics"] = used["topics"][-500:]
    save_json(used_topics_path, used)
