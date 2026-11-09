from diskcache_community import Cache, Disk

from pantheon.toolsets.rag import vectordb
from pantheon.toolsets.rag.vectordb import VectorDB

planted_calls = []


def planted():
    planted_calls.append(True)
    return [[0.0, 0.0]]


class Planted:
    def __reduce__(self):
        return (planted, ())


async def test_planted_embedding_is_a_cache_miss(tmp_path, monkeypatch):
    (tmp_path / "metadata.yaml").write_text("parameters: {}\n")
    db = VectorDB(tmp_path)
    with Cache(db.embedding_cache.directory, disk=Disk) as other_writer:
        other_writer.set(["PBMC 3k"], Planted())

    async def fake_embedding(texts, model):
        return [[0.5, 0.25]]

    monkeypatch.setattr(vectordb, "openai_embedding", fake_embedding)
    assert await db.get_embedding(["PBMC 3k"]) == [[0.5, 0.25]]
    assert planted_calls == []
    db.embedding_cache.close()
