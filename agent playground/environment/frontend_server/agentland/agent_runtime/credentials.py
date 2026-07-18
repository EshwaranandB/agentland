"""Volatile, per-browser-session credential storage. Never serialize values."""

import os
from threading import RLock
from pathlib import Path


class VolatileCredentialStore:
    def __init__(self):
        self._keys = {}
        self._lock = RLock()

    def set_openai_key(self, session_key, api_key):
        with self._lock:
            self._keys[session_key] = api_key

    def has_openai_key(self, session_key):
        with self._lock:
            return bool(self._keys.get(session_key))

    def get_openai_key(self, session_key):
        with self._lock:
            return self._keys.get(session_key)

    def clear_openai_key(self, session_key):
        with self._lock:
            self._keys.pop(session_key, None)


credential_store = VolatileCredentialStore()


def local_bootstrap_key():
    """Read an ignored local development key without ever returning it in HTTP."""
    environment_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if environment_key:
        return environment_key
    root = Path(__file__).resolve().parents[4]
    config = root / ".env.agentland.local"
    try:
        for line in config.read_text(encoding="utf-8").splitlines():
            name, separator, value = line.partition("=")
            if separator and name.strip() == "OPENAI_API_KEY":
                return value.strip().strip('"').strip("'")
    except OSError:
        pass
    return ""
