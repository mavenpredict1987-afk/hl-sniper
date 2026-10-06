"""Durable state for the HL sniper bot.

Paper-trading statistics used to live only in process memory, so every
container restart (redeploy, crash, platform maintenance) reset the
dashboard to zero. This module writes one JSON snapshot atomically and
restores it on startup.

Resolution order for load():
  1. <STATE_DIR>/state.json  - live snapshot, rewritten every STATE_SAVE_SEC
  2. <repo>/state_seed.json  - shipped bootstrap, used on the very first run
  3. no file                 - fresh start from INITIAL_CAPITAL

Set STATE_DIR to a mounted volume (for example /data) so the snapshot
survives a container rebuild, not just a process restart.
"""

import json
import os
import time

import config

SCHEMA_VERSION = 1
STATE_FILENAME = "state.json"
SEED_FILENAME = "state_seed.json"


def repo_dir():
    return os.path.dirname(os.path.abspath(__file__))


def state_dir():
    raw = (config.STATE_DIR or "").strip()
    if not raw:
        return os.path.join(repo_dir(), "state")
    return raw


def state_path():
    return os.path.join(state_dir(), STATE_FILENAME)


def seed_path():
    return os.path.join(repo_dir(), SEED_FILENAME)


def _atomic_write(path, text):
    folder = os.path.dirname(path) or "."
    os.makedirs(folder, exist_ok=True)
    tmp = "%s.tmp.%d" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def save(data, path=None):
    """Write a snapshot atomically. Returns the path written."""
    target = path or state_path()
    payload = dict(data)
    payload["schema"] = SCHEMA_VERSION
    payload["saved_at"] = time.time()
    _atomic_write(target, json.dumps(payload, ensure_ascii=True,
                                     separators=(",", ":")))
    return target


def _read(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if data.get("schema") != SCHEMA_VERSION:
        return None
    return data


def load(path=None):
    """Return (snapshot, source_path), or (None, None) when nothing usable."""
    if path:
        data = _read(path)
        return (data, path) if data else (None, None)
    for candidate in (state_path(), seed_path()):
        data = _read(candidate)
        if data:
            return data, candidate
    return None, None


def clear(path=None):
    """Delete the live snapshot. Missing file is not an error."""
    try:
        os.remove(path or state_path())
        return True
    except OSError:
        return False
