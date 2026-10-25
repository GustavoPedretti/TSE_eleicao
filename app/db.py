"""
Persistência local (SQLite).

Quando DATABASE_URL (Supabase/Postgres) estiver setada, usamos Postgres.
Na Vercel o filesystem é efêmero — sem DATABASE_URL os dados não sobrevivem
entre invocações do Cron.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from app.config import DATABASE_URL

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "eleicoes.db"

_pg = None


def _use_postgres() -> bool:
    return bool(DATABASE_URL)


def _pg_conn():
    global _pg
    import psycopg
    from psycopg.rows import dict_row

    if _pg is not None:
        try:
            if _pg.closed:
                _pg = None
            else:
                _pg.execute("SELECT 1")
        except Exception:
            _pg = None

    if _pg is None:
        url = DATABASE_URL
        if "sslmode" not in url and ("supabase.com" in url or "pooler" in url):
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}sslmode=require"
        _pg = psycopg.connect(url, row_factory=dict_row, autocommit=True)
        # Garante que as tabelas existam na primeira conexão
        try:
            _criar_tabelas_pg(_pg)
        except Exception as e:
            import logging
            logging.warning("Aviso ao auto-criar tabelas no Postgres: %s", e)
    return _pg


def _criar_tabelas_pg(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS snapshots (
            id BIGSERIAL PRIMARY KEY,
            eleicao TEXT NOT NULL,
            abrangencia TEXT NOT NULL DEFAULT 'br',
            turno TEXT,
            idg TEXT,
            gerado_em_tse TEXT,
            secoes_pct TEXT,
            baixado_em TIMESTAMPTZ NOT NULL,
            mudou BOOLEAN NOT NULL DEFAULT TRUE,
            resumo_json JSONB NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_snapshots_eleicao_abr_id
            ON snapshots (eleicao, abrangencia, id DESC);
        CREATE TABLE IF NOT EXISTS polls (
            id BIGSERIAL PRIMARY KEY,
            eleicao TEXT NOT NULL,
            abrangencia TEXT NOT NULL DEFAULT 'br',
            ok BOOLEAN NOT NULL,
            mudou BOOLEAN NOT NULL DEFAULT FALSE,
            idg TEXT,
            erro TEXT,
            em TIMESTAMPTZ NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_polls_eleicao_em
            ON polls (eleicao, em DESC);
        """
    )


def connect() -> sqlite3.Connection:
    # Na Vercel sem Postgres, grava em /tmp (único lugar gravável)
    path = DB_PATH
    if Path("/tmp").exists() and str(ROOT).startswith("/var/task"):
        path = Path("/tmp/eleicoes.db")
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    if _use_postgres():
        conn = _pg_conn()
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS snapshots (
                id BIGSERIAL PRIMARY KEY,
                eleicao TEXT NOT NULL,
                abrangencia TEXT NOT NULL DEFAULT 'br',
                turno TEXT,
                idg TEXT,
                gerado_em_tse TEXT,
                secoes_pct TEXT,
                baixado_em TIMESTAMPTZ NOT NULL,
                mudou BOOLEAN NOT NULL DEFAULT TRUE,
                resumo_json JSONB NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_snapshots_eleicao_abr_id
            ON snapshots (eleicao, abrangencia, id DESC)
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS polls (
                id BIGSERIAL PRIMARY KEY,
                eleicao TEXT NOT NULL,
                abrangencia TEXT NOT NULL DEFAULT 'br',
                ok BOOLEAN NOT NULL,
                mudou BOOLEAN NOT NULL DEFAULT FALSE,
                idg TEXT,
                erro TEXT,
                em TIMESTAMPTZ NOT NULL
            )
            """
        )
        return

    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                eleicao TEXT NOT NULL,
                abrangencia TEXT NOT NULL DEFAULT 'br',
                turno TEXT,
                idg TEXT,
                gerado_em_tse TEXT,
                secoes_pct TEXT,
                baixado_em TEXT NOT NULL,
                mudou INTEGER NOT NULL DEFAULT 1,
                resumo_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS polls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                eleicao TEXT NOT NULL,
                abrangencia TEXT NOT NULL DEFAULT 'br',
                ok INTEGER NOT NULL,
                mudou INTEGER NOT NULL DEFAULT 0,
                idg TEXT,
                erro TEXT,
                em TEXT NOT NULL
            );
            """
        )
        # Migração se o DB foi criado antes de `abrangencia`
        cols = {r[1] for r in conn.execute("PRAGMA table_info(snapshots)").fetchall()}
        if "abrangencia" not in cols:
            conn.execute(
                "ALTER TABLE snapshots ADD COLUMN abrangencia TEXT NOT NULL DEFAULT 'br'"
            )
        cols_p = {r[1] for r in conn.execute("PRAGMA table_info(polls)").fetchall()}
        if "abrangencia" not in cols_p:
            conn.execute(
                "ALTER TABLE polls ADD COLUMN abrangencia TEXT NOT NULL DEFAULT 'br'"
            )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_snapshots_eleicao_abr_id
                ON snapshots (eleicao, abrangencia, id DESC)
            """
        )


def ultimo_idg(eleicao: str, abrangencia: str = "br") -> str | None:
    abr = abrangencia.lower()
    if _use_postgres():
        row = _pg_conn().execute(
            """
            SELECT idg FROM snapshots
            WHERE eleicao = %s AND abrangencia = %s AND idg IS NOT NULL
            ORDER BY id DESC LIMIT 1
            """,
            (eleicao, abr),
        ).fetchone()
        return row["idg"] if row else None

    with connect() as conn:
        row = conn.execute(
            """
            SELECT idg FROM snapshots
            WHERE eleicao = ? AND abrangencia = ? AND idg IS NOT NULL
            ORDER BY id DESC LIMIT 1
            """,
            (eleicao, abr),
        ).fetchone()
    return row["idg"] if row else None


def salvar_snapshot(
    *,
    eleicao: str,
    abrangencia: str,
    turno: str,
    idg: str | None,
    gerado_em_tse: str,
    secoes_pct: str,
    baixado_em: str,
    resumo: dict[str, Any],
    mudou: bool,
) -> int:
    abr = abrangencia.lower()
    payload = json.dumps(resumo, ensure_ascii=False)

    if _use_postgres():
        row = _pg_conn().execute(
            """
            INSERT INTO snapshots (
                eleicao, abrangencia, turno, idg, gerado_em_tse, secoes_pct,
                baixado_em, mudou, resumo_json
            ) VALUES (%s, %s, %s, %s, %s, %s, %s::timestamptz, %s, %s::jsonb)
            RETURNING id
            """,
            (
                eleicao,
                abr,
                turno,
                idg,
                gerado_em_tse,
                secoes_pct,
                baixado_em,
                mudou,
                payload,
            ),
        ).fetchone()
        return int(row["id"])

    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO snapshots (
                eleicao, abrangencia, turno, idg, gerado_em_tse, secoes_pct,
                baixado_em, mudou, resumo_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                eleicao,
                abr,
                turno,
                idg,
                gerado_em_tse,
                secoes_pct,
                baixado_em,
                1 if mudou else 0,
                payload,
            ),
        )
        return int(cur.lastrowid)


def registrar_poll(
    *,
    eleicao: str,
    abrangencia: str,
    ok: bool,
    mudou: bool,
    idg: str | None,
    erro: str | None,
    em: str,
) -> None:
    abr = abrangencia.lower()
    if _use_postgres():
        _pg_conn().execute(
            """
            INSERT INTO polls (eleicao, abrangencia, ok, mudou, idg, erro, em)
            VALUES (%s, %s, %s, %s, %s, %s, %s::timestamptz)
            """,
            (eleicao, abr, ok, mudou, idg, erro, em),
        )
        return

    with connect() as conn:
        conn.execute(
            """
            INSERT INTO polls (eleicao, abrangencia, ok, mudou, idg, erro, em)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (eleicao, abr, 1 if ok else 0, 1 if mudou else 0, idg, erro, em),
        )


def ultimo_resumo(eleicao: str, abrangencia: str = "br") -> dict[str, Any] | None:
    abr = abrangencia.lower()
    if _use_postgres():
        row = _pg_conn().execute(
            """
            SELECT resumo_json FROM snapshots
            WHERE eleicao = %s AND abrangencia = %s
            ORDER BY id DESC LIMIT 1
            """,
            (eleicao, abr),
        ).fetchone()
        if not row:
            return None
        val = row["resumo_json"]
        return val if isinstance(val, dict) else json.loads(val)

    with connect() as conn:
        row = conn.execute(
            """
            SELECT resumo_json FROM snapshots
            WHERE eleicao = ? AND abrangencia = ?
            ORDER BY id DESC LIMIT 1
            """,
            (eleicao, abr),
        ).fetchone()
    if not row:
        return None
    return json.loads(row["resumo_json"])


def ultimos_resumos_ufs(eleicao: str) -> dict[str, dict[str, Any]]:
    """Último snapshot por UF (2 letras / zz). Ignora chaves de município (ex.: sp71072)."""
    if _use_postgres():
        rows = _pg_conn().execute(
            """
            SELECT DISTINCT ON (abrangencia) abrangencia, resumo_json
            FROM snapshots
            WHERE eleicao = %s
              AND abrangencia <> 'br'
              AND length(abrangencia) <= 2
            ORDER BY abrangencia, id DESC
            """,
            (eleicao,),
        ).fetchall()
        out: dict[str, dict[str, Any]] = {}
        for row in rows:
            val = row["resumo_json"]
            out[row["abrangencia"]] = val if isinstance(val, dict) else json.loads(val)
        return out

    with connect() as conn:
        rows = conn.execute(
            """
            SELECT abrangencia, resumo_json
            FROM snapshots
            WHERE id IN (
                SELECT MAX(id) FROM snapshots
                WHERE eleicao = ?
                  AND abrangencia <> 'br'
                  AND length(abrangencia) <= 2
                GROUP BY abrangencia
            )
            """,
            (eleicao,),
        ).fetchall()
    return {row["abrangencia"]: json.loads(row["resumo_json"]) for row in rows}


def ultimos_resumos_prefixo(eleicao: str, prefixo: str, tamanho: int) -> list[dict[str, Any]]:
    """Último snapshot por abrangencia com prefixo e length fixo (ex.: municípios sp?????)."""
    if _use_postgres():
        rows = _pg_conn().execute(
            """
            SELECT DISTINCT ON (abrangencia) abrangencia, resumo_json
            FROM snapshots
            WHERE eleicao = %s
              AND abrangencia LIKE %s
              AND length(abrangencia) = %s
            ORDER BY abrangencia, id DESC
            """,
            (eleicao, f"{prefixo}%", tamanho),
        ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            val = row["resumo_json"]
            out.append(val if isinstance(val, dict) else json.loads(val))
        return out

    with connect() as conn:
        rows = conn.execute(
            """
            SELECT abrangencia, resumo_json
            FROM snapshots
            WHERE id IN (
                SELECT MAX(id) FROM snapshots
                WHERE eleicao = ?
                  AND abrangencia LIKE ?
                  AND length(abrangencia) = ?
                GROUP BY abrangencia
            )
            """,
            (eleicao, f"{prefixo}%", tamanho),
        ).fetchall()
    return [json.loads(row["resumo_json"]) for row in rows]


def listar_historico(
    eleicao: str, abrangencia: str = "br", limite: int = 20
) -> list[dict[str, Any]]:
    abr = abrangencia.lower()
    limite = max(1, min(limite, 200))

    if _use_postgres():
        rows = _pg_conn().execute(
            """
            SELECT id, eleicao, abrangencia, turno, idg, gerado_em_tse, secoes_pct,
                   baixado_em, mudou, resumo_json
            FROM snapshots
            WHERE eleicao = %s AND abrangencia = %s
            ORDER BY id DESC
            LIMIT %s
            """,
            (eleicao, abr, limite),
        ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            val = item.pop("resumo_json")
            item["resumo"] = val if isinstance(val, dict) else json.loads(val)
            item["baixado_em"] = str(item["baixado_em"])
            out.append(item)
        return out

    with connect() as conn:
        rows = conn.execute(
            """
            SELECT id, eleicao, abrangencia, turno, idg, gerado_em_tse, secoes_pct,
                   baixado_em, mudou, resumo_json
            FROM snapshots
            WHERE eleicao = ? AND abrangencia = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (eleicao, abr, limite),
        ).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        item["resumo"] = json.loads(item.pop("resumo_json"))
        item["mudou"] = bool(item["mudou"])
        out.append(item)
    return out


def stats_polls(eleicao: str) -> dict[str, Any]:
    if _use_postgres():
        row = _pg_conn().execute(
            """
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE ok) AS ok,
                COUNT(*) FILTER (WHERE mudou) AS mudancas,
                MAX(em) AS ultimo_em
            FROM polls
            WHERE eleicao = %s
            """,
            (eleicao,),
        ).fetchone()
        return {
            "total": row["total"] or 0,
            "ok": row["ok"] or 0,
            "mudancas": row["mudancas"] or 0,
            "ultimo_em": str(row["ultimo_em"]) if row["ultimo_em"] else None,
            "backend": "postgres",
        }

    with connect() as conn:
        row = conn.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(ok) AS ok,
                SUM(mudou) AS mudancas,
                MAX(em) AS ultimo_em
            FROM polls
            WHERE eleicao = ?
            """,
            (eleicao,),
        ).fetchone()
    return {
        "total": row["total"] or 0,
        "ok": row["ok"] or 0,
        "mudancas": row["mudancas"] or 0,
        "ultimo_em": row["ultimo_em"],
        "backend": "sqlite",
        "path": str(DB_PATH),
    }
