"""Testes da sincronização dos emojis da aplicação (insígnias, patentes e emojis do bot)."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import discord

import emojis
import estilo
import tags


class EmojiFalso:
    def __init__(self, bot, nome, eid):
        self.bot, self.name, self.id = bot, nome, eid

    def __str__(self):
        return f"<:{self.name}:{self.id}>"

    async def delete(self):
        self.bot.apagados.append(self.name)
        self.bot.existentes = [e for e in self.bot.existentes if e is not self]


class BotFalso:
    def __init__(self, existentes=(), falhar_criacao=(), falhar_lista=False):
        self.proximo = 100
        self.existentes = [EmojiFalso(self, n, i) for n, i in existentes]
        self.criados, self.apagados = [], []
        self.falhar_criacao, self.falhar_lista = set(falhar_criacao), falhar_lista

    async def fetch_application_emojis(self):
        if self.falhar_lista:
            raise discord.HTTPException(NS(status=500, reason="x"), "falhou")
        return list(self.existentes)

    async def create_application_emoji(self, *, name, image):
        if name in self.falhar_criacao:
            raise discord.HTTPException(NS(status=400, reason="x"), "imagem inválida")
        self.proximo += 1
        emoji = EmojiFalso(self, name, self.proximo)
        self.existentes.append(emoji)
        self.criados.append(name)
        return emoji


class NS:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class Estado:
    def __init__(self):
        self.dados = {}

    def estado(self, chave, padrao=""):
        return self.dados.get(chave, padrao)

    def definir_estado(self, chave, valor):
        self.dados[chave] = str(valor)


class SincronizarTests(unittest.IsolatedAsyncioTestCase):
    async def test_envia_os_que_faltam_e_devolve_o_texto_do_emoji(self):
        bot, banco = BotFalso(), Estado()
        feitos = await emojis.sincronizar(bot, banco, {"mm_dj_call": b"a", "mm_papagaio": b"b"})
        self.assertEqual(sorted(bot.criados), ["mm_dj_call", "mm_papagaio"])
        self.assertEqual(feitos["mm_dj_call"], "<:mm_dj_call:101>")

    async def test_imagem_igual_nao_reenvia(self):
        bot, banco = BotFalso(), Estado()
        await emojis.sincronizar(bot, banco, {"mm_dj_call": b"a"})
        bot.criados.clear()
        feitos = await emojis.sincronizar(bot, banco, {"mm_dj_call": b"a"})
        self.assertEqual(bot.criados, [])
        self.assertEqual(feitos, {"mm_dj_call": "<:mm_dj_call:101>"})

    async def test_imagem_trocada_substitui_o_emoji(self):
        bot, banco = BotFalso(), Estado()
        await emojis.sincronizar(bot, banco, {"mm_dj_call": b"a"})
        feitos = await emojis.sincronizar(bot, banco, {"mm_dj_call": b"nova"})
        self.assertEqual(bot.apagados, ["mm_dj_call"])
        self.assertEqual(feitos["mm_dj_call"], "<:mm_dj_call:102>")

    async def test_remove_emojis_do_bot_que_sairam_do_catalogo_e_preserva_os_outros(self):
        bot, banco = BotFalso(existentes=[("mm_velho", 5), ("logo_do_dono", 6)]), Estado()
        await emojis.sincronizar(bot, banco, {"mm_dj_call": b"a"})
        self.assertEqual(bot.apagados, ["mm_velho"])
        self.assertIn("logo_do_dono", [e.name for e in bot.existentes])

    async def test_falha_ao_listar_nao_quebra_e_nao_envia_nada(self):
        bot = BotFalso(falhar_lista=True)
        with self.assertLogs(level="WARNING"):
            feitos = await emojis.sincronizar(bot, Estado(), {"mm_dj_call": b"a"})
        self.assertEqual(feitos, {})
        self.assertEqual(bot.criados, [])

    async def test_falha_num_emoji_nao_impede_os_outros(self):
        bot = BotFalso(falhar_criacao={"mm_ruim"})
        with self.assertLogs(level="WARNING"):
            feitos = await emojis.sincronizar(bot, Estado(), {"mm_ruim": b"x", "mm_bom": b"y"})
        self.assertEqual(list(feitos), ["mm_bom"])


class ItensTests(unittest.TestCase):
    def test_nome_do_emoji_respeita_o_limite_do_discord(self):
        self.assertEqual(emojis.nome_emoji("dj_call"), "mm_dj_call")
        self.assertLessEqual(len(emojis.nome_emoji("x" * 50)), 32)

    def test_itens_trazem_toda_insignia_com_imagem(self):
        itens = emojis.itens_para_enviar()
        for ident, info in tags.CATALOGO.items():
            if info.get("imagem"):
                self.assertIn(emojis.nome_emoji(ident), itens)
        self.assertIn(emojis.nome_emoji("breca"), itens)
        self.assertIn(emojis.nome_emoji("demiurgo"), itens)

    def test_emojis_do_bot_so_entram_se_houver_arquivo(self):
        with tempfile.TemporaryDirectory() as pasta:
            (Path(pasta) / "musica.png").write_bytes(b"png")
            (Path(pasta) / "nao_existe_no_estilo.png").write_bytes(b"png")
            with patch.object(emojis, "PASTA_EMOJIS", Path(pasta)):
                itens = emojis.itens_para_enviar()
        self.assertEqual(itens["mm_e_musica"], b"png")
        self.assertNotIn("mm_e_nao_existe_no_estilo", itens)

    def test_aplicar_troca_os_icones_das_tags_e_do_estilo(self):
        original = estilo.EMOJI["musica"]
        try:
            emojis.aplicar({"mm_dj_call": "<:mm_dj_call:1>", "mm_e_musica": "<:mm_e_musica:2>"})
            self.assertEqual(tags.icone("dj_call"), "<:mm_dj_call:1>")
            self.assertEqual(estilo.EMOJI["musica"], "<:mm_e_musica:2>")
        finally:
            tags.ICONES.clear()
            estilo.EMOJI["musica"] = original


if __name__ == "__main__":
    unittest.main()
