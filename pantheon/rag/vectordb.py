import json
import hashlib
from pathlib import Path

import yaml

import diskcache


from .wrap import RAGWrapper
from .text import smart_text_splitter
from ..utils.llm import openai_embedding
from ..utils.log import logger


class VectorDB(RAGWrapper):
    """
    Vector database for RAG.

    Args:
        db_path: The path to the database.

    Attributes:
        db_path: The path to the database.
        metadata: The metadata of the database.
        embedding_model: The embedding model to use.
        chunk_size: The chunk size to use.
        chunk_overlap: The chunk overlap to use.
        embedding_cache: The cache for the embeddings.
        store_path: The path to the database.
    """

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        metadata_path = self.db_path / "metadata.yaml"
        if not metadata_path.exists():
            raise FileNotFoundError(f"Metadata file not found: {metadata_path}")
        with open(metadata_path, "r", encoding="utf-8") as f:
            self.metadata = yaml.safe_load(f)
        parameters = self.metadata.get("parameters", {})
        self.embedding_model = parameters.get("embedding_model", "text-embedding-3-large")
        self.chunk_size = parameters.get("chunk_size", 4000)
        self.chunk_overlap = parameters.get("chunk_overlap", 200)
        _key = f"{self.embedding_model}"
        _cache_dir = self.db_path / "embedding_cache" / _key
        _cache_dir.mkdir(parents=True, exist_ok=True)
        self.embedding_cache = diskcache.Cache(str(_cache_dir))
        _key = f"{self.embedding_model}_{self.chunk_size}_{self.chunk_overlap}"
        self.store_path = self.db_path / "db" / _key
        self._table = None
        self._embedding_dim = None

    async def get_embedding(self, text: str):
        """Get the embedding of the text.

        Args:
            text: The text to get the embedding of.

        Returns:
            The embedding of the text.
        """
        cache_key = text
        if cache_key in self.embedding_cache:
            logger.info(f"Embedding cache hit for: {cache_key}")
            return self.embedding_cache[cache_key]
        else:
            logger.info(f"Embedding cache miss for: {cache_key}")
            embedding = await openai_embedding(text, model=self.embedding_model)
            self.embedding_cache[cache_key] = embedding
            logger.info(f"Embedding cache saved.")
            return embedding

    async def get_embedding_dim(self):
        """Get the dimension of the embedding.

        Returns:
            The dimension of the embedding.
        """
        if self._embedding_dim is not None:
            return self._embedding_dim
        p = self.store_path / ".vector_dim.txt"
        if p.exists():
            with open(p, "r", encoding="utf-8") as f:
                self._embedding_dim = int(f.read())
            return self._embedding_dim
        _v = await self.get_embedding("test")
        self._embedding_dim = len(_v[0])
        with open(p, "w", encoding="utf-8") as f:
            f.write(str(self._embedding_dim))
        return self._embedding_dim

    async def get_table(self):
        """Get the table of the vector database.

        Returns:
            The table of the vector database.
        """
        import lancedb
        import pyarrow as pa  
        if self._table is not None:
            return self._table
        self.db = lancedb.connect(str(self.store_path))
        vector_dim = await self.get_embedding_dim()
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
        """Split the text into chunks.

        Args:
            text: The text to split.

        Returns:
            The chunks of the text.
        """
        return smart_text_splitter(text, self.chunk_size, self.chunk_overlap)

    async def insert(self, text: str | list[str], metadata: dict | list[dict] | None = None):
        """Insert the text into the vector database.

        Args:
            text: The text to insert.
            metadata: The metadata of the text.
        """
        if isinstance(text, str):
            text = [text]
        text = [t for t in text if t]  # remove empty strings
        if len(text) == 0:
            logger.warning("No text to insert")
            return
        if isinstance(metadata, dict):
            metadata = [metadata for _ in range(len(text))]
        elif metadata is None:
            metadata = [{} for _ in range(len(text))]
        # sha256 hash of text
        ids = [hashlib.sha256(t.encode("utf-8")).hexdigest() for t in text]
        embeddings = await self.get_embedding(text)
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
        """Insert a file into the vector database.
        The file should be a text(markdown is recommended) file.

        Args:
            file_path: The path to the file.
            metadata: The metadata of the file.
        """
        metadata = metadata or {}
        metadata["source_file"] = str(file_path)
        with open(file_path, "r", encoding="utf-8") as f:
            text = f.read()
        splited_texts = self.split_text(text)
        await self.insert(splited_texts, metadata)

    async def query(self, query: str, top_k: int = 3, source: str | None = None) -> list:
        """Query the vector database.

        Args:
            query: The query to search.
            top_k: The number of results to return.
            source: The source of the query.

        Returns:
            The results of the query, a list of dicts with keys:
                - id: The id of the text.
                - text: The text of the text.
                - metadata: The metadata of the text.
        """
        vector = (await self.get_embedding([query]))[0]
        table = await self.get_table()
        resp = table.search(vector).to_list()
        res = [
            {
                "id": r["id"],
                "text": r["text"],
                "metadata": json.loads(r["metadata"]),
            }
            for r in resp
        ]
        if source is not None:
            res = [r for r in res if r.get("metadata", {}).get("source") == source]
        return res[:top_k]

    async def delete(self, id: str | list[str]):
        """Delete the text from the vector database.

        Args:
            id: The id of the text to delete.
        """
        if isinstance(id, str):
            id = [id]
        table = await self.get_table()
        for i in id:
            table.delete(f"id = '{i}'")
