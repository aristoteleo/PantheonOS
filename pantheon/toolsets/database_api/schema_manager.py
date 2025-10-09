"""Schema Manager for Database API specifications.

This module manages API schemas for various biological databases,
providing unified access to endpoint configurations and query patterns.
"""

import json
from pathlib import Path
from typing import Any, Dict, Optional, List
from pantheon.utils.log import logger


class SchemaManager:
    """Manages database API schemas for intelligent query generation.

    Schemas contain information about:
    - Base URLs and endpoints
    - Query parameter syntax
    - Response formats
    - Example queries
    """

    def __init__(self, schemas_dir: Optional[Path] = None):
        """Initialize the schema manager.

        Args:
            schemas_dir: Directory containing JSON schema files.
                        Defaults to the schemas/ subdirectory.
        """
        if schemas_dir is None:
            schemas_dir = Path(__file__).parent / "schemas"

        self.schemas_dir = Path(schemas_dir)
        self._cache: Dict[str, Dict[str, Any]] = {}

        if not self.schemas_dir.exists():
            logger.warning(f"Schema directory not found: {self.schemas_dir}")

    def load_schema(self, database: str) -> Optional[Dict[str, Any]]:
        """Load schema for a specific database.

        Args:
            database: Name of the database (e.g., 'uniprot', 'ensembl')

        Returns:
            Schema dictionary or None if not found
        """
        # Check cache first
        if database in self._cache:
            return self._cache[database]

        # Load from file
        schema_path = self.schemas_dir / f"{database.lower()}.json"
        if not schema_path.exists():
            logger.warning(f"Schema not found: {database}")
            return None

        try:
            with open(schema_path, 'r', encoding='utf-8') as f:
                schema = json.load(f)

            # Cache it
            self._cache[database] = schema
            return schema

        except Exception as e:
            logger.error(f"Error loading schema {database}: {e}")
            return None

    def list_available_databases(self) -> List[str]:
        """Get list of all available database schemas.

        Returns:
            List of database names
        """
        if not self.schemas_dir.exists():
            return []

        return [
            schema_file.stem
            for schema_file in self.schemas_dir.glob("*.json")
        ]

    def get_query_examples(self, database: str) -> List[str]:
        """Get example queries for a database.

        Args:
            database: Name of the database

        Returns:
            List of example query strings
        """
        schema = self.load_schema(database)
        if not schema:
            return []

        examples = []

        # Extract examples from categories
        categories = schema.get("categories", {})
        for category_info in categories.values():
            endpoints = category_info.get("endpoints", {})
            for endpoint_info in endpoints.values():
                if "example" in endpoint_info:
                    examples.append(endpoint_info["example"])

        return examples

    def get_base_url(self, database: str) -> Optional[str]:
        """Get base URL for a database API.

        Args:
            database: Name of the database

        Returns:
            Base URL string or None
        """
        schema = self.load_schema(database)
        if schema:
            return schema.get("base_url")
        return None

    def validate_database(self, database: str) -> bool:
        """Check if a database schema exists and is valid.

        Args:
            database: Name of the database

        Returns:
            True if schema exists and is loadable
        """
        schema = self.load_schema(database)
        return schema is not None and "base_url" in schema


__all__ = ["SchemaManager"]
