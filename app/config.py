"""
Configuração via ambiente — local e Vercel.
"""

from __future__ import annotations

import os

from app.tse_client import ELEICAO_FEDERAL_1T, ELEICAO_FEDERAL_2T

ELEICAO_1T = os.getenv("ELEICAO_1T", ELEICAO_FEDERAL_1T)
ELEICAO_2T = os.getenv("ELEICAO_2T", ELEICAO_FEDERAL_2T)
# Compat: ELEICAO_ATIVA = 2º turno (mapa ao vivo)
ELEICAO_ATIVA = os.getenv("ELEICAO_ATIVA", ELEICAO_2T)

POLL_INTERVAL_SEGUNDOS = int(os.getenv("POLL_INTERVAL_SEGUNDOS", "60"))
POLL_SECRET = os.getenv("POLL_SECRET") or os.getenv("CRON_SECRET") or ""
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

IS_VERCEL = os.getenv("VERCEL") == "1"
RUN_LOCAL_POLLER = os.getenv(
    "RUN_LOCAL_POLLER",
    "0" if IS_VERCEL else "1",
) == "1"

# Municípios do 1º turno: 1 = sincroniza também (mais lento)
SYNC_MUN_1T = os.getenv("SYNC_MUN_1T", "1") == "1"
