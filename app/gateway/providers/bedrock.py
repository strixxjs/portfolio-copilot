"""Amazon Bedrock adapter — interface stub, not implemented.

Mapping plan (Converse API, boto3 `bedrock-runtime`):
- LLMRequest.messages → `messages` [{role, content:[{text}|{toolUse}|{toolResult}]}]; system → `system`.
- LLMRequest.tools → `toolConfig.tools[].toolSpec {name, description, inputSchema.json}`;
  tool_choice auto/required → `toolConfig.toolChoice {auto:{}} | {any:{}}`.
- temperature / max_tokens → `inferenceConfig {temperature, maxTokens}`.
- complete → `converse`; stream → `converse_stream` (contentBlockDelta.text / toolUse.input deltas,
  metadata.usage {inputTokens, outputTokens} → Usage).
- ThrottlingException / ServiceUnavailableException / ModelTimeoutException → ProviderError(retryable=True).
Model ids and prices go into config and pricing.py like every other provider.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from app.gateway.types import LLMRequest, LLMResponse, StreamEvent

_MSG = "Bedrock adapter not implemented; see the module docstring for the mapping"


class BedrockProvider:
    name = "bedrock"

    async def complete(self, req: LLMRequest, model: str) -> LLMResponse:
        raise NotImplementedError(_MSG)

    def stream(self, req: LLMRequest, model: str) -> AsyncIterator[StreamEvent]:
        raise NotImplementedError(_MSG)
