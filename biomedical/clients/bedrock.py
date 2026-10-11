"""Amazon Bedrock client — embeddings and converse.

Records latency and token usage on every call, because a cost or latency claim
needs its own evidence.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass

import boto3
from botocore.config import Config


@dataclass(frozen=True)
class Completion:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    error: str | None = None


# us-west-2 pricing per 1k tokens, as configured for this project. Used only to
# report what a run cost; not a published claim.
PRICE_PER_1K = {
    "us.anthropic.claude-sonnet-4-6": (0.003, 0.015),
    "us.anthropic.claude-haiku-4-5-20251001-v1:0": (0.001, 0.005),
    "us.amazon.nova-2-lite-v1:0": (0.00006, 0.00024),
}


class BedrockClient:
    def __init__(
        self,
        region: str = "us-west-2",
        model_id: str = "us.anthropic.claude-sonnet-4-6",
        embedding_model_id: str = "amazon.titan-embed-text-v2:0",
        embedding_dim: int = 1024,
        *,
        temperature: float = 0.0,
        max_retries: int = 4,
    ):
        cfg = Config(retries={"max_attempts": max_retries, "mode": "adaptive"}, read_timeout=120)
        self._rt = boto3.client("bedrock-runtime", region_name=region, config=cfg)
        self.model_id = model_id
        self.embedding_model_id = embedding_model_id
        self.embedding_dim = embedding_dim
        self.temperature = temperature
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0

    # -- generation ---------------------------------------------------------

    def converse(self, prompt: str, *, max_tokens: int = 256) -> Completion:
        t0 = time.perf_counter()
        try:
            out = self._rt.converse(
                modelId=self.model_id,
                messages=[{"role": "user", "content": [{"text": prompt}]}],
                inferenceConfig={"maxTokens": max_tokens, "temperature": self.temperature},
            )
        except Exception as exc:  # noqa: BLE001
            return Completion(
                text="",
                latency_ms=(time.perf_counter() - t0) * 1000,
                error=f"{type(exc).__name__}: {str(exc)[:160]}",
            )

        usage = out.get("usage", {})
        self.calls += 1
        self.input_tokens += usage.get("inputTokens", 0)
        self.output_tokens += usage.get("outputTokens", 0)
        return Completion(
            text=out["output"]["message"]["content"][0]["text"],
            input_tokens=usage.get("inputTokens", 0),
            output_tokens=usage.get("outputTokens", 0),
            latency_ms=(time.perf_counter() - t0) * 1000,
        )

    # -- embeddings ---------------------------------------------------------

    def embed(self, text: str) -> list[float]:
        resp = self._rt.invoke_model(
            modelId=self.embedding_model_id,
            body=json.dumps({"inputText": text, "dimensions": self.embedding_dim}),
        )
        return json.loads(resp["body"].read())["embedding"]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(t) for t in texts]

    # -- accounting ---------------------------------------------------------

    def cost_usd(self) -> float | None:
        price = PRICE_PER_1K.get(self.model_id)
        if price is None:
            return None
        pin, pout = price
        return (self.input_tokens / 1000) * pin + (self.output_tokens / 1000) * pout

    def usage_summary(self) -> str:
        cost = self.cost_usd()
        c = f"${cost:.4f}" if cost is not None else "unpriced"
        return (
            f"{self.calls} calls · {self.input_tokens} in / {self.output_tokens} out tokens · {c}"
        )
