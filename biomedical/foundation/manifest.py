"""Run manifest (component 00 R8).

Every published figure must trace to a manifest. This resolves the class of
confusion v1 produced: a README table whose numbers came from three runs with
different designs, impossible to attribute after the fact.
"""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path


def _git_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        return out.stdout.strip() or "not-a-git-repo"
    except Exception:  # noqa: BLE001
        return "unknown"


@dataclass(frozen=True)
class RunManifest:
    run_id: str
    design_hash: str
    source_versions: dict[str, str]
    slice_params: dict[str, str]
    seeds: dict[str, int]
    trust_policy_version: str
    code_commit: str
    python_version: str
    created_at: str
    notes: dict[str, str] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        design: dict,
        source_versions: dict[str, str],
        slice_params: dict[str, str] | None = None,
        seeds: dict[str, int] | None = None,
        trust_policy_version: str = "1.0",
        notes: dict[str, str] | None = None,
    ) -> RunManifest:
        return cls(
            run_id=f"run_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:6]}",
            design_hash=design_hash(design),
            source_versions=dict(source_versions),
            slice_params=dict(slice_params or {}),
            seeds=dict(seeds or {}),
            trust_policy_version=trust_policy_version,
            code_commit=_git_commit(),
            python_version=platform.python_version(),
            created_at=datetime.now(timezone.utc).isoformat(),
            notes=dict(notes or {}),
        )

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)

    def write(self, directory: Path) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{self.run_id}.json"
        path.write_text(self.to_json())
        return path


def design_hash(design: dict) -> str:
    """Stable hash of an evaluation or ingest design.

    Two runs with the same design hash are comparable. Two runs with different
    hashes are not, and pooling them is the error that produced v1's
    irreconcilable README table.
    """
    blob = json.dumps(design, sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()[:16]
