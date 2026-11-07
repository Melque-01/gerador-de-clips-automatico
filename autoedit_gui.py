#!/usr/bin/env python3
"""
autoedit_gui.py - Fase 3: interface do auto-editor.

Escolhe o video, ajusta as opcoes, roda o pipeline e mostra os clipes
gerados em cartoes com miniatura, gancho e botoes para abrir.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import av
from PIL import Image, ImageTk

import theme
from theme import ACCENT, BG, ERR, FIELD, MUTED, OK, PANEL, TEXT

HERE = Path(__file__).resolve().parent
CONFIG = HERE / "autoedit_config.json"

VIDEO_TYPES = [
    ("Videos", "*.mp4 *.mov *.mkv *.avi *.webm *.m4v"),
    ("Todos os arquivos", "*.*"),
]


# --------------------------------------------------------------------------
# miniatura
# --------------------------------------------------------------------------

def make_thumb(path: Path, height: int = 300) -> ImageTk.PhotoImage | None:
    """Primeiro frame util do clipe, redimensionado para o cartao."""
    try:
        frame = None
        with av.open(str(path)) as container:
            for f in container.decode(video=0):
                frame = f
                if float(f.pts * f.time_base) >= 0.5:
                    break
        if frame is None:
            return None
        img = Image.fromarray(frame.to_ndarray(format="rgb24"))
        w = max(1, int(img.width * height / img.height))
        img = img.resize((w, height), Image.LANCZOS)
        return ImageTk.PhotoImage(img)
    except Exception:
        return None


class AutoEditor(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Auto-editor TikTok - FilmCraft")
        self.configure(bg=BG)
        self.geometry("1120x980")
        self.minsize(980, 820)

        self.proc: subprocess.Popen | None = None
        self.queue: queue.Queue[str] = queue.Queue()
        self.manifest_path: Path | None = None
        self.thumbs: list[ImageTk.PhotoImage] = []
        self.cancelled = False

        theme.apply(self)
        self._build_ui()
        self._load_cfg()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------------------------------------------------------------- layout

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=(18, 14, 18, 14))
        root.pack(fill="both", expand=True)

        head = ttk.Frame(root)
        head.pack(fill="x")

        left = ttk.Frame(head)
        left.pack(side="left", fill="x", expand=True)
        ttk.Label(left, text="Auto-editor", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            left,
            text="Corta os silencios, reframa 9:16, legenda e escolhe os melhores trechos",
            style="Sub.TLabel",
        ).pack(anchor="w", pady=(2, 0))

        ttk.Button(head, text="Editor de legendas", style="Ghost.TButton",
                   command=self._open_subtitles).pack(side="right")

        body = ttk.Frame(root)
        body.pack(fill="both", expand=True, pady=(12, 0))

        left = ttk.Frame(body, width=430)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)

        right = ttk.Frame(body)
        right.pack(side="left", fill="both", expand=True, padx=(18, 0))

        # ---------------------------------------------------------- video
        card = ttk.Frame(left, style="Card.TFrame", padding=12)
        card.pack(fill="x")

        ttk.Label(card, text="Vídeo", style="Card.TLabel").pack(anchor="w")
        row = ttk.Frame(card, style="Card.TFrame")
        row.pack(fill="x", pady=(6, 0))

        self.media_var = tk.StringVar()
        ttk.Entry(row, textvariable=self.media_var).pack(
            side="left", fill="x", expand=True)
        ttk.Button(row, text="Procurar", style="Ghost.TButton",
                   command=self._pick_video).pack(side="left", padx=(8, 0))

        self.out_var = tk.StringVar(value="Mesma pasta do vídeo")
        ttk.Label(card, textvariable=self.out_var, style="Muted.TLabel",
                  wraplength=380, justify="left").pack(anchor="w", pady=(8, 0))

        # ---------------------------------------------------------- ajustes
        card = ttk.Frame(left, style="Card.TFrame", padding=12)
        card.pack(fill="x", pady=(8, 0))
        ttk.Label(card, text="Ajustes", style="Card.TLabel").pack(anchor="w")

        grid = ttk.Frame(card, style="Card.TFrame")
        grid.pack(fill="x", pady=(4, 0))

        self.clips_var = tk.IntVar(value=3)
        self.len_var = tk.IntVar(value=30)
        self.min_var = tk.IntVar(value=15)
        self.words_var = tk.IntVar(value=2)
        self.reframe_var = tk.StringVar(value="crop")
        self.height_var = tk.IntVar(value=1920)

        self._field(grid, 0, 0, "Clipes", self.clips_var, 1, 8)
        self._field(grid, 0, 1, "Duração (s)", self.len_var, 5, 90)
        self._field(grid, 1, 0, "Mínimo (s)", self.min_var, 3, 60)
        self._field(grid, 1, 1, "Palavras/linha", self.words_var, 1, 4)
        self._field(grid, 2, 0, "Altura", self.height_var, 1280, 2160)

        ref = ttk.Frame(grid, style="Card.TFrame")
        ref.grid(row=2, column=1, sticky="w", pady=4)
        ttk.Label(ref, text="Reframe", style="Card.TLabel").pack(side="left")
        ttk.Combobox(
            ref, textvariable=self.reframe_var, width=7, state="readonly",
            values=["crop", "face", "blur", "fit"],
        ).pack(side="left", padx=(8, 0))

        chips = ttk.Frame(card, style="Card.TFrame")
        chips.pack(fill="x", pady=(10, 0))

        self.hook_var = tk.BooleanVar(value=True)
        self.llm_var = tk.BooleanVar(value=False)
        self.box_var = tk.BooleanVar(value=False)

        ttk.Checkbutton(chips, text="Gancho no topo", style="Chip.TCheckbutton",
                        variable=self.hook_var).pack(side="left", padx=(0, 6))
        ttk.Checkbutton(chips, text="Gancho com IA", style="Chip.TCheckbutton",
                        variable=self.llm_var).pack(side="left", padx=(0, 6))
        ttk.Checkbutton(chips, text="Caixa", style="Chip.TCheckbutton",
                        variable=self.box_var).pack(side="left", padx=(0, 6))

        ttk.Button(card, text="Preset TikTok", style="Ghost.TButton",
                   command=self._preset).pack(fill="x", pady=(8, 0))

        # ---------------------------------------------------------- acoes
        actions = ttk.Frame(left)
        actions.pack(fill="x", pady=(10, 6))

        self.run_btn = ttk.Button(actions, text="Gerar clipes", style="Accent.TButton",
                                  command=self._run)
        self.run_btn.pack(side="left")

        self.cancel_btn = ttk.Button(actions, text="Cancelar", style="Ghost.TButton",
                                     command=self._cancel, state="disabled")
        self.cancel_btn.pack(side="left", padx=(8, 0))

        self.folder_btn = ttk.Button(actions, text="Abrir pasta", style="Ghost.TButton",
                                     command=self._open_last_folder, state="disabled")
        self.folder_btn.pack(side="left", padx=(8, 0))

        self.log_btn = ttk.Button(left, text="Ver log", style="Link.TButton",
                                  command=self._toggle_log)
        self.log_btn.pack(anchor="e")

        self.progress = ttk.Progressbar(left, mode="determinate", maximum=100)
        self.progress.pack(fill="x", pady=(2, 6))

        self.status = ttk.Label(left, text="Escolha um vídeo para começar.",
                                style="Sub.TLabel", wraplength=400, justify="left")
        self.status.pack(anchor="w")

        self.log = tk.Text(left, height=8, bg=FIELD, fg=MUTED, bd=0,
                           font=("Consolas", 9), wrap="none", padx=10, pady=8)

        # ---------------------------------------------------------- resultados
        ttk.Label(right, text="Clipes gerados", style="Section.TLabel").pack(
            anchor="w", pady=(0, 6))

        wrap = ttk.Frame(right)
        wrap.pack(fill="both", expand=True)
        wrap.rowconfigure(0, weight=1)
        wrap.columnconfigure(0, weight=1)

        self.canvas = tk.Canvas(wrap, bg=BG, highlightthickness=0)
        self.bar = ttk.Scrollbar(wrap, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.bar.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.bar.grid(row=0, column=1, sticky="ns")
        self.bar.grid_remove()

        self.results = ttk.Frame(self.canvas)
        self.window_id = self.canvas.create_window((0, 0), window=self.results,
                                                   anchor="nw")
        self.results.bind("<Configure>", self._on_results_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.bind("<Enter>", lambda e: self.canvas.bind_all("<MouseWheel>",
                                                                 self._on_wheel))
        self.canvas.bind("<Leave>", lambda e: self.canvas.unbind_all("<MouseWheel>"))

        self.placeholder = ttk.Label(
            self.results,
            text="Nenhum clipe ainda.\nOs cartões aparecem aqui depois de gerar.",
            style="Sub.TLabel", justify="center",
        )
        self.placeholder.pack(pady=30)

    def _field(self, parent, row: int, col: int, label: str, var, lo: int, hi: int) -> None:
        cell = ttk.Frame(parent, style="Card.TFrame")
        cell.grid(row=row, column=col, sticky="w", padx=(0, 18), pady=4)
        ttk.Label(cell, text=label, style="Card.TLabel").pack(side="left")
        ttk.Spinbox(cell, from_=lo, to=hi, textvariable=var, width=6,
                    justify="center").pack(side="left", padx=(8, 0))

    def _on_wheel(self, event) -> None:
        self.canvas.yview_scroll(int(-event.delta / 120), "units")

    def _on_results_configure(self, _event=None) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        self._sync_scroll()

    def _on_canvas_configure(self, event) -> None:
        self.canvas.itemconfigure(self.window_id, width=event.width)
        self._sync_scroll()

    def _sync_scroll(self) -> None:
        need = self.results.winfo_reqheight() > self.canvas.winfo_height() + 4
        if need and not self.bar.winfo_ismapped():
            self.bar.grid()
        elif not need and self.bar.winfo_ismapped():
            self.bar.grid_remove()
            self.canvas.yview_moveto(0)

    # ---------------------------------------------------------------- acoes

    def _pick_video(self) -> None:
        path = filedialog.askopenfilename(title="Escolha o vídeo", filetypes=VIDEO_TYPES)
        if path:
            p = Path(path)
            self.media_var.set(path)
            self.out_var.set(f"Saída: {p.parent}\\{p.stem}_tiktok_clip1.mp4 …")

    def _preset(self) -> None:
        self.clips_var.set(3)
        self.len_var.set(30)
        self.min_var.set(12)
        self.words_var.set(2)
        self.reframe_var.set("crop")
        self.height_var.set(1920)
        self.hook_var.set(True)
        self.llm_var.set(False)
        self.box_var.set(False)
        self.status.configure(text="Preset TikTok aplicado.", foreground=MUTED)

    def _open_subtitles(self) -> None:
        target = HERE / "gui.py"
        if not target.exists():
            messagebox.showerror("Auto-editor", f"não encontrei {target.name}")
            return
        subprocess.Popen([sys.executable, str(target)], cwd=str(HERE))

    def _open_last_folder(self) -> None:
        media = Path(self.media_var.get() or "")
        if media.exists():
            os.startfile(str(media.parent))

    def _open(self, path: Path) -> None:
        if path.exists():
            os.startfile(str(path))
        else:
            messagebox.showerror("Auto-editor", f"arquivo não encontrado:\n{path}")

    def _select(self, path: Path) -> None:
        if path.exists():
            subprocess.Popen(["explorer", "/select,", str(path)])
        else:
            os.startfile(str(path.parent))

    def _toggle_log(self) -> None:
        if self.log.winfo_ismapped():
            self.log.pack_forget()
        else:
            self.log.pack(fill="x", pady=(8, 0))

    # ---------------------------------------------------------------- run

    def _run(self) -> None:
        media = Path(self.media_var.get().strip('"'))
        if not media.exists():
            messagebox.showwarning("Auto-editor", "Escolha um vídeo válido primeiro.")
            return
        if self.proc is not None:
            return
        if self.llm_var.get() and not (
            os.environ.get("OPENAI_API_KEY") or os.environ.get("OPENROUTER_API_KEY")
        ):
            if not messagebox.askyesno(
                "Gancho com IA",
                "Nenhuma chave de API encontrada.\n\n"
                "Defina OPENAI_API_KEY (ou OPENROUTER_API_KEY) antes de abrir o app.\n"
                "Sem chave, vou usar a heurística.\n\nContinuar?",
            ):
                return

        self._clear_results()
        self.cancelled = False
        self.manifest_path = media.with_name(media.stem + "_tiktok_clips.json")
        self.progress.configure(value=0)
        self.status.configure(text="Transcrevendo e analisando...", foreground=MUTED)

        cmd = [
            sys.executable, "-u", str(HERE / "autoedit.py"), str(media),
            "--highlights",
            "--clips", str(self.clips_var.get()),
            "--clip-len", str(self.len_var.get()),
            "--min-len", str(self.min_var.get()),
            "--words", str(self.words_var.get()),
            "--height", str(self.height_var.get()),
            "--reframe", self.reframe_var.get(),
        ]
        if not self.hook_var.get():
            cmd.append("--no-hook")
        if self.llm_var.get():
            cmd.append("--llm-hook")
        if self.box_var.get():
            cmd.append("--box")

        self.log.delete("1.0", "end")
        self._append_log(" ".join(cmd) + "\n")

        creation = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            self.proc = subprocess.Popen(
                cmd, cwd=str(HERE), stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                errors="replace", bufsize=1, creationflags=creation,
            )
        except Exception as exc:
            messagebox.showerror("Auto-editor", f"não consegui iniciar:\n{exc}")
            self.proc = None
            return

        self.run_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        self.folder_btn.configure(state="disabled")

        threading.Thread(target=self._reader, daemon=True).start()
        self.after(120, self._pump)

    def _reader(self) -> None:
        assert self.proc is not None
        for line in self.proc.stdout:
            self.queue.put(line.rstrip("\n"))
        self.proc.wait()
        self.queue.put(f"__EXIT__{self.proc.returncode}")

    def _pump(self) -> None:
        done = False
        code = 0
        while True:
            try:
                line = self.queue.get_nowait()
            except queue.Empty:
                break
            if line.startswith("__EXIT__"):
                code = int(line.replace("__EXIT__", ""))
                done = True
                continue
            self._append_log(line)
            self._note(line)

        if done:
            self._finish(code)
        elif self.proc is not None:
            self.after(120, self._pump)

    def _note(self, line: str) -> None:
        low = line.lower()
        if "video" in low and "%" in line:
            try:
                pct = int(line.split("%")[0].split()[-1])
                self.progress.configure(value=pct)
            except ValueError:
                pass
        elif "transcrevendo" in low or "carregando" in low:
            self.status.configure(text="Transcrevendo...", foreground=MUTED)
        elif "analisando cenas" in low:
            self.status.configure(text="Analisando cenas e energia...", foreground=MUTED)
            self.progress.configure(value=5)
        elif "clipe" in low and "render em" in low:
            self.status.configure(text=line.strip(), foreground=MUTED)
        elif "gancho" in low and '"' in line:
            self.status.configure(text=line.strip(), foreground=MUTED)

    def _append_log(self, line: str) -> None:
        self.log.insert("end", line + "\n")
        self.log.see("end")

    def _cancel(self) -> None:
        if self.proc is None:
            return
        self.cancelled = True
        self.status.configure(text="Cancelando...", foreground=ERR)
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(self.proc.pid)],
                    capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW,
                )
            else:
                self.proc.terminate()
        except Exception:
            pass

    def _finish(self, code: int) -> None:
        self.proc = None
        self.run_btn.configure(state="normal")
        self.cancel_btn.configure(state="disabled")
        self.folder_btn.configure(state="normal")

        if self.cancelled:
            self.status.configure(text="Cancelado.", foreground=ERR)
            self.progress.configure(value=0)
            return
        if code != 0:
            self.status.configure(text=f"Falhou (código {code}). Veja o log.",
                                  foreground=ERR)
            self._toggle_log()
            return

        self.progress.configure(value=100)
        if self.manifest_path and self.manifest_path.exists():
            self._show_results(self.manifest_path)
        else:
            self.status.configure(text="Terminou, mas não achei o manifesto dos clipes.",
                                  foreground=MUTED)

    # ---------------------------------------------------------------- cartoes

    def _clear_results(self) -> None:
        for child in self.results.winfo_children():
            child.destroy()
        self.thumbs.clear()

    def _show_results(self, manifest_path: Path) -> None:
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception as exc:
            self.status.configure(text=f"Manifesto ilegível: {exc}", foreground=ERR)
            return

        clips = data.get("clips") or []
        if not clips:
            self.status.configure(text="Nenhum clipe gerado.", foreground=MUTED)
            return

        self._clear_results()
        for col in range(2):
            self.results.columnconfigure(col, weight=1, uniform="cards")

        for i, info in enumerate(clips):
            self._make_card(i // 2, i % 2, info)

        self.after(80, self._sync_scroll)

        total = sum(float(c.get("dur", 0)) for c in clips)
        self.status.configure(
            text=f"{len(clips)} clipes prontos • {total:.0f}s no total",
            foreground=OK,
        )

    def _make_card(self, row: int, col: int, info: dict) -> None:
        path = Path(info.get("path", ""))
        card = ttk.Frame(self.results, style="Card.TFrame", padding=10)
        card.grid(row=row, column=col, sticky="nsew", padx=6, pady=6)

        thumb = make_thumb(path, height=200) if path.exists() else None
        if thumb is not None:
            self.thumbs.append(thumb)
            img = tk.Label(card, image=thumb, bg=PANEL, bd=0, cursor="hand2")
            img.pack()
            img.bind("<Button-1>", lambda e, p=path: self._open(p))
        else:
            tk.Label(card, text="sem preview", bg=PANEL, fg=MUTED,
                     height=10, width=20).pack()

        title = ttk.Frame(card, style="Card.TFrame")
        title.pack(fill="x", pady=(8, 0))
        tk.Label(title, text=f"Clipe {info.get('index', '?')}", bg=PANEL,
                 fg=TEXT, font=("Segoe UI Semibold", 10)).pack(side="left")
        tk.Label(title, text=f"{float(info.get('score', 0)):.2f}", bg=PANEL,
                 fg=ACCENT, font=("Segoe UI Semibold", 10)).pack(side="right")

        tk.Label(
            card,
            text=f"{float(info.get('dur', 0)):.1f}s  •  {path.name}",
            bg=PANEL, fg=MUTED, font=("Segoe UI", 8), wraplength=200,
            justify="left",
        ).pack(anchor="w")

        hook = info.get("hook") or ""
        if hook:
            tk.Label(
                card, text=hook, bg=PANEL, fg=ACCENT, font=("Segoe UI Semibold", 9),
                wraplength=200, justify="left",
            ).pack(anchor="w", pady=(6, 0))

        btns = ttk.Frame(card, style="Card.TFrame")
        btns.pack(fill="x", pady=(8, 0))
        ttk.Button(btns, text="▶ Abrir", style="Ghost.TButton",
                   command=lambda p=path: self._open(p)).pack(side="left")
        ttk.Button(btns, text="Pasta", style="Ghost.TButton",
                   command=lambda p=path: self._select(p)).pack(side="left", padx=(6, 0))

    # ---------------------------------------------------------------- config

    def _load_cfg(self) -> None:
        if not CONFIG.exists():
            return
        try:
            cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
        except Exception:
            return
        for key, var in (
            ("clips", self.clips_var), ("length", self.len_var),
            ("min_len", self.min_var), ("words", self.words_var),
            ("height", self.height_var),
        ):
            if key in cfg:
                try:
                    var.set(int(cfg[key]))
                except Exception:
                    pass
        if cfg.get("reframe"):
            self.reframe_var.set(cfg["reframe"])
        self.hook_var.set(bool(cfg.get("hook", True)))
        self.llm_var.set(bool(cfg.get("llm", False)))
        self.box_var.set(bool(cfg.get("box", False)))

    def _save_cfg(self) -> None:
        cfg = {
            "clips": self.clips_var.get(),
            "length": self.len_var.get(),
            "min_len": self.min_var.get(),
            "words": self.words_var.get(),
            "height": self.height_var.get(),
            "reframe": self.reframe_var.get(),
            "hook": self.hook_var.get(),
            "llm": self.llm_var.get(),
            "box": self.box_var.get(),
        }
        try:
            CONFIG.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _on_close(self) -> None:
        if self.proc is not None:
            if not messagebox.askyesno(
                "Auto-editor", "O processamento ainda está rodando. Cancelar e sair?"
            ):
                return
            self._cancel()
        self._save_cfg()
        self.destroy()


def main() -> int:
    app = AutoEditor()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
