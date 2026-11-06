#!/usr/bin/env python3
"""
analyze.py - Fase 2: escolhe os melhores trechos do video.

Sinais usados (todos medidos na timeline de saida, depois do corte de silencio):
  - densidade de fala  (palavras por segundo)
  - energia do audio   (RMS, picos de empolgacao)
  - troca de cena      (diferenca entre frames consecutivos)

O score e a soma ponderada. As bordas do clipe sao encaixadas em inicio/fim de
palavra, entao o corte nunca cai no meio de uma frase.
"""

from __future__ import annotations

import json
import math
import os
import re
import urllib.request
from dataclasses import dataclass

import av
import numpy as np

GRID = 0.5  # resolucao da analise, em segundos


@dataclass
class Clip:
    start: float          # tempo na timeline de saida
    end: float
    score: float
    text: str = ""

    @property
    def dur(self) -> float:
        return self.end - self.start


# --------------------------------------------------------------------------
# sinais
# --------------------------------------------------------------------------


def rms_envelope(audio: np.ndarray, rate: int, hop: float = 0.05) -> np.ndarray:
    """Energia RMS por janela de `hop` segundos."""
    if audio.size == 0:
        return np.zeros(1, dtype=np.float32)
    mono = audio.mean(axis=0) if audio.ndim > 1 else audio
    win = max(1, int(rate * hop))
    n = max(1, len(mono) // win)
    trimmed = mono[: n * win].reshape(n, win)
    return np.sqrt((trimmed.astype(np.float32) ** 2).mean(axis=1))


def scene_cuts(
    path,
    width: int = 64,
    height: int = 36,
    threshold: float = 12.0,
    max_scan: float = 1800.0,
) -> list[float]:
    """Timestamps (na fonte) onde o frame muda bruscamente."""
    cuts: list[float] = []
    prev = None
    try:
        with av.open(str(path)) as container:
            stream = container.streams.video[0]
            for frame in container.decode(stream):
                t = float(frame.pts * frame.time_base)
                if t > max_scan:
                    break
                small = frame.reformat(width=width, height=height, format="gray")
                arr = np.asarray(small.to_ndarray(), dtype=np.float32)
                if prev is not None and float(np.abs(arr - prev).mean()) > threshold:
                    cuts.append(t)
                prev = arr
    except Exception as exc:
        print(f"      deteccao de cena falhou ({exc}) - seguindo sem ela")
    return cuts


# --------------------------------------------------------------------------
# score
# --------------------------------------------------------------------------


def _map_words(segments, words) -> list[tuple[float, float]]:
    """Palavras com tempo na timeline de saida (ignora as que caem em corte)."""
    from autoedit import remap

    out = []
    for w in words:
        a = remap(segments, w.start)
        b = remap(segments, w.end - 1e-4)
        if a is None and b is None:
            continue
        a = a if a is not None else b
        b = b if b is not None else a
        out.append((a, b))
    return out


def score_clips(
    segments,
    words,
    audio: np.ndarray,
    rate: int,
    scenes: list[float],
    clip_len: float = 45.0,
    clips: int = 3,
    hop: float = 3.0,
    min_len: float = 15.0,
    weights: tuple[float, float, float] = (0.5, 0.32, 0.18),
) -> list[Clip]:
    from autoedit import remap

    total = sum(s.dur for s in segments)
    if total <= 0:
        return []

    clip_len = min(clip_len, total)
    min_len = min(min_len, clip_len)

    cells = max(1, int(math.ceil(total / GRID)))
    speech = np.zeros(cells, dtype=np.float32)
    energy = np.zeros(cells, dtype=np.float32)
    scene = np.zeros(cells, dtype=np.float32)

    spans = _map_words(segments, words)
    for a, b in spans:
        for c in range(int(a / GRID), min(cells, int(b / GRID) + 1)):
            speech[c] = 1.0

    env = rms_envelope(audio, rate)
    if env.size:
        idx = np.clip((np.arange(cells) * GRID / 0.05).astype(int), 0, env.size - 1)
        raw = env[idx]
        peak = float(np.percentile(raw, 95)) or 1.0
        energy = np.clip(raw / peak, 0.0, 1.0)

    for t in scenes:
        o = remap(segments, t)
        if o is not None:
            c = int(o / GRID)
            if 0 <= c < cells:
                scene[c] = min(2.0, scene[c] + 1.0) / 2.0

    ws, we, wc = weights
    wlen = max(1, int(round(clip_len / GRID)))
    step = max(1, int(round(hop / GRID)))

    scored = []
    for start_cell in range(0, max(1, cells - wlen + 1), step):
        end_cell = min(cells, start_cell + wlen)
        s = (
            ws * float(speech[start_cell:end_cell].mean())
            + we * float(energy[start_cell:end_cell].mean())
            + wc * float(scene[start_cell:end_cell].mean())
        )
        scored.append((s, start_cell * GRID, end_cell * GRID))

    scored.sort(reverse=True)

    picked: list[Clip] = []
    for s, a, b in scored:
        # encaixa nas bordas de palavra (nao corta fala no meio)
        start = _snap_start(spans, a)
        end = _snap_end(spans, b)
        if end - start < min_len:
            continue
        if any(not (end + 1.0 <= p.start or start - 1.0 >= p.end) for p in picked):
            continue
        text = _text_between(words, segments, start, end)
        picked.append(Clip(start=start, end=end, score=s, text=text))
        if len(picked) >= clips:
            break

    picked.sort(key=lambda c: c.start)
    return picked


def _snap_start(spans, t: float, tol: float = 0.6) -> float:
    best = t
    for a, _ in spans:
        if abs(a - t) <= tol:
            return a
        if a <= t:
            best = a
    return best


def _snap_end(spans, t: float, tol: float = 0.6) -> float:
    best = t
    for _, b in spans:
        if abs(b - t) <= tol:
            return b
        if b <= t:
            best = b
    return best


def _text_between(words, segments, start: float, end: float) -> str:
    from autoedit import remap

    picked = []
    for w in words:
        a = remap(segments, w.start)
        if a is not None and start - 0.3 <= a <= end:
            picked.append(w.text)
    return " ".join(picked)


# --------------------------------------------------------------------------
# gancho (hook)
# --------------------------------------------------------------------------


HOOK_WORDS = {"maior", "melhor", "pior", "nunca", "sempre", "segredo", "erro",
              "gratis", "grátis", "de graça", "de graca", "ninguem", "ninguém",
              "impossivel", "impossível", "absurdo", "verdade", "mentira"}


STOPWORDS = {"e", "a", "o", "as", "os", "de", "da", "do", "das", "dos", "que",
             "um", "uma", "mas", "entao", "então", "tipo", "ai", "aí", "pra",
             "para", "com", "no", "na", "nos", "nas", "em", "ele", "ela", "eu",
             "voce", "você", "isso", "esse", "essa", "aqui", "muito", "vai",
             "quando", "ta", "tá"}


def heuristic_hook(text: str, max_words: int = 6) -> str:
    """Pega a frase mais 'chamativa' da transcricao (sem depender de LLM)."""
    words = text.split()
    if not words:
        return ""
    if len(words) <= max_words:
        return text.upper()

    best_score = -1e9
    best = words[:max_words]

    for size in (max_words, max_words - 1):
        if size < 3:
            continue
        for i in range(0, len(words) - size + 1):
            chunk = words[i:i + size]
            low = " ".join(chunk).lower()
            score = 0.0
            score += 2.0 * sum(1 for kw in HOOK_WORDS if kw in low)
            score += 1.5 * (1 if re.search(r"\d", low) else 0)
            score += 0.6 * sum(1 for w in chunk if len(w) > 3 and w.lower() not in STOPWORDS)
            score -= 1.2 * sum(1 for w in chunk if w.lower() in STOPWORDS)
            # gancho bom nao comeca com conectivo
            score -= 1.5 if chunk[0].lower() in STOPWORDS else 0.0
            if score > best_score:
                best_score = score
                best = chunk

    hook = " ".join(best).strip(" ,;:-")
    hook_words = hook.split()
    while len(hook_words) > 3 and hook_words[-1].lower() in STOPWORDS:
        hook_words.pop()
    return " ".join(hook_words).upper()


def llm_hook(text: str, timeout: float = 20.0) -> str | None:
    key = os.environ.get("OPENAI_API_KEY") or os.environ.get("OPENROUTER_API_KEY")
    if not key:
        return None
    base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.environ.get("AUTOEDIT_HOOK_MODEL", "gpt-4o-mini")
    payload = {
        "model": model,
        "temperature": 0.8,
        "max_tokens": 40,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Voce escreve ganchos de TikTok em portugues do Brasil. "
                    "Responda APENAS o gancho: no maximo 6 palavras, caixa alta, "
                    "sem hashtag, sem emoji, sem aspas, sem ponto final."
                ),
            },
            {
                "role": "user",
                "content": f"Trecho transcrito:\n{text[:1500]}\n\nEscreva o gancho.",
            },
        ],
    }
    req = urllib.request.Request(
        f"{base}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        hook = data["choices"][0]["message"]["content"].strip()
        hook = hook.strip('"\'').split("\n")[0].strip()
        return hook.upper() if hook else None
    except Exception as exc:
        print(f"      hook via LLM falhou ({exc}) - usando heuristica")
        return None


def make_hook(text: str, use_llm: bool = False) -> str:
    if use_llm:
        hook = llm_hook(text)
        if hook:
            return hook
    return heuristic_hook(text)
