#!/usr/bin/env python3
"""
Build local rare-disease ontology database (SQLite) from offline sources.

Inputs (default under ~/.pantheon/rd_ontology/data):
  - orphanet/en_product1.xml  (disease + synonyms + xrefs)
  - orphanet/en_product4.xml  (disease-HPO associations)
  - orphanet/en_product6.xml  (disease-gene associations)
  - hpo/hp.obo                (HPO terms)
  - hpo/phenotype.hpoa        (disease-HPO annotations)
  - omim/mim2gene.txt         (OMIM-gene mapping)

Output: ~/.pantheon/rd_ontology/rd_ontology.sqlite

Examples:
  python build_rd_ontology.py build --reset
  python build_rd_ontology.py stats
  python build_rd_ontology.py search "Marfan syndrome"
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Iterable, Optional


def default_rd_dir() -> Path:
    return Path.home() / ".pantheon" / "rd_ontology"


DEFAULT_INPUT_DIR = Path.home() / ".pantheon" / "rd_ontology" / "data"
DEFAULT_OUTPUT_DB = Path.home() / ".pantheon" / "rd_ontology" / "rd_ontology.sqlite"


def clean_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    s = value.strip()
    return s if s else None


def child_text(elem: ET.Element, path: str) -> Optional[str]:
    node = elem.find(path)
    if node is None:
        return None
    return clean_text(node.text)


def iter_disorders(xml_path: Path) -> Iterable[ET.Element]:
    # Streaming parser to keep memory stable for large XML files.
    context = ET.iterparse(xml_path, events=("start", "end"))
    _, root = next(context)
    for event, elem in context:
        if event == "end" and elem.tag == "Disorder":
            yield elem
            root.clear()


class RdOntologyBuilder:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA foreign_keys=ON")

    def close(self):
        self.conn.close()

    def reset(self):
        self.conn.executescript(
            """
            DROP TABLE IF EXISTS source_meta;
            DROP TABLE IF EXISTS disease_aliases;
            DROP TABLE IF EXISTS disease_xrefs;
            DROP TABLE IF EXISTS phenotype_assoc;
            DROP TABLE IF EXISTS gene_assoc;
            DROP TABLE IF EXISTS hpo_terms;
            DROP TABLE IF EXISTS diseases;
            """
        )
        self.conn.commit()

    def create_schema(self):
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS diseases (
              disease_uid TEXT PRIMARY KEY,
              canonical_name TEXT,
              primary_source TEXT,
              primary_id TEXT,
              definition TEXT,
              expert_link TEXT,
              raw_payload TEXT,
              created_at INTEGER DEFAULT (strftime('%s','now')),
              updated_at INTEGER DEFAULT (strftime('%s','now'))
            );

            CREATE TABLE IF NOT EXISTS disease_aliases (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              disease_uid TEXT NOT NULL,
              alias TEXT NOT NULL,
              alias_type TEXT,
              source TEXT,
              UNIQUE(disease_uid, alias, source)
            );

            CREATE TABLE IF NOT EXISTS disease_xrefs (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              disease_uid TEXT NOT NULL,
              xref_db TEXT NOT NULL,
              xref_id TEXT NOT NULL,
              source TEXT,
              UNIQUE(disease_uid, xref_db, xref_id, source)
            );

            CREATE TABLE IF NOT EXISTS hpo_terms (
              hpo_id TEXT PRIMARY KEY,
              label TEXT,
              definition TEXT,
              synonyms_json TEXT,
              parents_json TEXT
            );

            CREATE TABLE IF NOT EXISTS phenotype_assoc (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              disease_uid TEXT NOT NULL,
              hpo_id TEXT,
              hpo_term TEXT,
              qualifier TEXT,
              evidence TEXT,
              onset TEXT,
              frequency_label TEXT,
              frequency_id TEXT,
              sex TEXT,
              modifier TEXT,
              aspect TEXT,
              reference TEXT,
              source TEXT,
              raw_payload TEXT
            );

            CREATE TABLE IF NOT EXISTS gene_assoc (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              disease_uid TEXT NOT NULL,
              gene_symbol TEXT,
              gene_name TEXT,
              association_type TEXT,
              association_status TEXT,
              source_of_validation TEXT,
              entrez_id TEXT,
              ensembl_id TEXT,
              hgnc_id TEXT,
              omim_gene_id TEXT,
              source TEXT,
              raw_payload TEXT
            );

            CREATE TABLE IF NOT EXISTS source_meta (
              source_name TEXT PRIMARY KEY,
              source_path TEXT,
              version TEXT,
              loaded_at INTEGER,
              row_count INTEGER
            );

            CREATE INDEX IF NOT EXISTS idx_diseases_name ON diseases(canonical_name);
            CREATE INDEX IF NOT EXISTS idx_alias_text ON disease_aliases(alias);
            CREATE INDEX IF NOT EXISTS idx_xrefs_lookup ON disease_xrefs(xref_db, xref_id);
            CREATE INDEX IF NOT EXISTS idx_hpo_label ON hpo_terms(label);
            CREATE INDEX IF NOT EXISTS idx_pa_disease ON phenotype_assoc(disease_uid);
            CREATE INDEX IF NOT EXISTS idx_pa_hpo ON phenotype_assoc(hpo_id);
            CREATE INDEX IF NOT EXISTS idx_gene_disease ON gene_assoc(disease_uid);
            CREATE INDEX IF NOT EXISTS idx_gene_symbol ON gene_assoc(gene_symbol);
            """
        )
        self.conn.commit()

    def upsert_disease(
        self,
        disease_uid: str,
        canonical_name: Optional[str] = None,
        primary_source: Optional[str] = None,
        primary_id: Optional[str] = None,
        definition: Optional[str] = None,
        expert_link: Optional[str] = None,
        raw_payload: Optional[dict] = None,
    ):
        self.conn.execute(
            """
            INSERT OR IGNORE INTO diseases
            (disease_uid, canonical_name, primary_source, primary_id, definition, expert_link, raw_payload)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                disease_uid,
                canonical_name,
                primary_source,
                primary_id,
                definition,
                expert_link,
                json.dumps(raw_payload, ensure_ascii=False) if raw_payload else None,
            ),
        )

        # Fill blanks only; do not overwrite existing non-empty values.
        if canonical_name:
            self.conn.execute(
                """
                UPDATE diseases
                SET canonical_name = CASE
                    WHEN canonical_name IS NULL OR canonical_name = '' THEN ?
                    ELSE canonical_name END,
                    updated_at = strftime('%s','now')
                WHERE disease_uid = ?
                """,
                (canonical_name, disease_uid),
            )
        if primary_source:
            self.conn.execute(
                """
                UPDATE diseases
                SET primary_source = COALESCE(primary_source, ?),
                    updated_at = strftime('%s','now')
                WHERE disease_uid = ?
                """,
                (primary_source, disease_uid),
            )
        if primary_id:
            self.conn.execute(
                """
                UPDATE diseases
                SET primary_id = COALESCE(primary_id, ?),
                    updated_at = strftime('%s','now')
                WHERE disease_uid = ?
                """,
                (primary_id, disease_uid),
            )
        if definition:
            self.conn.execute(
                """
                UPDATE diseases
                SET definition = COALESCE(definition, ?),
                    updated_at = strftime('%s','now')
                WHERE disease_uid = ?
                """,
                (definition, disease_uid),
            )
        if expert_link:
            self.conn.execute(
                """
                UPDATE diseases
                SET expert_link = COALESCE(expert_link, ?),
                    updated_at = strftime('%s','now')
                WHERE disease_uid = ?
                """,
                (expert_link, disease_uid),
            )

    def insert_alias(
        self, disease_uid: str, alias: Optional[str], alias_type: str, source: str
    ):
        alias = clean_text(alias)
        if not alias:
            return
        self.conn.execute(
            """
            INSERT OR IGNORE INTO disease_aliases(disease_uid, alias, alias_type, source)
            VALUES (?, ?, ?, ?)
            """,
            (disease_uid, alias, alias_type, source),
        )

    def insert_xref(
        self, disease_uid: str, xref_db: Optional[str], xref_id: Optional[str], source: str
    ):
        xref_db = clean_text(xref_db)
        xref_id = clean_text(xref_id)
        if not xref_db or not xref_id:
            return
        self.conn.execute(
            """
            INSERT OR IGNORE INTO disease_xrefs(disease_uid, xref_db, xref_id, source)
            VALUES (?, ?, ?, ?)
            """,
            (disease_uid, xref_db, xref_id, source),
        )

    def parse_hpo_obo(self, hp_obo: Path):
        print(f"[build] HPO terms: {hp_obo}")
        current = None
        rows = 0

        def flush_term(term: dict):
            nonlocal rows
            if not term or "id" not in term:
                return
            self.conn.execute(
                """
                INSERT OR REPLACE INTO hpo_terms(hpo_id, label, definition, synonyms_json, parents_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    term["id"],
                    term.get("name"),
                    term.get("definition"),
                    json.dumps(term.get("synonyms", []), ensure_ascii=False),
                    json.dumps(term.get("parents", []), ensure_ascii=False),
                ),
            )
            rows += 1

        synonym_re = re.compile(r'^synonym:\s+"(.+?)"')
        parent_re = re.compile(r"^is_a:\s+(HP:\d+)")
        def_re = re.compile(r'^def:\s+"(.+?)"')

        with hp_obo.open("r", encoding="utf-8", errors="ignore") as f:
            for raw in f:
                line = raw.rstrip("\n")
                if line == "[Term]":
                    flush_term(current)
                    current = {"synonyms": [], "parents": []}
                    continue
                if not current:
                    continue
                if line.startswith("id: "):
                    current["id"] = line[4:].strip()
                elif line.startswith("name: "):
                    current["name"] = line[6:].strip()
                elif line.startswith("def: "):
                    m = def_re.search(line)
                    if m:
                        current["definition"] = m.group(1)
                elif line.startswith("synonym: "):
                    m = synonym_re.search(line)
                    if m:
                        current["synonyms"].append(m.group(1))
                elif line.startswith("is_a: "):
                    m = parent_re.search(line)
                    if m:
                        current["parents"].append(m.group(1))
            flush_term(current)

        self.conn.execute(
            """
            INSERT OR REPLACE INTO source_meta(source_name, source_path, version, loaded_at, row_count)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("hpo_hp_obo", str(hp_obo), None, int(time.time()), rows),
        )
        self.conn.commit()
        print(f"[done] HPO terms loaded: {rows}")

    def parse_hpoa(self, hpoa: Path):
        print(f"[build] HPOA annotations: {hpoa}")
        rows = 0
        with hpoa.open("r", encoding="utf-8", errors="ignore") as f:
            data_lines = (line for line in f if line and not line.startswith("#"))
            reader = csv.DictReader(data_lines, delimiter="\t")
            for rec in reader:
                disease_uid = clean_text(rec.get("database_id"))
                disease_name = clean_text(rec.get("disease_name"))
                hpo_id = clean_text(rec.get("hpo_id"))
                if not disease_uid:
                    continue

                db_prefix = disease_uid.split(":", 1)[0] if ":" in disease_uid else "UNKNOWN"
                db_id = disease_uid.split(":", 1)[1] if ":" in disease_uid else disease_uid

                self.upsert_disease(
                    disease_uid=disease_uid,
                    canonical_name=disease_name,
                    primary_source="hpoa",
                    primary_id=db_id,
                )
                self.insert_alias(disease_uid, disease_name, "canonical", "hpoa")
                self.insert_xref(disease_uid, db_prefix, db_id, "hpoa")

                self.conn.execute(
                    """
                    INSERT INTO phenotype_assoc(
                      disease_uid, hpo_id, hpo_term, qualifier, evidence, onset,
                      frequency_label, frequency_id, sex, modifier, aspect, reference, source, raw_payload
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        disease_uid,
                        hpo_id,
                        None,  # filled in post-process from hpo_terms
                        clean_text(rec.get("qualifier")),
                        clean_text(rec.get("evidence")),
                        clean_text(rec.get("onset")),
                        clean_text(rec.get("frequency")),
                        None,
                        clean_text(rec.get("sex")),
                        clean_text(rec.get("modifier")),
                        clean_text(rec.get("aspect")),
                        clean_text(rec.get("reference")),
                        "hpoa",
                        json.dumps(
                            {
                                "biocuration": clean_text(rec.get("biocuration")),
                                "disease_name": disease_name,
                            },
                            ensure_ascii=False,
                        ),
                    ),
                )
                rows += 1
                if rows % 50000 == 0:
                    self.conn.commit()
                    print(f"  ... hpoa rows={rows}")

        self.conn.execute(
            """
            INSERT OR REPLACE INTO source_meta(source_name, source_path, version, loaded_at, row_count)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("hpo_phenotype_hpoa", str(hpoa), None, int(time.time()), rows),
        )
        self.conn.commit()
        print(f"[done] HPOA annotations loaded: {rows}")

    def parse_orphanet_product1(self, path: Path):
        print(f"[build] Orphanet product1: {path}")
        rows = 0
        for disorder in iter_disorders(path):
            orpha_code = child_text(disorder, "OrphaCode")
            if not orpha_code:
                continue
            disease_uid = f"ORPHA:{orpha_code}"
            name = child_text(disorder, "Name")
            expert_link = child_text(disorder, "ExpertLink")

            self.upsert_disease(
                disease_uid=disease_uid,
                canonical_name=name,
                primary_source="orphanet_product1",
                primary_id=orpha_code,
                expert_link=expert_link,
                raw_payload={"xml_id": disorder.attrib.get("id")},
            )
            self.insert_alias(disease_uid, name, "canonical", "orphanet_product1")

            for syn in disorder.findall("./SynonymList/Synonym"):
                self.insert_alias(
                    disease_uid,
                    clean_text(syn.text),
                    "synonym",
                    "orphanet_product1",
                )

            for x in disorder.findall("./ExternalReferenceList/ExternalReference"):
                self.insert_xref(
                    disease_uid=disease_uid,
                    xref_db=child_text(x, "Source"),
                    xref_id=child_text(x, "Reference"),
                    source="orphanet_product1",
                )

            rows += 1
            if rows % 2000 == 0:
                self.conn.commit()
                print(f"  ... product1 disorders={rows}")

        self.conn.execute(
            """
            INSERT OR REPLACE INTO source_meta(source_name, source_path, version, loaded_at, row_count)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("orphanet_product1", str(path), None, int(time.time()), rows),
        )
        self.conn.commit()
        print(f"[done] Orphanet product1 disorders loaded: {rows}")

    def parse_orphanet_product4(self, path: Path):
        print(f"[build] Orphanet product4 (HPO links): {path}")
        rows = 0
        for disorder in iter_disorders(path):
            orpha_code = child_text(disorder, "OrphaCode")
            if not orpha_code:
                continue
            disease_uid = f"ORPHA:{orpha_code}"
            name = child_text(disorder, "Name")
            self.upsert_disease(
                disease_uid=disease_uid,
                canonical_name=name,
                primary_source="orphanet_product4",
                primary_id=orpha_code,
            )
            self.insert_alias(disease_uid, name, "canonical", "orphanet_product4")

            for assoc in disorder.findall("./HPODisorderAssociationList/HPODisorderAssociation"):
                hpo_id = child_text(assoc, "HPO/HPOId")
                hpo_term = child_text(assoc, "HPO/HPOTerm")
                freq_node = assoc.find("HPOFrequency")
                freq_label = child_text(assoc, "HPOFrequency/Name")
                freq_id = freq_node.attrib.get("id") if freq_node is not None else None
                diagnostic = child_text(assoc, "DiagnosticCriteria/Name") or child_text(
                    assoc, "DiagnosticCriteria"
                )
                evidence = child_text(assoc, "ValidationStatus/Name")
                reference = child_text(assoc, "Source")

                self.conn.execute(
                    """
                    INSERT INTO phenotype_assoc(
                      disease_uid, hpo_id, hpo_term, qualifier, evidence, onset,
                      frequency_label, frequency_id, sex, modifier, aspect, reference, source, raw_payload
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        disease_uid,
                        hpo_id,
                        hpo_term,
                        None,
                        evidence,
                        None,
                        freq_label,
                        clean_text(freq_id),
                        None,
                        diagnostic,
                        None,
                        reference,
                        "orphanet_product4",
                        None,
                    ),
                )
                rows += 1

            if rows and rows % 50000 == 0:
                self.conn.commit()
                print(f"  ... product4 phenotype_assoc={rows}")

        self.conn.execute(
            """
            INSERT OR REPLACE INTO source_meta(source_name, source_path, version, loaded_at, row_count)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("orphanet_product4", str(path), None, int(time.time()), rows),
        )
        self.conn.commit()
        print(f"[done] Orphanet product4 phenotype associations loaded: {rows}")

    def parse_orphanet_product6(self, path: Path):
        print(f"[build] Orphanet product6 (gene links): {path}")
        rows = 0
        for disorder in iter_disorders(path):
            orpha_code = child_text(disorder, "OrphaCode")
            if not orpha_code:
                continue
            disease_uid = f"ORPHA:{orpha_code}"
            name = child_text(disorder, "Name")
            self.upsert_disease(
                disease_uid=disease_uid,
                canonical_name=name,
                primary_source="orphanet_product6",
                primary_id=orpha_code,
            )
            self.insert_alias(disease_uid, name, "canonical", "orphanet_product6")

            for assoc in disorder.findall("./DisorderGeneAssociationList/DisorderGeneAssociation"):
                source_of_validation = child_text(assoc, "SourceOfValidation")
                assoc_type = child_text(assoc, "DisorderGeneAssociationType/Name")
                assoc_status = child_text(assoc, "DisorderGeneAssociationStatus/Name")
                gene = assoc.find("Gene")
                if gene is None:
                    continue
                gene_symbol = child_text(gene, "Symbol")
                gene_name = child_text(gene, "Name")

                entrez_id = None
                ensembl_id = None
                hgnc_id = None
                omim_gene_id = None
                xrefs_payload = []
                for x in gene.findall("./ExternalReferenceList/ExternalReference"):
                    src = clean_text(child_text(x, "Source"))
                    ref = clean_text(child_text(x, "Reference"))
                    if not src or not ref:
                        continue
                    src_l = src.lower()
                    if "ensembl" in src_l:
                        ensembl_id = ref
                    elif "hgnc" in src_l:
                        hgnc_id = ref
                    elif src_l == "omim":
                        omim_gene_id = ref
                    elif "entrez" in src_l or "ncbi gene" in src_l:
                        entrez_id = ref
                    xrefs_payload.append({"source": src, "reference": ref})

                self.conn.execute(
                    """
                    INSERT INTO gene_assoc(
                      disease_uid, gene_symbol, gene_name, association_type, association_status,
                      source_of_validation, entrez_id, ensembl_id, hgnc_id, omim_gene_id, source, raw_payload
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        disease_uid,
                        gene_symbol,
                        gene_name,
                        assoc_type,
                        assoc_status,
                        source_of_validation,
                        entrez_id,
                        ensembl_id,
                        hgnc_id,
                        omim_gene_id,
                        "orphanet_product6",
                        json.dumps({"gene_xrefs": xrefs_payload}, ensure_ascii=False),
                    ),
                )
                rows += 1

            if rows and rows % 50000 == 0:
                self.conn.commit()
                print(f"  ... product6 gene_assoc={rows}")

        self.conn.execute(
            """
            INSERT OR REPLACE INTO source_meta(source_name, source_path, version, loaded_at, row_count)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("orphanet_product6", str(path), None, int(time.time()), rows),
        )
        self.conn.commit()
        print(f"[done] Orphanet product6 gene associations loaded: {rows}")

    def parse_omim_mim2gene(self, path: Path):
        print(f"[build] OMIM mim2gene: {path}")
        rows = 0
        with path.open("r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if not line.strip() or line.startswith("#"):
                    continue
                parts = line.rstrip("\n").split("\t")
                if len(parts) < 5:
                    continue
                mim_number, entry_type, entrez_gene_id, gene_symbol, ensembl_id = parts[:5]
                mim_number = clean_text(mim_number)
                if not mim_number:
                    continue
                disease_uid = f"OMIM:{mim_number}"
                self.upsert_disease(
                    disease_uid=disease_uid,
                    canonical_name=None,
                    primary_source="omim_mim2gene",
                    primary_id=mim_number,
                )
                self.insert_xref(disease_uid, "OMIM", mim_number, "omim_mim2gene")
                self.conn.execute(
                    """
                    INSERT INTO gene_assoc(
                      disease_uid, gene_symbol, gene_name, association_type, association_status,
                      source_of_validation, entrez_id, ensembl_id, hgnc_id, omim_gene_id, source, raw_payload
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        disease_uid,
                        clean_text(gene_symbol),
                        None,
                        clean_text(entry_type),
                        None,
                        None,
                        clean_text(entrez_gene_id),
                        clean_text(ensembl_id),
                        None,
                        mim_number,
                        "omim_mim2gene",
                        None,
                    ),
                )
                rows += 1
                if rows % 50000 == 0:
                    self.conn.commit()
                    print(f"  ... omim rows={rows}")

        self.conn.execute(
            """
            INSERT OR REPLACE INTO source_meta(source_name, source_path, version, loaded_at, row_count)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("omim_mim2gene", str(path), None, int(time.time()), rows),
        )
        self.conn.commit()
        print(f"[done] OMIM mim2gene rows loaded: {rows}")

    def post_process(self):
        print("[build] post-process derived links / labels")
        # fill missing HPO labels in phenotype_assoc from hpo_terms
        self.conn.execute(
            """
            UPDATE phenotype_assoc
            SET hpo_term = (
                SELECT label FROM hpo_terms t WHERE t.hpo_id = phenotype_assoc.hpo_id
            )
            WHERE (hpo_term IS NULL OR hpo_term = '')
              AND hpo_id IS NOT NULL
            """
        )

        # Derive reciprocal ORPHA links for OMIM entries when orphanet xref has OMIM
        self.conn.execute(
            """
            INSERT OR IGNORE INTO diseases(disease_uid, primary_source, primary_id)
            SELECT DISTINCT 'OMIM:' || xref_id, 'derived_orphanet_omim', xref_id
            FROM disease_xrefs
            WHERE UPPER(xref_db) = 'OMIM' AND disease_uid LIKE 'ORPHA:%'
            """
        )
        self.conn.execute(
            """
            INSERT OR IGNORE INTO disease_xrefs(disease_uid, xref_db, xref_id, source)
            SELECT DISTINCT 'OMIM:' || x.xref_id, 'ORPHA', substr(x.disease_uid, 7), 'derived_from_orphanet_xref'
            FROM disease_xrefs x
            WHERE UPPER(x.xref_db) = 'OMIM' AND x.disease_uid LIKE 'ORPHA:%'
            """
        )
        self.conn.commit()
        print("[done] post-process complete")

    def print_stats(self):
        cur = self.conn.cursor()
        tables = [
            "diseases",
            "disease_aliases",
            "disease_xrefs",
            "hpo_terms",
            "phenotype_assoc",
            "gene_assoc",
        ]
        print("\n=== rd_ontology stats ===")
        for t in tables:
            n = cur.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            print(f"{t:16s}: {n}")

        print("\nTop xref dbs:")
        for row in cur.execute(
            """
            SELECT UPPER(xref_db) AS db, COUNT(*) AS n
            FROM disease_xrefs GROUP BY UPPER(xref_db)
            ORDER BY n DESC LIMIT 10
            """
        ):
            print(f"  {row['db']}: {row['n']}")

    def search(self, query: str, limit: int = 10):
        cur = self.conn.cursor()
        q = f"%{query}%"
        rows = cur.execute(
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

        if not rows:
            print(f"No match for query={query!r}")
            return

        print(f"\nSearch results for {query!r}:")
        for i, row in enumerate(rows, 1):
            print(
                f"{i:2d}. {row['disease_uid']} | {row['canonical_name'] or '<no_name>'} "
                f"| phenotypes={row['phenotype_count']} genes={row['gene_count']}"
            )
            xrefs = cur.execute(
                """
                SELECT xref_db, xref_id
                FROM disease_xrefs
                WHERE disease_uid = ?
                ORDER BY xref_db, xref_id
                LIMIT 8
                """,
                (row["disease_uid"],),
            ).fetchall()
            if xrefs:
                joined = ", ".join([f"{x['xref_db']}:{x['xref_id']}" for x in xrefs])
                print(f"    xrefs: {joined}")


def cmd_build(args: argparse.Namespace):
    input_dir = Path(args.input_dir).expanduser().resolve()
    out_db = Path(args.output_db).expanduser().resolve()
    b = RdOntologyBuilder(out_db)
    try:
        if args.reset:
            b.reset()
        b.create_schema()

        hp_obo = input_dir / "hpo" / "hp.obo"
        hpoa = input_dir / "hpo" / "phenotype.hpoa"
        p1 = input_dir / "orphanet" / "en_product1.xml"
        p4 = input_dir / "orphanet" / "en_product4.xml"
        p6 = input_dir / "orphanet" / "en_product6.xml"
        mim2gene = input_dir / "omim" / "mim2gene.txt"

        missing = [p for p in [hp_obo, hpoa, p1, p4, p6, mim2gene] if not p.exists()]
        if missing:
            print("[error] missing required input files:")
            for p in missing:
                print("  -", p)
            raise SystemExit(1)

        b.parse_hpo_obo(hp_obo)
        b.parse_orphanet_product1(p1)
        b.parse_orphanet_product4(p4)
        b.parse_orphanet_product6(p6)
        b.parse_hpoa(hpoa)
        b.parse_omim_mim2gene(mim2gene)
        b.post_process()
        b.print_stats()

        if args.smoke_query:
            b.search(args.smoke_query, limit=10)

        print(f"\n[DONE] rd_ontology db ready: {out_db}")
    finally:
        b.close()


def cmd_stats(args: argparse.Namespace):
    db = Path(args.output_db).expanduser().resolve()
    if not db.exists():
        print(f"[error] db not found: {db}")
        raise SystemExit(1)
    b = RdOntologyBuilder(db)
    try:
        b.print_stats()
    finally:
        b.close()


def cmd_search(args: argparse.Namespace):
    db = Path(args.output_db).expanduser().resolve()
    if not db.exists():
        print(f"[error] db not found: {db}")
        raise SystemExit(1)
    b = RdOntologyBuilder(db)
    try:
        b.search(args.query, limit=args.limit)
    finally:
        b.close()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Build/query local rare disease ontology SQLite")
    sp = p.add_subparsers(dest="command", required=True)

    p_build = sp.add_parser("build", help="Build rd_ontology SQLite from offline files")
    p_build.add_argument("--input-dir", default=str(DEFAULT_INPUT_DIR))
    p_build.add_argument("--output-db", default=str(DEFAULT_OUTPUT_DB))
    p_build.add_argument("--reset", action="store_true")
    p_build.add_argument("--smoke-query", default="Marfan syndrome")
    p_build.set_defaults(func=cmd_build)

    p_stats = sp.add_parser("stats", help="Show db stats")
    p_stats.add_argument("--output-db", default=str(DEFAULT_OUTPUT_DB))
    p_stats.set_defaults(func=cmd_stats)

    p_search = sp.add_parser("search", help="Search disease by name/alias")
    p_search.add_argument("query")
    p_search.add_argument("--limit", type=int, default=10)
    p_search.add_argument("--output-db", default=str(DEFAULT_OUTPUT_DB))
    p_search.set_defaults(func=cmd_search)

    return p


def main():
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

