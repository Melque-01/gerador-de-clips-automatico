#!/usr/bin/env python3
"""
gui.py - interface grafica do make_srt.py (legendas estilo TikTok).

Abre com duplo clique em "Legendas.bat" ou:
    pythonw gui.py

Nada de dependencia extra: usa tkinter (ja vem com o Python) e chama o
make_srt.py por subprocesso, mostrando a saida ao vivo.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

BASE = Path(__file__).resolve().parent
SCRIPT = BASE / "make_srt.py"
CONFIG = BASE / "gui_config.json"

# --------------------------------------------------------------------------
# tema
# --------------------------------------------------------------------------

BG = "#12141a"
PANEL = "#1a1d25"
FIELD = "#232733"
BORDER = "#2f3542"
TEXT = "#e7e9f0"
MUTED = "#9aa3b2"
ACCENT = "#7c5cff"
ACCENT_HOVER = "#8f73ff"
OK = "#3ddc97"
ERR = "#ff6b6b"

VIDEO_TYPES = [
    ("Video ou audio", "*.mp4 *.mov *.mkv *.webm *.avi *.m4v *.wav *.mp3 *.m4a *.flac *.ogg"),
    ("Todos os arquivos", "*.*"),
]

MODELS = ["tiny", "base", "small", "medium", "large-v3"]
LANGS = ["pt", "en", "es", "auto"]

DEFAULTS = {
    "words": 2,
    "model": "small",
    "lang": "pt",
    "upper": True,
    "tight": True,
    "keep_punct": False,
    "vtt": False,
    "last_dir": str(Path.home() / "Videos"),
}

# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def build_args(cfg: dict, media: str, output: str) -> list[str]:
    """Monta a linha de comando do make_srt.py a partir do estado da tela."""
    args = [
        sys.executable,
        "-u",
        str(SCRIPT),
        media,
        "-o",
        output,
        "-w",
        str(cfg["words"]),
        "-m",
        cfg["model"],
        "-l",
        cfg["lang"],
    ]
    if cfg["upper"]:
        args.append("--upper")
    if cfg["tight"]:
        args.append("--tight")
    if cfg["keep_punct"]:
        args.append("--keep-punct")
    if cfg["vtt"]:
        args.append("--vtt")
    return args


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    try:
        cfg.update(json.loads(CONFIG.read_text(encoding="utf-8")))
    except Exception:
        pass
    return cfg


def human_time(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m}m {s:02d}s" if m else f"{s}s"


# --------------------------------------------------------------------------
# app
# --------------------------------------------------------------------------


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.cfg = load_config()
        self.proc: subprocess.Popen | None = None
        self.started = 0.0
        self.last_srt: Path | None = None

        self.title("Legendas TikTok - FilmCraft")
        self.configure(bg=BG)
        self.geometry("900x800")
        self.minsize(840, 700)

        self._build_style()
        self._build_ui()
        self._apply_cfg()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------------------------------------------------------------- estilo

    def _build_style(self) -> None:
        from theme import apply as apply_theme

        apply_theme(self)

    # ------------------------------------------------------------------- ui

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=(20, 16, 20, 14))
        root.pack(fill="both", expand=True)

        # cabecalho -----------------------------------------------------
        head = ttk.Frame(root)
        head.pack(fill="x", pady=(0, 14))
        titles = ttk.Frame(head)
        titles.pack(side="left", fill="x", expand=True)
        ttk.Label(titles, text="Legendas automaticas", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            titles,
            text="Gera SRT curto estilo TikTok e importa no FilmCraft.",
            style="Sub.TLabel",
        ).pack(anchor="w", pady=(2, 0))
        ttk.Button(head, text="Preset TikTok", style="Ghost.TButton",
                   command=self._preset_tiktok).pack(side="right", anchor="n")
        ttk.Button(head, text="Auto-editar", style="Accent.TButton",
                   command=self._open_autoeditor).pack(side="right", anchor="n",
                                                         padx=(0, 8))

        # arquivo -------------------------------------------------------
        card = ttk.Frame(root, style="Card.TFrame", padding=14)
        card.pack(fill="x")

        ttk.Label(card, text="Vídeo ou áudio", style="Card.TLabel").grid(
            row=0, column=0, columnspan=3, sticky="w"
        )
        self.media_var = tk.StringVar()
        ttk.Entry(card, textvariable=self.media_var).grid(
            row=1, column=0, sticky="ew", pady=(6, 0), padx=(0, 8)
        )
        ttk.Button(card, text="Escolher…", style="Ghost.TButton",
                   command=self._pick_media).grid(row=1, column=1, pady=(6, 0))

        ttk.Label(card, text="Salvar SRT em", style="Card.TLabel").grid(
            row=2, column=0, columnspan=3, sticky="w", pady=(14, 0)
        )
        self.out_var = tk.StringVar()
        ttk.Entry(card, textvariable=self.out_var).grid(
            row=3, column=0, sticky="ew", pady=(6, 0), padx=(0, 8)
        )
        ttk.Button(card, text="Escolher…", style="Ghost.TButton",
                   command=self._pick_output).grid(row=3, column=1, pady=(6, 0))
        card.columnconfigure(0, weight=1)

        # opcoes --------------------------------------------------------
        opt = ttk.Frame(root, style="Card.TFrame", padding=14)
        opt.pack(fill="x", pady=(12, 0))

        ttk.Label(opt, text="Palavras por linha", style="Card.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        self.words_var = tk.IntVar(value=2)
        wrow = ttk.Frame(opt, style="Card.TFrame")
        wrow.grid(row=0, column=1, columnspan=2, sticky="w", padx=(16, 0))
        for n in (1, 2, 3):
            ttk.Radiobutton(wrow, text=str(n), value=n, variable=self.words_var,
                            style="TRadiobutton").pack(side="left", padx=(0, 10))

        ttk.Label(opt, text="Modelo", style="Card.TLabel").grid(
            row=1, column=0, sticky="w", pady=(12, 0)
        )
        self.model_var = tk.StringVar(value="small")
        ttk.Combobox(opt, textvariable=self.model_var, values=MODELS,
                     state="readonly", width=12).grid(row=1, column=1, sticky="w",
                                                      padx=(16, 0), pady=(12, 0))

        ttk.Label(opt, text="Idioma", style="Card.TLabel").grid(
            row=1, column=2, sticky="w", padx=(24, 0), pady=(12, 0)
        )
        self.lang_var = tk.StringVar(value="pt")
        ttk.Combobox(opt, textvariable=self.lang_var, values=LANGS,
                     state="readonly", width=8).grid(row=1, column=3, sticky="w",
                                                     padx=(10, 0), pady=(12, 0))

        checks = ttk.Frame(opt, style="Card.TFrame")
        checks.grid(row=2, column=0, columnspan=4, sticky="w", pady=(14, 0))
        self.upper_var = tk.BooleanVar(value=True)
        self.tight_var = tk.BooleanVar(value=True)
        self.punct_var = tk.BooleanVar(value=False)
        self.vtt_var = tk.BooleanVar(value=False)
        for text, var in (
            ("CAIXA ALTA", self.upper_var),
            ("Legenda contínua", self.tight_var),
            ("Manter pontuação", self.punct_var),
            ("Gerar .vtt também", self.vtt_var),
        ):
            ttk.Checkbutton(checks, text=text, variable=var,
                            style="Chip.TCheckbutton").pack(side="left", padx=(0, 8))

        # acoes ---------------------------------------------------------
        act = ttk.Frame(root)
        act.pack(fill="x", pady=(14, 0))
        self.run_btn = ttk.Button(act, text="Gerar legendas", style="Accent.TButton",
                                  command=self._run)
        self.run_btn.pack(side="left")
        self.cancel_btn = ttk.Button(act, text="Cancelar", style="Ghost.TButton",
                                     command=self._cancel, state="disabled")
        self.cancel_btn.pack(side="left", padx=(8, 0))
        self.open_btn = ttk.Button(act, text="Abrir pasta", style="Ghost.TButton",
                                   command=self._open_folder)
        self.open_btn.pack(side="right")

        self.status = ttk.Label(root, text="Pronto.", style="Sub.TLabel")
        self.status.pack(anchor="w", pady=(12, 6))

        # barra so aparece enquanto roda (o tema clam desenha um toco quando parada)
        self.prog_holder = ttk.Frame(root, height=5)
        self.prog_holder.pack(fill="x", pady=(0, 9))
        self.prog_holder.pack_propagate(False)
        self.progress = ttk.Progressbar(self.prog_holder, mode="indeterminate")

        # log -----------------------------------------------------------
        self.log = tk.Text(root, height=10, bg="#0e1015", fg=TEXT, bd=0,
                           padx=12, pady=10, font=("Consolas", 9),
                           insertbackground=TEXT, wrap="word",
                           highlightthickness=1, highlightbackground=BORDER,
                           highlightcolor=BORDER, state="disabled")
        self.log.pack(fill="both", expand=True)
        self.log.tag_configure("ok", foreground=OK)
        self.log.tag_configure("err", foreground=ERR)
        self.log.tag_configure("muted", foreground=MUTED)

        self.log_line("A saída da transcrição aparece aqui.\n", "muted")

        self.media_var.trace_add("write", self._on_media_change)

    # -------------------------------------------------------------- estado

    def _apply_cfg(self) -> None:
        c = self.cfg
        self.words_var.set(int(c.get("words", 2)))
        self.model_var.set(c.get("model", "small"))
        self.lang_var.set(c.get("lang", "pt"))
        self.upper_var.set(bool(c.get("upper", True)))
        self.tight_var.set(bool(c.get("tight", True)))
        self.punct_var.set(bool(c.get("keep_punct", False)))
        self.vtt_var.set(bool(c.get("vtt", False)))

    def _collect_cfg(self) -> dict:
        return {
            "words": self.words_var.get(),
            "model": self.model_var.get(),
            "lang": self.lang_var.get(),
            "upper": self.upper_var.get(),
            "tight": self.tight_var.get(),
            "keep_punct": self.punct_var.get(),
            "vtt": self.vtt_var.get(),
            "last_dir": self.cfg.get("last_dir", DEFAULTS["last_dir"]),
        }

    def _save_cfg(self) -> None:
        try:
            CONFIG.write_text(json.dumps(self._collect_cfg(), indent=2), encoding="utf-8")
        except Exception:
            pass

    # ---------------------------------------------------------------- acoes

    def _pick_media(self) -> None:
        path = filedialog.askopenfilename(
            title="Escolha o vídeo ou áudio",
            initialdir=self.cfg.get("last_dir") or str(BASE),
            filetypes=VIDEO_TYPES,
        )
        if path:
            self.media_var.set(path)

    def _pick_output(self) -> None:
        current = Path(self.out_var.get()) if self.out_var.get() else None
        path = filedialog.asksaveasfilename(
            title="Onde salvar o SRT",
            defaultextension=".srt",
            initialfile=current.name if current else "legenda.srt",
            initialdir=str(current.parent) if current else str(BASE),
            filetypes=[("Legenda SRT", "*.srt")],
        )
        if path:
            self.out_var.set(path)

    def _on_media_change(self, *_: None) -> None:
        media = self.media_var.get().strip()
        if media:
            self.out_var.set(str(Path(media).with_suffix(".srt")))

    def _preset_tiktok(self) -> None:
        self.words_var.set(2)
        self.upper_var.set(True)
        self.tight_var.set(True)
        self.punct_var.set(False)
        self._set_status("Preset TikTok aplicado: 2 palavras, caixa alta, contínua.")

    def _open_autoeditor(self) -> None:
        target = Path(__file__).resolve().parent / "autoedit_gui.py"
        if not target.exists():
            messagebox.showerror("Auto-editar", f"não encontrei {target.name}")
            return
        subprocess.Popen([sys.executable, str(target)],
                         cwd=str(target.parent))

    def _open_folder(self) -> None:
        target = None
        if self.last_srt and self.last_srt.exists():
            target = self.last_srt.parent
        elif self.out_var.get().strip():
            target = Path(self.out_var.get().strip()).parent
        if target and target.exists():
            os.startfile(str(target))  # type: ignore[attr-defined]
        else:
            messagebox.showinfo("Abrir pasta", "Nenhuma pasta para abrir ainda.")

    # ----------------------------------------------------------------- run

    def _run(self) -> None:
        if self.proc is not None:
            return

        media = self.media_var.get().strip().strip('"')
        if not media or not Path(media).exists():
            messagebox.showerror("Arquivo inválido", "Escolha um vídeo ou áudio existente.")
            return

        out = self.out_var.get().strip() or str(Path(media).with_suffix(".srt"))
        if not out.lower().endswith(".srt"):
            out += ".srt"
        self.out_var.set(out)

        cfg = self._collect_cfg()
        self.cfg["last_dir"] = str(Path(media).parent)
        self._save_cfg()

        self._clear_log()
        self._set_status("Rodando… primeira vez baixa o modelo, pode demorar.")
        self.log_line(f"$ make_srt.py \"{Path(media).name}\" -w {cfg['words']}"
                      f" -m {cfg['model']} -l {cfg['lang']}\n", "muted")

        self.started = time.time()
        self.run_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        self.progress.pack(fill="x")
        self.progress.start(12)

        flags = 0
        if sys.platform == "win32":
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        try:
            self.proc = subprocess.Popen(
                build_args(cfg, media, out),
                cwd=str(BASE),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=flags,
            )
        except Exception as exc:
            self._finish(-1, str(exc))
            return

        self._pump()

    def _pump(self) -> None:
        """Le a saida do processo sem travar a tela."""
        if self.proc is None:
            return
        line = self.proc.stdout.readline() if self.proc.stdout else ""
        if line:
            self.log_line(line)
            self.after(30, self._pump)
            return
        if self.proc.poll() is None:
            self.after(80, self._pump)
            return
        self._finish(self.proc.returncode or 0)

    def _finish(self, code: int, message: str = "") -> None:
        self.progress.stop()
        self.progress.pack_forget()
        self.run_btn.configure(state="normal")
        self.cancel_btn.configure(state="disabled")
        self.proc = None
        elapsed = human_time(time.time() - self.started) if self.started else ""

        if code == 0:
            out = Path(self.out_var.get().strip())
            self.last_srt = out if out.exists() else None
            self.log_line(f"\nPronto em {elapsed}.\n", "ok")
            self._set_status(f"Legenda gerada: {out.name}  ({elapsed})", OK)
        else:
            self.log_line(f"\nFalhou (código {code}). {message}\n", "err")
            self._set_status("Falhou. Veja o log acima.", ERR)

    def _cancel(self) -> None:
        if self.proc is not None:
            try:
                self.proc.kill()
            except Exception:
                pass
            self.log_line("\nCancelado.\n", "err")
            self._set_status("Cancelado.", MUTED)
            self.progress.stop()
            self.progress.pack_forget()
            self.run_btn.configure(state="normal")
            self.cancel_btn.configure(state="disabled")
            self.proc = None

    def _on_close(self) -> None:
        if self.proc is not None:
            if not messagebox.askyesno("Sair", "A transcrição está rodando. Cancelar e sair?"):
                return
            self._cancel()
        self.destroy()

    # ------------------------------------------------------------------ log

    def _clear_log(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def log_line(self, text: str, tag: str | None = None) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text, tag or ())
        self.log.see("end")
        self.log.configure(state="disabled")

    def _set_status(self, text: str, color: str = MUTED) -> None:
        self.status.configure(text=text, foreground=color)


def main() -> None:
    if not SCRIPT.exists():
        messagebox.showerror("Erro", f"make_srt.py não encontrado em {BASE}")
        return
    App().mainloop()


if __name__ == "__main__":
    main()
