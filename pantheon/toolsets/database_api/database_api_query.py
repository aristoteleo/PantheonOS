"""DatabaseAPIQuery Toolset - LLM-enhanced database queries for Pantheon.

This toolset provides natural language query capabilities for biological databases
using schema-driven API parameter generation. It uses LLM to convert natural
language queries into appropriate API parameters for various biological databases.
"""

from typing import Any, Dict, Optional
from pathlib import Path

from pantheon.toolset import ToolSet, tool
from pantheon.utils.log import logger


class DatabaseAPIQueryToolSet(ToolSet):
    """Query biological databases using natural language with LLM-enhanced parameter generation.

    This toolset supports 26+ biological databases including:
    - Proteins: UniProt, PDB, AlphaFold, InterPro, STRING, EMDB
    - Genomics: Ensembl, ClinVar, dbSNP, GnomAD, GWAS Catalog, UCSC, RegulomeDB
    - Expression: GEO, CCRE, OpenTargets, OpenTargets Genetics, ReMap
    - Pathways: KEGG, Reactome, GtoPdb
    - Specialized: BLAST, JASPAR, MPD, IUCN, PRIDE, cBioPortal, WoRMS, Paleobiology

    Commands:
    - /database_api query <natural_language_query> <database> [--max_results 5]
    - /database_api list_databases
    - /database_api database_info <database_name>
    """

    def __init__(
        self,
        name: str = "database_api",
        workspace_path: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(name, **kwargs)
        self.workspace_path = Path(workspace_path) if workspace_path else Path.cwd()

    @tool(name="query")
    def query(
        self,
        prompt: str,
        database: str,
        max_results: int = 5,
        llm_service_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Query a biological database using natural language.

        This method uses LLM to generate appropriate API parameters from your
        natural language query, then calls the database API and returns formatted
        results.

        Args:
            prompt: Natural language query describing what to find
                Examples:
                - "Find BRCA1 mutations in breast cancer"
                - "Get human insulin protein structure"
                - "Search for p53 tumor suppressor interactions"
            database: Target database name (e.g., 'uniprot', 'ensembl', 'clinvar')
                Use list_databases to see all available options
            max_results: Maximum number of results to return (default: 5)
            llm_service_id: Optional LLM service ID for parameter generation
                If not provided, uses PANTHEON_AGENT_SERVICE_ID environment variable

        Returns:
            Dict containing:
            - success: Boolean indicating if query succeeded
            - database: Database name
            - prompt: Original query
            - api_parameters: Generated API parameters (for transparency)
            - raw_count: Total number of matching results
            - results: Formatted results as human-readable text
            - strategy: Always "llm_enhanced"
            - error: Error message if success is False

        Examples:
            >>> toolset = DatabaseAPIQueryToolSet()
            >>> result = toolset.query(
            ...     "Find BRCA1 mutations in breast cancer",
            ...     "clinvar",
            ...     max_results=5
            ... )
            >>> print(result["results"])
        """
        try:
            from .schema_manager import SchemaManager
        except ImportError:
            return {
                "success": False,
                "error": "Schema manager not available",
                "prompt": prompt,
                "database": database,
            }

        # Load schema
        schema_mgr = SchemaManager()
        schema = schema_mgr.load_schema(database)

        if not schema:
            return {
                "success": False,
                "error": f"Schema not found for database: {database}",
                "prompt": prompt,
                "database": database,
                "available_databases": schema_mgr.list_available_databases(),
            }

        # Generate API parameters using LLM
        api_params = self._generate_api_params_with_llm(
            prompt, schema, database, llm_service_id
        )

        if not api_params.get("success"):
            return {
                "success": False,
                "error": api_params.get("error", "Failed to generate API parameters"),
                "prompt": prompt,
                "database": database,
            }

        # Call the API
        result = self._call_database_api(
            schema, api_params.get("parameters", {}), max_results
        )

        if not result.get("success"):
            return {
                "success": False,
                "error": result.get("error", "API call failed"),
                "prompt": prompt,
                "database": database,
                "api_parameters": api_params.get("parameters"),
            }

        # Format results for LLM consumption
        formatted = self._format_results_for_llm(
            result.get("data", {}), max_results, database
        )

        return {
            "success": True,
            "database": database,
            "prompt": prompt,
            "api_parameters": api_params.get("parameters"),
            "raw_count": result.get("total_count", 0),
            "results": formatted,
            "strategy": "llm_enhanced",
        }

    @tool
    def list_databases(self) -> Dict[str, Any]:
        """List all available databases with their categories.

        Returns:
            Dict containing:
            - success: Boolean
            - databases: List of available database names
            - categories: Dict mapping category names to database lists
            - total: Total number of databases
        """
        try:
            from .schema_manager import SchemaManager
        except ImportError:
            return {
                "success": False,
                "error": "Schema manager not available",
            }

        schema_mgr = SchemaManager()
        databases = schema_mgr.list_available_databases()

        # Categorize databases
        categories = {
            "proteins": ["uniprot", "pdb", "alphafold", "interpro", "string", "emdb"],
            "genomics": [
                "ensembl",
                "clinvar",
                "dbsnp",
                "gnomad",
                "gwas_catalog",
                "ucsc",
                "regulomedb",
            ],
            "expression": [
                "geo",
                "ccre",
                "opentargets",
                "opentargets_genetics",
                "remap",
            ],
            "pathways": ["kegg", "reactome", "gtopdb"],
            "specialized": [
                "blast",
                "jaspar",
                "mpd",
                "iucn",
                "pride",
                "cbioportal",
                "worms",
                "paleobiology",
            ],
        }

        return {
            "success": True,
            "databases": sorted(databases),
            "categories": categories,
            "total": len(databases),
        }

    @tool
    def database_info(self, database: str) -> Dict[str, Any]:
        """Get detailed information about a specific database.

        Args:
            database: Database name (e.g., 'uniprot', 'ensembl')

        Returns:
            Dict containing:
            - success: Boolean
            - database: Database name
            - base_url: API base URL
            - categories: Available API categories
            - example_queries: List of example queries
            - is_valid: Whether database schema is valid
        """
        try:
            from .schema_manager import SchemaManager
        except ImportError:
            return {
                "success": False,
                "error": "Schema manager not available",
                "database": database,
            }

        schema_mgr = SchemaManager()
        schema = schema_mgr.load_schema(database)

        if not schema:
            return {
                "success": False,
                "error": f"Schema not found for database: {database}",
                "database": database,
                "available_databases": schema_mgr.list_available_databases(),
            }

        return {
            "success": True,
            "database": database,
            "base_url": schema_mgr.get_base_url(database),
            "categories": list(schema.get("categories", {}).keys()),
            "example_queries": schema_mgr.get_query_examples(database),
            "is_valid": schema_mgr.validate_database(database),
        }

    def _generate_api_params_with_llm(
        self,
        prompt: str,
        schema: Dict[str, Any],
        database: str,
        llm_service_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Use LLM to generate API parameters from natural language prompt."""
        try:
            from ..utils.remote import connect_remote
        except ImportError:
            return {"success": False, "error": "Remote LLM service not available"}

        import os
        import asyncio
        import json

        service_id = llm_service_id or os.getenv("PANTHEON_AGENT_SERVICE_ID")
        if not service_id:
            return {
                "success": False,
                "error": "LLM service ID not configured. Set PANTHEON_AGENT_SERVICE_ID environment variable.",
            }

        # Build system prompt with schema information
        system_prompt = self._build_schema_prompt(schema, database)

        async def _generate() -> Dict[str, Any]:
            try:
                svc = await connect_remote(service_id)
                resp = await svc.invoke(
                    "chat",
                    {
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {
                                "role": "user",
                                "content": f"Generate API parameters for this query: {prompt}",
                            },
                        ],
                        "response_format": {"type": "json_object"},
                    },
                )

                content = resp.get("content") if isinstance(resp, dict) else resp
                if isinstance(content, str):
                    params = json.loads(content)
                else:
                    params = content

                return {"success": True, "parameters": params}

            except Exception as e:
                logger.error(f"LLM parameter generation failed: {e}")
                return {"success": False, "error": str(e)}

        try:
            return asyncio.run(_generate())
        except Exception as e:
            return {"success": False, "error": f"Async execution failed: {e}"}

    def _build_schema_prompt(self, schema: Dict[str, Any], database: str) -> str:
        """Build system prompt with schema information for LLM."""
        import json

        base_url = schema.get("base_url", "")
        categories = schema.get("categories", {})
        query_fields = schema.get("query_fields", {})

        prompt = f"""You are an API parameter generator for the {database} database.

Base URL: {base_url}

Available endpoints and their parameters:
{json.dumps(categories, indent=2)}

Query field syntax:
{json.dumps(query_fields, indent=2)}

Your task: Generate a JSON object with API parameters based on the user's natural language query.
The JSON should contain the appropriate query parameters for the database API.

For example, if querying UniProt for a specific gene:
{{"query": "gene:BRCA1 AND organism:9606", "format": "json", "size": 25}}

Always return valid JSON with the appropriate field names for this database.
"""
        return prompt

    def _call_database_api(
        self,
        schema: Dict[str, Any],
        params: Dict[str, Any],
        max_results: int = 5,
    ) -> Dict[str, Any]:
        """Execute the API call with generated parameters."""
        import httpx

        base_url = schema.get("base_url", "")
        if not base_url:
            return {"success": False, "error": "Base URL not found in schema"}

        # Add size/limit parameter if not already present
        if "size" not in params and "limit" not in params:
            params["size"] = max_results

        # Build full URL (assuming search endpoint for now)
        # This can be made more sophisticated based on schema categories
        url = f"{base_url}/search" if "/search" not in base_url else base_url

        try:
            response = httpx.get(url, params=params, timeout=30.0)
            response.raise_for_status()

            data = response.json()

            return {
                "success": True,
                "data": data,
                "total_count": self._extract_count(data),
                "url": url,
                "params": params,
            }

        except httpx.HTTPStatusError as e:
            logger.error(f"API request failed: {e}")
            return {
                "success": False,
                "error": f"API request failed: {str(e)}",
                "url": url,
                "params": params,
            }
        except Exception as e:
            return {"success": False, "error": f"Unexpected error: {str(e)}"}

    def _extract_count(self, data: Dict[str, Any]) -> int:
        """Extract total count from API response (database-specific)."""
        # Try common patterns
        if "total" in data:
            return data["total"]
        if "count" in data:
            return data["count"]
        if "results" in data:
            return len(data["results"])
        if "items" in data:
            return len(data["items"])
        return 0

    def _format_results_for_llm(
        self,
        data: Dict[str, Any],
        max_results: int,
        database: str,
    ) -> str:
        """Format API results as human-readable text for LLM consumption."""
        output = []

        # Extract results list (database-specific)
        results = (
            data.get("results")
            or data.get("items")
            or data.get("entries")
            or data.get("data")
            or []
        )

        if not results:
            return "No results found."

        # Limit results
        results = results[:max_results]

        output.append(f"Found {len(results)} results from {database}:\n")

        for i, item in enumerate(results, 1):
            output.append(f"\n{i}. Entry:")

            # Extract key information (try common fields)
            if isinstance(item, dict):
                # ID
                item_id = (
                    item.get("id")
                    or item.get("accession")
                    or item.get("primaryAccession")
                    or "N/A"
                )
                output.append(f"   ID: {item_id}")

                # Name/Title
                name = (
                    item.get("name")
                    or item.get("title")
                    or item.get("geneName")
                    or item.get("symbol")
                )
                if name:
                    output.append(f"   Name: {name}")

                # Description
                desc = (
                    item.get("description")
                    or item.get("proteinDescription")
                    or item.get("function")
                )
                if desc:
                    # Truncate long descriptions
                    desc_str = str(desc)
                    if len(desc_str) > 200:
                        desc_str = desc_str[:200] + "..."
                    output.append(f"   Description: {desc_str}")

                # Organism
                organism = item.get("organism") or item.get("species")
                if organism:
                    if isinstance(organism, dict):
                        organism = organism.get("scientificName", organism)
                    output.append(f"   Organism: {organism}")

            else:
                output.append(f"   {str(item)[:200]}")

        return "\n".join(output)


__all__ = ["DatabaseAPIQueryToolSet"]
