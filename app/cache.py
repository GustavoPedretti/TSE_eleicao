"""
Cache em memória — BR + mapa de UFs.

Na Vercel cada invocação começa fria: o cache ajuda *dentro* do request
e no processo local com poller contínuo. Persistência real = banco.
"""

from __future__ import annotations

import threading
from typing import Any

_lock = threading.Lock()

_STATE: dict[str, Any] = {
    "br": None,
    "ufs": {},  # sigla -> resumo
    "eleicao": None,
    "idg_br": None,
    "atualizado_em": None,
    "ultimo_poll_em": None,
    "ultimo_erro": None,
    "polls_ok": 0,
    "polls_erro": 0,
    "mudancas": 0,
    "hits": 0,
}


def set_br(
    *,
    eleicao: str,
    idg: str | None,
    data: dict[str, Any],
    atualizado_em: str,
    mudou: bool,
    contar_poll: bool = True,
) -> None:
    with _lock:
        _STATE["br"] = data
        _STATE["eleicao"] = eleicao
        _STATE["idg_br"] = idg
        _STATE["atualizado_em"] = atualizado_em
        _STATE["ultimo_erro"] = None
        if contar_poll:
            _STATE["ultimo_poll_em"] = atualizado_em
            _STATE["polls_ok"] += 1
            if mudou:
                _STATE["mudancas"] += 1


def set_uf(abr: str, data: dict[str, Any]) -> None:
    with _lock:
        _STATE["ufs"][abr.lower()] = data


def set_ufs_bulk(ufs: dict[str, dict[str, Any]]) -> None:
    with _lock:
        _STATE["ufs"].update({k.lower(): v for k, v in ufs.items()})


def marcar_poll_ok(*, em: str) -> None:
    with _lock:
        _STATE["ultimo_poll_em"] = em
        _STATE["ultimo_erro"] = None
        _STATE["polls_ok"] += 1


def marcar_erro(*, em: str, erro: str) -> None:
    with _lock:
        _STATE["ultimo_poll_em"] = em
        _STATE["ultimo_erro"] = erro
        _STATE["polls_erro"] += 1


def tem_br() -> bool:
    with _lock:
        return _STATE["br"] is not None


def get_br(*, contar_hit: bool = True) -> dict[str, Any] | None:
    with _lock:
        data = _STATE["br"]
        if data is not None and contar_hit:
            _STATE["hits"] += 1
        return data


def get_ufs() -> dict[str, dict[str, Any]]:
    with _lock:
        return dict(_STATE["ufs"])


def snapshot_meta() -> dict[str, Any]:
    with _lock:
        return {
            "eleicao": _STATE["eleicao"],
            "idg_br": _STATE["idg_br"],
            "tem_br": _STATE["br"] is not None,
            "ufs_em_cache": len(_STATE["ufs"]),
            "atualizado_em": _STATE["atualizado_em"],
            "ultimo_poll_em": _STATE["ultimo_poll_em"],
            "ultimo_erro": _STATE["ultimo_erro"],
            "polls_ok": _STATE["polls_ok"],
            "polls_erro": _STATE["polls_erro"],
            "mudancas": _STATE["mudancas"],
            "hits": _STATE["hits"],
        }
