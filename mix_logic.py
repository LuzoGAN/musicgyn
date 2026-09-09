"""Lógica do Modo Aula: fila justa base + pedidos.

Problema resolvido: um aluno pede 10 músicas e outro pede 1 — sem justiça,
só o primeiro toca. Aqui aplicamos:

1. Pedidos ordenados por round-robin por pessoa (com sorteio dentro de
   cada pessoa), então ninguém emenda 2 pedidos seguidos enquanto houver
   outras pessoas na fila.
2. Intercalação com a playlist base (ex: 3 da base + 1 pedido).
3. Corte pela duração da aula (padrão 60 min p/ CrossFit) — se há pedidos
   demais, o sorteio justo decide quem entra, não a ordem de chegada.
"""

import random

DEFAULT_TRACK_SECONDS = 210  # ~3min30 por faixa quando não sabemos a duração


def _norm_user(name):
    return (name or "anonimo").strip().lower() or "anonimo"


def _duration(item):
    try:
        d = int((item.get("duration_sec") if isinstance(item, dict)
                 else getattr(item, "duration_sec", None)) or 0)
    except (TypeError, ValueError):
        d = 0
    return d if d > 0 else DEFAULT_TRACK_SECONDS


def _as_dict(item):
    if isinstance(item, dict):
        return dict(item)
    return {
        "title": getattr(item, "title", "Faixa"),
        "artist": getattr(item, "artist", None),
        "platform": getattr(item, "platform", None),
        "platform_url": getattr(item, "platform_url", None),
        "embed_id": getattr(item, "embed_id", None),
        "duration_sec": getattr(item, "duration_sec", None),
        "suggested_by": getattr(item, "suggested_by", None),
        "requested_by": getattr(item, "suggested_by", None) or getattr(item, "requested_by", None),
        "id": getattr(item, "id", None),
    }


def fair_order_requests(songs, seed=None):
    """Ordena pedidos de forma justa (round-robin por pessoa + sorteio).

    - Agrupa por quem pediu (case-insensitive).
    - Sorteia a ordem dentro de cada pessoa e a ordem das pessoas.
    - Intercala uma de cada pessoa por rodada.
    Retorna nova lista (não altera a original).
    """
    songs = list(songs or [])
    if len(songs) <= 1:
        return list(songs)
    rng = random.Random(seed)
    groups = {}
    for s in songs:
        d = _as_dict(s)
        groups.setdefault(_norm_user(d.get("suggested_by") or d.get("requested_by")), []).append(s)
    for bucket in groups.values():
        rng.shuffle(bucket)
    keys = list(groups.keys())
    rng.shuffle(keys)
    ordered = []
    while any(groups[k] for k in keys):
        for k in keys:
            if groups[k]:
                ordered.append(groups[k].pop(0))
    return ordered


def build_class_queue(base_tracks, requests, base_per_request=3,
                      target_minutes=60, seed=None):
    """Monta a fila da aula e retorna (itens, total_segundos).

    Cada item: dict com kind ('base'|'request'|'block'), title, artist,
    platform, platform_url, embed_id, requested_by, duration_sec.

    - base_tracks: faixas cadastradas da playlist base (pode ser vazio — aí
      usamos blocos da playlist por link).
    - requests: pedidos de preferência já em ordem justa; por segurança,
      aplicamos fair_order de novo quando seed é dado.
    - base_per_request: quantas da base a cada 1 pedido (<=0 = só pedidos).
    - target_minutes: corta a fila p/ caber na aula (limitado a 1–480).
    """
    rng = random.Random(seed)
    base = [_as_dict(t) for t in (base_tracks or [])]
    reqs = [_as_dict(r) for r in (requests or [])]
    if seed is not None and reqs:
        reqs = [_as_dict(r) for r in fair_order_requests(reqs, seed=seed)]
    rng.shuffle(base)

    try:
        bpr = int(base_per_request)
    except (TypeError, ValueError):
        bpr = 3
    try:
        target_min = min(480, max(1, int(target_minutes)))
    except (TypeError, ValueError):
        target_min = 60
    target_sec = target_min * 60

    queue, total = [], 0

    def push(kind, d):
        nonlocal total
        dur = _duration(d)
        item = {
            "kind": kind,
            "title": d.get("title") or "Faixa",
            "artist": d.get("artist"),
            "platform": d.get("platform"),
            "platform_url": d.get("platform_url"),
            "embed_id": d.get("embed_id"),
            "requested_by": d.get("requested_by") or d.get("suggested_by"),
            "duration_sec": dur,
            "source_id": d.get("id"),
        }
        queue.append(item)
        total += dur

    # Sem faixas cadastradas na base: a playlist toca por link no player
    # externo; o roteiro alterna blocos da base (N faixas estimadas) com
    # 1 pedido. Ex: bpr=3 -> bloco de ~10min da base + 1 pedido.
    if not base:
        if not reqs:
            return [], 0
        if bpr <= 0:
            for r in reqs:
                if total >= target_sec:
                    break
                push("request", r)
            return queue, total
        block_sec = max(1, bpr) * DEFAULT_TRACK_SECONDS
        for r in reqs:
            if total >= target_sec:
                break
            push("block", {"title": "Bloco da playlist base",
                           "duration_sec": min(block_sec, target_sec - total)})
            if total >= target_sec:
                break
            push("request", r)
        # pedidos acabaram mas ainda há tempo: completa com a base
        while total < target_sec:
            push("block", {"title": "Bloco da playlist base",
                           "duration_sec": min(block_sec, target_sec - total)})
        return queue, total

    # Com faixas na base: intercala N base + 1 pedido, ciclando a base
    # (playlist em loop) até encher o tempo da aula.
    if bpr <= 0:
        for r in reqs:
            if total >= target_sec:
                break
            push("request", r)
        # se ainda há tempo e zero base solicitada, completa? não — respeita.
        return queue, total

    base_cycle = list(base)
    bi, ri = 0, 0
    cycles = 0
    stalled = False
    while total < target_sec and not stalled:
        # N faixas da base
        for _ in range(bpr):
            if total >= target_sec:
                break
            if bi >= len(base_cycle):
                bi = 0
                cycles += 1
                rng.shuffle(base_cycle)
                if cycles > 50 and ri >= len(reqs):
                    stalled = True  # trava de segurança: nada mais a enfileirar
                    break
            push("base", base_cycle[bi])
            bi += 1
        if total >= target_sec or stalled:
            break
        # 1 pedido (sem repetir: pedidos tocam uma vez só)
        if ri < len(reqs):
            push("request", reqs[ri])
            ri += 1
        # senão: segue só com a base até encher
    return queue, total


def queue_summary(queue):
    """Resumo p/ UI e testes: totais por tipo e por pessoa."""
    by_user = {}
    counts = {"base": 0, "request": 0, "block": 0}
    total = 0
    for it in queue or []:
        counts[it.get("kind", "base")] = counts.get(it.get("kind"), 0) + 1
        total += it.get("duration_sec") or DEFAULT_TRACK_SECONDS
        if it.get("kind") == "request":
            u = _norm_user(it.get("requested_by"))
            by_user[u] = by_user.get(u, 0) + 1
    return {"counts": counts, "total_sec": total,
            "total_min": round(total / 60, 1), "by_user": by_user}
