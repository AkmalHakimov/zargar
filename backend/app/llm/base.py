from abc import ABC, abstractmethod


class LLMProvider(ABC):
    @abstractmethod
    async def json_completion(self, system: str, user: str) -> dict:
        raise NotImplementedError

    @abstractmethod
    async def text_completion(self, system: str, user: str) -> str:
        raise NotImplementedError

