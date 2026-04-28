"""
Local rd_ontology ToolSet (SQLite-backed).

Provides agent-native ontology tools for rare disease workflows:
- resolve_term
- search_disease
- get_disease
- get_hpo_term
- find_by_hpo
- stats
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from pantheon.toolset import ToolSet, tool


class RdOntologyToolSet(ToolSet):
    def __init__(
        self,
        name: str = "rd_ontology",
        db_path: str | None = None,
        **kwargs,
    ):
        super().__init__(name=name, **kwargs)
        self.db_path = db_path

    def _resolve_db_path(self) -> Path:
        if self.db_path:
            p = Path(self.db_path).expanduser()
            return p if p.is_absolute() else (Path.cwd() / p).resolve()

        workdir = self._get_effective_workdir()
        base = Path(workdir) if workdir else Path.cwd()
        return (base / "data" / "rd_ontology_store" / "rd_ontology.sqlite").resolve()

    def _connect(self) -> sqlite3.Connection:
        db = self._resolve_db_path()
        if not db.exists():
            raise FileNotFoundError(
                f"rd_ontology database not found: {db}. "
                "Build it first with: "
                "python scripts/rare_disease/build_rd_ontology.py build --reset"
            )
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        return conn

    @tool
    async def resolve_term(self, query: str, limit: int = 10) -> dict:
        """Resolve disease term/alias to canonical candidates."""
        try:
            normalized = (query or "").strip()
            result = await self.search_disease(normalized, limit=limit)
            if not result.get("success"):
                return result
            result["query_original"] = query
            result["query_normalized"] = normalized
            return result
        except Exception as e:
            return {"success": False, "error": str(e)}

    @tool
    async def search_disease(self, query: str, limit: int = 10) -> dict:
        """Search diseases by canonical name or aliases."""
        try:
            with self._connect() as conn:
                q = f"%{query}%"
                rows = conn.execute(
                    """
                    SELECT d.disease_uid, d.canonical_name,
                           (SELECT COUNT(*) FROM phenotype_assoc p WHERE p.disease_uid=d.disease_uid) AS phenotype_count,
                           (SELECT COUNT(*) FROM gene_assoc g WHERE g.disease_uid=d.disease_uid) AS gene_count
                    FROM diseases d
                    WHERE d.canonical_name LIKE ?
                       OR EXISTS (
                            SELECT 1 FROM disease_aliases a
                            WHERE a.disease_uid = d.disease_uid
                              AND a.alias LIKE ?
                       )
                    ORDER BY
                      CASE WHEN LOWER(d.canonical_name) = LOWER(?) THEN 0 ELSE 1 END,
                      LENGTH(COALESCE(d.canonical_name, '')),
                      d.disease_uid
                    LIMIT ?
                    """,
                    (q, q, query, max(1, min(limit, 50))),
                ).fetchall()

                items: list[dict[str, Any]] = []
                for row in rows:
                    uid = row["disease_uid"]
                    xrefs = conn.execute(
                        """
                        SELECT xref_db, xref_id
                        FROM disease_xrefs
                        WHERE disease_uid=?
                        ORDER BY xref_db, xref_id
                        LIMIT 12
                        """,
                        (uid,),
                    ).fetchall()
                    items.append(
                        {
                            "disease_uid": uid,
                            "canonical_name": row["canonical_name"],
                            "phenotype_count": row["phenotype_count"],
                            "gene_count": row["gene_count"],
                            "xrefs": [f"{x['xref_db']}:{x['xref_id']}" for x in xrefs],
                        }
                    )

                return {
                    "success": True,
                    "query": query,
                    "matched_count": len(items),
                    "results": items,
                }
        except Exception as e:
            return {"success": False, "error": str(e)}

    @tool
    async def get_disease(
        self,
        disease_uid: str,
        phenotype_limit: int = 50,
        gene_limit: int = 40,
    ) -> dict:
        """Get detailed disease record with aliases/xrefs/phenotypes/genes."""
        try:
            with self._connect() as conn:
                d = conn.execute(
                    """
                    SELECT disease_uid, canonical_name, primary_source, primary_id, definition, expert_link
                    FROM diseases WHERE disease_uid=?
                    """,
                    (disease_uid,),
                ).fetchone()
                if not d:
                    return {"success": False, "error": f"disease not found: {disease_uid}"}

                aliases = conn.execute(
                    """
                    SELECT alias, alias_type, source
                    FROM disease_aliases
                    WHERE disease_uid=?
                    ORDER BY alias_type, alias
                    LIMIT 120
                    """,
                    (disease_uid,),
                ).fetchall()
                xrefs = conn.execute(
                    """
                    SELECT xref_db, xref_id, source
                    FROM disease_xrefs
                    WHERE disease_uid=?
                    ORDER BY xref_db, xref_id
                    LIMIT 200
                    """,
                    (disease_uid,),
                ).fetchall()
                phenotypes = conn.execute(
                    """
                    SELECT hpo_id, hpo_term, frequency_label, evidence, source
                    FROM phenotype_assoc
                    WHERE disease_uid=?
                    ORDER BY hpo_id
                    LIMIT ?
                    """,
                    (disease_uid, max(1, min(phenotype_limit, 500))),
                ).fetchall()
                genes = conn.execute(
                    """
                    SELECT gene_symbol, gene_name, association_type, source, omim_gene_id
                    FROM gene_assoc
                    WHERE disease_uid=?
                    ORDER BY gene_symbol
                    LIMIT ?
                    """,
                    (disease_uid, max(1, min(gene_limit, 200))),
                ).fetchall()

                return {
                    "success": True,
                    "record": {
                        "disease_uid": d["disease_uid"],
                        "canonical_name": d["canonical_name"],
                        "primary_source": d["primary_source"],
                        "primary_id": d["primary_id"],
                        "definition": d["definition"],
                        "expert_link": d["expert_link"],
                        "aliases": [dict(a) for a in aliases],
                        "xrefs": [dict(x) for x in xrefs],
                        "phenotypes": [dict(p) for p in phenotypes],
                        "genes": [dict(g) for g in genes],
                    },
                }
        except Exception as e:
            return {"success": False, "error": str(e)}

    @tool
    async def get_hpo_term(self, hpo_id: str) -> dict:
        """Get HPO term detail by ID (e.g., HP:0001166)."""
        try:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT hpo_id, label, definition, synonyms_json, parents_json
                    FROM hpo_terms WHERE hpo_id=?
                    """,
                    (hpo_id.strip(),),
                ).fetchone()
                if not row:
                    return {"success": False, "error": f"HPO term not found: {hpo_id}"}
                return {
                    "success": True,
                    "term": {
                        "hpo_id": row["hpo_id"],
                        "label": row["label"],
                        "definition": row["definition"],
                        "synonyms": json.loads(row["synonyms_json"] or "[]"),
                        "parents": json.loads(row["parents_json"] or "[]"),
                    },
                }
        except Exception as e:
            return {"success": False, "error": str(e)}

    @tool
    async def find_by_hpo(self, hpo_ids: list[str], limit: int = 20) -> dict:
        """Find diseases associated with one or more HPO IDs."""
        try:
            ids = [x.strip() for x in (hpo_ids or []) if x and x.strip()]
            if not ids:
                return {"success": False, "error": "hpo_ids cannot be empty"}

            placeholders = ",".join("?" * len(ids))
            with self._connect() as conn:
                rows = conn.execute(
                    f"""
                    SELECT p.disease_uid,
                           d.canonical_name,
                           COUNT(DISTINCT p.hpo_id) AS matched_hpo_count,
                           COUNT(*) AS assoc_count
                    FROM phenotype_assoc p
                    JOIN diseases d ON d.disease_uid = p.disease_uid
                    WHERE p.hpo_id IN ({placeholders})
                    GROUP BY p.disease_uid, d.canonical_name
                    ORDER BY matched_hpo_count DESC, assoc_count DESC, d.canonical_name
                    LIMIT ?
                    """,
                    (*ids, max(1, min(limit, 100))),
                ).fetchall()
                return {
                    "success": True,
                    "hpo_ids": ids,
                    "matched_count": len(rows),
                    "results": [dict(r) for r in rows],
                }
        except Exception as e:
            return {"success": False, "error": str(e)}

    @tool
    async def stats(self) -> dict:
        """Return rd_ontology table counts."""
        try:
            with self._connect() as conn:
                tables = [
                    "diseases",
                    "disease_aliases",
                    "disease_xrefs",
                    "hpo_terms",
                    "phenotype_assoc",
                    "gene_assoc",
                ]
                counts = {
                    t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables
                }
                return {
                    "success": True,
                    "db_path": str(self._resolve_db_path()),
                    "counts": counts,
                }
        except Exception as e:
            return {"success": False, "error": str(e)}
