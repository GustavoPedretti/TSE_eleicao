"""
FastAPI — BFF.

Poller (único falante do TSE) → banco
Browser → GET /api/dashboard (uma leitura) + mapa municipal do banco
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import cache, db
from app.config import (
    ELEICAO_1T,
    ELEICAO_2T,
    ELEICAO_ATIVA,
    IS_VERCEL,
    POLL_INTERVAL_SEGUNDOS,
    POLL_SECRET,
    RUN_LOCAL_POLLER,
)
from app.enrich import enriquecer_resumo
from app.municipios import ler_municipios_uf, ler_todos_municipios
from app.poller import ciclo_completo, loop_poller
from app.tse_client import UF_NOMES

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT / "static"


def _ufs_payload(eleicao: str) -> list[dict[str, Any]]:
    ufs = db.ultimos_resumos_ufs(eleicao)
    itens = []
    for sigla, raw in sorted(ufs.items()):
        resumo = enriquecer_resumo(raw) or {}
        itens.append(
            {
                "uf": sigla,
                "nome": resumo.get("nome_abrangencia") or UF_NOMES.get(sigla, sigla),
                "secoes_pct": resumo.get("secoes_totalizadas_pct"),
                "secoes_pct_num": resumo.get("secoes_pct_num"),
                "tem_votos": resumo.get("tem_votos"),
                "votos_13": resumo.get("votos_13"),
                "votos_22": resumo.get("votos_22"),
                "score_disputa": resumo.get("score_disputa"),
                "cand_13": resumo.get("cand_13"),
                "cand_22": resumo.get("cand_22"),
                "lider": resumo.get("lider"),
                "candidatos": resumo.get("candidatos", []),
                "idg": resumo.get("idg"),
                "baixado_em": resumo.get("baixado_em"),
            }
        )
    return itens


def _bloco_turno(eleicao: str, label: str) -> dict[str, Any]:
    br = enriquecer_resumo(db.ultimo_resumo(eleicao, "br"))
    return {
        "eleicao": eleicao,
        "label": label,
        "brasil": br,
        "ufs": _ufs_payload(eleicao),
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    # Esquenta cache do 2º turno se já houver dado
    br = db.ultimo_resumo(ELEICAO_2T, "br")
    if br:
        cache.set_br(
            eleicao=ELEICAO_2T,
            idg=br.get("idg"),
            data=br,
            atualizado_em=str(br.get("baixado_em", "")),
            mudou=False,
            contar_poll=False,
        )
    ufs = db.ultimos_resumos_ufs(ELEICAO_2T)
    if ufs:
        cache.set_ufs_bulk(ufs)

    task = None
    if RUN_LOCAL_POLLER:
        task = asyncio.create_task(loop_poller(ELEICAO_ATIVA, POLL_INTERVAL_SEGUNDOS))
    try:
        yield
    finally:
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


app = FastAPI(
    title="TSE Eleição — estudo",
    description="1º+2º turno no banco · mapa disputa 13×22 · abas",
    version="0.4.0",
    lifespan=lifespan,
)


def _autorizar_poll(authorization: str | None) -> None:
    if not POLL_SECRET:
        if IS_VERCEL:
            raise HTTPException(status_code=500, detail="POLL_SECRET não configurado")
        return
    esperado = f"Bearer {POLL_SECRET}"
    if not authorization or not secrets.compare_digest(authorization, esperado):
        raise HTTPException(status_code=401, detail="Unauthorized")


@app.post("/api/poll")
@app.get("/api/poll")
async def api_poll(authorization: str | None = Header(default=None)):
    _autorizar_poll(authorization)
    db.init_db()
    return await ciclo_completo()


@app.get("/api/dashboard")
async def api_dashboard():
    """
    Uma leitura do banco com 1º e 2º turno (BR + UFs).
    Front carrega uma vez e monta as duas abas.
    """
    t1 = _bloco_turno(ELEICAO_1T, "1º turno")
    t2 = _bloco_turno(ELEICAO_2T, "2º turno")
    if not t2["brasil"] and not t1["brasil"]:
        raise HTTPException(
            status_code=503,
            detail="Sem dados ainda — aguarde o poller (/api/poll).",
        )
    return {
        "fonte": "banco",
        "eleicao_1t": ELEICAO_1T,
        "eleicao_2t": ELEICAO_2T,
        "turno_1": t1,
        "turno_2": t2,
    }


@app.get("/api/presidente")
async def api_presidente(turno: int = Query(2, ge=1, le=2)):
    eleicao = ELEICAO_1T if turno == 1 else ELEICAO_2T
    data = db.ultimo_resumo(eleicao, "br")
    if data is None:
        raise HTTPException(status_code=503, detail=f"Sem dados do turno {turno}")
    return {"turno": turno, "eleicao": eleicao, "resultado": data}


@app.get("/api/mapa/ufs")
async def api_mapa_ufs(turno: int = Query(2, ge=1, le=2)):
    eleicao = ELEICAO_1T if turno == 1 else ELEICAO_2T
    itens = _ufs_payload(eleicao)
    return {"turno": turno, "eleicao": eleicao, "total": len(itens), "ufs": itens}


@app.get("/api/mapa/municipios-brasil")
async def api_mapa_municipios_brasil(turno: int = Query(2, ge=1, le=2)):
    """Vista nacional por cidade — só lê banco (todas as UFs)."""
    eleicao = ELEICAO_1T if turno == 1 else ELEICAO_2T
    data = ler_todos_municipios(eleicao=eleicao)
    data["turno"] = turno
    return data


@app.get("/api/mapa/{uf}/municipios")
async def api_mapa_municipios(uf: str, turno: int = Query(2, ge=1, le=2)):
    uf = uf.lower()
    if uf == "zz":
        raise HTTPException(status_code=400, detail="Exterior sem malha municipal")
    if uf not in UF_NOMES:
        raise HTTPException(status_code=404, detail=f"UF desconhecida: {uf}")
    eleicao = ELEICAO_1T if turno == 1 else ELEICAO_2T
    return ler_municipios_uf(uf, eleicao=eleicao)


@app.get("/api/status")
async def api_status():
    return {
        "eleicao_1t": ELEICAO_1T,
        "eleicao_2t": ELEICAO_2T,
        "intervalo_segundos": POLL_INTERVAL_SEGUNDOS,
        "is_vercel": IS_VERCEL,
        "local_poller": RUN_LOCAL_POLLER,
        "cache": cache.snapshot_meta(),
        "banco_2t": db.stats_polls(ELEICAO_2T),
        "banco_1t": db.stats_polls(ELEICAO_1T),
    }


@app.get("/")
async def home():
    index = STATIC_DIR / "index.html"
    if not index.exists():
        raise HTTPException(status_code=404, detail="static/index.html não encontrado")
    return FileResponse(index)


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
