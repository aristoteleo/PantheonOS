from abc import ABC


class RAGWrapper(ABC):
    async def insert(self, text: str | list[str], metadata: dict | list[dict] | None = None):
        pass

    async def query(self, query: str, top_k: int = 10) -> list:
        pass

    async def delete(self, id: str | list[str]):
        pass

