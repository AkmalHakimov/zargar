import asyncio
from types import SimpleNamespace

from app.llm.openai_provider import OpenAICompatibleProvider
from app.llm.schemas import EntityExtractionResult
from app.llm.validation import validated_json_completion


class InvalidThenValidProvider:
    def __init__(self):
        self.calls = 0

    async def json_completion(self, system: str, user: str) -> dict:
        self.calls += 1
        if self.calls == 1:
            return {"entities": [{"type": "policy"}]}
        return {"entities": [{"name": "Refund Policy", "type": "policy", "summary": "Refund rules."}]}

    async def text_completion(self, system: str, user: str) -> str:
        return "{}"


def test_json_validation_success():
    provider = InvalidThenValidProvider()

    result = asyncio.run(validated_json_completion(provider, EntityExtractionResult, "system", "user"))

    assert provider.calls == 2
    assert result.entities[0].name == "Refund Policy"


class MalformedThenValidOpenAIProvider(OpenAICompatibleProvider):
    def __init__(self):
        super().__init__(
            SimpleNamespace(
                openai_api_key="key",
                openai_base_url="https://example.test/v1",
                openai_model="test-model",
                openai_compatible_base_url=None,
                openai_chat_model=None,
            )
        )
        self.calls = 0

    async def text_completion(self, system: str, user: str) -> str:
        self.calls += 1
        if self.calls == 1:
            return "{bad json"
        return '{"entities":[{"name":"Payment Process","type":"workflow","summary":"Payments."}]}'


def test_invalid_json_repair_fallback():
    provider = MalformedThenValidOpenAIProvider()

    result = asyncio.run(provider.json_completion("system", "user"))

    assert provider.calls == 2
    assert result["entities"][0]["name"] == "Payment Process"
