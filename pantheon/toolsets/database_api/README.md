# Database API Query ToolSet

LLM-enhanced natural language queries for biological databases.

## Overview

This toolset provides a natural language interface to query 26+ biological databases using LLM-driven API parameter generation. It translates your plain English queries into appropriate database API calls and returns formatted results.

## Supported Databases

### Proteins (6)
- `uniprot` - Universal Protein Resource
- `pdb` - Protein Data Bank
- `alphafold` - AlphaFold Structure Database
- `interpro` - Protein families and domains
- `string` - Protein-protein interactions
- `emdb` - Electron Microscopy Data Bank

### Genomics (7)
- `ensembl` - Genome annotations
- `clinvar` - Clinical variants
- `dbsnp` - Single nucleotide polymorphisms
- `gnomad` - Genome aggregation database
- `gwas_catalog` - GWAS associations
- `ucsc` - UCSC Genome Browser
- `regulomedb` - Regulatory elements

### Expression (5)
- `geo` - Gene Expression Omnibus
- `ccre` - Candidate cis-regulatory elements
- `opentargets` - Drug target validation
- `opentargets_genetics` - Genetics portal
- `remap` - Transcription factor binding

### Pathways (3)
- `kegg` - Kyoto Encyclopedia of Genes and Genomes
- `reactome` - Pathway database
- `gtopdb` - Guide to Pharmacology

### Specialized (5)
- `blast` - Sequence similarity
- `jaspar` - Transcription factor binding profiles
- `mpd` - Mouse Phenome Database
- `iucn` - Species conservation status
- `pride` - Proteomics data
- `cbioportal` - Cancer genomics
- `worms` - Marine species
- `paleobiology` - Fossil records

## Quick Start

```python
from pantheon.toolsets.database_api import DatabaseAPIQueryToolSet

# Initialize
toolset = DatabaseAPIQueryToolSet()

# Query a database
result = toolset.query(
    prompt="Find BRCA1 mutations associated with breast cancer",
    database="clinvar",
    max_results=10
)

if result["success"]:
    print(result["results"])
else:
    print(f"Error: {result['error']}")
```

## Main Methods

### `query(prompt, database, max_results=5, llm_service_id=None)`

Query a database using natural language.

**Parameters:**
- `prompt` (str): Natural language description of what you're looking for
- `database` (str): Target database name (see list above)
- `max_results` (int): Maximum results to return (default: 5)
- `llm_service_id` (str, optional): LLM service ID for parameter generation

**Returns:**
Dict with keys:
- `success` (bool): Whether query succeeded
- `database` (str): Database name
- `prompt` (str): Original query
- `api_parameters` (dict): Generated API parameters
- `raw_count` (int): Total matching results
- `results` (str): Formatted results text
- `strategy` (str): Always "llm_enhanced"
- `error` (str): Error message if failed

**Example:**
```python
result = toolset.query(
    "Human insulin protein structure",
    "uniprot",
    max_results=5
)
```

### `list_databases()`

Get a list of all available databases grouped by category.

**Returns:**
Dict with keys:
- `success` (bool)
- `databases` (list): All database names
- `categories` (dict): Databases grouped by category
- `total` (int): Total number of databases

**Example:**
```python
dbs = toolset.list_databases()
print(f"Available: {dbs['total']} databases")
for category, db_list in dbs['categories'].items():
    print(f"{category}: {', '.join(db_list)}")
```

### `database_info(database)`

Get detailed information about a specific database.

**Parameters:**
- `database` (str): Database name

**Returns:**
Dict with keys:
- `success` (bool)
- `database` (str): Database name
- `base_url` (str): API base URL
- `categories` (list): Available API categories
- `example_queries` (list): Example query strings
- `is_valid` (bool): Schema validation status

**Example:**
```python
info = toolset.database_info("uniprot")
print(f"Base URL: {info['base_url']}")
print(f"Categories: {info['categories']}")
```

## Configuration

### Environment Variable

Set the LLM service ID for parameter generation:

```bash
export PANTHEON_AGENT_SERVICE_ID=your-llm-service-id
```

Alternatively, pass it directly to the query method:

```python
result = toolset.query(
    prompt="...",
    database="...",
    llm_service_id="your-service-id"
)
```

## Usage Examples

### Example 1: Finding Protein Variants

```python
result = toolset.query(
    "Find pathogenic variants in BRCA1 gene",
    "clinvar",
    max_results=10
)

if result["success"]:
    print(f"Found {result['raw_count']} total results")
    print(result["results"])
```

### Example 2: Protein Structure Search

```python
result = toolset.query(
    "Get structure information for tumor suppressor p53",
    "uniprot"
)

print(result["results"])
```

### Example 3: Pathway Information

```python
result = toolset.query(
    "Cell cycle regulation pathway in humans",
    "kegg",
    max_results=5
)

print(result["results"])
```

### Example 4: Gene Expression Data

```python
result = toolset.query(
    "RNA-seq data for breast cancer samples",
    "geo",
    max_results=10
)

if result["success"]:
    print(result["results"])
```

## How It Works

1. **Schema Loading**: Each database has a JSON schema with API specifications
2. **LLM Parameter Generation**: Your natural language query is sent to an LLM with the schema
3. **API Call**: Generated parameters are used to call the database REST API
4. **Result Formatting**: API response is formatted into human-readable text
5. **Return**: Formatted results with metadata are returned

## Error Handling

The toolset handles various error conditions:

```python
result = toolset.query("...", "invalid_database")

if not result["success"]:
    print(f"Error: {result['error']}")
    if "available_databases" in result:
        print(f"Try one of: {result['available_databases']}")
```

Common error scenarios:
- Database schema not found
- LLM service unavailable
- API request failed
- Invalid response format

## Comparison with Bio ToolSet

This toolset (`database_api`) is complementary to the existing `bio.database_query` toolset:

| Feature | bio.database_query | database_api |
|---------|-------------------|--------------|
| Backend | OmicVerse wrappers | Direct REST APIs |
| Query Style | Mixed LLM/heuristic | Pure LLM-driven |
| Result Format | pandas/anndata objects | Text for LLM |
| Use Case | Data analysis | Conversational queries |

Both can be used together in the same application.

## Testing

Run the test suite:

```bash
cd pantheon-agents
source .venv/bin/activate
python test_database_api.py
```

All 7 tests should pass.

## Dependencies

- `httpx` - HTTP client
- `pantheon.toolset` - ToolSet base class
- Remote LLM service via NATS

## Contributing

To add a new database:

1. Create a JSON schema in `schemas/{database}.json`
2. Include: `base_url`, `categories`, `query_fields`, examples
3. Test with `database_info()` and sample queries
4. Update this README with the new database

## License

Part of the Pantheon agents framework.

## See Also

- `pantheon/toolsets/bio/database_query.py` - OmicVerse-based queries
- Schema files in `schemas/` directory
- `schema_manager.py` - Schema loading and validation
