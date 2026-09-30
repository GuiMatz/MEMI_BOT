"""Testes de estilo.py (aparência das mensagens): não usam Discord nem banco."""

import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import discord

import estilo


class BarraTests(unittest.TestCase):
    def test_proporcional(self):
        self.assertEqual(estilo.barra(34, 80), "▰▰▰▰▱▱▱▱▱▱")

    def test_valores_fora_da_faixa_nao_estouram(self):
        self.assertEqual(estilo.barra(0, 21), "▱" * 10)
        self.assertEqual(estilo.barra(5, 5), "▰" * 10)
        self.assertEqual(estilo.barra(9, 5), "▰" * 10)
        self.assertEqual(estilo.barra(-3, 5), "▱" * 10)
        self.assertEqual(estilo.barra(1, 0), "▱" * 10)

    def test_largura_personalizada(self):
        self.assertEqual(estilo.barra(1, 2, largura=4), "▰▰▱▱")


class TextoTests(unittest.TestCase):
    def test_rodape_ignora_partes_vazias(self):
        self.assertEqual(estilo.rodape(), "MeMi BOT")
        self.assertEqual(estilo.rodape("a", "", None, "b"), "MeMi BOT · a · b")

    def test_prefixo_de_posicao(self):
        self.assertEqual(estilo.prefixo_posicao(1), "🥇")
        self.assertEqual(estilo.prefixo_posicao(3), "🥉")
        self.assertEqual(estilo.prefixo_posicao(4), "`04`")
        self.assertEqual(estilo.prefixo_posicao(12), "`12`")
        self.assertEqual(estilo.prefixo_posicao(100), "`100`")

    def test_cortar(self):
        self.assertEqual(estilo.cortar("abc", 5), "abc")
        self.assertEqual(estilo.cortar("abcdef", 4), "abc…")
        self.assertEqual(len(estilo.cortar("x" * 5000, 1024)), 1024)


class CampoTests(unittest.TestCase):
    def test_campo_vazio_nao_e_adicionado(self):
        embed = discord.Embed()
        for vazio in ("", None, "   ", "\n"):
            self.assertFalse(estilo.campo(embed, "nome", vazio))
        self.assertEqual(len(embed.fields), 0)

    def test_campo_longo_e_cortado_nos_limites(self):
        embed = discord.Embed()
        self.assertTrue(estilo.campo(embed, "n" * 400, "v" * 2000, inline=False))
        self.assertEqual(len(embed.fields[0].name), estilo.LIMITE_TITULO)
        self.assertEqual(len(embed.fields[0].value), estilo.LIMITE_CAMPO)
        self.assertFalse(embed.fields[0].inline)


class FeedbackTests(unittest.IsolatedAsyncioTestCase):
    def test_cores_e_emojis_por_tipo(self):
        for tipo, cor, emoji in (
            ("sucesso", estilo.COR_SUCESSO, "✅"),
            ("aviso", estilo.COR_AVISO, "⚠️"),
            ("erro", estilo.COR_ERRO, "❌"),
        ):
            embed = estilo.feedback(tipo, "texto")
            self.assertEqual(embed.description, f"{emoji} texto")
            self.assertEqual(embed.color.value, cor)

    def test_texto_enorme_cabe_no_limite(self):
        self.assertLessEqual(len(estilo.feedback("erro", "x" * 9000).description), 4096)

    async def test_responder_envia_embed_sem_mencoes(self):
        ctx = NS(send=AsyncMock())
        await estilo.responder(ctx, "erro", "falhou")
        kw = ctx.send.call_args.kwargs
        self.assertEqual(kw["embed"].description, "❌ falhou")
        self.assertEqual(kw["allowed_mentions"].to_dict(), {"parse": []})


class RankingTests(unittest.TestCase):
    def setUp(self):
        self.itens = [f"**P{i}** · {i}" for i in range(1, 26)]

    def linhas(self, embed):
        return embed.description.split("\n")

    def test_medalhas_e_numeros(self):
        linhas = self.linhas(estilo.embed_ranking("T", self.itens))
        self.assertEqual(linhas[0], "🥇 **P1** · 1")
        self.assertEqual(linhas[2], "🥉 **P3** · 3")
        self.assertEqual(linhas[3], "`04` **P4** · 4")
        self.assertEqual(len(linhas), 10)

    def test_segunda_pagina_continua_a_numeracao(self):
        embed = estilo.embed_ranking("T", self.itens, pagina=1, rodape_partes=("x",))
        self.assertEqual(self.linhas(embed)[0], "`11` **P11** · 11")
        self.assertEqual(embed.footer.text, "MeMi BOT · página 2/3 · x")

    def test_pagina_fora_do_intervalo_e_ajustada(self):
        embed = estilo.embed_ranking("T", self.itens, pagina=99)
        self.assertEqual(embed.footer.text, "MeMi BOT · página 3/3")
        self.assertEqual(self.linhas(embed)[0], "`21` **P21** · 21")

    def test_ranking_vazio_mostra_mensagem(self):
        embed = estilo.embed_ranking("T", [])
        self.assertIn("Ninguém no ranking ainda", embed.description)
        self.assertEqual(embed.footer.text, "MeMi BOT · página 1/1")

    def test_subtitulo_em_italico(self):
        embed = estilo.embed_ranking("T", self.itens, subtitulo="mês atual")
        self.assertTrue(embed.description.startswith("*mês atual*\n\n🥇"))

    def test_sua_posicao_so_quando_fora_da_pagina(self):
        fora = estilo.embed_ranking("T", self.itens, pagina=0, meu_indice=14)
        self.assertTrue(fora.description.endswith("📍 Sua posição: **#15** de 25"))
        dentro = estilo.embed_ranking("T", self.itens, pagina=1, meu_indice=14)
        self.assertNotIn("Sua posição", dentro.description)
        nenhum = estilo.embed_ranking("T", self.itens, meu_indice=None)
        self.assertNotIn("Sua posição", nenhum.description)

    def test_sem_numeracao_nao_tem_medalha_nem_numero(self):
        embed = estilo.embed_ranking("T", ["a", "b", "c", "d"], numerar=False)
        self.assertEqual(embed.description, "a\nb\nc\nd")

    def test_capa_vira_miniatura(self):
        embed = estilo.embed_ranking("T", self.itens, capa="https://example.test/c.jpg")
        self.assertEqual(embed.thumbnail.url, "https://example.test/c.jpg")

    def test_itens_enormes_respeitam_o_limite(self):
        embed = estilo.embed_ranking("T" * 400, ["x" * 900] * 12)
        self.assertLessEqual(len(embed.description), estilo.LIMITE_DESCRICAO)
        self.assertLessEqual(len(embed.title), estilo.LIMITE_TITULO)
        self.assertLessEqual(len(embed), estilo.LIMITE_EMBED)


class AjudaTests(unittest.TestCase):
    def test_secoes_com_titulo_em_negrito(self):
        embed = estilo.embed_ajuda(
            [
                ("🏆 Rankings", [("mm!levels", "níveis")]),
                ("🎵 Música", [("mm!aleatoria", "sorteia")]),
            ]
        )
        self.assertIn("**🏆 Rankings**\n`mm!levels` — níveis", embed.description)
        self.assertIn("**🎵 Música**\n`mm!aleatoria` — sorteia", embed.description)
        self.assertIn("horário de Brasília", embed.footer.text)


DADOS = {
    "djs": [[20, 2], [10, 1]],
    "tagarelas": [[10, 3], [20, 2]],
    "mensagens": 12340,
    "pedidos": 410,
    "musicas": [["Faixa", "Artista", 18], ["Outra", "Banda", 4]],
    "artistas": [["Artista", 1, 18], ["Banda", 2, 5]],
}


def nome(uid):
    return f"P{uid}"


class FormatosTests(unittest.TestCase):
    def test_milhar_plural_e_faixa(self):
        self.assertEqual(estilo.milhar(1234567), "1.234.567")
        self.assertEqual(estilo.plural(1, "pedido", "pedidos"), "1 pedido")
        self.assertEqual(estilo.plural(2000, "pedido", "pedidos"), "2.000 pedidos")
        self.assertEqual(estilo.linha_faixa("Faixa", "Artista", 3), "**Faixa** · Artista · 3x")
        self.assertEqual(estilo.linha_faixa("Faixa", "", 1), "**Faixa** · 1x")

    def test_rotulo_de_periodo(self):
        self.assertEqual(estilo.rotulo_periodo("mes", "2025-12"), "dezembro de 2025")
        self.assertEqual(estilo.rotulo_periodo("mes", "2026-03"), "março de 2026")
        self.assertEqual(estilo.rotulo_periodo("ano", "2025"), "2025")


class ResumoTests(unittest.TestCase):
    def test_com_imagem_o_resumo_do_mes_fica_so_com_o_titulo(self):
        embed = estilo.embed_resumo("mes", "2025-12", DADOS, nome, com_imagem=True)
        self.assertEqual(embed.title, "📅 Resumo de dezembro de 2025")
        self.assertEqual(len(embed.fields), 0)
        self.assertEqual(embed.footer.text, "MeMi BOT · fechamento de dezembro")

    def test_com_imagem_o_resumo_do_ano_mantem_os_tops_e_tira_os_numeros(self):
        embed = estilo.embed_resumo("ano", "2025", DADOS, nome, com_imagem=True)
        nomes = [f.name for f in embed.fields]
        self.assertIn("🏆 Top 3 DJs", nomes)
        self.assertIn("🎤 Top 5 artistas", nomes)
        self.assertNotIn("📊 O ano em números", nomes)

    def campos(self, embed):
        return {f.name: f.value for f in embed.fields}

    def test_resumo_do_mes(self):
        embed = estilo.embed_resumo("mes", "2025-12", DADOS, nome)
        self.assertEqual(embed.title, "📅 Resumo de dezembro de 2025")
        campos = self.campos(embed)
        self.assertEqual(campos["🎧 DJ do Mês"], "**P20** · 2 pedidos")
        self.assertEqual(campos["💬 Tagarela do Mês"], "**P10** · 3 mensagens")
        self.assertEqual(campos["🎵 Música do mês"], "**Faixa** · Artista · 18x")
        self.assertEqual(campos["📊 O mês em números"], "12.340 mensagens · 410 pedidos")
        self.assertEqual(embed.footer.text, "MeMi BOT · fechamento de dezembro")

    def test_resumo_do_ano(self):
        embed = estilo.embed_resumo("ano", "2025", DADOS, nome)
        self.assertEqual(embed.title, "🏁 Resumo de 2025")
        campos = self.campos(embed)
        self.assertEqual(campos["🏆 Top 3 DJs"], "🥇 **P20** · 2 pedidos\n🥈 **P10** · 1 pedido")
        self.assertIn("🥇 **P10** · 3 mensagens", campos["🏆 Top 3 tagarelas"])
        self.assertEqual(
            campos["🎵 Top 5 músicas"], "🥇 **Faixa** · Artista · 18x\n🥈 **Outra** · Banda · 4x"
        )
        self.assertEqual(
            campos["🎤 Top 5 artistas"],
            "🥇 **Artista** · 1 música · 18 tocadas\n🥈 **Banda** · 2 músicas · 5 tocadas",
        )
        self.assertEqual(campos["📊 O ano em números"], "12.340 mensagens · 410 pedidos")
        self.assertEqual(embed.footer.text, "MeMi BOT · fechamento de 2025")

    def test_dados_vazios_nao_geram_campos_vazios(self):
        vazio = {
            "djs": [],
            "tagarelas": [],
            "mensagens": 0,
            "pedidos": 0,
            "musicas": [],
            "artistas": [],
        }
        for tipo, periodo in (("mes", "2025-12"), ("ano", "2025")):
            embed = estilo.embed_resumo(tipo, periodo, vazio, nome)
            for campo in embed.fields:
                self.assertTrue(campo.value.strip())

    def test_textos_enormes_respeitam_os_limites(self):
        grande = dict(DADOS)
        grande["musicas"] = [["T" * 500, "A" * 500, 9]] * 5
        grande["artistas"] = [["B" * 900, 9, 9]] * 5
        for tipo, periodo in (("mes", "2025-12"), ("ano", "2025")):
            embed = estilo.embed_resumo(tipo, periodo, grande, lambda uid: "N" * 300)
            self.assertLessEqual(len(embed), estilo.LIMITE_EMBED)
            for campo in embed.fields:
                self.assertLessEqual(len(campo.value), estilo.LIMITE_CAMPO)


PATENTE = {"nome": "Resenheiro", "cor": 0xCD7F32}


class EmojiSimplesTests(unittest.TestCase):
    def test_emoji_simples_usa_o_padrao_quando_ha_personalizado(self):
        original = estilo.EMOJI["mudae"]
        try:
            self.assertEqual(estilo.emoji_simples("mudae"), "🎎")
            estilo.EMOJI["mudae"] = "<:mm_e_mudae:1>"
            self.assertEqual(estilo.emoji_simples("mudae"), "🎎")
            estilo.EMOJI["mudae"] = "🎲"
            self.assertEqual(estilo.emoji_simples("mudae"), "🎲")
        finally:
            estilo.EMOJI["mudae"] = original


class NivelTests(unittest.TestCase):
    def test_com_imagem_o_embed_fica_enxuto(self):
        embed = estilo.embed_nivel(
            "Fulano",
            32,
            (32, 5, 10),
            PATENTE,
            avatar_url="https://example.test/a.png",
            com_imagem=True,
        )
        self.assertEqual(embed.title, "⬆️ Nível 32")
        self.assertEqual(embed.description, "**Fulano** subiu para o nível **32** · Resenheiro")
        self.assertEqual(len(embed.fields), 0)
        self.assertIsNone(embed.thumbnail.url)  # o avatar já está na imagem
        self.assertIn("5/10 XP para o nível 33", embed.footer.text)  # progresso fica no rodapé

    def test_embed_de_level_up(self):
        embed = estilo.embed_nivel(
            "Fulano", 32, (32, 5, 10), PATENTE, avatar_url="https://example.test/a.png"
        )
        campo = embed.fields[0]
        self.assertEqual(campo.name, "Nível 32")
        self.assertEqual(campo.value, "▰▰▰▰▰▱▱▱▱▱ 5/10 XP")
        self.assertEqual(embed.thumbnail.url, "https://example.test/a.png")

    def test_troca_de_patente_tem_destaque_e_cor_da_patente(self):
        embed = estilo.embed_nivel(
            "Fulano", 20, (20, 0, 820), PATENTE, trocou=True, icone="<:mm_x:1>"
        )
        self.assertEqual(embed.title, "🎖️ Nova patente: Resenheiro")
        self.assertIn("agora é <:mm_x:1> **Resenheiro**", embed.description)
        self.assertEqual(embed.color.value, 0xCD7F32)

    def test_nivel_maximo(self):
        embed = estilo.embed_nivel("Fulano", 100, (100, 1, 1), PATENTE)
        self.assertIn("nível máximo", embed.fields[0].value)

    def test_sem_avatar_e_com_nome_enorme(self):
        embed = estilo.embed_nivel("N" * 3000, 2, (2, 0, 60), PATENTE)
        self.assertIsNone(embed.thumbnail.url)
        self.assertLessEqual(len(embed), estilo.LIMITE_EMBED)
        self.assertLessEqual(len(embed.description), estilo.LIMITE_DESCRICAO)


if __name__ == "__main__":
    unittest.main()
