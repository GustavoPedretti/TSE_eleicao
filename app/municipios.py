"""
Municípios — sincronizados SÓ pelo poller (servidor → TSE).

A API pública apenas LÊ o que já está no banco.
O browser nunca dispara download no TSE.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from app import db
from app.config import ELEICAO_ATIVA
from app.enrich import enriquecer_resumo
from app.tse_client import (
    IBGE_UF_CODIGO,
    TIMEOUT_SEGUNDOS,
    UFS_TSE,
    ResultadoPresidente,
    extrair_candidatos_presidente,
    montar_url_municipio,
    resumo_para_dict,
)

logger = logging.getLogger("tse.municipios")

_MUN_CONFIG: dict[str, Any] | None = None
_MUN_CONFIG_LOCK = asyncio.Lock()

# UFs com malha municipal (sem ZZ)
UFS_COM_MUNICIPIO = [u for u in UFS_TSE if u != "zz" and u in IBGE_UF_CODIGO]


async def carregar_config_municipios(
    eleicao: str = ELEICAO_ATIVA,
    *,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Um arquivo de config do TSE lista todos os municípios (cd + cdi IBGE)."""
    global _MUN_CONFIG
    async with _MUN_CONFIG_LOCK:
        if _MUN_CONFIG and _MUN_CONFIG.get("_eleicao") == eleicao:
            return _MUN_CONFIG
        url = (
            f"https://resultados.tse.jus.br/oficial/ele2026/{eleicao}/"
            f"config/mun-e{eleicao.zfill(6)}-cm.json"
        )
        if client is None:
            async with httpx.AsyncClient(timeout=60.0) as c:
                resp = await c.get(url)
                resp.raise_for_status()
                data = resp.json()
        else:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
        data["_eleicao"] = eleicao
        _MUN_CONFIG = data
        return data


def lista_municipios_uf(cfg: dict[str, Any], uf: str) -> list[dict[str, str]]:
    uf = uf.lower()
    for abr in cfg.get("abr") or []:
        if str(abr.get("cd", "")).lower() == uf:
            return [
                {
                    "cd": str(mu.get("cd", "")).zfill(5),
                    "cdi": str(mu.get("cdi", "")),
                    "nome": str(mu.get("nm", "")),
                }
                for mu in (abr.get("mu") or [])
            ]
    return []


def chave_abrangencia(uf: str, cd_mun: str) -> str:
    return f"{uf.lower()}{str(cd_mun).zfill(5)}"


def geojson_url_uf(uf: str) -> str | None:
    codigo = IBGE_UF_CODIGO.get(uf.lower())
    if not codigo:
        return None
    return (
        "https://cdn.jsdelivr.net/gh/tbrugz/geodata-br@master/geojson/"
        f"geojs-{codigo}-mun.json"
    )


async def _baixar_e_salvar(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    eleicao: str,
    uf: str,
    mun: dict[str, str],
) -> tuple[bool, bool]:
    """
    Retorna (ok, mudou).
    Sempre tenta o TSE (o poller é o único cliente); persiste no DB.
    """
    abr = chave_abrangencia(uf, mun["cd"])
    url = montar_url_municipio(cd_eleicao=eleicao, uf=uf, cd_municipio=mun["cd"])
    async with sem:
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            payload = resp.json()
        except Exception as exc:  # noqa: BLE001
            agora = datetime.now(timezone.utc).isoformat()
            db.registrar_poll(
                eleicao=eleicao,
                abrangencia=abr,
                ok=False,
                mudou=False,
                idg=None,
                erro=str(exc),
                em=agora,
            )
            return False, False

    secoes = payload.get("s") or {}
    idg = payload.get("idg")
    resultado = ResultadoPresidente(
        eleicao=str(payload.get("ele", eleicao)),
        turno=str(payload.get("t", "")),
        abrangencia=abr,
        idg=str(idg) if idg is not None else None,
        gerado_em=f"{payload.get('dg', '?')} {payload.get('hg', '?')}",
        secoes_totalizadas_pct=str(secoes.get("pst", "?")),
        candidatos=extrair_candidatos_presidente(payload),
        baixado_em=datetime.now(timezone.utc).isoformat(),
        url=url,
        bruto=payload,
    )
    resumo = resumo_para_dict(resultado)
    resumo["cd"] = mun["cd"]
    resumo["cdi"] = mun["cdi"]
    resumo["nome"] = mun["nome"]

    idg_ant = db.ultimo_idg(eleicao, abr)
    mudou = resultado.idg != idg_ant or idg_ant is None
    agora = datetime.now(timezone.utc).isoformat()
    db.salvar_snapshot(
        eleicao=resultado.eleicao,
        abrangencia=abr,
        turno=resultado.turno,
        idg=resultado.idg,
        gerado_em_tse=resultado.gerado_em,
        secoes_pct=resultado.secoes_totalizadas_pct,
        baixado_em=resultado.baixado_em,
        resumo=resumo,
        mudou=mudou,
    )
    db.registrar_poll(
        eleicao=resultado.eleicao,
        abrangencia=abr,
        ok=True,
        mudou=mudou,
        idg=resultado.idg,
        erro=None,
        em=agora,
    )
    return True, mudou


async def sincronizar_todos_municipios(
    eleicao: str = ELEICAO_ATIVA,
    *,
    client: httpx.AsyncClient | None = None,
    concorrencia: int = 20,
) -> dict[str, Any]:
    """
    Ciclo servidor: baixa TODOS os municípios de TODAS as UFs no TSE.
    Chamado só pelo poller — nunca pela rota do browser.
    """
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=TIMEOUT_SEGUNDOS)

    assert client is not None
    try:
        cfg = await carregar_config_municipios(eleicao, client=client)
        tarefas: list[tuple[str, dict[str, str]]] = []
        for uf in UFS_COM_MUNICIPIO:
            for mun in lista_municipios_uf(cfg, uf):
                tarefas.append((uf, mun))

        sem = asyncio.Semaphore(concorrencia)
        resultados = await asyncio.gather(
            *[_baixar_e_salvar(client, sem, eleicao, uf, mun) for uf, mun in tarefas]
        )
        ok = sum(1 for o, _ in resultados if o)
        mudancas = sum(1 for o, m in resultados if o and m)
        return {
            "total_alvo": len(tarefas),
            "ok": ok,
            "falhas": len(tarefas) - ok,
            "mudancas": mudancas,
        }
    finally:
        if own_client:
            await client.aclose()


def ler_municipios_uf(uf: str, *, eleicao: str | None = None) -> dict[str, Any]:
    """Somente leitura do banco — usado pela API do dashboard."""
    eleicao = eleicao or ELEICAO_ATIVA
    uf = uf.lower()
    municipios = [
        enriquecer_resumo(m) or m
        for m in db.ultimos_resumos_prefixo(eleicao, uf, len(uf) + 5)
    ]
    municipios.sort(key=lambda m: m.get("nome") or m.get("abrangencia") or "")

    total_esperado = None
    if _MUN_CONFIG and _MUN_CONFIG.get("_eleicao") == eleicao:
        total_esperado = len(lista_municipios_uf(_MUN_CONFIG, uf))

    return {
        "uf": uf,
        "total": total_esperado if total_esperado is not None else len(municipios),
        "com_dado": sum(1 for m in municipios if m.get("lider")),
        "fonte": "banco",
        "municipios": municipios,
        "ibge_uf": IBGE_UF_CODIGO.get(uf),
        "geojson_url": geojson_url_uf(uf),
    }


def ler_todos_municipios(*, eleicao: str | None = None) -> dict[str, Any]:
    """Todos os municípios do turno (só banco) — vista 'por cidade' nacional."""
    eleicao = eleicao or ELEICAO_ATIVA
    por_uf: dict[str, dict[str, Any]] = {}
    total = 0
    com_dado = 0
    for uf in UFS_COM_MUNICIPIO:
        bloco = ler_municipios_uf(uf, eleicao=eleicao)
        por_uf[uf] = {
            "ibge_uf": bloco["ibge_uf"],
            "geojson_url": bloco["geojson_url"],
            "total": bloco["total"],
            "com_dado": bloco["com_dado"],
            "municipios": bloco["municipios"],
        }
        total += bloco["total"]
        com_dado += bloco["com_dado"]
    return {
        "eleicao": eleicao,
        "fonte": "banco",
        "total": total,
        "com_dado": com_dado,
        "ufs": por_uf,
    }
