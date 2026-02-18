"""Model registry – versioning and persistence for ML models."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

from app.config import settings


class ModelRegistry:
    """Tracks model versions, paths, and metadata."""

    def __init__(self, storage_path: str | None = None) -> None:
        self.storage_path = Path(storage_path or settings.models_storage_path)
        self.storage_path.mkdir(parents=True, exist_ok=True)
        self._registry_file = self.storage_path / "registry.json"
        self._registry = self._load_registry()

    def _load_registry(self) -> dict:
        if self._registry_file.exists():
            try:
                return json.loads(self._registry_file.read_text())
            except json.JSONDecodeError:
                return {"models": {}}
        return {"models": {}}

    def _save_registry(self) -> None:
        self._registry_file.write_text(json.dumps(self._registry, indent=2, default=str))

    def register(
        self,
        model_name: str,
        version: str,
        path: str,
        metrics: dict | None = None,
    ) -> None:
        """Register a new model version."""
        if model_name not in self._registry["models"]:
            self._registry["models"][model_name] = []

        entry = {
            "version": version,
            "path": path,
            "metrics": metrics or {},
            "registered_at": datetime.now(timezone.utc).isoformat(),
        }
        self._registry["models"][model_name].append(entry)
        self._save_registry()
        logger.info(f"Registered model {model_name} v{version}")

    def get_latest_version(self, model_name: str) -> dict | None:
        """Get the most recently registered version of a model."""
        versions = self._registry["models"].get(model_name, [])
        if not versions:
            return None
        return versions[-1]

    def get_version(self, model_name: str, version: str) -> dict | None:
        """Get a specific version of a model."""
        versions = self._registry["models"].get(model_name, [])
        for v in versions:
            if v["version"] == version:
                return v
        return None

    def list_versions(self, model_name: str) -> list[dict]:
        """List all versions of a model."""
        return self._registry["models"].get(model_name, [])

    def generate_version(self) -> str:
        """Generate a timestamp-based version string."""
        return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
