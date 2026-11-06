"""theme.py - paleta e estilos ttk compartilhados pelas interfaces."""

from __future__ import annotations

import ctypes
from tkinter import ttk

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

_DPI_SCALE = 1.0


def _enable_dpi() -> None:
    """Sem isso o Windows estica a janela em telas com escala > 100% (fica borrado)."""
    global _DPI_SCALE
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            return
    try:
        dc = ctypes.windll.user32.GetDC(0)
        dpi = ctypes.windll.gdi32.GetDeviceCaps(dc, 88)  # LOGPIXELSX
        ctypes.windll.user32.ReleaseDC(0, dc)
        if dpi:
            _DPI_SCALE = dpi / 96.0
    except Exception:
        _DPI_SCALE = 1.0


_enable_dpi()


def apply(root) -> ttk.Style:
    st = ttk.Style(root)
    st.theme_use("clam")
    try:
        root.tk.call("tk", "scaling", _DPI_SCALE * 96 / 72)
    except Exception:
        pass

    st.configure(".", background=BG, foreground=TEXT, font=("Segoe UI", 10))
    st.configure("Card.TFrame", background=PANEL)
    st.configure("TFrame", background=BG)
    st.configure("TLabel", background=BG, foreground=TEXT)
    st.configure("Card.TLabel", background=PANEL, foreground=TEXT)
    st.configure("Muted.TLabel", background=PANEL, foreground=MUTED,
                 font=("Segoe UI", 9))
    st.configure("Title.TLabel", background=BG, foreground=TEXT,
                 font=("Segoe UI Semibold", 17))
    st.configure("Sub.TLabel", background=BG, foreground=MUTED,
                 font=("Segoe UI", 9))
    st.configure("Section.TLabel", background=BG, foreground=TEXT,
                 font=("Segoe UI Semibold", 11))

    st.configure("TEntry", fieldbackground=FIELD, background=FIELD,
                 foreground=TEXT, insertcolor=TEXT, bordercolor=BORDER,
                 lightcolor=BORDER, darkcolor=BORDER, padding=6)
    st.map("TEntry", bordercolor=[("focus", ACCENT)])

    st.configure("TSpinbox", fieldbackground=FIELD, background=FIELD,
                 foreground=TEXT, insertcolor=TEXT, arrowcolor=MUTED,
                 bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER,
                 padding=4)
    st.map("TSpinbox", bordercolor=[("focus", ACCENT)])

    st.configure("TCombobox", fieldbackground=FIELD, background=FIELD,
                 foreground=TEXT, arrowcolor=MUTED, bordercolor=BORDER,
                 lightcolor=BORDER, darkcolor=BORDER, padding=5)
    st.map(
        "TCombobox",
        fieldbackground=[("readonly", FIELD)],
        foreground=[("readonly", TEXT)],
        bordercolor=[("focus", ACCENT)],
    )

    st.configure("TCheckbutton", background=PANEL, foreground=TEXT,
                 focuscolor=PANEL)
    st.map("TCheckbutton",
           background=[("active", PANEL)],
           indicatorcolor=[("selected", ACCENT), ("!selected", FIELD)])

    # chips: checkbox estilizado como botao (sem o "X" feio do tema clam)
    st.configure("Chip.TCheckbutton", background=FIELD, foreground=MUTED,
                 borderwidth=0, padding=(13, 7), font=("Segoe UI", 9))
    st.map(
        "Chip.TCheckbutton",
        background=[("selected", ACCENT), ("active", BORDER)],
        foreground=[("selected", "#ffffff"), ("active", TEXT)],
    )
    st.layout("Chip.TCheckbutton", [
        ("Checkbutton.padding", {
            "sticky": "nswe",
            "children": [("Checkbutton.label", {"sticky": "nswe"})],
        }),
    ])

    st.configure("TRadiobutton", background=PANEL, foreground=TEXT,
                 focuscolor=PANEL)
    st.map("TRadiobutton",
           background=[("active", PANEL)],
           indicatorcolor=[("selected", ACCENT), ("!selected", FIELD)])

    st.configure("Accent.TButton", background=ACCENT, foreground="#ffffff",
                 borderwidth=0, focusthickness=0, padding=(18, 9),
                 font=("Segoe UI Semibold", 10))
    st.map("Accent.TButton",
           background=[("active", ACCENT_HOVER), ("disabled", "#3a3f4d")],
           foreground=[("disabled", MUTED)])

    st.configure("Ghost.TButton", background=FIELD, foreground=TEXT,
                 borderwidth=0, padding=(14, 9))
    st.map("Ghost.TButton", background=[("active", BORDER)])

    st.configure("Link.TButton", background=BG, foreground=ACCENT,
                 borderwidth=0, padding=(2, 0))
    st.map("Link.TButton", background=[("active", BG)],
           foreground=[("active", ACCENT_HOVER)])

    st.configure("TProgressbar", background=ACCENT, troughcolor=FIELD,
                 borderwidth=0, thickness=4)

    st.configure("Vertical.TScrollbar", background=FIELD, troughcolor=BG,
                 bordercolor=BG, arrowcolor=MUTED, darkcolor=FIELD,
                 lightcolor=FIELD, borderwidth=0)
    st.map("Vertical.TScrollbar", background=[("active", BORDER)])

    return st
