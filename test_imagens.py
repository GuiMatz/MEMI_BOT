"""Testes das imagens do bot (exigem Pillow, exceto os de disponibilidade)."""

import io
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import imagens

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None

CARTAO = {
    "nome": "Fulano de Tal",
    "titulo": "Resenha Torta",
    "nivel": 71,
    "avanco": 34,
    "meta": 80,
    "mensagens": 2714,
    "pedidos": 120,
    "roletadas": 2117,
    "pos_mensagens": "#2 de 138",
    "pos_pedidos": "#1 de 21",
    "pos_mudae": "",
    "cor": 0xFF8800,
}
NIVEL = {
    "nome": "Fulano",
    "nivel": 30,
    "atual": 32,
    "avanco": 61,
    "meta": 100,
    "patente": "Veterano da Call",
    "trocou": True,
    "cor": 0xF0704E,
}
RESUMO = {
    "tipo": "mes",
    "titulo": "Dezembro",
    "ano": "2025",
    "dj": ("Ciclano", "120 pedidos"),
    "tagarela": ("Fulano de Tal", "3.410 mensagens"),
    "musica": ("Faixa", "Artista", 18),
    "mensagens": "12.340",
    "pedidos": "410",
    "cor": None,
}
WRAPPED = {
    "nome": "Fulano",
    "meses": [12, 30, 22, 48, 60, 41, 25, 33, 80, 64, 20, 9],
    "pedidos": 2714,
    "dias": 187,
    "genero": "Dance",
    "top": [("Te amo, Te odeio", 6), ("Farroupilha Flow", 5), ("Stand by Me", 4)],
    "cor": None,
}
TAMANHOS = {
    "cartao": (1200, 400),
    "nivel": (1200, 400),
    "ajuda": (1200, 400),
    "resumo": (1200, 480),
    "wrapped": (1200, 630),
}


def abrir(png):
    return Image.open(io.BytesIO(png))


def perto(a, b, tolerancia=14):
    return all(abs(x - y) <= tolerancia for x, y in zip(a, b))


def avatar(cor=(200, 30, 30)):
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), cor).save(buf, "PNG")
    return buf.getvalue()


def gerar(nome, dados=None, av=None):
    if nome == "cartao":
        return imagens.gerar_cartao(dados or CARTAO, av)
    if nome == "nivel":
        return imagens.gerar_nivel(dados or NIVEL, av)
    if nome == "ajuda":
        return imagens.gerar_ajuda((dados or {}).get("cor"))
    if nome == "resumo":
        return imagens.gerar_resumo(dados or RESUMO, av, av, av)
    return imagens.gerar_wrapped(dados or WRAPPED, av)


@unittest.skipUnless(Image is not None, "Pillow não instalado")
class GeradoresTests(unittest.TestCase):
    def test_todas_geram_png_do_tamanho_certo_e_nao_vazio(self):
        for nome, tamanho in TAMANHOS.items():
            with self.subTest(nome):
                png = gerar(nome, av=avatar())
                imagem = abrir(png)
                self.assertEqual((imagem.format, imagem.size), ("PNG", tamanho))
                self.assertGreater(len(imagem.convert("RGB").getcolors(maxcolors=2_000_000)), 50)
                self.assertLess(len(png), 700_000)  # leve para enviar no Discord

    def test_a_cor_pessoal_chega_ao_acento_da_imagem(self):
        """O grão do fundo é aleatório, então comparamos o pixel do acento (não os bytes)."""
        # (imagem, dados com cor azul, pixel que deve ter a cor de acento)
        casos = {
            "cartao": (dict(CARTAO, cor=0x2255FF), (1170, 38)),
            "nivel": (dict(NIVEL, cor=0x2255FF), (1170, 38)),
            "resumo": (dict(RESUMO, cor=0x2255FF), (1170, 38)),
            "wrapped": (dict(WRAPPED, cor=0x2255FF), (1170, 38)),
            "ajuda": ({"cor": 0x2255FF}, (1130, 215)),
        }
        for nome, (azul, pixel) in casos.items():
            with self.subTest(nome):
                sem_cor = {"cartao": dict(CARTAO, cor=None)}.get(nome)
                padrao = abrir(gerar(nome, sem_cor, avatar())).convert("RGB").getpixel(pixel)
                pessoal = abrir(gerar(nome, azul, avatar())).convert("RGB").getpixel(pixel)
                self.assertTrue(perto(padrao, (240, 112, 78)), padrao)  # coral padrão
                self.assertTrue(perto(pessoal, (34, 85, 255)), pessoal)  # cor escolhida

    def test_ajuda_e_guardada_em_cache_por_cor(self):
        self.assertIs(imagens.gerar_ajuda(None), imagens.gerar_ajuda(None))
        self.assertIs(imagens.gerar_ajuda(0x2255FF), imagens.gerar_ajuda(0x2255FF))

    def test_ajustar_respeita_o_limite_e_e_rapido_com_nomes_gigantes(self):
        inicio = time.monotonic()
        texto, corpo = imagens.ajustar("N" * 500, "forte", 52, 620)
        self.assertLessEqual(imagens.largura(texto, "forte", corpo), 620)
        self.assertTrue(texto.endswith("…"))
        self.assertLess(time.monotonic() - inicio, 1.5)

    def test_genero_enorme_no_wrapped_nao_estoura_a_imagem(self):
        dados = dict(WRAPPED, genero="G" * 300)
        self.assertEqual(abrir(imagens.gerar_wrapped(dados, avatar())).size, (1200, 630))

    def test_entradas_ruins_nao_quebram(self):
        nomes_ruins = ("N" * 500, "田中太郎", "Ana 🎉🔥 ção", "", "\x07\x00")
        for nome in nomes_ruins:
            for chave in ("cartao", "resumo"):
                with self.subTest(nome=nome[:8], imagem=chave):
                    base = {"cartao": CARTAO, "nivel": NIVEL, "resumo": RESUMO, "wrapped": WRAPPED}[
                        chave
                    ]
                    dados = dict(base, nome=nome)
                    if chave == "resumo":
                        dados = dict(base, dj=(nome, "1 pedido"), tagarela=(nome, "2 mensagens"))
                    imagem = abrir(gerar(chave, dados, avatar()))
                    self.assertEqual(imagem.size, TAMANHOS[chave])

    def test_numeros_extremos_e_dados_faltando(self):
        for dados in (
            dict(CARTAO, nivel=1, avanco=0, meta=21, mensagens=0, pedidos=0, roletadas=0),
            dict(CARTAO, nivel=100, avanco=1, meta=1, mensagens=999_999_999),
            dict(CARTAO, meta=0),
            {"nome": "Só o nome"},
        ):
            self.assertEqual(abrir(imagens.gerar_cartao(dados, avatar())).size, (1200, 400))
        self.assertEqual(
            abrir(imagens.gerar_nivel({"nivel": 100, "avanco": 1, "meta": 1})).size,
            (1200, 400),
        )
        vazio = dict(WRAPPED, meses=[0] * 12, pedidos=0, dias=0, genero="", top=[])
        self.assertEqual(abrir(imagens.gerar_wrapped(vazio, None)).size, (1200, 630))
        sem_nada = dict(RESUMO, dj=None, tagarela=None, musica=None)
        self.assertEqual(abrir(imagens.gerar_resumo(sem_nada, None, None, None)).size, (1200, 480))
        ano = dict(RESUMO, tipo="ano", titulo="2025")
        self.assertEqual(abrir(imagens.gerar_resumo(ano, None, None, None)).size, (1200, 480))

    def test_avatar_e_capa_ausentes_ou_invalidos_usam_reservas(self):
        for lixo in (None, b"isto nao e uma imagem"):
            self.assertEqual(abrir(imagens.gerar_cartao(CARTAO, lixo)).size, (1200, 400))
            self.assertEqual(abrir(imagens.gerar_nivel(NIVEL, lixo)).size, (1200, 400))
            self.assertEqual(
                abrir(imagens.gerar_resumo(RESUMO, lixo, lixo, lixo)).size, (1200, 480)
            )


@unittest.skipUnless(Image is not None, "Pillow não instalado")
class FontesTests(unittest.TestCase):
    def test_fontes_incluidas_desenham_o_portugues(self):
        self.assertTrue(imagens.FONTE_MANROPE.exists() and imagens.FONTE_DEJAVU.exists())
        self.assertEqual(imagens.limpo("Nível Ção ãõêü ÁÉÍÓÚ"), "Nível Ção ãõêü ÁÉÍÓÚ")

    def test_limpo_mantem_alfabetos_cobertos_e_descarta_o_resto(self):
        self.assertEqual(imagens.limpo("Владимир Νίκος"), "Владимир Νίκος")
        # CJK sem glifo; árabe e hebraico sem shaping saem desconexos: todos descartados.
        self.assertEqual(imagens.limpo("田中太郎 محمد שלום"), "")
        self.assertEqual(imagens.limpo("Ana 🎉 Silva"), "Ana Silva")
        self.assertEqual(imagens.limpo("Ana\x07\x00Silva"), "AnaSilva")

    def test_sem_as_fontes_usa_ascii_simples(self):
        ausente = Path("nao-existe.ttf")
        with (
            patch.object(imagens, "FONTE_MANROPE", ausente),
            patch.object(imagens, "FONTE_DEJAVU", ausente),
        ):
            self.assertEqual(imagens.limpo("Nível Ção 🎉"), "Nivel Cao")
            self.assertEqual(abrir(imagens.gerar_cartao(CARTAO, avatar())).size, (1200, 400))

    def test_fonte_corrompida_se_comporta_como_ausente(self):
        with tempfile.TemporaryDirectory() as pasta:
            ruim = Path(pasta) / "quebrada.ttf"
            ruim.write_bytes(b"isto nao e uma fonte")
            with (
                patch.object(imagens, "FONTE_MANROPE", ruim),
                patch.object(imagens, "FONTE_DEJAVU", ruim),
            ):
                self.assertEqual(imagens.limpo("Nível"), "Nivel")
                self.assertEqual(abrir(imagens.gerar_nivel(NIVEL, avatar())).size, (1200, 400))

    def test_freetype_sem_fonte_variavel_nao_derruba_a_imagem(self):
        with patch.object(
            imagens.ImageFont.FreeTypeFont, "set_variation_by_axes", side_effect=NotImplementedError
        ):
            self.assertEqual(abrir(imagens.gerar_nivel(NIVEL, avatar())).size, (1200, 400))

    def test_sem_manrope_a_dejavu_assume(self):
        with patch.object(imagens, "FONTE_MANROPE", Path("nao-existe.ttf")):
            self.assertEqual(imagens.limpo("Nível"), "Nível")
            self.assertEqual(abrir(imagens.gerar_cartao(CARTAO, avatar())).size, (1200, 400))


class DisponibilidadeTests(unittest.TestCase):
    def test_disponivel_reflete_a_presenca_do_pillow(self):
        self.assertEqual(imagens.disponivel(), Image is not None)

    @unittest.skipIf(Image is not None, "só sem Pillow")
    def test_gerar_sem_pillow_levanta_erro_claro(self):
        with self.assertRaises(RuntimeError):
            imagens.gerar_cartao(CARTAO)

    def test_bot_e_imagens_importam_sem_pillow(self):
        """Garante em qualquer ambiente (inclusive o do CI, que tem Pillow) que o Pillow é opcional."""
        codigo = (
            "import sys; sys.modules['PIL'] = None; import imagens, memi_bot; "
            "assert not imagens.disponivel(); print('ok')"
        )
        resultado = subprocess.run(
            [sys.executable, "-c", codigo],
            cwd=Path(imagens.__file__).parent,
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(
            (resultado.returncode, resultado.stdout.strip()), (0, "ok"), resultado.stderr
        )


if __name__ == "__main__":
    unittest.main()
