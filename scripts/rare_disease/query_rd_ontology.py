#!/usr/bin/env python3
"""
Query helper for local rd_ontology SQLite.

Examples:
  python scripts/rare_disease/query_rd_ontology.py resolve "Marfan syndrome"
  python scripts/rare_disease/query_rd_ontology.py resolve "your local alias"
  python scripts/rare_disease/query_rd_ontology.py disease OMIM:154700
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


DEFAULT_DB = repo_root() / "data" / "rd_ontology_store" / "rd_ontology.sqlite"


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def search(conn: sqlite3.Connection, query: str, limit: int = 10):
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
        (q, q, query, limit),
    ).fetchall()

    out = []
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
        out.append(
            {
                "disease_uid": uid,
                "canonical_name": row["canonical_name"],
                "phenotype_count": row["phenotype_count"],
                "gene_count": row["gene_count"],
                "xrefs": [f"{x['xref_db']}:{x['xref_id']}" for x in xrefs],
            }
        )
    return out


def disease_detail(conn: sqlite3.Connection, disease_uid: str):
    d = conn.execute(
        "SELECT disease_uid, canonical_name, primary_source, primary_id, definition, expert_link FROM diseases WHERE disease_uid=?",
        (disease_uid,),
    ).fetchone()
    if not d:
        return None

    aliases = conn.execute(
        """
        SELECT alias, alias_type, source
        FROM disease_aliases
        WHERE disease_uid=?
        ORDER BY alias_type, alias
        LIMIT 80
        """,
        (disease_uid,),
    ).fetchall()

    xrefs = conn.execute(
        """
        SELECT xref_db, xref_id, source
        FROM disease_xrefs
        WHERE disease_uid=?
        ORDER BY xref_db, xref_id
        LIMIT 120
        """,
        (disease_uid,),
    ).fetchall()

    phenotypes = conn.execute(
        """
        SELECT hpo_id, hpo_term, frequency_label, evidence, source
        FROM phenotype_assoc
        WHERE disease_uid=?
        ORDER BY hpo_id
        LIMIT 50
        """,
        (disease_uid,),
    ).fetchall()

    genes = conn.execute(
        """
        SELECT gene_symbol, gene_name, association_type, source, omim_gene_id
        FROM gene_assoc
        WHERE disease_uid=?
        ORDER BY gene_symbol
        LIMIT 40
        """,
        (disease_uid,),
    ).fetchall()

    return {
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
    }


def cmd_resolve(args: argparse.Namespace):
    db = Path(args.db).expanduser().resolve()
    conn = connect(db)
    try:
        original = args.query
        normalized = original.strip()
        results = search(conn, normalized, limit=args.limit)
        print(
            json.dumps(
                {
                    "query_original": original,
                    "query_normalized": normalized,
                    "matched_count": len(results),
                    "results": results,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        conn.close()


def cmd_disease(args: argparse.Namespace):
    db = Path(args.db).expanduser().resolve()
    conn = connect(db)
    try:
        detail = disease_detail(conn, args.disease_uid)
        if not detail:
            print(json.dumps({"error": f"not found: {args.disease_uid}"}, ensure_ascii=False, indent=2))
            raise SystemExit(1)
        print(json.dumps(detail, ensure_ascii=False, indent=2))
    finally:
        conn.close()


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Query helper for rd_ontology SQLite")
    sp = p.add_subparsers(dest="command", required=True)

    p_resolve = sp.add_parser("resolve", help="Normalize query + search")
    p_resolve.add_argument("query")
    p_resolve.add_argument("--limit", type=int, default=10)
    p_resolve.add_argument("--db", default=str(DEFAULT_DB))
    p_resolve.set_defaults(func=cmd_resolve)

    p_disease = sp.add_parser("disease", help="Get detailed disease record")
    p_disease.add_argument("disease_uid")
    p_disease.add_argument("--db", default=str(DEFAULT_DB))
    p_disease.set_defaults(func=cmd_disease)
    return p


def main():
    args = parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
