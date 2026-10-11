"""Secret-safe configuration.

Reads .env, exposes typed settings, and never prints a secret. `python config.py`
emits a readiness report showing only an 8-character fingerprint per secret.

Carried forward from v1, which got this part right.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).parent


class Secret:
    """A string that refuses to appear in logs, reprs or tracebacks."""

    __slots__ = ("_value",)

    def __init__(self, value: str | None):
        self._value = value or ""

    def reveal(self) -> str:
        return self._value

    @property
    def present(self) -> bool:
        return bool(self._value)

    @property
    def fingerprint(self) -> str:
        if not self._value:
            return "-"
        return hashlib.sha256(self._value.encode()).hexdigest()[:8]

    def __bool__(self) -> bool:
        return self.present

    def __repr__(self) -> str:
        return f"Secret(fp={self.fingerprint})"

    __str__ = __repr__


def _load_dotenv(path: Path = ROOT / ".env") -> None:
    """Minimal .env loader. Existing environment variables win."""
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv()


def _env(*names: str, default: str = "") -> str:
    for n in names:
        v = os.environ.get(n)
        if v:
            return v
    return default


@dataclass(frozen=True)
class Neo4jConfig:
    uri: str = field(default_factory=lambda: _env("NEO4J_URI"))
    username: str = field(default_factory=lambda: _env("NEO4J_USERNAME", "NEO4J_USER"))
    password: Secret = field(default_factory=lambda: Secret(_env("NEO4J_PASSWORD")))
    database: str = field(default_factory=lambda: _env("NEO4J_DATABASE", default="neo4j"))

    @property
    def configured(self) -> bool:
        return bool(self.uri and self.username and self.password.present)


@dataclass(frozen=True)
class QdrantConfig:
    url: str = field(default_factory=lambda: _env("QDRANT_URL"))
    api_key: Secret = field(default_factory=lambda: Secret(_env("QDRANT_API_KEY")))
    timeout: int = field(default_factory=lambda: int(_env("QDRANT_TIMEOUT", default="30")))

    @property
    def configured(self) -> bool:
        return bool(self.url and self.api_key.present)


@dataclass(frozen=True)
class EmbeddingConfig:
    """Embeddings are optional. Without them the vector layer runs in
    deterministic-stand-in mode, which is enough for every structural test."""

    provider: str = field(default_factory=lambda: _env("EMBEDDING_PROVIDER", default="none"))
    model_id: str = field(default_factory=lambda: _env("EMBEDDING_MODEL_ID", default="stand-in-hash-v1"))
    dim: int = field(default_factory=lambda: int(_env("EMBEDDING_DIM", default="256")))

    @property
    def configured(self) -> bool:
        return self.provider != "none"


@dataclass(frozen=True)
class TrustConfig:
    """Component 00 trust policy. Versioned, because verdicts record it."""

    policy_version: str = field(default_factory=lambda: _env("TRUST_POLICY_VERSION", default="1.0"))
    confidence_floor: float = field(default_factory=lambda: float(_env("CONFIDENCE_FLOOR", default="0.75")))
    min_tier: str = field(default_factory=lambda: _env("MIN_TIER", default="ingested"))
    contradiction_eligible: bool = field(
        default_factory=lambda: _env("CONTRADICTION_ELIGIBLE", default="false") == "true"
    )


@dataclass(frozen=True)
class Config:
    neo4j: Neo4jConfig = field(default_factory=Neo4jConfig)
    qdrant: QdrantConfig = field(default_factory=QdrantConfig)
    embedding: EmbeddingConfig = field(default_factory=EmbeddingConfig)
    trust: TrustConfig = field(default_factory=TrustConfig)


def load() -> Config:
    return Config()


def _redact_host(url: str) -> str:
    """Keep the provider, hide the instance.

    An Aura hostname and username are not the password, but they identify the
    exact instance to target. This report lands in committed notebook outputs,
    so the instance id is masked too.
    """
    if not url:
        return "-"
    scheme, _, rest = url.partition("://")
    host = rest.split("@")[-1].split("/")[0]
    parts = host.split(".")
    if len(parts) > 2:
        parts[0] = "<redacted>"
    elif parts:
        parts[0] = "<redacted>"
    return f"{scheme}://{'.'.join(parts)}" if scheme else ".".join(parts)


def report() -> str:
    """Redacted readiness report. Safe to commit — no secret, no instance id."""
    c = load()
    lines = [
        "readiness",
        "=" * 58,
        f"  neo4j      uri={_redact_host(c.neo4j.uri)}",
        f"             user fp={Secret(c.neo4j.username).fingerprint}"
        f"  db fp={Secret(c.neo4j.database).fingerprint}",
        f"             password fp={c.neo4j.password.fingerprint}",
        f"             configured={c.neo4j.configured}",
        f"  qdrant     url={_redact_host(c.qdrant.url)}",
        f"             api_key fp={c.qdrant.api_key.fingerprint}",
        f"             configured={c.qdrant.configured}",
        f"  embedding  provider={c.embedding.provider}  model={c.embedding.model_id}  dim={c.embedding.dim}",
        f"             configured={c.embedding.configured}",
        f"  trust      policy=v{c.trust.policy_version}  floor={c.trust.confidence_floor}"
        f"  min_tier={c.trust.min_tier}",
        "=" * 58,
    ]
    if not c.embedding.configured:
        lines.append("  note: no embedding provider -> vector layer uses a deterministic")
        lines.append("        stand-in. Structural tests pass; semantic quality is not")
        lines.append("        measurable until a real provider is configured.")
    return "\n".join(lines)


if __name__ == "__main__":
    print(report())
