from .rag import VectorRAGToolSet
from ...remote.toolset import toolset_cli


toolset_cli(VectorRAGToolSet, "vector_rag")
