#!/usr/bin/env python3
"""
autoedit.py - Fase 1 do auto-editor para TikTok.

O que faz:
  1. transcreve com word timestamps (faster-whisper, via make_srt.py)
  2. detecta silencios longos e corta eles (jump cuts)
  3. reframa 16:9 -> 9:16 (crop no centro ou fundo desfocado)
  4. queima legenda animada: 2-3 palavras, palavra ativa destacada e maior
  5. exporta MP4 H.264 + AAC, pronto pra postar

Sem ffmpeg no PATH: usa PyAV (ja instalado) pra decodificar e codificar.

Uso:
    python autoedit.py video.mp4
    python autoedit.py video.mp4 --max-silence 0.3 --words 2 --reframe blur
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

import av
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent))
import make_srt as ms  # noqa: E402

# --------------------------------------------------------------------------
# estilo
# --------------------------------------------------------------------------

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\seguibl.ttf",
    r"C:\Windows\Fonts\ariblk.ttf",
    r"C:\Windows\Fonts\arialbd.ttf",
]

TEXT_COLOR = (255, 255, 255, 255)
ACTIVE_COLOR = (255, 212, 0, 255)   # amarelo TikTok
OUTLINE = (0, 0, 0, 255)


# --------------------------------------------------------------------------
# corte por silencio
# --------------------------------------------------------------------------


@dataclass
class Segment:
    src_in: float
    src_out: float
    out_in: float = 0.0

    @property
    def dur(self) -> float:
        return self.src_out - self.src_in


def speech_segments(
    words: list[ms.Word],
    lead: float = 0.12,
    tail: float = 0.18,
    max_silence: float = 0.35,
    min_seg: float = 0.25,
) -> list[Segment]:
    """Agrupa fala e corta pausas maiores que max_silence, guardando respiro."""
    spans: list[list[float]] = []
    for w in words:
        if spans and (w.start - spans[-1][1]) <= max_silence:
            spans[-1][1] = max(spans[-1][1], w.end)
        else:
            spans.append([w.start, w.end])

    padded: list[list[float]] = []
    for s, e in spans:
        s = max(0.0, s - lead)
        e = e + tail
        if padded and s <= padded[-1][1]:
            padded[-1][1] = max(padded[-1][1], e)
        else:
            padded.append([s, e])

    segs = [Segment(s, e) for s, e in padded if e - s >= min_seg]

    # tempos na timeline de saida
    cursor = 0.0
    for seg in segs:
        seg.out_in = cursor
        cursor += seg.dur
    return segs


def remap(segments: list[Segment], src_time: float) -> float | None:
    """Tempo na fonte -> tempo na saida (None se caiu no corte)."""
    for seg in segments:
        if seg.src_in <= src_time < seg.src_out:
            return seg.out_in + (src_time - seg.src_in)
    return None


def slice_segments(segments: list[Segment], start: float, end: float) -> list[Segment]:
    """Recorta a lista de trechos por um intervalo da timeline de saida."""
    out: list[Segment] = []
    for seg in segments:
        o_in = max(seg.out_in, start)
        o_out = min(seg.out_in + seg.dur, end)
        if o_out - o_in <= 0.02:
            continue
        src_in = seg.src_in + (o_in - seg.out_in)
        out.append(Segment(src_in, src_in + (o_out - o_in), out_in=o_in - start))
    return out


# --------------------------------------------------------------------------
# legendas
# --------------------------------------------------------------------------


def pick_font(size: int, path: str | None = None) -> ImageFont.FreeTypeFont:
    candidates = [path] if path else FONT_CANDIDATES
    for cand in candidates:
        if cand and Path(cand).exists():
            return ImageFont.truetype(cand, size)
    return ImageFont.load_default()


@dataclass
class Cue:
    words: list[ms.Word]
    start: float
    end: float
    top: bool = False


def build_cues(
    words: list[ms.Word],
    max_words: int = 2,
    max_chars: int = 24,
    max_dur: float = 2.4,
    gap: float = 0.45,
    pad: float = 0.06,
) -> list[Cue]:
    raw = ms.group_words(words, max_words=max_words, max_chars=max_chars,
                         max_dur=max_dur, gap=gap)
    timed = ms.apply_timing(raw, pad=pad, tight=True)

    cues: list[Cue] = []
    # reagrupa as palavras nas mesmas regras do group_words
    groups: list[list[ms.Word]] = []
    cur: list[ms.Word] = []
    for w in words:
        if cur:
            too_many = len(cur) >= max_words
            too_chars = max_chars > 0 and (
                sum(len(x.text) + 1 for x in cur) + len(w.text) > max_chars
            )
            too_long = (w.end - cur[0].start) > max_dur
            pause = (w.start - cur[-1].end) > gap
            ends = cur[-1].text.endswith((".", "!", "?"))
            if too_many or too_chars or too_long or pause or ends:
                groups.append(cur)
                cur = []
        cur.append(w)
    if cur:
        groups.append(cur)

    for gi, group in enumerate(groups):
        if gi >= len(timed):
            break
        t = timed[gi]
        cues.append(Cue(words=group, start=t.start, end=t.end))
    return cues


class CaptionPainter:
    """Desenha a linha de legenda com a palavra ativa destacada.

    Cada variacao visual e renderizada uma vez e reaproveitada (cache),
    entao o custo por frame e so um paste.
    """

    def __init__(
        self,
        out_w: int,
        out_h: int,
        font_size: int | None = None,
        font_path: str | None = None,
        bottom_margin: float = 0.18,
        box: bool = False,
        hook: Cue | None = None,
    ) -> None:
        self.out_w = out_w
        self.out_h = out_h
        self.font_path = font_path
        self.base_size = font_size or max(28, int(out_h * 0.055))
        self.bottom_margin = bottom_margin
        self.box = box
        self.cache: dict[tuple, Image.Image] = {}
        self.fonts: dict[int, ImageFont.FreeTypeFont] = {}
        self.max_text_w = int(out_w * 0.90)
        self.baseline_y = int(out_h * (1.0 - bottom_margin))
        self._cursor = 0
        self.hook = hook
        self._hook_img: Image.Image | None = None

    def reset(self) -> None:
        self._cursor = 0

    def font(self, size: int) -> ImageFont.FreeTypeFont:
        size = max(8, size)
        if size not in self.fonts:
            self.fonts[size] = pick_font(size, self.font_path)
        return self.fonts[size]

    def active_index(self, cue: Cue, t: float) -> int:
        idx = 0
        for i, w in enumerate(cue.words):
            if w.start <= t + 0.02:
                idx = i
        return idx

    def _split(self, words: list[ms.Word], n: int) -> list[list[ms.Word]]:
        if n <= 1 or len(words) <= 1:
            return [words]
        total = sum(len(w.text) + 1 for w in words)
        target = total / n
        lines: list[list[ms.Word]] = []
        cur: list[ms.Word] = []
        acc = 0
        for w in words:
            if cur and acc + len(w.text) + 1 > target and len(lines) < n - 1:
                lines.append(cur)
                cur, acc = [], 0
            cur.append(w)
            acc += len(w.text) + 1
        lines.append(cur)
        return lines

    def _fit(self, words, active, nlines: int, base: int):
        """Acha o maior tamanho de fonte que faz as linhas caberem."""
        lines = self._split(words, nlines)
        size = base
        while size >= 18:
            asz = int(size * 1.12)
            offset = 0
            widths = []
            fits = True
            for line in lines:
                w = 0
                for i, word in enumerate(line):
                    f = self.font(asz if offset + i == active else size)
                    w += f.getbbox(word.text)[2]
                    if i:
                        w += int(size * 0.26)
                widths.append(w)
                offset += len(line)
                if w > self.max_text_w:
                    fits = False
                    break
            if fits:
                return lines, size, max(widths) if widths else 0, True
            size = int(size * 0.92)
        return lines, 18, 0, False

    def _strip(self, cue: Cue, active: int, all_color=None) -> Image.Image:
        key = (id(cue), active, all_color)
        if key in self.cache:
            return self.cache[key]

        if cue.top:
            # gancho: fonte maior e quebra em 2-3 linhas (nunca espreme numa linha so)
            n = max(2, min(3, (len(cue.words) + 1) // 2))
            base = int(self.base_size * 1.25)
            lines, size, line_width, fits = self._fit(cue.words, active, n, base)
            if not fits:
                lines, size, line_width, fits = self._fit(cue.words, active, 2, base)
        else:
            base = self.base_size
            lines, size, line_width, fits = self._fit(cue.words, active, 1, base)
            if not fits:
                lines, size, line_width, fits = self._fit(cue.words, active, 2, base)
        if not fits:
            line_width = self.max_text_w

        active_size = int(size * 1.12)
        stroke = max(2, int(size * 0.085))
        line_h = int(max(size, active_size) * 1.28)
        width = int(line_width + stroke * 4)
        height = int(line_h * len(lines) + stroke * 4)

        img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        if self.box or cue.top:
            pad = int(size * 0.22)
            draw.rounded_rectangle(
                (pad // 2, pad // 2, width - pad // 2, height - pad // 2),
                radius=int(size * 0.18), fill=(0, 0, 0, 165),
            )

        offset = 0
        for li, line in enumerate(lines):
            x = (width - self._line_width(line, active, offset, size, active_size)) / 2
            base_y = stroke * 2 + line_h * (li + 1) - int(size * 0.28)
            for i, w in enumerate(line):
                idx = offset + i
                f = self.font(active_size if idx == active else size)
                color = all_color or (ACTIVE_COLOR if idx == active else TEXT_COLOR)
                draw.text((x, base_y), w.text, font=f, fill=color, anchor="ls",
                          stroke_width=stroke, stroke_fill=OUTLINE)
                x += f.getbbox(w.text)[2] + int(size * 0.26)
            offset += len(line)

        self.cache[key] = img
        return img

    def _line_width(self, line, active, offset, size, active_size) -> float:
        total = 0.0
        for i, word in enumerate(line):
            f = self.font(active_size if offset + i == active else size)
            total += f.getbbox(word.text)[2]
            if i:
                total += int(size * 0.26)
        return total

    def paint(self, frame_rgb: Image.Image, t: float, cues: list[Cue]) -> Image.Image:
        # cursor: as cues estao ordenadas, nao precisa varrer todas por frame
        i = self._cursor
        n = len(cues)
        while i < n and cues[i].end <= t:
            i += 1
        self._cursor = i
        cue = cues[i] if i < n and cues[i].start <= t < cues[i].end else None
        if cue is not None:
            strip = self._strip(cue, self.active_index(cue, t))
            x = (self.out_w - strip.width) // 2
            frame_rgb.paste(strip, (x, self.baseline_y - strip.height), strip)

        # gancho e uma camada separada: fica no topo enquanto a legenda roda embaixo
        if self.hook is not None and self.hook.start <= t < self.hook.end:
            if self._hook_img is None:
                self._hook_img = self._strip(self.hook, -1, all_color=ACTIVE_COLOR)
            hx = (self.out_w - self._hook_img.width) // 2
            hy = int(self.out_h * 0.08)
            frame_rgb.paste(self._hook_img, (hx, hy), self._hook_img)

        return frame_rgb


# --------------------------------------------------------------------------
# reframe
# --------------------------------------------------------------------------


def clamp01(v: float) -> float:
    return min(max(v, 0.0), 1.0)


def reframe_frame(img: Image.Image, out_w: int, out_h: int, mode: str = "crop",
                  crop_x: float = 0.5, crop_y: float = 0.5) -> Image.Image:
    if mode == "blur":
        bg = img.resize((out_w, out_h), Image.BILINEAR).filter(
            ImageFilter.GaussianBlur(radius=max(8, out_w // 28))
        )
        bg = Image.eval(bg, lambda v: int(v * 0.55))
        fg_w = out_w
        fg_h = int(img.height * (fg_w / img.width))
        fg = img.resize((fg_w, fg_h), Image.LANCZOS)
        bg.paste(fg, (0, (out_h - fg_h) // 2))
        return bg

    if mode == "fit":
        bg = Image.new("RGB", (out_w, out_h), (0, 0, 0))
        fg_w = out_w
        fg_h = int(img.height * (fg_w / img.width))
        fg = img.resize((fg_w, fg_h), Image.LANCZOS)
        bg.paste(fg, (0, (out_h - fg_h) // 2))
        return bg

    # crop 9:16 -- crop_x/crop_y sao o ponto focal (0 = esquerda/topo, 1 = direita/base)
    target_ratio = out_w / out_h
    src_ratio = img.width / img.height
    if src_ratio > target_ratio:
        crop_w = int(img.height * target_ratio)
        crop_w -= crop_w % 2
        slack = img.width - crop_w
        center = clamp01(crop_x) * img.width
        left = int(min(max(center - crop_w / 2, 0), slack))
        box = (left, 0, left + crop_w, img.height)
    else:
        crop_h = int(img.width / target_ratio)
        crop_h -= crop_h % 2
        slack = img.height - crop_h
        center = clamp01(crop_y) * img.height
        top = int(min(max(center - crop_h / 2, 0), slack))
        box = (0, top, img.width, top + crop_h)
    return img.crop(box).resize((out_w, out_h), Image.LANCZOS)


class FaceTracker:
    """Segue o rosto e devolve o ponto focal (0..1) suavizado, sem tremor.

    Usa o Haar cascade que ja vem no OpenCV (nada pra baixar). Roda a deteccao
    em uma versao reduzida do frame, a cada N frames, e interpola o resto.
    Se nao achar rosto nenhum, mantem o enquadramento central.
    """

    def __init__(self, cadence: int = 3, smooth: float = 0.22,
                 max_step: float = 0.035) -> None:
        self.ok = False
        self.cx = 0.5
        self.cy = 0.5
        self.tx = 0.5
        self.ty = 0.5
        self.cadence = max(1, cadence)
        self.smooth = smooth
        self.max_step = max_step
        self.frames = 0
        self.hits = 0
        self.misses = 0
        self.detections = 0
        try:
            import cv2

            self.cv2 = cv2
            model = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
            self.cascade = cv2.CascadeClassifier(model)
            self.ok = not self.cascade.empty()
        except Exception as exc:
            print(f"      rastreio de rosto indisponivel ({exc}) - usando centro")
            return
        if not self.ok:
            print("      rastreio de rosto indisponivel - usando centro")

    def _detect(self, rgb: Image.Image) -> None:
        width = 320
        small = rgb.resize((width, max(1, int(rgb.height * width / rgb.width))),
                           Image.BILINEAR).convert("L")
        gray = np.array(small)
        faces = self.cascade.detectMultiScale(
            gray, scaleFactor=1.15, minNeighbors=6, minSize=(30, 30)
        )
        if len(faces) == 0:
            self.misses += 1
            # video sem rosto (tela, paisagem, maos): para de tentar depois de um tempo
            if self.hits == 0 and self.misses > 90:
                self.ok = False
                print("      nenhum rosto encontrado - mantendo enquadramento central")
            return
        self.hits += 1
        self.misses = 0
        x, y, w, h = max(faces, key=lambda f: int(f[2]) * int(f[3]))
        self.detections += 1
        self.tx = clamp01((x + w / 2) / gray.shape[1])
        # rosto um pouco acima do centro (regra dos tercos)
        self.ty = clamp01((y + h / 2) / gray.shape[0] - 0.03)

    def reset(self) -> None:
        self.cx = self.cy = self.tx = self.ty = 0.5
        self.frames = self.hits = self.misses = 0

    def _follow(self, cur: float, tgt: float) -> float:
        delta = (tgt - cur) * self.smooth
        delta = max(-self.max_step, min(self.max_step, delta))
        return clamp01(cur + delta)

    def step(self, rgb: Image.Image) -> tuple[float, float]:
        if not self.ok:
            return 0.5, 0.5
        self.frames += 1
        if self.frames % self.cadence == 1:
            self._detect(rgb)

        self.cx = self._follow(self.cx, self.tx)
        self.cy = self._follow(self.cy, self.ty)

        # se perdemos o rosto, fica parado onde estava (nao pula pro centro)
        return self.cx, self.cy


# --------------------------------------------------------------------------
# audio
# --------------------------------------------------------------------------


def read_audio(path: Path) -> tuple[np.ndarray, int, str] | None:
    """Decodifica o audio inteiro: (canais, amostras), taxa, layout."""
    try:
        with av.open(str(path)) as container:
            if not container.streams.audio:
                return None
            stream = container.streams.audio[0]
            layout = stream.codec_context.layout.name
            rate = stream.codec_context.sample_rate or 48000
            resampler = av.AudioResampler(format="fltp", layout=layout, rate=rate)
            chunks: list[np.ndarray] = []
            for frame in container.decode(stream):
                for out in resampler.resample(frame):
                    chunks.append(out.to_ndarray())
            for out in resampler.resample(None):
                chunks.append(out.to_ndarray())
    except Exception as exc:
        print(f"      sem audio utilizavel ({exc})")
        return None

    if not chunks:
        return None
    return np.concatenate(chunks, axis=1), rate, layout


def cut_audio(audio: np.ndarray, rate: int, segments: list[Segment],
              fade: float = 0.008) -> np.ndarray:
    parts = []
    ramp = max(1, int(rate * fade))
    for seg in segments:
        i0 = max(0, int(seg.src_in * rate))
        i1 = min(audio.shape[1], int(seg.src_out * rate))
        if i1 <= i0:
            continue
        piece = audio[:, i0:i1].astype(np.float32, copy=True)
        n = piece.shape[1]
        r = min(ramp, n // 2)
        if r > 1:  # micro-fade evita estalo no ponto de corte
            fade_curve = np.linspace(0.0, 1.0, r, dtype=np.float32)
            piece[:, :r] *= fade_curve
            piece[:, -r:] *= fade_curve[::-1]
        parts.append(piece)
    if not parts:
        return np.zeros((audio.shape[0], 0), dtype=np.float32)
    return np.concatenate(parts, axis=1)


# --------------------------------------------------------------------------
# render
# --------------------------------------------------------------------------


def render(
    src: Path,
    dst: Path,
    segments: list[Segment],
    cues: list[Cue],
    out_w: int,
    out_h: int,
    fps: float,
    reframe: str,
    crop_x: float,
    font_size: int | None,
    font_path: str | None,
    box: bool,
    crf: int,
    preset: str,
    audio_cut: tuple[np.ndarray, int, str] | None = None,
    seek: bool = False,
    label: str = "",
    hook: Cue | None = None,
    tracker: "FaceTracker | None" = None,
) -> None:
    painter = CaptionPainter(out_w, out_h, font_size=font_size,
                             font_path=font_path, box=box, hook=hook)
    painter.reset()
    if tracker is not None:
        tracker.reset()

    total_out = sum(s.dur for s in segments)
    total_frames = max(1, int(total_out * fps))

    if audio_cut is not None:
        audio_out, audio_rate, audio_layout = audio_cut
        has_audio = audio_out.shape[1] > 0
    else:
        audio_data = read_audio(src)
        audio_out = cut_audio(audio_data[0], audio_data[1], segments) if audio_data else None
        has_audio = audio_out is not None and audio_out.shape[1] > 0
        if audio_data:
            audio_rate, audio_layout = audio_data[1], audio_data[2]
        else:
            audio_rate, audio_layout = 48000, "stereo"

    out = av.open(str(dst), "w")
    vstream = out.add_stream("libx264", rate=int(round(fps)))
    vstream.width = out_w
    vstream.height = out_h
    vstream.pix_fmt = "yuv420p"
    vstream.options = {"crf": str(crf), "preset": preset, "profile": "high"}

    astream = None
    if has_audio:
        astream = out.add_stream("aac", rate=audio_rate)
        astream.layout = audio_layout
        astream.bit_rate = 192000

    timebase = Fraction(1, int(round(fps)))
    written = 0
    last_pts = -1
    started = time.time()
    last_report = -1

    with av.open(str(src)) as container:
        vinput = container.streams.video[0]
        vinput.thread_type = "AUTO"
        if seek and segments and segments[0].src_in > 0.5:
            try:
                container.seek(int(segments[0].src_in / vinput.time_base),
                               stream=vinput, backward=True)
            except Exception:
                pass

        seg_idx = 0
        for frame in container.decode(vinput):
            src_t = float(frame.pts * vinput.time_base)

            while seg_idx < len(segments) and src_t >= segments[seg_idx].src_out:
                seg_idx += 1
            if seg_idx >= len(segments):
                break

            seg = segments[seg_idx]
            if src_t < seg.src_in:
                continue

            out_t = seg.out_in + (src_t - seg.src_in)

            # 60fps na fonte -> 30fps na saida: descarta frames, nao duplica
            target_pts = int(round(out_t * fps))
            if target_pts <= last_pts:
                continue

            img = Image.fromarray(frame.to_ndarray(format="rgb24"))
            if tracker is not None:
                fx, fy = tracker.step(img)
                img = reframe_frame(img, out_w, out_h, reframe, fx, fy)
            else:
                img = reframe_frame(img, out_w, out_h, reframe, crop_x)
            img = painter.paint(img, src_t, cues)

            new = av.VideoFrame.from_ndarray(np.asarray(img), format="rgb24")
            new.pts = target_pts
            new.time_base = timebase
            last_pts = target_pts
            for packet in vstream.encode(new):
                out.mux(packet)
            written += 1

            pct = int(written * 100 / total_frames)
            if pct >= last_report + 10:
                last_report = pct
                print(f"      video {pct:3d}%  ({written} frames)", flush=True)

    for packet in vstream.encode(None):
        out.mux(packet)

    if astream is not None and audio_out is not None:
        print("      audio ...", flush=True)
        step = 1024
        for i in range(0, audio_out.shape[1], step):
            chunk = audio_out[:, i:i + step]
            if chunk.shape[1] == 0:
                continue
            aframe = av.AudioFrame.from_ndarray(
                np.ascontiguousarray(chunk), format="fltp", layout=audio_layout
            )
            aframe.sample_rate = audio_rate
            aframe.pts = i
            aframe.time_base = Fraction(1, audio_rate)
            for packet in astream.encode(aframe):
                out.mux(packet)
        for packet in astream.encode(None):
            out.mux(packet)

    out.close()
    print(f"      {label}render em {time.time() - started:.1f}s")


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Auto-editor fase 1: corta silencio, reframa 9:16 e legenda estilo TikTok.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("media", nargs="?", help="video de entrada")
    p.add_argument("-o", "--output", help="mp4 de saida")
    p.add_argument("-m", "--model", default="small")
    p.add_argument("-l", "--lang", default="pt")
    p.add_argument("--max-silence", type=float, default=0.35,
                   help="pausa (s) acima disso vira corte")
    p.add_argument("--lead", type=float, default=0.12, help="respiro antes da fala")
    p.add_argument("--tail", type=float, default=0.18, help="respiro depois da fala")
    p.add_argument("-w", "--words", type=int, default=2, help="palavras por linha")
    p.add_argument("--reframe", choices=["crop", "blur", "fit", "face"], default="crop")
    p.add_argument("--crop-x", type=float, default=0.5,
                   help="0 = esquerda, 1 = direita (modo crop)")
    p.add_argument("--height", type=int, default=1920, help="altura da saida 9:16")
    p.add_argument("--fps", type=float, default=30.0)
    p.add_argument("--font-size", type=int, default=0)
    p.add_argument("--font", default=None)
    p.add_argument("--box", action="store_true", help="caixa preta atras do texto")
    p.add_argument("--crf", type=int, default=20)
    p.add_argument("--preset", default="veryfast")
    p.add_argument("--keep-silence", action="store_true",
                   help="nao corta silencio (so legenda e reframe)")
    # fase 2
    p.add_argument("--highlights", action="store_true",
                   help="escolhe os melhores trechos e gera N clipes")
    p.add_argument("--clips", type=int, default=3, help="quantos clipes gerar")
    p.add_argument("--clip-len", type=float, default=45.0,
                   help="duracao alvo de cada clipe (s)")
    p.add_argument("--min-len", type=float, default=15.0,
                   help="duracao minima de um clipe (s)")
    p.add_argument("--hook", dest="hook", action="store_true", default=True,
                   help="escreve um gancho no topo do clipe (padrao)")
    p.add_argument("--no-hook", dest="hook", action="store_false")
    p.add_argument("--llm-hook", action="store_true",
                   help="usa LLM pro gancho (precisa OPENAI_API_KEY)")
    p.add_argument("--hook-dur", type=float, default=10.0,
                   help="quanto tempo o gancho fica na tela (s)")
    args = p.parse_args(argv)

    if not args.media:
        p.error("informe o video de entrada")

    src = Path(args.media).expanduser()
    if not src.exists():
        sys.exit(f"arquivo nao encontrado: {src}")
    dst = Path(args.output) if args.output else src.with_name(src.stem + "_tiktok.mp4")

    words = ms.transcribe(
        media=src,
        model_name=args.model,
        language=None if args.lang.lower() == "auto" else args.lang,
        device="auto",
        compute_type="auto",
        beam_size=5,
        vad=True,
        keep_punct=False,
        upper=True,
        merge=True,
    )
    if not words:
        sys.exit("nenhuma fala detectada - tente --keep-silence ou outro modelo")

    with av.open(str(src)) as probe:
        vs = probe.streams.video[0]
        src_w, src_h = vs.codec_context.width, vs.codec_context.height
        fps = float(vs.average_rate) if vs.average_rate else 30.0

    out_h = args.height - (args.height % 2)
    out_w = int(out_h * 9 / 16)
    out_w -= out_w % 2

    if args.keep_silence:
        dur = probe_duration(src) or 0.0
        segments = [Segment(0.0, dur)] if dur else []
    else:
        segments = speech_segments(words, lead=args.lead, tail=args.tail,
                                   max_silence=args.max_silence)
    if not segments:
        sys.exit("corte resultou vazio - relaxe --max-silence")

    cues = build_cues(words, max_words=args.words)
    total_in = segments[-1].src_out
    total_out = sum(s.dur for s in segments)

    print(f"\n{src.name}: {src_w}x{src_h} @ {fps:.0f}fps")
    print(f"  {len(segments)} trechos mantidos, {len(cues)} linhas de legenda")
    print(f"  duracao {total_in:.1f}s -> {total_out:.1f}s "
          f"({100 - total_out * 100 / max(total_in, 0.01):.0f}% de silencio removido)")
    print(f"  saida {out_w}x{out_h} @ {args.fps:.0f}fps\n")

    tracker = FaceTracker() if args.reframe == "face" else None

    if not args.highlights:
        print(f"  arquivo -> {dst.name}\n")
        render(
            src=src, dst=dst, segments=segments, cues=cues,
            out_w=out_w, out_h=out_h, fps=args.fps,
            reframe=args.reframe, crop_x=args.crop_x,
            font_size=args.font_size or None, font_path=args.font,
            box=args.box, crf=args.crf, preset=args.preset,
            tracker=tracker,
        )
        print(f"\nOK -> {dst}")
        return 0

    # ---------------------------------------------------------- fase 2
    import analyze

    audio_data = read_audio(src)
    audio_cut = cut_audio(audio_data[0], audio_data[1], segments) if audio_data else None

    print("  analisando cenas e energia ...")
    scenes = analyze.scene_cuts(src)
    print(f"  {len(scenes)} trocas de cena detectadas")

    clips = analyze.score_clips(
        segments=segments,
        words=words,
        audio=audio_cut if audio_cut is not None else np.zeros((1, 1), dtype=np.float32),
        rate=audio_data[1] if audio_data else 48000,
        scenes=scenes,
        clip_len=args.clip_len,
        clips=args.clips,
        min_len=args.min_len,
    )
    if not clips:
        sys.exit("nenhum trecho bom o bastante - tente --clip-len menor")

    print(f"  {len(clips)} clipes escolhidos\n")
    if len(clips) < args.clips:
        print(f"  (so {len(clips)} de {args.clips} couberam sem sobrepor - "
              f"use --clip-len menor para mais clipes)\n")
    made: list[Path] = []
    manifest: list[dict] = []

    for idx, clip in enumerate(clips, 1):
        sub = slice_segments(segments, clip.start, clip.end)
        if not sub:
            continue
        src_start = sub[0].src_in
        src_end = sub[-1].src_out

        clip_cues = [c for c in cues if c.end > src_start and c.start < src_end]

        hook_cue = None
        hook_text = ""
        if args.hook:
            hook_text = analyze.make_hook(clip.text, use_llm=args.llm_hook)
            if hook_text:
                hook_cue = Cue(
                    words=[ms.Word(part, src_start, src_start + 0.01)
                           for part in hook_text.split()],
                    start=src_start,
                    end=src_start + min(args.hook_dur, clip.dur),
                    top=True,
                )
                print(f"  clipe {idx}: gancho \"{hook_text}\"")

        clip_cues.sort(key=lambda c: c.start)

        out_path = dst.with_name(f"{dst.stem}_clip{idx}.mp4")
        print(f"  clipe {idx}: {clip.dur:.1f}s  score {clip.score:.2f}  -> {out_path.name}")

        clip_audio = None
        if audio_cut is not None:
            i0 = int(clip.start * audio_data[1])
            i1 = int(clip.end * audio_data[1])
            clip_audio = (audio_cut[:, i0:i1], audio_data[1], audio_data[2])

        render(
            src=src, dst=out_path, segments=sub, cues=clip_cues,
            out_w=out_w, out_h=out_h, fps=args.fps,
            reframe=args.reframe, crop_x=args.crop_x,
            font_size=args.font_size or None, font_path=args.font,
            box=args.box, crf=args.crf, preset=args.preset,
            audio_cut=clip_audio, seek=idx > 1,
            label=f"clipe {idx}: ",
            hook=hook_cue,
            tracker=tracker,
        )
        made.append(out_path)
        manifest.append({
            "index": idx,
            "path": str(out_path),
            "name": out_path.name,
            "start": round(clip.start, 3),
            "end": round(clip.end, 3),
            "dur": round(clip.dur, 3),
            "score": round(clip.score, 4),
            "hook": hook_text,
            "text": clip.text,
        })

    print("\nOK ->")
    for path in made:
        print(f"  {path}")

    if manifest:
        manifest_path = dst.with_name(f"{dst.stem}_clips.json")
        payload = {
            "source": str(src),
            "output": str(dst),
            "width": out_w,
            "height": out_h,
            "fps": args.fps,
            "clips": manifest,
        }
        try:
            manifest_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"  manifesto -> {manifest_path}")
        except Exception as exc:
            print(f"  aviso: nao consegui salvar o manifesto ({exc})")

    return 0


def probe_duration(path: Path) -> float | None:
    try:
        with av.open(str(path)) as c:
            if c.duration:
                return c.duration / 1_000_000
    except Exception:
        return None
    return None


if __name__ == "__main__":
    raise SystemExit(main())
