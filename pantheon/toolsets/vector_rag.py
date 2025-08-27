from ..toolset import ToolSet, tool
from ..utils.log import logger
from ..rag.vectordb import VectorDB


class VectorRAGToolSet(ToolSet):
    """Vector RAG toolset. Allow agent to query and modify(optional) the vector database.

    Args:
        name: The name of the toolset.
        db_path: The path to the vector database.
        worker_params: The parameters for the worker.
        allow_insert: Whether to allow inserting documents into the vector database.
        allow_delete: Whether to allow deleting documents from the vector database.
        db_params: The parameters for the vector database.
        **kwargs: Additional keyword arguments.
    """

    def __init__(
            self,
            name: str,
            db_path: str,
            worker_params: dict | None = None,
            allow_insert: bool = False,
            allow_delete: bool = False,
            db_params: dict | None = None,
            **kwargs,
            ):
        super().__init__(name, worker_params, **kwargs)
        self.db = VectorDB(db_path, **(db_params or {}))
        if allow_insert:
            self.insert_vector_db = tool(self.insert_vector_db)
        if allow_delete:
            self.delete_vector_db = tool(self.delete_vector_db)
        self.inject_description()

    def inject_description(self):
        _doc = (
            f"\n\nDatabase description: {self.db.metadata['description']}"
        )
        self.query_vector_db.__func__.__doc__ += _doc
        self.insert_vector_db.__func__.__doc__ += _doc
        self.delete_vector_db.__func__.__doc__ += _doc

    @tool
    async def query_vector_db(self, query: str, top_k: int = 3, source: str | None = None) -> list:
        """Query the vector database. Before querying,
        you can use the get_vector_db_info tool to get the information of the vector database.
        
        Args:
            query: The query string.
            top_k: The number of results to return.
            source: The source of the query. If provided, only results from the specified source will be returned.
        """
        logger.info(f"[cyan]🔍 Querying vector database: {query[:50]}{'...' if len(query) > 50 else ''}[/cyan]")
        if source:
            logger.info(f"[dim]Filtering by source: {source}[/dim]")
        
        results = await self.db.query(query, top_k, source)
        
        if results:
            logger.info(f"[green]✅ Found {len(results)} relevant documents[/green]")
        else:
            logger.info("[yellow]⚠️ No matching documents found[/yellow]")
        
        return results

    @tool
    async def get_vector_db_info(self) -> dict:
        """Get the information of the vector database."""
        logger.info("[cyan]📊 Retrieving vector database information[/cyan]")
        metadata = self.db.metadata
        
        if metadata:
            logger.info(f"[green]✅ Database info retrieved: {metadata.get('description', 'No description')}[/green]")
        else:
            logger.info("[yellow]⚠️ No database metadata available[/yellow]")
        
        return metadata

    async def insert_vector_db(self, text: str, metadata: dict | None = None):
        """Insert a text into the vector database."""
        logger.info(f"[cyan]📝 Inserting document: {text[:50]}{'...' if len(text) > 50 else ''}[/cyan]")
        
        try:
            await self.db.insert(text, metadata)
            logger.info("[green]✅ Document inserted successfully[/green]")
        except Exception as e:
            logger.info(f"[red]❌ Insert failed: {str(e)}[/red]")
            raise

    async def delete_vector_db(self, id: str | list[str]):
        """Delete a text from the vector database."""
        id_display = id if isinstance(id, str) else f"{len(id)} documents"
        logger.info(f"[cyan]🗑️ Deleting: {id_display}[/cyan]")
        
        try:
            await self.db.delete(id)
            logger.info("[green]✅ Documents deleted successfully[/green]")
        except Exception as e:
            logger.info(f"[red]❌ Delete failed: {str(e)}[/red]")
            raise


__all__ = ["VectorRAGToolSet"]
