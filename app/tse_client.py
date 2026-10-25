"""
Cliente HTTP do TSE — arquivos JSON no CDN.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

TSE_BASE = "https://resultados.tse.jus.br"
AMBIENTE = "oficial"
CICLO = "ele2026"

ELEICAO_FEDERAL_1T = "6257"
ELEICAO_FEDERAL_2T = "6258"
CARGO_PRESIDENTE = "1"
TIMEOUT_SEGUNDOS = 30.0

# Abrangências de Presidente no arquivo do TSE (UF + exterior)
UFS_TSE = [
    "ac", "al", "am", "ap", "ba", "ce", "df", "es", "go", "ma",
    "mg", "ms", "mt", "pa", "pb", "pe", "pi", "pr", "rj", "rn",
    "ro", "rr", "rs", "sc", "se", "sp", "to", "zz",
]

# Nome amigável / match com GeoJSON comum
UF_NOMES: dict[str, str] = {
    "ac": "Acre", "al": "Alagoas", "am": "Amazonas", "ap": "Amapá",
    "ba": "Bahia", "ce": "Ceará", "df": "Distrito Federal", "es": "Espírito Santo",
    "go": "Goiás", "ma": "Maranhão", "mg": "Minas Gerais", "ms": "Mato Grosso do Sul",
    "mt": "Mato Grosso", "pa": "Pará", "pb": "Paraíba", "pe": "Pernambuco",
    "pi": "Piauí", "pr": "Paraná", "rj": "Rio de Janeiro", "rn": "Rio Grande do Norte",
    "ro": "Rondônia", "rr": "Roraima", "rs": "Rio Grande do Sul", "sc": "Santa Catarina",
    "se": "Sergipe", "sp": "São Paulo", "to": "Tocantins", "zz": "Exterior",
}


@dataclass
class CandidatoResumo:
    numero: str
    nome: str
    votos: int
    percentual: str


@dataclass
class ResultadoPresidente:
    eleicao: str
    turno: str
    abrangencia: str
    idg: str | None
    gerado_em: str
    secoes_totalizadas_pct: str
    candidatos: list[CandidatoResumo]
    baixado_em: str
    url: str
    bruto: dict[str, Any]


def montar_url_resultado(
    *,
    cd_eleicao: str,
    abrangencia: str = "br",
    cargo: str = CARGO_PRESIDENTE,
) -> str:
    """UF ou Brasil: sp-c0001-e006258-u.json / br-c0001-e006258-u.json"""
    abr = abrangencia.lower()
    cargo_4 = cargo.zfill(4)
    eleicao_6 = cd_eleicao.zfill(6)
    nome = f"{abr}-c{cargo_4}-e{eleicao_6}-u.json"
    return f"{TSE_BASE}/{AMBIENTE}/{CICLO}/{cd_eleicao}/dados/{abr}/{nome}"


def montar_url_municipio(
    *,
    cd_eleicao: str,
    uf: str,
    cd_municipio: str,
    cargo: str = CARGO_PRESIDENTE,
) -> str:
    """
    Município: sp71072-c0001-e006258-u.json
    (prefixo = sigla UF + código TSE 5 dígitos, sem hífen)
    """
    uf = uf.lower()
    cd = str(cd_municipio).zfill(5)
    cargo_4 = cargo.zfill(4)
    eleicao_6 = cd_eleicao.zfill(6)
    nome = f"{uf}{cd}-c{cargo_4}-e{eleicao_6}-u.json"
    return f"{TSE_BASE}/{AMBIENTE}/{CICLO}/{cd_eleicao}/dados/{uf}/{nome}"


# Código IBGE da UF → arquivo de municípios (geodata-br)
IBGE_UF_CODIGO: dict[str, str] = {
    "ac": "12", "al": "27", "am": "13", "ap": "16", "ba": "29", "ce": "23",
    "df": "53", "es": "32", "go": "52", "ma": "21", "mg": "31", "ms": "50",
    "mt": "51", "pa": "15", "pb": "25", "pe": "26", "pi": "22", "pr": "41",
    "rj": "33", "rn": "24", "ro": "11", "rr": "14", "rs": "43", "sc": "42",
    "se": "28", "sp": "35", "to": "17",
}


def _parse_int(valor: str | int | None) -> int:
    if valor is None:
        return 0
    if isinstance(valor, int):
        return valor
    return int(str(valor).replace(".", "").strip() or "0")


def _parse_pct(valor: str | None) -> float | None:
    """'47,03' → 47.03"""
    if valor is None:
        return None
    try:
        return float(str(valor).replace(".", "").replace(",", "."))
    except ValueError:
        return None


def extrair_candidatos_presidente(payload: dict[str, Any]) -> list[CandidatoResumo]:
    candidatos: list[CandidatoResumo] = []
    for cargo in payload.get("carg", []):
        if str(cargo.get("cd")) != CARGO_PRESIDENTE:
            continue
        for agrupamento in cargo.get("agr", []):
            for partido in agrupamento.get("par", []):
                for cand in partido.get("cand", []):
                    candidatos.append(
                        CandidatoResumo(
                            numero=str(cand.get("n", "")),
                            nome=str(cand.get("nm", "")),
                            votos=_parse_int(cand.get("vap")),
                            percentual=str(cand.get("pvap", "0")),
                        )
                    )
    candidatos.sort(key=lambda c: c.votos, reverse=True)
    return candidatos


def _cand_dict(c: CandidatoResumo | None) -> dict[str, Any] | None:
    if c is None:
        return None
    return {
        "numero": c.numero,
        "nome": c.nome,
        "votos": c.votos,
        "percentual": c.percentual,
        "percentual_num": _parse_pct(c.percentual),
    }


def resumo_para_dict(resultado: ResultadoPresidente) -> dict[str, Any]:
    por_num = {c.numero: c for c in resultado.candidatos}
    c13 = por_num.get("13")
    c22 = por_num.get("22")
    votos_13 = c13.votos if c13 else 0
    votos_22 = c22.votos if c22 else 0
    votos_soma = votos_13 + votos_22
    lider = resultado.candidatos[0] if resultado.candidatos else None

    # -1 = 100% no 13 (vermelho), +1 = 100% no 22 (azul), None = sem votos
    score_disputa: float | None
    if votos_soma <= 0:
        score_disputa = None
    else:
        score_disputa = (votos_22 - votos_13) / votos_soma

    return {
        "eleicao": resultado.eleicao,
        "turno": resultado.turno,
        "abrangencia": resultado.abrangencia,
        "idg": resultado.idg,
        "gerado_em_tse": resultado.gerado_em,
        "secoes_totalizadas_pct": resultado.secoes_totalizadas_pct,
        "secoes_pct_num": _parse_pct(resultado.secoes_totalizadas_pct),
        "baixado_em": resultado.baixado_em,
        "fonte_url": resultado.url,
        "nome_abrangencia": UF_NOMES.get(
            resultado.abrangencia, resultado.abrangencia.upper()
        ),
        "tem_votos": votos_soma > 0,
        "votos_13": votos_13,
        "votos_22": votos_22,
        "score_disputa": score_disputa,
        "cand_13": _cand_dict(c13),
        "cand_22": _cand_dict(c22),
        "lider": _cand_dict(lider),
        "candidatos": [_cand_dict(c) for c in resultado.candidatos],
    }


def _montar_resultado(
    payload: dict[str, Any],
    *,
    cd_eleicao: str,
    abrangencia: str,
    url: str,
) -> ResultadoPresidente:
    secoes = payload.get("s") or {}
    idg = payload.get("idg")
    return ResultadoPresidente(
        eleicao=str(payload.get("ele", cd_eleicao)),
        turno=str(payload.get("t", "")),
        abrangencia=abrangencia.lower(),
        idg=str(idg) if idg is not None else None,
        gerado_em=f"{payload.get('dg', '?')} {payload.get('hg', '?')}",
        secoes_totalizadas_pct=str(secoes.get("pst", "?")),
        candidatos=extrair_candidatos_presidente(payload),
        baixado_em=datetime.now(timezone.utc).isoformat(),
        url=url,
        bruto=payload,
    )


async def buscar_presidente(
    *,
    cd_eleicao: str,
    abrangencia: str = "br",
    client: httpx.AsyncClient | None = None,
) -> ResultadoPresidente:
    url = montar_url_resultado(cd_eleicao=cd_eleicao, abrangencia=abrangencia)
    if client is None:
        async with httpx.AsyncClient(timeout=TIMEOUT_SEGUNDOS) as c:
            resposta = await c.get(url)
            resposta.raise_for_status()
            payload = resposta.json()
    else:
        resposta = await client.get(url)
        resposta.raise_for_status()
        payload = resposta.json()
    return _montar_resultado(
        payload, cd_eleicao=cd_eleicao, abrangencia=abrangencia, url=url
    )
