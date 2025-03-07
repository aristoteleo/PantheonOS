import json
import hashlib
from pathlib import Path

import yaml
import lancedb
import pyarrow as pa  

from .wrap import RAGWrapper
from ..utils.text import smart_text_splitter
from ..utils.llm import openai_embedding


class VectorDB(RAGWrapper):
    def __init__(
            self,
            db_path: str | Path,
            embedding_model: str = "text-embedding-3-large",
            chunk_size: int = 4000,
            chunk_overlap: int = 200,
        ):
        self.db_path = Path(db_path)
        metadata_path = self.db_path / "metadata.yaml"
        if not metadata_path.exists():
            raise FileNotFoundError(f"Metadata file not found: {metadata_path}")
        with open(metadata_path, "r", encoding="utf-8") as f:
            self.metadata = yaml.safe_load(f)
        self.embedding_model = embedding_model
        self.store_path = self.db_path / "db"
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self._table = None

    async def get_table(self):
        if self._table is not None:
            return self._table
        self.db = lancedb.connect(str(self.store_path))
        _v = await openai_embedding("test")
        vector_dim = len(_v[0])
        schema = pa.schema([  
            pa.field('id', pa.string()),  
            pa.field('text', pa.string()),
            pa.field('vector', pa.list_(pa.float32(), vector_dim)),  
            pa.field('metadata', pa.string()),  
        ])  
        self._table = self.db.create_table(
            name="default",
            exist_ok=True,
            schema=schema,
        )
        return self._table

    def split_text(self, text: str) -> list[str]:
        return smart_text_splitter(text, self.chunk_size, self.chunk_overlap)

    async def insert(self, text: str | list[str], metadata: dict | list[dict] | None = None):
        if isinstance(text, str):
            text = [text]
        if isinstance(metadata, dict):
            metadata = [metadata for _ in range(len(text))]
        elif metadata is None:
            metadata = [{} for _ in range(len(text))]
        # sha256 hash of text
        ids = [hashlib.sha256(t.encode("utf-8")).hexdigest() for t in text]
        embeddings = await openai_embedding(text)
        table = await self.get_table()
        table.add([
            {
                "id": ids[i],
                "text": text[i],
                "vector": embeddings[i],
                "metadata": json.dumps(metadata[i]),
            }
            for i in range(len(text))
        ])

    async def insert_from_file(self, file_path: str, metadata: dict | None = None):
        metadata = metadata or {}
        metadata["source_file"] = str(file_path)
        with open(file_path, "r", encoding="utf-8") as f:
            text = f.read()
        splited_texts = self.split_text(text)
        await self.insert(splited_texts, metadata)

    async def query(self, query: str, top_k: int = 3) -> list:
        vector = (await openai_embedding([query]))[0]
        table = await self.get_table()
        resp = table.search(vector).limit(top_k).to_list()
        return [
            {
                "id": r["id"],
                "text": r["text"],
                "metadata": json.loads(r["metadata"]),
            }
            for r in resp
        ]

    async def delete(self, id: str | list[str]):
        if isinstance(id, str):
            id = [id]
        table = await self.get_table()
        for i in id:
            table.delete(f"id = '{i}'")
