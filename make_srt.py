#!/usr/bin/env python3
"""
make_srt.py - transcreve video/audio e gera SRT curto no estilo TikTok
(2-3 palavras por cue, timing por palavra, padding e gaps fechados).

Feito para importar no FilmCraft (arrasta o .srt na timeline) ou em qualquer
editor que aceite SRT / WebVTT.

Uso rapido:
    python make_srt.py video.mp4
    python make_srt.py video.mp4 -w 3 --upper --tight
    python make_srt.py audio.wav -m medium -o legenda.srt

Dependencia:
    pip install faster-whisper

Nao precisa ter ffmpeg no PATH: a decodificacao usa PyAV (vem junto).
"""

from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass
from pathlib import Path

# --------------------------------------------------------------------------
# dados
# --------------------------------------------------------------------------


@dataclass
class Word:
    text: str
    start: float
    end: float


@dataclass
class Cue:
    index: int
    text: str
    start: float
    end: float


# --------------------------------------------------------------------------
# normalizacao de texto
# --------------------------------------------------------------------------

# pontuacao removida no modo "limpo" (estilo legenda de TikTok)
PUNCT = '.,!?;:...""\u201c\u201d\u2018\u2019'


def clean_word(word: str, keep_punct: bool, upper: bool) -> str:
    w = word.strip()
    if not w:
        return ""
    if not keep_punct:
        w = w.strip(PUNCT)
        # remove pontuacao solta no meio de hifen/aspas residuais
        w = w.replace("\u2014", "").replace("\u2013", "").strip()
    if not w:
        return ""
    if upper:
        w = w.upper()
    return w


# --------------------------------------------------------------------------
# agrupamento em cues
# --------------------------------------------------------------------------


def group_words(
    words: list[Word],
    max_words: int = 2,
    max_chars: int = 24,
    max_dur: float = 2.4,
    gap: float = 0.45,
) -> list[tuple[str, float, float]]:
    """Agrupa palavras em cues curtas respeitando palavras, chars, duracao e pausas."""
    cues: list[tuple[str, float, float]] = []
    cur: list[Word] = []

    def flush() -> None:
        if not cur:
            return
        text = " ".join(w.text for w in cur)
        cues.append((text, cur[0].start, cur[-1].end))
        cur.clear()

    for w in words:
        if not w.text:
            continue

        if cur:
            too_many_words = len(cur) >= max_words
            too_many_chars = max_chars > 0 and (
                sum(len(x.text) + 1 for x in cur) + len(w.text) > max_chars
            )
            too_long = (w.end - cur[0].start) > max_dur
            pause = (w.start - cur[-1].end) > gap
            ends_sentence = cur[-1].text.endswith((".", "!", "?"))

            if too_many_words or too_many_chars or too_long or pause or ends_sentence:
                flush()

        cur.append(w)

    flush()
    return cues


# --------------------------------------------------------------------------
# timing: padding, duracao minima, fechar buracos
# --------------------------------------------------------------------------


def apply_timing(
    raw: list[tuple[str, float, float]],
    pad: float = 0.06,
    min_dur: float = 0.35,
    tight: bool = False,
    tight_gap: float = 0.02,
    tight_max: float = 0.9,
) -> list[Cue]:
    n = len(raw)
    out: list[Cue] = []

    for i, (text, start, end) in enumerate(raw):
        prev_end = out[-1].end if out else -math.inf
        next_start = raw[i + 1][1] if i + 1 < n else math.inf

        s = max(start - pad, 0.0)
        e = end + pad

        # duracao minima (texto legivel)
        if e - s < min_dur:
            e = s + min_dur

        # nao invadir o vizinho
        if prev_end > -math.inf:
            s = max(s, prev_end + 0.01)
        if next_start < math.inf:
            e = min(e, next_start - 0.01)

        # fecha buracos curtos ate a proxima cue; pausa longa fica em branco
        if tight and next_start < math.inf:
            silence = next_start - e
            if silence <= tight_max:
                e = next_start - tight_gap

        # sanidade: ajusta se o padding estourou o espaco disponivel
        if e <= s:
            s = start
            e = max(end, start + min_dur)
            if next_start < math.inf:
                e = min(e, next_start - 0.005)
            if e <= s:
                e = s + 0.05

        out.append(Cue(index=len(out) + 1, text=text, start=s, end=e))

    return out


# --------------------------------------------------------------------------
# escrita SRT / VTT
# --------------------------------------------------------------------------


def fmt_time(seconds: float, sep: str) -> str:
    if seconds < 0:
        seconds = 0.0
    total_ms = round(seconds * 1000)
    h, rem = divmod(total_ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def to_srt(cues: list[Cue]) -> str:
    blocks = [
        f"{c.index}\n{fmt_time(c.start, ',')} --> {fmt_time(c.end, ',')}\n{c.text}\n"
        for c in cues
    ]
    return "\n".join(blocks).strip() + "\n"


def to_vtt(cues: list[Cue]) -> str:
    lines = ["WEBVTT", ""]
    for c in cues:
        lines.append(f"{fmt_time(c.start, '.')} --> {fmt_time(c.end, '.')}")
        lines.append(c.text)
        lines.append("")
    return "\n".join(lines).strip() + "\n"


# --------------------------------------------------------------------------
# transcricao
# --------------------------------------------------------------------------


def merge_fragments(words: list[Word]) -> list[Word]:
    """Junta fragmentos que o Whisper solta com hifen ("guarda" + "-roupa").

    Sem isso aparecem cues orfas tipo "-SE DE".
    """
    out: list[Word] = []
    for w in words:
        if not w.text:
            continue
        if out and w.text.startswith("-") and len(w.text.strip("-")) > 0:
            prev = out[-1]
            prev.text = prev.text + w.text
            prev.end = max(prev.end, w.end)
            continue
        if out and w.text.strip("-") == "":
            out[-1].end = max(out[-1].end, w.end)
            continue
        out.append(w)
    return out


def transcribe(
    media: Path,
    model_name: str,
    language: str | None,
    device: str,
    compute_type: str,
    beam_size: int,
    vad: bool,
    keep_punct: bool,
    upper: bool,
    merge: bool = True,
) -> list[Word]:
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except ImportError:
        sys.exit(
            "faster-whisper nao instalado.\n"
            "Rode:  pip install faster-whisper\n"
            "Teste sem modelo:  python make_srt.py --self-test"
        )

    def run(dev: str, ctype: str) -> list[Word]:
        print(f"[1/3] carregando modelo '{model_name}' ({dev}/{ctype}) ...")
        model = WhisperModel(model_name, device=dev, compute_type=ctype)

        print(f"[2/3] transcrevendo {media.name} ...")
        segments, info = model.transcribe(
            str(media),
            language=language,
            beam_size=beam_size,
            word_timestamps=True,
            vad_filter=vad,
            condition_on_previous_text=False,
        )
        print(f"      idioma detectado: {info.language} ({info.language_probability:.0%})")

        out: list[Word] = []
        # consome o gerador aqui dentro para que erros de runtime caiam no retry
        for seg in segments:
            for w in seg.words or []:
                text = clean_word(w.word, keep_punct=keep_punct, upper=upper)
                out.append(Word(text=text, start=float(w.start), end=float(w.end)))
        return out

    if device == "cpu":
        words = run("cpu", "int8" if compute_type == "auto" else compute_type)
    else:
        try:
            words = run(
                "cuda" if device == "auto" else device,
                "float16" if compute_type == "auto" else compute_type,
            )
        except Exception as exc:  # driver/CUDA ausente, cublas faltando, OOM...
            print(f"      GPU indisponivel ({type(exc).__name__}: {exc})")
            print("      caindo para CPU (mais lento, mesmo resultado)")
            words = run("cpu", "int8" if compute_type == "auto" else compute_type)

    words = [w for w in words if w.text]
    if merge:
        words = merge_fragments(words)
    print(f"[3/3] {len(words)} palavras com timing")
    return words


# --------------------------------------------------------------------------
# self-test (sem modelo)
# --------------------------------------------------------------------------


def self_test(max_words: int, upper: bool, tight: bool = True, tight_max: float = 0.6) -> None:
    demo = [
        ("isso", 0.10, 0.32),
        ("aqui", 0.34, 0.55),
        ("e", 0.56, 0.62),
        ("muito", 0.63, 0.90),
        ("facil", 0.92, 1.20),
        # pausa longa -> deve quebrar cue
        ("de", 2.10, 2.20),
        ("fazer", 2.22, 2.55),
        ("legenda", 2.57, 2.95),
        ("bonita", 2.97, 3.40),
    ]
    words = [
        Word(clean_word(t, keep_punct=False, upper=upper) or t, s, e) for t, s, e in demo
    ]
    raw = group_words(words, max_words=max_words)
    cues = apply_timing(raw, tight=tight, tight_max=tight_max)
    print(to_srt(cues))


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Gera SRT curto estilo TikTok (2-3 palavras por cue) via faster-whisper.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("media", nargs="?", help="video ou audio de entrada")
    p.add_argument("-o", "--output", help="arquivo .srt de saida")
    p.add_argument("-m", "--model", default="small",
                   help="tiny|base|small|medium|large-v3|distil-large-v3")
    p.add_argument("-l", "--lang", default="pt", help="idioma (pt, en, es...) ou 'auto'")
    p.add_argument("-w", "--words", type=int, default=2,
                   help="maximo de palavras por cue (1 = uma palavra por vez)")
    p.add_argument("--max-chars", type=int, default=24, help="maximo de chars por cue (0 = off)")
    p.add_argument("--max-dur", type=float, default=2.4, help="duracao maxima da cue em segundos")
    p.add_argument("--gap", type=float, default=0.45,
                   help="pausa (s) que forca quebra de cue")
    p.add_argument("--pad", type=float, default=0.06, help="padding em volta de cada cue (s)")
    p.add_argument("--min-dur", type=float, default=0.35, help="duracao minima da cue (s)")
    p.add_argument("--tight", action="store_true",
                   help="estica a cue ate a proxima (visual continuo de TikTok)")
    p.add_argument("--tight-max", type=float, default=0.6,
                   help="com --tight, fecha buracos de ate N segundos (pausa maior fica vazia)")
    p.add_argument("--upper", action="store_true", help="TEXTO EM CAIXA ALTA")
    p.add_argument("--keep-punct", action="store_true", help="manter pontuacao")
    p.add_argument("--vtt", action="store_true", help="escrever tambem um .vtt")
    p.add_argument("--device", default="auto", help="auto|cpu|cuda")
    p.add_argument("--compute-type", default="auto", help="auto|int8|float16|float32")
    p.add_argument("--beam-size", type=int, default=5)
    p.add_argument("--no-vad", action="store_true", help="desliga o filtro de silencio")
    p.add_argument("--no-merge", action="store_true",
                   help="nao junta fragmentos hifenizados (guarda + -roupa)")
    p.add_argument("--self-test", action="store_true",
                   help="testa o agrupamento sem baixar modelo")
    args = p.parse_args(argv)

    if args.self_test:
        self_test(args.words, args.upper, tight=args.tight, tight_max=args.tight_max)
        return 0

    if not args.media:
        p.error("informe o arquivo de video/audio (ou use --self-test)")

    media = Path(args.media).expanduser()
    if not media.exists():
        sys.exit(f"arquivo nao encontrado: {media}")

    if not 1 <= args.words <= 5:
        sys.exit("--words deve ficar entre 1 e 5")

    words = transcribe(
        media=media,
        model_name=args.model,
        language=None if args.lang.lower() == "auto" else args.lang,
        device=args.device,
        compute_type=args.compute_type,
        beam_size=args.beam_size,
        vad=not args.no_vad,
        keep_punct=args.keep_punct,
        upper=args.upper,
        merge=not args.no_merge,
    )
    if not words:
        sys.exit("nenhuma fala detectada")

    raw = group_words(
        words,
        max_words=args.words,
        max_chars=args.max_chars,
        max_dur=args.max_dur,
        gap=args.gap,
    )
    cues = apply_timing(
        raw,
        pad=args.pad,
        min_dur=args.min_dur,
        tight=args.tight,
        tight_max=args.tight_max,
    )

    out = Path(args.output) if args.output else media.with_suffix(".srt")
    out.write_text(to_srt(cues), encoding="utf-8")

    print(f"\nOK -> {out}  ({len(cues)} cues, {cues[-1].end:.1f}s)")
    if args.vtt:
        vtt = out.with_suffix(".vtt")
        vtt.write_text(to_vtt(cues), encoding="utf-8")
        print(f"OK -> {vtt}")
    print("\nDica FilmCraft: importe o video, File > New > Sequence From Clip,"
          "\nso depois arraste o .srt na timeline (vira caption track C1).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
