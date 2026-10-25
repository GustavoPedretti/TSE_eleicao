"""Normaliza snapshots antigos (sem score_disputa) na leitura."""

from __future__ import annotations

from typing import Any


def enriquecer_resumo(resumo: dict[str, Any] | None) -> dict[str, Any] | None:
    if not resumo:
        return resumo
    out = dict(resumo)
    cands = out.get("candidatos") or []
    por = {str(c.get("numero")): c for c in cands if isinstance(c, dict)}

    def _num(c: dict | None) -> dict | None:
        return c

    c13 = out.get("cand_13") or por.get("13")
    c22 = out.get("cand_22") or por.get("22")
    if c13 and not out.get("cand_13"):
        out["cand_13"] = c13
    if c22 and not out.get("cand_22"):
        out["cand_22"] = c22

    v13 = int((c13 or {}).get("votos") or out.get("votos_13") or 0)
    v22 = int((c22 or {}).get("votos") or out.get("votos_22") or 0)
    out["votos_13"] = v13
    out["votos_22"] = v22
    soma = v13 + v22
    out["tem_votos"] = soma > 0
    out["score_disputa"] = ((v22 - v13) / soma) if soma > 0 else None
    return out
