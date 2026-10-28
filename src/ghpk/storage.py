"""Atomic JSON configuration and state storage."""
import json
import os
from pathlib import Path
import tempfile

def read_json(path, default):
    return json.loads(path.read_text()) if path.exists() else default


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".ghpk-")
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(data, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


class Store:
    def __init__(self):
        home = Path.home()
        self.config = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config")) / "ghpk" / "repos.json"
        self.cache = Path(os.environ.get("XDG_CACHE_HOME", home / ".cache")) / "ghpk"
        self.cache.mkdir(parents=True, exist_ok=True)
        self.releases = self.cache / "releases.json"
        self.state = self.cache / "installed.json"

    def load(self):
        from .github import normalize_repo
        import re
        config = read_json(self.config, {"repos": []})
        if not isinstance(config, dict) or not isinstance(config.get("repos"), list):
            raise ValueError("Конфигурация должна содержать список repos.")
        seen = set()
        for entry in config["repos"]:
            if not isinstance(entry, dict):
                raise ValueError("Каждая запись repos должна быть объектом.")
            entry["repo"] = normalize_repo(entry.get("repo", ""))
            if entry["repo"] in seen:
                raise ValueError("Повтор репозитория: " + entry["repo"])
            seen.add(entry["repo"])
            if "asset" in entry:
                re.compile(entry["asset"])
            if "enabled" in entry and not isinstance(entry["enabled"], bool):
                raise ValueError("enabled должно быть true или false.")
        return config
