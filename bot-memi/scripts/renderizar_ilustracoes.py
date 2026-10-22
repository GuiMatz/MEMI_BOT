"""Renderiza as ilustrações SVG do FUT.AE em PNG com um navegador headless.

Os medalhões e o pacote da crate vêm de `src.medallions` (ícones Tabler em
`assets/ilustracoes/svg/icones/`) e as molduras das cartas, de `src.card_frames`. Os PNGs vão para `assets/ilustracoes/` e entram no repositório: o
bot só lê os PNGs, sem precisar de navegador. Rode de novo depois de mudar um SVG:

    python -m scripts.renderizar_ilustracoes

Usa Chrome ou Edge; defina CHROME_PATH se o navegador estiver em outro lugar.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from src.card_frames import FRAME_SIZE, frame_svgs
from src.db import ROOT
from src.icons import ICON_DIR
from src.medallions import art_svgs, emoji_svgs

OUTPUT = ROOT / "assets" / "ilustracoes"
CANDIDATES = (
    os.environ.get("CHROME_PATH", ""),
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "google-chrome",
    "chromium",
    "chromium-browser",
)


def browser() -> str:
    for candidate in CANDIDATES:
        if candidate and (Path(candidate).is_file() or shutil.which(candidate)):
            return candidate
    raise SystemExit("Não achei Chrome/Edge. Defina CHROME_PATH.")


def render(svg: str, output: Path, width: int, height: int) -> None:
    with tempfile.TemporaryDirectory() as folder:
        page = Path(folder) / "page.html"
        page.write_text(
            "<!doctype html><html><head><meta charset='utf-8'><style>"
            "html,body{margin:0;background:transparent;overflow:hidden}"
            f"svg{{display:block;width:{width}px;height:{height}px}}"
            f"</style></head><body>{svg}</body></html>",
            encoding="utf-8",
        )
        subprocess.run(
            [
                browser(),
                "--headless=new",
                "--disable-gpu",
                "--hide-scrollbars",
                "--default-background-color=00000000",
                f"--window-size={width},{height}",
                f"--screenshot={output}",
                page.as_uri(),
            ],
            check=True,
            capture_output=True,
        )


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for name, (svg, (width, height)) in art_svgs().items():
        render(svg, OUTPUT / f"{name}.png", width, height)
        print("ok", name)
    ICON_DIR.mkdir(parents=True, exist_ok=True)
    for name, svg in emoji_svgs().items():
        render(svg, ICON_DIR / f"{name}.png", 128, 128)
        print("ok emoji", name)
    frames = OUTPUT / "molduras"
    frames.mkdir(exist_ok=True)
    for name, svg in frame_svgs().items():
        render(svg, frames / f"{name}.png", *FRAME_SIZE)
        print("ok moldura", name)


if __name__ == "__main__":
    main()
