"""
MeMi BOT na bandeja do Windows.

Coloque este arquivo na MESMA pasta do memi_bot.py e do token.txt.

Instalar as dependências (uma vez):
    python -m pip install -r requirements-tray.txt

Comandos:
    python memi_tray.py                -> roda agora (com ícone na bandeja)
    python memi_tray.py --instalar     -> passa a abrir sozinho quando o Windows ligar
    python memi_tray.py --desinstalar  -> deixa de abrir sozinho

Sem janela de console: o log é o mesmo do memi_bot.py, o arquivo memi_bot.log
(menu do ícone -> "Ver log"). O tray e o `python memi_bot.py` usam a mesma trava, então
não dá para abrir os dois ao mesmo tempo.
"""

import asyncio
import logging
import os
import sys
import threading
from pathlib import Path

PASTA = Path(__file__).resolve().parent
os.chdir(PASTA)  # token.txt e memi.db são procurados na pasta do bot
sys.path.insert(0, str(PASTA))

ARQ_LOG = PASTA / "memi_bot.log"
NOME_ATALHO = "MeMiBOT.vbs"


def pasta_inicializacao() -> Path:
    return Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Startup"


def instalar():
    """Cria um .vbs na pasta 'Inicializar' do Windows que abre este script sem janela."""
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    if not pythonw.exists():
        pythonw = Path(sys.executable)
    linha = f'CreateObject("Wscript.Shell").Run """{pythonw}"" ""{Path(__file__).resolve()}""", 0, False'
    destino = pasta_inicializacao() / NOME_ATALHO
    destino.write_text(linha + "\n", encoding="utf-8")
    print(f"✅ Instalado! O bot vai abrir sozinho quando o Windows ligar.\n   ({destino})")


def desinstalar():
    destino = pasta_inicializacao() / NOME_ATALHO
    if destino.exists():
        destino.unlink()
        print("✅ Removido. O bot não abre mais sozinho.")
    else:
        print("Não estava instalado.")


if len(sys.argv) > 1:
    if sys.argv[1] in ("--instalar", "--install"):
        instalar()
    elif sys.argv[1] in ("--desinstalar", "--uninstall"):
        desinstalar()
    else:
        print(__doc__)
    sys.exit(0)

# --- a partir daqui: rodar de verdade --------------------------------------
import memi_bot  # noqa: E402  (só importa; o bot não roda sozinho)

memi_bot.configurar_log(ARQ_LOG)  # o mesmo log rotativo do memi_bot.py
# sem console (pythonw) não existe stdout/stderr: cada linha impressa vira um registro no log
if sys.stdout is None or sys.stderr is None or Path(sys.executable).name.lower() == "pythonw.exe":
    sys.stdout = sys.stderr = memi_bot.FluxoLog()

import pystray  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

estado = {"loop": None, "icone": None}


def criar_imagem():
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((2, 2, 62, 62), fill=(88, 101, 242, 255))  # azul do Discord
    d.text((21, 23), "MeMi", fill=(255, 255, 255, 255))
    return img


def rodar_bot():
    async def principal():
        estado["loop"] = asyncio.get_running_loop()
        try:
            await memi_bot.conectar()
        except memi_bot.discord.PrivilegedIntentsRequired:
            logging.error(memi_bot.MSG_INTENT_MENSAGENS)
        except Exception:  # noqa: BLE001
            logging.exception("O bot não pôde continuar.")

    asyncio.run(principal())
    if estado["icone"]:  # o bot parou sozinho (ex.: token errado): fecha o ícone também
        estado["icone"].stop()


def sair(icone, item):
    loop = estado["loop"]
    if loop and loop.is_running():
        try:
            asyncio.run_coroutine_threadsafe(memi_bot.bot.close(), loop).result(timeout=10)
        except Exception:  # noqa: BLE001
            pass
    icone.stop()


def abrir_pasta(icone, item):
    os.startfile(PASTA)


def ver_log(icone, item):
    os.startfile(ARQ_LOG)


def main():
    if not memi_bot.TOKEN or memi_bot.TOKEN == "COLE_SEU_TOKEN_AQUI":
        logging.error("Falta o token.txt na pasta do bot.")
        return

    trava = memi_bot.travar_instancia()  # mantida viva até o fim do main()
    if trava is None:
        logging.error("O MeMi BOT já está rodando.")
        return

    threading.Thread(target=rodar_bot, daemon=True).start()

    menu = pystray.Menu(
        pystray.MenuItem("Abrir pasta do bot", abrir_pasta),
        pystray.MenuItem("Ver log", ver_log),
        pystray.MenuItem("Sair", sair),
    )
    estado["icone"] = pystray.Icon("memibot", criar_imagem(), "MeMi BOT", menu)
    estado["icone"].run()  # fica aqui até clicar em "Sair"


if __name__ == "__main__":
    main()
