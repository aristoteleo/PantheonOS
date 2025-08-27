"""DatabaseQuery Toolset - OmicVerse DataCollect wrappers for Pantheon

This toolset exposes convenient commands to query public biological databases
via the OmicVerse external DataCollect module and convert results to common
formats (pandas, AnnData, MuData) for downstream analysis.
"""

from typing import Any, Dict, List, Optional
from pathlib import Path

from ..utils.toolset import ToolSet, tool
from ..utils.log import logger


def _import_datacollect():
    """Import omicverse.external.datacollect safely.

    Returns a tuple (dc_module, error_message). If import succeeds, error_message is None.
    """
    try:
        import omicverse as ov  # type: ignore
    except Exception as e:  # pragma: no cover - environment dependent
        return None, f"Failed to import omicverse: {e}"

    # Try the attribute first (preferred by omicverse __init__)
    dc = getattr(ov, "external", None)
    if dc is not None:
        dc = getattr(dc, "datacollect", None)
        if dc is not None:
            return dc, None

    # Fallback to direct import
    try:
        import importlib
        dc = importlib.import_module("omicverse.external.datacollect")
        return dc, None
    except Exception as e:  # pragma: no cover - environment dependent
        return None, f"omicverse.external.datacollect not available: {e}"


class DatabaseQueryToolSet(ToolSet):
    """Query public bio databases through OmicVerse DataCollect.

    Commands:
    - /bio database_query list_sources
    - /bio database_query protein <identifier> [--source uniprot|pdb|alphafold|interpro|string] [--to_format pandas|anndata|mudata|dict]
    - /bio database_query expression <identifier> [--source geo|ccre] [--to_format anndata|pandas|mudata|dict]
    - /bio database_query pathway <identifier> [--source kegg|reactome|gtopdb] [--to_format pandas|anndata|mudata|dict]
    - /bio database_query client_info <client_name>
    """

    def __init__(
        self,
        name: str = "database_query",
        workspace_path: str | None = None,
        worker_params: dict | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(name, worker_params, **kwargs)
        self.workspace_path = Path(workspace_path) if workspace_path else Path.cwd()

    @tool(name="database_query")
    def nl_query(
        self,
        query: str,
        to_format: str | None = None,
        use_llm: bool = True,
        llm_service_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Natural-language database query. Example: /bio database_query "KEGG pathway hsa04110 as DataFrame"

        The tool interprets the intent, selects an appropriate client (or wrapper),
        and returns results. If an LLM service is configured it will be used to
        improve intent detection; otherwise a robust heuristic parser is used.
        """
        decision = None
        # Try LLM routing first if requested
        if use_llm:
            decision = self._route_with_llm(query, to_format=to_format, llm_service_id=llm_service_id)
        if decision is None:
            decision = self._route_with_heuristics(query, to_format=to_format)

        # No decision made
        if decision is None:
            return {"success": False, "error": "Could not interpret query", "query": query}

        # Execute based on decision
        category = decision.get("category")
        client = decision.get("client")
        source = decision.get("source")
        identifier = decision.get("identifier")
        fmt = decision.get("to_format") or "pandas"
        rationale = decision.get("rationale", "")

        # Use high-level wrappers when possible
        try:
            if category == "protein" and source in {"uniprot", "pdb", "alphafold", "interpro", "string"}:
                resp = self.protein(identifier, source=source, to_format=fmt)
            elif category == "expression" and source in {"geo", "ccre"}:
                resp = self.expression(identifier, source=source, to_format=fmt)
            elif category == "pathway" and source in {"kegg", "reactome", "gtopdb"}:
                resp = self.pathway(identifier, source=source, to_format=fmt)
            else:
                # Fallback to direct client invocation
                resp = self._invoke_client_direct(client, identifier, to_format=fmt)

            resp.setdefault("decision", decision)
            resp.setdefault("rationale", rationale)
            resp.setdefault("query", query)
            return resp
        except Exception as e:  # pragma: no cover - runtime dependent
            return {"success": False, "error": str(e), "decision": decision, "query": query}

    # --- Routing helpers ---
    def _route_with_llm(
        self,
        query: str,
        to_format: Optional[str] = None,
        llm_service_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Attempt to use a remote LLM service via magique to classify the query.

        Returns a decision dict or None on failure.
        """
        try:
            # Lazy import to avoid hard dependency failures in lean environments
            from ..utils.remote import connect_remote  # type: ignore
        except Exception:
            return None

        # Service ID can be passed explicitly or via env var
        import os, asyncio, json
        service_id = llm_service_id or os.getenv("PANTHEON_AGENT_SERVICE_ID")
        if not service_id:
            return None

        prompt = {
            "system": (
                "You route bio database queries to OmicVerse DataCollect clients. "
                "Return a JSON with fields: category(one of protein|expression|pathway|genomics|specialized), "
                "client(class name like UniProtClient, GEOClient, KEGGClient, EnsemblClient, dbSNPClient, etc.), "
                "source(key for wrapper if applicable like uniprot|pdb|alphafold|string|geo|ccre|kegg|reactome|gtopdb), "
                "identifier(main identifier e.g., P04637, GSE12345, rs429358, hsa04110), "
                "to_format(one of pandas|anndata|mudata|dict), and rationale(short)."
            ),
            "user": f"Query: {query}\nPreferred format: {to_format or 'auto'}"
        }

        async def _ask() -> Optional[Dict[str, Any]]:
            try:
                svc = await connect_remote(service_id)
                resp = await svc.invoke("chat", {"messages": [
                    {"role": "system", "content": prompt["system"]},
                    {"role": "user", "content": prompt["user"]},
                ], "response_format": {"type": "json_object"}})
                content = resp.get("content") if isinstance(resp, dict) else resp
                if isinstance(content, str):
                    data = json.loads(content)
                else:
                    data = content
                if isinstance(data, dict) and data.get("client"):
                    data.setdefault("strategy", "llm")
                    return data
            except Exception:
                return None
            return None

        try:
            return asyncio.run(_ask())
        except Exception:
            return None

    def _route_with_heuristics(self, query: str, to_format: Optional[str]) -> Optional[Dict[str, Any]]:
        import re

        q = query.lower()
        fmt = (to_format or ("anndata" if any(k in q for k in ["expression", "gse", "rna"]) else "pandas")).lower()

        # Identifiers
        m_pdb = re.search(r"\b[0-9][a-z0-9]{3}\b", q)
        m_uniprot = re.search(r"\b[opq][0-9][a-z0-9]{3}[0-9]\b|\b[a-nr-z][0-9][a-z0-9]{3}[0-9]\b", q)
        m_gene = re.search(r"\b[a-z0-9]{2,6}\d{0,2}\b", q)
        m_gse = re.search(r"\bGSE\d{3,}\b", query)
        m_react = re.search(r"\bR-HSA-\d+\b", query)
        m_kegg = re.search(r"\bhsa\d{5}\b", q)
        m_rs = re.search(r"\brs\d+\b", q)
        m_ensg = re.search(r"\bENSG\d+\b", query)

        # Protein/structure
        if any(k in q for k in ["pdb", "structure"]) and m_pdb:
            return {"category": "protein", "client": "PDBClient", "source": "pdb", "identifier": m_pdb.group(0), "to_format": fmt, "strategy": "heuristic", "rationale": "PDB-like identifier with structure intent"}
        if any(k in q for k in ["alphafold", "af-"]):
            ident = (m_uniprot or m_gene)
            if ident:
                return {"category": "protein", "client": "AlphaFoldClient", "source": "alphafold", "identifier": ident.group(0).upper(), "to_format": fmt, "strategy": "heuristic", "rationale": "AlphaFold requested"}
        if any(k in q for k in ["string", "interaction"]) and (m_uniprot or m_gene):
            ident = (m_uniprot or m_gene).group(0).upper()
            return {"category": "protein", "client": "STRINGClient", "source": "string", "identifier": ident, "to_format": fmt, "strategy": "heuristic", "rationale": "Protein interaction via STRING"}
        if any(k in q for k in ["uniprot", "protein"]) and (m_uniprot or m_gene):
            ident = (m_uniprot or m_gene).group(0).upper()
            return {"category": "protein", "client": "UniProtClient", "source": "uniprot", "identifier": ident, "to_format": fmt, "strategy": "heuristic", "rationale": "Protein info via UniProt"}

        # Expression
        if m_gse or "geo" in q or "expression" in q or "rna" in q:
            ident = (m_gse.group(0) if m_gse else query)
            return {"category": "expression", "client": "GEOClient", "source": "geo", "identifier": ident, "to_format": fmt if fmt in {"anndata", "pandas", "mudata", "dict"} else "anndata", "strategy": "heuristic", "rationale": "Expression/GEO detected"}

        # Pathways
        if m_kegg or "kegg" in q:
            ident = (m_kegg.group(0) if m_kegg else query)
            return {"category": "pathway", "client": "KEGGClient", "source": "kegg", "identifier": ident, "to_format": fmt, "strategy": "heuristic", "rationale": "KEGG pathway requested"}
        if m_react or "reactome" in q:
            ident = (m_react.group(0) if m_react else query)
            return {"category": "pathway", "client": "ReactomeClient", "source": "reactome", "identifier": ident, "to_format": fmt, "strategy": "heuristic", "rationale": "Reactome pathway requested"}

        # Genomics/variants
        if m_rs:
            return {"category": "genomics", "client": "dbSNPClient", "source": None, "identifier": m_rs.group(0), "to_format": fmt, "strategy": "heuristic", "rationale": "dbSNP variant detected"}
        if m_ensg or "ensembl" in q:
            ident = (m_ensg.group(0) if m_ensg else query)
            return {"category": "genomics", "client": "EnsemblClient", "source": None, "identifier": ident, "to_format": fmt, "strategy": "heuristic", "rationale": "Ensembl gene requested"}

        return None

    # --- Direct client invocation ---
    def _invoke_client_direct(self, client_name: str, identifier: str, to_format: str = "pandas") -> Dict[str, Any]:
        dc, err = _import_datacollect()
        if err or dc is None:
            return {"success": False, "error": err or "datacollect unavailable"}
        try:
            client_cls = getattr(dc, client_name, None)
            if client_cls is None:
                return {"success": False, "error": f"Client '{client_name}' not found"}
            client = client_cls()
            raw = client.get_data(identifier)

            # Attempt conversion using adapters
            try:
                from omicverse.external.datacollect.utils.omicverse_adapters import to_pandas, to_anndata, to_mudata
                if to_format == "pandas":
                    payload = to_pandas(raw)
                elif to_format == "anndata":
                    payload = to_anndata(raw)
                elif to_format == "mudata":
                    payload = to_mudata(raw)
                else:
                    payload = raw
            except Exception:
                payload = raw

            return {"success": True, "client": client_name, "to_format": to_format, "payload": payload}
        except Exception as e:  # pragma: no cover - runtime dependent
            return {"success": False, "error": str(e)}

    @tool
    def list_sources(self) -> Dict[str, List[str]]:
        """List available data sources grouped by category."""
        dc, err = _import_datacollect()
        if err:
            logger.warning(err)
        # Static map, aligned with datacollect __init__.py
        sources = {
            "proteins": ["uniprot", "pdb", "alphafold", "interpro", "string", "emdb"],
            "genomics": ["ensembl", "clinvar", "dbsnp", "gnomad", "gwas_catalog", "ucsc", "regulomedb"],
            "expression": ["geo", "ccre", "opentargets", "opentargets_genetics", "remap"],
            "pathways": ["kegg", "reactome", "gtopdb"],
            "specialized": ["blast", "jaspar", "mpd", "iucn", "pride", "cbioportal", "worms", "paleobiology"],
        }
        return sources

    @tool
    def protein(
        self,
        identifier: str,
        source: str = "uniprot",
        to_format: str = "pandas",
    ) -> Dict[str, Any]:
        """Collect protein data and convert to the requested format.

        Returns a dict with keys: success, source, to_format, payload or error.
        """
        dc, err = _import_datacollect()
        if err or dc is None:
            return {"success": False, "error": err or "datacollect unavailable"}
        try:
            result = dc.collect_protein_data(identifier, source=source, to_format=to_format)
            return {"success": True, "source": source, "to_format": to_format, "payload": result}
        except Exception as e:
            return {"success": False, "source": source, "to_format": to_format, "error": str(e)}

    @tool
    def expression(
        self,
        identifier: str,
        source: str = "geo",
        to_format: str = "anndata",
    ) -> Dict[str, Any]:
        """Collect expression data (e.g., GEO, CCRE) with default AnnData output."""
        dc, err = _import_datacollect()
        if err or dc is None:
            return {"success": False, "error": err or "datacollect unavailable"}
        try:
            result = dc.collect_expression_data(identifier, source=source, to_format=to_format)
            return {"success": True, "source": source, "to_format": to_format, "payload": result}
        except Exception as e:
            return {"success": False, "source": source, "to_format": to_format, "error": str(e)}

    @tool
    def pathway(
        self,
        identifier: str,
        source: str = "kegg",
        to_format: str = "pandas",
    ) -> Dict[str, Any]:
        """Collect pathway data (KEGG/Reactome/GtoPdb) and convert to format."""
        dc, err = _import_datacollect()
        if err or dc is None:
            return {"success": False, "error": err or "datacollect unavailable"}
        try:
            result = dc.collect_pathway_data(identifier, source=source, to_format=to_format)
            return {"success": True, "source": source, "to_format": to_format, "payload": result}
        except Exception as e:
            return {"success": False, "source": source, "to_format": to_format, "error": str(e)}

    @tool
    def client_info(self, client_name: str) -> Dict[str, Any]:
        """Introspect a specific API client (e.g., 'UniProtClient')."""
        dc, err = _import_datacollect()
        if err or dc is None:
            return {"success": False, "error": err or "datacollect unavailable"}
        try:
            client_cls = getattr(dc, client_name, None)
            if client_cls is None:
                return {"success": False, "error": f"Client '{client_name}' not found"}
            import inspect
            methods = [
                n for n, m in inspect.getmembers(client_cls)
                if inspect.isfunction(m) and not n.startswith("_")
            ]
            return {"success": True, "client": client_name, "methods": methods}
        except Exception as e:
            return {"success": False, "error": str(e)}


__all__ = ["DatabaseQueryToolSet"]
