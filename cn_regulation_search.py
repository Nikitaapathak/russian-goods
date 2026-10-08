"""Extract and search CN-code references from a saved EUR-Lex regulation."""

from __future__ import annotations

import argparse
import html
import json
import re
import sqlite3
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin


DEFAULT_HTML = Path("Consolidated TEXT_ 32014R0833 — EN — 24.07.2026.html")
DEFAULT_DB = Path("cn_regulation.db")
DEFAULT_JSON = Path("cn_regulation.json")
CN_CONTEXT_RE = re.compile(r"\bCN\s+codes?\b", re.IGNORECASE)
CN_VALUE_RE = re.compile(r"(?<!\d)(?:ex\s+)?\d{4}(?:\s+\d{2}){0,2}(?!\d)", re.IGNORECASE)
TABLE_CODE_RE = re.compile(r"^(ex\s+)?(\d{4}(?:\s+\d{2}){0,2})$", re.IGNORECASE)
REFERRED_ARTICLE_RE = re.compile(r"\bArticle\s+[0-9]+[a-z]*\b", re.IGNORECASE)


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def normalize_code(value: str) -> str:
    return re.sub(r"\D", "", value)


class RegulationParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.canonical_url = ""
        self.section_stack: list[tuple[str, str]] = []
        self.current_section: tuple[str, str] | None = None
        self.current_block: dict | None = None
        self.current_row: dict | None = None
        self.blocks: list[dict] = []
        self.rows: list[dict] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        element_id = attributes.get("id", "") or ""
        if tag == "link" and attributes.get("rel") == "canonical":
            self.canonical_url = attributes.get("href", "") or ""
        if element_id.startswith(("art_", "anx_")):
            self.current_section = (element_id, "")
            self.section_stack.append(self.current_section)
        if tag in {"p", "div"} and self.current_section and self.current_block is None:
            self.current_block = {"section": self.current_section, "text": [], "links": []}
        if tag == "tr":
            self.current_row = {"section": self.current_section, "cells": [], "links": []}
        elif tag in {"td", "th"} and self.current_row is not None:
            self.current_row["cells"].append([])
        if tag == "a" and attributes.get("href"):
            if self.current_row is not None:
                self.current_row["links"].append(attributes["href"])
            if self.current_block is not None:
                self.current_block["links"].append(attributes["href"])

    def handle_endtag(self, tag: str) -> None:
        if tag in {"p", "div"} and self.current_block is not None:
            text = clean_text(" ".join(self.current_block["text"]))
            if text:
                self.current_block["text"] = text
                self.blocks.append(self.current_block)
            self.current_block = None
        if tag == "tr" and self.current_row is not None:
            self.current_row["cells"] = [clean_text(" ".join(cell)) for cell in self.current_row["cells"]]
            if any(self.current_row["cells"]):
                self.rows.append(self.current_row)
            self.current_row = None
        if tag == "div" and self.section_stack:
            # EUR-Lex article and annex containers are div elements.
            section_id = self.section_stack[-1][0]
            if self.get_starttag_text() and f'id="{section_id}"' in self.get_starttag_text():
                self.section_stack.pop()
                self.current_section = self.section_stack[-1] if self.section_stack else None

    def handle_data(self, data: str) -> None:
        if self.current_block is not None:
            self.current_block["text"].append(data)
        if self.current_row is not None and self.current_row["cells"]:
            self.current_row["cells"][-1].append(data)


def section_label(section_id: str) -> str:
    if section_id.startswith("art_"):
        return f"Article {section_id.removeprefix('art_')}"
    return f"Annex {section_id.removeprefix('anx_').replace('_', ' ')}"


def source_link(base_url: str, section_id: str) -> str:
    return f"{base_url}#{section_id}" if base_url else f"#{section_id}"


def extract_records(html_path: Path) -> tuple[str, list[dict]]:
    parser = RegulationParser()
    parser.feed(html_path.read_text(encoding="utf-8"))
    records: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    section_metadata: dict[str, dict] = {}

    for block in parser.blocks:
        section = block["section"]
        text = block["text"]
        if not section or not section[0].startswith("anx_"):
            continue
        if text.upper() == section_label(section[0]).upper():
            continue
        if text.lower().startswith("list of ") and "referred to in article" in text.lower():
            section_metadata[section[0]] = {
                "section_title": text,
                "referred_articles": REFERRED_ARTICLE_RE.findall(text),
            }

    for block in parser.blocks:
        section = block["section"]
        if not section or not section[0].startswith("art_") or not CN_CONTEXT_RE.search(block["text"]):
            continue
        for match in CN_VALUE_RE.finditer(block["text"]):
            raw = clean_text(match.group())
            key = (normalize_code(raw), section[0], block["text"])
            if key not in seen:
                seen.add(key)
                records.append(make_record(raw, section[0], block["text"], block["links"], parser.canonical_url, "article"))

    for row in parser.rows:
        if not row["section"] or not row["section"][0].startswith("anx_") or not row["cells"]:
            continue
        match = TABLE_CODE_RE.fullmatch(row["cells"][0])
        if not match:
            continue
        raw = clean_text(match.group())
        details = " | ".join(cell for cell in row["cells"] if cell)
        key = (normalize_code(raw), row["section"][0], details)
        if key not in seen:
            seen.add(key)
            records.append(
                make_record(
                    raw,
                    row["section"][0],
                    details,
                    row["links"],
                    parser.canonical_url,
                    "annex",
                    section_metadata.get(row["section"][0]),
                )
            )

    return parser.canonical_url, records


def make_record(
    raw: str,
    section_id: str,
    details: str,
    links: list[str],
    base_url: str,
    kind: str,
    metadata: dict | None = None,
) -> dict:
    metadata = metadata or {}
    return {
        "cn_code": normalize_code(raw),
        "display_code": re.sub(r"^ex\s+", "", raw, flags=re.IGNORECASE),
        "is_ex": bool(re.match(r"^ex\b", raw, re.IGNORECASE)),
        "section": section_label(section_id),
        "section_id": section_id,
        "section_title": metadata.get("section_title", ""),
        "referred_articles": metadata.get("referred_articles", []),
        "record_type": kind,
        "details": details,
        "source_url": source_link(base_url, section_id),
        "related_links": sorted({urljoin(base_url, link) for link in links if link and not link.startswith("#")}),
    }


def write_outputs(records: list[dict], json_path: Path, db_path: Path) -> None:
    json_path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    with sqlite3.connect(db_path) as connection:
        connection.execute("DROP TABLE IF EXISTS cn_records")
        connection.execute(
            """CREATE TABLE cn_records (
                id INTEGER PRIMARY KEY, cn_code TEXT NOT NULL, display_code TEXT NOT NULL,
                is_ex INTEGER NOT NULL, section TEXT NOT NULL, section_id TEXT NOT NULL,
                section_title TEXT NOT NULL, referred_articles TEXT NOT NULL,
                record_type TEXT NOT NULL, details TEXT NOT NULL, source_url TEXT NOT NULL,
                related_links TEXT NOT NULL
            )"""
        )
        connection.executemany(
            """INSERT INTO cn_records
               (cn_code, display_code, is_ex, section, section_id, section_title, referred_articles,
                record_type, details, source_url, related_links)
               VALUES (:cn_code, :display_code, :is_ex, :section, :section_id, :section_title, :referred_articles,
                       :record_type, :details, :source_url, :related_links)""",
            [
                {
                    **record,
                    "is_ex": int(record["is_ex"]),
                    "referred_articles": json.dumps(record["referred_articles"]),
                    "related_links": json.dumps(record["related_links"]),
                }
                for record in records
            ],
        )
        connection.execute("CREATE INDEX idx_cn_code ON cn_records(cn_code)")


def search(db_path: Path, query: str, ex_only: bool) -> list[sqlite3.Row]:
    code = normalize_code(query)
    if len(code) < 4:
        raise ValueError("Enter at least the first 4 digits of a CN code.")
    prefixes = [code[:4]]
    if len(code) >= 6:
        prefixes.append(code[:6])
    connection = sqlite3.connect(db_path)
    try:
        connection.row_factory = sqlite3.Row
        prefix_conditions = " OR ".join("cn_code LIKE ?" for _ in prefixes)
        sql = f"SELECT * FROM cn_records WHERE ({prefix_conditions})"
        params: list[object] = [f"{prefix}%" for prefix in prefixes]
        if ex_only or query.strip().lower().startswith("ex"):
            sql += " AND is_ex = 1"
        sql += " ORDER BY cn_code, section, id"
        rows = connection.execute(sql, params).fetchall()
        return rows
    finally:
        connection.close()


def main() -> None:
    argument_parser = argparse.ArgumentParser(description=__doc__)
    argument_parser.add_argument("query", nargs="?", help="CN code or prefix, optionally starting with 'ex'")
    argument_parser.add_argument("--html", type=Path, default=DEFAULT_HTML)
    argument_parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    argument_parser.add_argument("--json", type=Path, default=DEFAULT_JSON)
    argument_parser.add_argument("--build", action="store_true", help="Rebuild JSON and SQLite data")
    argument_parser.add_argument("--ex-only", action="store_true", help="Only return entries explicitly marked ex")
    args = argument_parser.parse_args()

    if args.build or not args.db.exists():
        _, records = extract_records(args.html)
        write_outputs(records, args.json, args.db)
        print(f"Built {len(records)} records in {args.db} and {args.json}")
    if args.query:
        for row in search(args.db, args.query, args.ex_only):
            marker = "ex " if row["is_ex"] else ""
            print(f"\n{marker}{row['display_code']} — {row['section']}")
            print(row["details"])
            print(row["source_url"])


if __name__ == "__main__":
    main()
