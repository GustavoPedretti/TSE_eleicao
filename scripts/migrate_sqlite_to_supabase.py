"""
Script utilitário para migrar dados do SQLite local (data/eleicoes.db) para o Supabase (Postgres).

Uso:
  python scripts/migrate_sqlite_to_supabase.py

Pré-requisito:
  Variável DATABASE_URL configurada no .env ou no terminal apontando para o Supabase.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

# Carrega .env da raiz do projeto se python-dotenv estiver disponível
ROOT = Path(__file__).resolve().parent.parent
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
SQLITE_PATH = ROOT / "data" / "eleicoes.db"


def main():
    if not DATABASE_URL:
        print("[ERRO] DATABASE_URL não foi definida. Configure no seu arquivo .env.")
        sys.exit(1)

    if not SQLITE_PATH.exists():
        print(f"[ERRO] Banco SQLite local não encontrado em: {SQLITE_PATH}")
        sys.exit(1)

    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError:
        print("[ERRO] psycopg não está instalado. Execute: pip install 'psycopg[binary]'")
        sys.exit(1)

    print(f"Conectando ao SQLite local ({SQLITE_PATH})...")
    sqlite_conn = sqlite3.connect(SQLITE_PATH)
    sqlite_conn.row_factory = sqlite3.Row

    print("Conectando ao Supabase (Postgres)...")
    pg_conn = psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=True)

    # Garante tabelas no Postgres
    with open(ROOT / "supabase" / "schema.sql", encoding="utf-8") as f:
        schema_sql = f.read()
    print("Aplicando schema no Supabase...")
    pg_conn.execute(schema_sql)

    # 1. Migrar snapshots
    print("Lendo snapshots do SQLite...")
    snapshots = sqlite_conn.execute(
        "SELECT id, eleicao, abrangencia, turno, idg, gerado_em_tse, secoes_pct, baixado_em, mudou, resumo_json FROM snapshots ORDER BY id ASC"
    ).fetchall()
    print(f"Total de snapshots locais encontrados: {len(snapshots)}")

    if snapshots:
        print("Inserindo snapshots no Supabase...")
        count_snap = 0
        with pg_conn.cursor() as cur:
            for s in snapshots:
                cur.execute(
                    """
                    INSERT INTO snapshots (
                        eleicao, abrangencia, turno, idg, gerado_em_tse,
                        secoes_pct, baixado_em, mudou, resumo_json
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s::timestamptz, %s, %s::jsonb)
                    """,
                    (
                        s["eleicao"],
                        s["abrangencia"],
                        s["turno"],
                        s["idg"],
                        s["gerado_em_tse"],
                        s["secoes_pct"],
                        s["baixado_em"],
                        bool(s["mudou"]),
                        s["resumo_json"],
                    ),
                )
                count_snap += 1
                if count_snap % 200 == 0:
                    print(f"  -> {count_snap}/{len(snapshots)} snapshots inseridos...")
        print(f"[OK] {count_snap} snapshots migrados com sucesso!")

    # 2. Migrar polls
    print("Lendo histórico de polls do SQLite...")
    polls = sqlite_conn.execute(
        "SELECT id, eleicao, abrangencia, ok, mudou, idg, erro, em FROM polls ORDER BY id ASC"
    ).fetchall()
    print(f"Total de registros de poll encontrados: {len(polls)}")

    if polls:
        print("Inserindo polls no Supabase...")
        count_polls = 0
        with pg_conn.cursor() as cur:
            for p in polls:
                cur.execute(
                    """
                    INSERT INTO polls (
                        eleicao, abrangencia, ok, mudou, idg, erro, em
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s::timestamptz)
                    """,
                    (
                        p["eleicao"],
                        p["abrangencia"],
                        bool(p["ok"]),
                        bool(p["mudou"]),
                        p["idg"],
                        p["erro"],
                        p["em"],
                    ),
                )
                count_polls += 1
                if count_polls % 500 == 0:
                    print(f"  -> {count_polls}/{len(polls)} polls inseridos...")
        print(f"[OK] {count_polls} polls migrados com sucesso!")

    print("\n[SUCESSO] Migração concluída com sucesso no Supabase!")


if __name__ == "__main__":
    main()
