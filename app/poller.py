"""
Poller — ÚNICO lugar que fala com o TSE.

Ciclo:
  1. BR + UFs do 1º e do 2º turno
  2. Municípios (2º turno sempre; 1º se SYNC_MUN_1T=1)

Browser só lê o banco.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import httpx

from app import cache, db
from app.config import (
    ELEICAO_1T,
    ELEICAO_2T,
    ELEICAO_ATIVA,
    POLL_INTERVAL_SEGUNDOS,
    SYNC_MUN_1T,
)
from app.municipios import sincronizar_todos_municipios
from app.tse_client import (
    TIMEOUT_SEGUNDOS,
    UFS_TSE,
    buscar_presidente,
    resumo_para_dict,
)

logger = logging.getLogger("tse.poller")

CONCORRENCIA_UF = 4
CONCORRENCIA_MUN = 10


async def _persistir_abr(*, eleicao: str, abrangencia: str, resultado) -> bool:
    agora = datetime.now(timezone.utc).isoformat()
    idg_anterior = db.ultimo_idg(eleicao, abrangencia)
    mudou = resultado.idg != idg_anterior or idg_anterior is None
    resumo = resumo_para_dict(resultado)

    db.salvar_snapshot(
        eleicao=resultado.eleicao,
        abrangencia=abrangencia,
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
        abrangencia=abrangencia,
        ok=True,
        mudou=mudou,
        idg=resultado.idg,
        erro=None,
        em=agora,
    )
    return mudou


async def _fetch_uma(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    eleicao: str,
    abr: str,
) -> tuple[str, dict | None, str | None, bool]:
    async with sem:
        try:
            resultado = await buscar_presidente(
                cd_eleicao=eleicao, abrangencia=abr, client=client
            )
            mudou = await _persistir_abr(
                eleicao=eleicao, abrangencia=abr, resultado=resultado
            )
            return abr, resumo_para_dict(resultado), None, mudou
        except Exception as exc:  # noqa: BLE001
            agora = datetime.now(timezone.utc).isoformat()
            erro = str(exc)
            db.registrar_poll(
                eleicao=eleicao,
                abrangencia=abr,
                ok=False,
                mudou=False,
                idg=None,
                erro=erro,
                em=agora,
            )
            return abr, None, erro, False


async def _sync_br_ufs(client: httpx.AsyncClient, eleicao: str) -> dict:
    alvos = ["br", *UFS_TSE]
    sem = asyncio.Semaphore(CONCORRENCIA_UF)
    resultados = await asyncio.gather(
        *[_fetch_uma(client, sem, eleicao, abr) for abr in alvos]
    )

    ok_count = 0
    erro_count = 0
    mudancas = 0
    erros: dict[str, str] = {}
    ufs_cache: dict[str, dict] = {}
    br_resumo = None
    br_mudou = False

    for abr, resumo, erro, mudou in resultados:
        if erro:
            erro_count += 1
            erros[abr] = erro
            continue
        ok_count += 1
        if mudou:
            mudancas += 1
        if abr == "br":
            br_resumo = resumo
            br_mudou = mudou
        else:
            ufs_cache[abr] = resumo  # type: ignore[assignment]

    # Cache em RAM só espelha o 2º turno (aba ao vivo); 1º fica no banco
    if eleicao == ELEICAO_2T:
        agora = datetime.now(timezone.utc).isoformat()
        if br_resumo:
            cache.set_br(
                eleicao=eleicao,
                idg=br_resumo.get("idg"),
                data=br_resumo,
                atualizado_em=br_resumo.get("baixado_em") or agora,
                mudou=br_mudou,
                contar_poll=True,
            )
        elif erro_count:
            cache.marcar_erro(em=agora, erro=f"{erro_count} falhas; BR 2T indisponível")
        if ufs_cache:
            cache.set_ufs_bulk(ufs_cache)

    return {
        "eleicao": eleicao,
        "ok": br_resumo is not None,
        "baixados_ok": ok_count,
        "falhas": erro_count,
        "mudancas": mudancas,
        "erros": erros,
    }


async def ciclo_completo(eleicao: str | None = None) -> dict:
    """
    Sincroniza 1º + 2º turno (BR/UFs) e municípios.
    `eleicao` ignorado se passado — mantido por compat; o ciclo cobre os dois.
    """
    agora = datetime.now(timezone.utc).isoformat()
    turnos = [ELEICAO_1T, ELEICAO_2T]
    por_turno: dict[str, dict] = {}
    mun_stats: dict[str, dict] = {}

    async with httpx.AsyncClient(timeout=TIMEOUT_SEGUNDOS) as client:
        for el in turnos:
            por_turno[el] = await _sync_br_ufs(client, el)

        # Municípios: 2º sempre; 1º opcional (comparativo municipal)
        mun_stats[ELEICAO_2T] = await sincronizar_todos_municipios(
            ELEICAO_2T, client=client, concorrencia=CONCORRENCIA_MUN
        )
        if SYNC_MUN_1T:
            mun_stats[ELEICAO_1T] = await sincronizar_todos_municipios(
                ELEICAO_1T, client=client, concorrencia=CONCORRENCIA_MUN
            )

    logger.info("ciclo turnos=%s mun=%s", {k: v.get("ok") for k, v in por_turno.items()}, mun_stats)
    return {
        "ok": all(v.get("ok") for v in por_turno.values()),
        "em": agora,
        "turnos": por_turno,
        "municipios": mun_stats,
        "eleicao_ativa": ELEICAO_ATIVA,
    }


async def ciclo_unico(eleicao: str = ELEICAO_ATIVA) -> dict:
    return await ciclo_completo(eleicao)


async def loop_poller(
    eleicao: str = ELEICAO_ATIVA,
    intervalo: int = POLL_INTERVAL_SEGUNDOS,
):
    logger.info(
        "poller | 1T=%s 2T=%s | intervalo=%ss | mun_1t=%s",
        ELEICAO_1T,
        ELEICAO_2T,
        intervalo,
        SYNC_MUN_1T,
    )
    await ciclo_completo()
    while True:
        await asyncio.sleep(intervalo)
        await ciclo_completo()
