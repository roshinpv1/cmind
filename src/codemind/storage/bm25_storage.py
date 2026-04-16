"""
SQLite FTS5-backed BM25 retrieval for indexed code chunks.

This provides true lexical ranking (bm25) over chunk text, separate from
regex scans and vector similarity retrieval.
"""

from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path


class BM25Storage:
    """Manage FTS5 index used for lexical BM25 retrieval."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        if db_path is None:
            base_default = os.getenv("CODEMIND_BASE_PATH", "./tmp/")
            db_path = os.getenv("CODEMIND_DB_PATH", os.path.join(base_default, "codemind.db"))
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=5.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS code_chunks_fts
                USING fts5(
                  repo_id UNINDEXED,
                  file_path UNINDEXED,
                  chunk_hash UNINDEXED,
                  start_line UNINDEXED,
                  end_line UNINDEXED,
                  language UNINDEXED,
                  symbol_name UNINDEXED,
                  chunk_text,
                  tokenize='unicode61 remove_diacritics 2'
                )
                """
            )
            conn.commit()

    @staticmethod
    def _fts_query(text: str) -> str:
        tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]{1,}", (text or "").lower())
        if not tokens:
            return ""
        # OR query improves recall; bm25 will rank by density.
        return " OR ".join(dict.fromkeys(tokens[:14]))

    def delete_by_files(self, repo_id: str, file_paths: list[str]) -> int:
        if not repo_id or not file_paths:
            return 0
        removed = 0
        with self._connect() as conn:
            for fp in file_paths:
                if not fp:
                    continue
                cur = conn.execute(
                    "DELETE FROM code_chunks_fts WHERE repo_id = ? AND file_path = ?",
                    (repo_id, fp),
                )
                removed += int(cur.rowcount or 0)
            conn.commit()
        return removed

    def upsert_chunks(self, repo_id: str, rows: list[dict]) -> int:
        if not repo_id or not rows:
            return 0
        payload: list[tuple] = []
        for row in rows:
            chunk_text = str(row.get("chunk_text") or "").strip()
            file_path = str(row.get("file_path") or "").strip()
            if not chunk_text or not file_path:
                continue
            payload.append(
                (
                    repo_id,
                    file_path,
                    str(row.get("chunk_hash") or ""),
                    int(row.get("start_line") or 0),
                    int(row.get("end_line") or 0),
                    str(row.get("language") or ""),
                    str(row.get("symbol_name") or ""),
                    chunk_text,
                )
            )
        if not payload:
            return 0
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO code_chunks_fts(
                    repo_id, file_path, chunk_hash, start_line, end_line,
                    language, symbol_name, chunk_text
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                payload,
            )
            conn.commit()
        return len(payload)

    def search(
        self,
        *,
        query: str,
        repo_id: str | None = None,
        limit: int = 20,
        file_types: list[str] | None = None,
    ) -> list[dict]:
        fts_q = self._fts_query(query)
        if not fts_q:
            return []
        limit = max(1, min(int(limit or 20), 200))
        where = ["code_chunks_fts MATCH ?"]
        args: list[object] = [fts_q]
        if repo_id:
            where.append("repo_id = ?")
            args.append(repo_id)
        if file_types:
            suffixes = [str(ft).strip() for ft in file_types if str(ft).strip()]
            if suffixes:
                clauses = []
                for suf in suffixes:
                    clauses.append("file_path LIKE ?")
                    args.append(f"%{suf}")
                where.append("(" + " OR ".join(clauses) + ")")
        sql = (
            "SELECT repo_id, file_path, chunk_hash, start_line, end_line, language, "
            "symbol_name, chunk_text, bm25(code_chunks_fts) AS bm25_score "
            "FROM code_chunks_fts "
            f"WHERE {' AND '.join(where)} "
            "ORDER BY bm25_score ASC LIMIT ?"
        )
        args.append(limit)
        with self._connect() as conn:
            cur = conn.execute(sql, tuple(args))
            out = []
            for row in cur.fetchall():
                bm25_score = float(row[8] or 0.0)
                norm = 1.0 / (1.0 + max(bm25_score, 0.0))
                out.append(
                    {
                        "repo_id": row[0],
                        "file_path": row[1],
                        "chunk_hash": row[2],
                        "start_line": int(row[3] or 0),
                        "end_line": int(row[4] or 0),
                        "language": row[5] or "",
                        "symbol_name": row[6] or "",
                        "chunk_text": row[7] or "",
                        "bm25_raw": bm25_score,
                        "score": norm,
                    }
                )
            return out
