"""Testes locais: python -m unittest -v test_memi_bot.py (não conecta ao Discord)."""

import asyncio
import gzip
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import memi_bot as m


def sid(iso="2026-09-15T12:00:00", seq=0):
    return discord.utils.time_snowflake(datetime.fromisoformat(iso).replace(tzinfo=m.FUSO)) + seq


def msg(uid=10, seq=0, content="olá", bot=False, date="2026-09-15T12:00:00", channel=1):
    return NS(
        id=sid(date, seq),
        author=NS(
            id=uid, bot=bot, name="jockie" if bot else f"u{uid}", display_name=f"Apelido {uid}"
        ),
        content=content,
        channel=NS(id=channel),
        embeds=[],
        guild=NS(id=1),
    )


class BancoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "memi.db"
        self.b = m.Banco(self.path)

    def tearDown(self):
        self.b.con.close()
        self.temp.cleanup()

    def possui(self, uid, item):
        return item in dict(self.b.itens(uid, "titulo"))

    def lote(self, uid, n, fonte="mensagens", bot=False, date="2026-09-10T00:00:00"):
        """Grande histórico simulado com eventos reais e reconstrução do cache por SQL."""
        tabela, autor, contador, ultimo = self.b.FONTES[fonte]
        base = sid(date)
        with self.b.con:
            self.b.con.execute(
                "INSERT OR REPLACE INTO autores VALUES (?,?,?)", (uid, int(bot), str(uid))
            )
            if fonte == "mensagens":
                self.b.con.executemany(
                    "INSERT INTO mensagens VALUES (?,?,?,?)",
                    ((base + i, uid, 1, int(bot)) for i in range(n)),
                )
            elif fonte == "pedidos":
                self.b.con.executemany(
                    "INSERT INTO pedidos VALUES (?,?,?,?)",
                    ((base + i, 1, uid, "m!p canção") for i in range(n)),
                )
            else:
                self.b.con.executemany(
                    "INSERT INTO mudae VALUES (?,?)", ((base + i, uid) for i in range(n))
                )
            self.b.con.execute("INSERT OR IGNORE INTO totais(usuario_id) VALUES (?)", (uid,))
            self.b.con.execute(
                f"UPDATE totais SET {contador}=(SELECT COUNT(*) FROM {tabela} WHERE {autor}=?), {ultimo}=(SELECT MAX(message_id) FROM {tabela} WHERE {autor}=?) WHERE usuario_id=?",
                (uid, uid, uid),
            )

    def test_01_mensagem_total_mes_ano_e_persistencia(self):
        self.b.receber(msg(), avancar=True, recompensar=True)
        mes, _ = m.intervalo("mes", datetime(2026, 9, 20, tzinfo=m.FUSO))
        ano, _ = m.intervalo("ano", datetime(2026, 9, 20, tzinfo=m.FUSO))
        self.assertEqual(
            [self.b.total_usuario(10, "mensagens", d) for d in (0, mes, ano)], [1, 1, 1]
        )
        self.assertEqual(self.b.ranking_atividade(), [(10, 1)])
        self.assertEqual(m.nivel(1), 1)
        self.b.con.close()
        self.b = m.Banco(self.path)
        self.assertEqual(self.b.total_usuario(10, "mensagens"), 1)
        self.assertEqual(self.b.marca(1), sid())

    def test_02_duplicacao_mensagem_pedido_roletada_recompensa(self):
        a, b = msg(content="m!play música"), msg(seq=1, content="$wa")
        for _ in range(3):
            self.b.receber(a, avancar=True, recompensar=True, avisar=True)
            self.b.receber(b, avancar=True, recompensar=True, avisar=True)
        self.assertEqual(
            [self.b.total_usuario(10, x) for x in ("mensagens", "pedidos", "mudae")], [2, 1, 1]
        )
        self.assertEqual(self.b.con.execute("SELECT COUNT(*) FROM avisos").fetchone()[0], 2)

    def test_03_niveis_e_progresso_todos_limites(self):
        for total, esperado in m.PONTOS_NIVEL:
            with self.subTest(total=total):
                self.assertEqual(m.nivel(total), esperado)
                if total:
                    self.assertEqual(m.nivel(total - 1), esperado - 1)
                if total < 200000:
                    self.assertEqual(m.nivel(total + 1), esperado)
        self.assertEqual(m.progresso_nivel(75500), (277, "100/200"))
        self.assertEqual(m.progresso_nivel(50500), (250, "500/1000"))
        self.assertEqual(m.progresso_nivel(0), (1, "0/21"))
        self.assertEqual(m.nivel(200001), 1000)
        self.assertEqual(m.nivel(9999999), 1000)

    def test_04_desempate_ultima_ocorrencia_e_nao_id_usuario(self):
        for item in [msg(20, seq=1), msg(10, seq=2), msg(10, seq=4), msg(20, seq=3)]:
            self.b.receber(item)
        self.assertEqual(self.b.ranking_atividade(False), [(20, 2), (10, 2)])
        for item in [msg(20, seq=5, content="m!p x"), msg(10, seq=6, content="m!p x")]:
            self.b.receber(item)
        self.assertEqual(self.b.ranking_usuarios(), [(20, 1), (10, 1)])

    def test_05_bots_contados_sem_titulos_nem_ranking_humano(self):
        self.lote(5, 1000, bot=True)
        self.b.receber(msg(10))
        self.b.reconciliar(avisar=True)
        self.assertEqual(m.nivel(self.b.total_usuario(5, "mensagens")), 50)
        self.assertEqual(self.b.ranking_atividade(False), [(10, 1)])
        self.assertEqual(self.b.ranking_atividade(True), [(5, 1000)])
        self.assertEqual(self.b.itens(5, "titulo"), [])
        with self.assertRaises(ValueError):
            self.b.conceder_manual(5, "titulo", "BONGAS", True)

    def test_06_titulos_mensagens_cada_limite(self):
        for i, (limite, item, _) in enumerate(m.TITULOS_MENSAGENS):
            uid = 100 + i
            self.lote(uid, limite - 1, date=f"2026-09-{i+1:02}T00:00:00")
            self.b.reconciliar()
            self.assertFalse(self.possui(uid, item))
            self.b.receber(msg(uid, seq=i), recompensar=True)
            self.assertTrue(self.possui(uid, item))
            self.assertTrue(all(self.possui(uid, k) for _, k, _ in m.TITULOS_MENSAGENS[: i + 1]))

    def test_07_titulos_musicas_cada_limite(self):
        for i, (limite, item, _) in enumerate(m.TITULOS_MUSICAS):
            uid = 200 + i
            self.lote(uid, limite - 1, "pedidos", date=f"2026-09-{i+1:02}T00:00:00")
            self.b.reconciliar()
            self.assertFalse(self.possui(uid, item))
            self.b.receber(msg(uid, seq=i, content="m!p som"), recompensar=True)
            self.assertTrue(self.possui(uid, item))

    def test_08_mudae_todos_comandos_argumentos_e_roletador(self):
        for i, cmd in enumerate(sorted(m.COMANDOS_MUDAE)):
            self.b.receber(msg(seq=i, content="$" + cmd.upper() + " argumento"))
        self.assertEqual(self.b.total_usuario(10, "mudae"), 12)
        for i, content in enumerate(["$wish", "$waifu", "$wxyz", "$w!", "$marry", "oi $w"], 20):
            self.b.receber(msg(seq=i, content=content))
        self.b.receber(msg(seq=30, content="$w", bot=True, uid=30))
        self.assertEqual(self.b.total_usuario(10, "mudae"), 12)
        self.assertEqual(self.b.total_usuario(30, "mudae"), 0)
        for i in range(12, 1000):
            self.b.receber(msg(seq=100 + i, content="$w"), recompensar=True)
        self.assertTrue(self.possui(10, "roletador"))

    def test_09_top_rotativo_limpa_favorito_e_retorno_avisa(self):
        self.b.receber(msg(10, 1), recompensar=True, avisar=True)
        self.assertTrue(self.b.selecionar_titulo(10, "TAGARELA DO CHAT"))
        self.b.receber(msg(20, 2), recompensar=True, avisar=True)
        self.assertTrue(self.possui(10, "tagarela_chat"))
        self.b.receber(msg(20, 3), recompensar=True, avisar=True)
        self.assertFalse(self.possui(10, "tagarela_chat"))
        self.assertEqual(self.b.perfil(10)["titulo"], "")
        self.assertTrue(self.possui(20, "tagarela_chat"))
        self.b.receber(msg(10, 4), recompensar=True, avisar=True)
        self.b.receber(msg(10, 5), recompensar=True, avisar=True)
        self.assertEqual(
            self.b.con.execute("SELECT COUNT(*) FROM avisos WHERE item='tagarela_chat'").fetchone()[
                0
            ],
            3,
        )

    def test_10_fechamentos_calendario_brasilia_pendentes_e_sem_duplicar(self):
        self.b.receber(msg(10, date="2025-12-31T23:59:59", content="m!p a"))
        self.b.receber(msg(20, date="2026-01-01T00:00:00", content="m!p b"))
        self.b.receber(msg(20, seq=1, date="2026-01-01T00:00:00", content="m!p c"))
        fechados = self.b.fechar_periodos(datetime(2026, 1, 1, tzinfo=m.FUSO))
        self.assertEqual(set(fechados), {("mes", "2025-12"), ("ano", "2025")})
        self.assertEqual(dict(self.b.itens(10, "titulo"))["dj_mes"], 1)
        self.assertTrue(self.possui(10, "tagarela_ano"))
        self.assertFalse(self.possui(20, "dj_mes"))
        self.b.fechar_periodos(datetime(2026, 3, 1, tzinfo=m.FUSO))
        self.assertTrue(self.possui(20, "dj_mes"))
        self.assertEqual(self.b.fechar_periodos(datetime(2026, 3, 1, tzinfo=m.FUSO)), [])

    def test_11_retroatividade_silenciosa_e_acumulativa(self):
        self.lote(10, 1000, date="2025-10-01T00:00:00")
        self.lote(10, 500, "pedidos", date="2025-11-01T00:00:00")
        self.lote(10, 1000, "mudae", date="2025-12-01T00:00:00")
        self.b.receber(msg(10, date="2025-11-02T00:00:00"))
        for _ in range(2):
            self.b.reconciliar()
            self.b.fechar_periodos(datetime(2026, 1, 1, tzinfo=m.FUSO))
        self.assertEqual(dict(self.b.itens(10, "insignia"))["tagarela_mes"], 2)
        self.assertTrue(self.possui(10, "dj_roger"))
        self.assertTrue(self.possui(10, "roletador"))
        self.assertEqual(self.b.con.execute("SELECT COUNT(*) FROM avisos").fetchone()[0], 0)

    def test_12_give_catalogo_deduplicacao_sem_aviso(self):
        with self.assertRaises(ValueError):
            self.b.conceder_manual(10, "titulo", "Não existe")
        self.assertTrue(self.b.conceder_manual(10, "titulo", "BONGAS"))
        self.assertFalse(self.b.conceder_manual(10, "titulo", "bongas"))
        self.assertTrue(self.b.conceder_manual(10, "insignia", "BONGAS"))
        self.assertEqual(dict(self.b.itens(10, "insignia")), {"bongas": 1})
        self.assertEqual(self.b.con.execute("SELECT COUNT(*) FROM avisos").fetchone()[0], 0)

    def test_13_titulo_possuido_sem_acento(self):
        self.assertFalse(self.b.selecionar_titulo(10, "Bréca Games"))
        self.b.conceder_manual(10, "titulo", "breca games")
        self.assertTrue(self.b.selecionar_titulo(10, "BRECA GAMES"))
        self.assertEqual(self.b.perfil(10)["titulo"], "breca")

    def test_14_playlist_um_pedido_tres_faixas(self):
        playlist = "https://open.spotify.com/playlist/abc"
        self.b.receber(msg(content="m!play " + playlist))
        for i in range(3):
            evento = msg(50, i + 1, bot=True)
            evento.embeds = [
                NS(
                    title="Started playing",
                    description=f"[Música {i} by Artista](https://example.test)",
                )
            ]
            self.b.receber(evento)
        self.assertEqual(self.b.total_usuario(10, "pedidos"), 1)
        self.assertEqual(self.b.total(), 3)
        self.assertEqual(len(self.b.ranking_musicas()), 3)
        self.assertEqual(self.b.consultas_usuario(10)[0], {})

    def test_15_loop_nao_conta_pedidos_aliases_contam(self):
        for i, cmd in enumerate(["m!play", "m!p", "p!play", "p!p"]):
            self.b.receber(msg(seq=i, content=cmd.upper() + " canção"))
        for i, cmd in enumerate(["m!loop", "m!pause", "p!playlist", "m!pl", "oi m!play"], 10):
            self.b.receber(msg(seq=i, content=cmd + " algo"))
        self.assertEqual(self.b.total_usuario(10, "pedidos"), 4)

    def test_16_generos_acento_faixas_distintas_e_periodo(self):
        for i, t in enumerate(["Canção A", "Canção A", "Canção B"]):
            self.b.inserir([(sid(seq=i), 1, t, "Banda", "jockie")])
        for t in ["Canção A", "Canção B"]:
            self.b.salvar_cache_deezer(m.chave_deezer(t + " Banda"), "", "Música Brasileira", 1)
        self.assertEqual(self.b.ranking_generos(), [("Música Brasileira", 3, 2)])
        self.assertEqual(len(self.b.ranking_generos(genero="musica brasileira")), 2)
        self.assertEqual(self.b.generos_pendentes(), 0)
        self.assertEqual(self.b.ranking_generos(sid("2026-10-01T00:00:00")), [])

    def test_17_backup_sqlite_consistente_retencao_e_uma_copia_por_dia(self):
        self.b.receber(msg())
        primeiro = self.b.backup_diario(datetime(2026, 9, 1, tzinfo=m.FUSO))
        stamp = primeiro.stat().st_mtime_ns
        self.b.backup_diario(datetime(2026, 9, 1, tzinfo=m.FUSO))
        self.assertEqual(stamp, primeiro.stat().st_mtime_ns)
        for dia in range(2, 10):
            final = self.b.backup_diario(datetime(2026, 9, dia, tzinfo=m.FUSO))
        self.assertEqual(len(list(final.parent.glob("memi_*.db.gz"))), 7)
        restaurado = Path(self.temp.name) / "restaurado.db"
        restaurado.write_bytes(gzip.decompress(final.read_bytes()))
        con = sqlite3.connect(restaurado)
        try:
            self.assertEqual(con.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(con.execute("SELECT COUNT(*) FROM mensagens").fetchone()[0], 1)
        finally:
            con.close()

    def test_18_migracao_preserva_tabelas_historicas_e_faz_backup(self):
        outro = Path(self.temp.name) / "legado.db"
        legado = m.BancoLegado(outro)
        legado.inserir([(sid(), 1, "Título", "Artista", "jockie")])
        legado.inserir_pedidos([(sid(seq=1), 1, 10, "m!p faixa")])
        legado.substituir_atividade({10: 9000}, {10: False}, 123)
        legado.salvar_cache_deezer("faixa", "capa", "Rock", 123)
        legado.salvar_cache_link("url", "nome", 123)
        legado.salvar_marca(1, sid(seq=2))
        legado.con.close()
        novo = m.Banco(outro)
        try:
            self.assertEqual(novo.total(), 1)
            self.assertEqual(novo.total_pedidos(), 1)
            self.assertEqual(novo.ranking_usuarios(), [(10, 1)])
            self.assertEqual(novo.cache_deezer("faixa"), ("capa", "Rock", 123))
            self.assertEqual(novo.cache_link("url"), ("nome", 123))
            self.assertEqual(novo.marca(1), 0)  # Música antiga não comprova scan de mensagens.
            self.assertEqual(
                novo.con.execute("SELECT mensagens FROM atividade").fetchone()[0], 9000
            )
            self.assertTrue(list(outro.parent.glob("backups/migracao_*/memi.db.gz")))
            novo.receber(msg(seq=1, content="m!p faixa"))
            self.assertEqual(novo.total_pedidos(), 1)
        finally:
            novo.con.close()

    def test_19_filtragem_membros_preserva_dados(self):
        self.b.receber(msg(20, 1))
        self.b.receber(msg(10, 2))
        self.assertEqual(self.b.ranking_contagens("mensagens", elegiveis={10}), [(10, 1)])
        self.assertEqual(
            self.b.ranking_contagens("mensagens", elegiveis={10, 20}), [(20, 1), (10, 1)]
        )

    def test_20_transacao_falha_nao_avanca_cursor_nem_eventos(self):
        with patch.object(self.b, "_avaliar_usuario", side_effect=RuntimeError("falha simulada")):
            with self.assertRaises(RuntimeError):
                self.b.receber(msg(content="m!p faixa"), avancar=True, recompensar=True)
        self.assertEqual(self.b.total_pedidos(), 0)
        self.assertEqual(self.b.total_usuario(10, "mensagens"), 0)
        self.assertEqual(self.b.marca(1), 0)


class Canal:
    def __init__(self, cid, eventos):
        self.id, self.eventos = cid, eventos

    async def history(self, *, after=None, before=None, **kwargs):
        for evento in sorted(self.eventos, key=lambda x: x.id):
            if after and evento.id <= after.id:
                continue
            if before and evento.id >= before.id:
                continue
            await asyncio.sleep(0)
            yield evento


class IntegracaoTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        fake = NS(get_guild=lambda _: None, intents=NS(members=False), get_channel=lambda _: None)
        with patch.object(m, "ARQUIVO_BANCO", Path(self.temp.name) / "memi.db"):
            self.cog = m.Musicas(fake)
        self.b = self.cog.banco

    async def asyncTearDown(self):
        await self.cog.cog_unload()
        self.temp.cleanup()

    async def test_21_permissoes_id_memi_read_scan_give(self):
        for command in (m.Musicas.read, m.Atividade.scan, m.Atividade.give):
            for uid, permitido in [(m.MEMI_ID, True), (123, False)]:
                ctx = NS(author=NS(id=uid), guild=NS(id=1))
                if permitido:
                    for check in command.checks:
                        self.assertTrue(await discord.utils.maybe_coroutine(check, ctx))
                else:
                    with self.assertRaises(m.commands.CheckFailure):
                        for check in command.checks:
                            await discord.utils.maybe_coroutine(check, ctx)

    async def test_22_importacao_recuperacao_ordenada_deduplicacao(self):
        a = Canal(1, [msg(10, 1, content="m!p a", channel=1), msg(10, 5, channel=1)])
        b = Canal(2, [msg(20, 2, content="$w", channel=2), msg(20, 3, channel=2)])
        self.cog.canais_legiveis = AsyncMock(return_value=([a, b], []))
        status = NS(edit=AsyncMock())
        total, erros = await self.cog.sincronizar(status=status)
        self.assertEqual((total, erros), (4, []))
        self.assertEqual(self.b.estado("importacao_concluida"), "1")
        self.assertEqual(self.b.con.execute("SELECT COUNT(*) FROM avisos").fetchone()[0], 0)
        self.assertEqual(self.b.ranking_atividade(False), [(20, 2), (10, 2)])
        # IDs novos depois do ponto seguro, como quando o PC ficou desligado.
        novo = msg(10, content="m!p b", channel=1)
        novo.id = self.b.marca(1) + 10
        a.eventos.append(novo)
        with patch.object(
            m.discord.utils, "utcnow", return_value=discord.utils.utcnow() + timedelta(seconds=1)
        ):
            total, erros = await self.cog.sincronizar()
        self.assertEqual(total, 1)
        self.assertEqual(self.b.total_usuario(10, "mensagens"), 3)
        self.assertTrue(
            self.b.con.execute(
                "SELECT 1 FROM avisos WHERE item='tagarela_chat' AND usuario_id=10"
            ).fetchone()
        )
        await self.cog.sincronizar(completo=True, silencioso=True)
        self.assertEqual(self.b.total_usuario(10, "pedidos"), 2)

    async def test_23_leitura_parcial_nao_fecha_periodos(self):
        canal = Canal(1, [msg(10, date="2025-01-01T00:00:00")])
        self.cog.canais_legiveis = AsyncMock(return_value=([canal], ["thread não pôde ser lida"]))
        await self.cog.sincronizar()
        self.assertNotEqual(self.b.estado("importacao_concluida"), "1")
        self.assertEqual(
            self.b.con.execute("SELECT COUNT(*) FROM periodos_fechados").fetchone()[0], 0
        )

    async def test_24_perfil_tres_paginas_vazios_e_botoes_publicos(self):
        pessoa = NS(
            id=10,
            display_name="Apelido",
            bot=False,
            display_avatar=NS(with_size=lambda _: NS(url="https://example.test/avatar.png")),
        )
        paginas = self.cog.paginas_perfil(pessoa)
        self.assertEqual(len(paginas), 3)
        self.assertEqual(paginas[0].title, "APELIDO")
        self.assertIn("0/21", str(paginas[0].to_dict()))
        self.assertNotIn("sem dados", str([x.to_dict() for x in paginas]))
        self.assertTrue(all(len(x) <= 6000 for x in paginas))
        view = m.PerfilView(paginas)
        interaction = NS(response=NS(edit_message=AsyncMock()), user=NS(id=999))
        self.assertTrue(await view.interaction_check(interaction))
        await view.proxima.callback(interaction)
        self.assertEqual(view.pagina, 1)
        await view.on_timeout()
        self.assertTrue(all(x.disabled for x in view.children))

    async def test_25_genero_favorito_todos_pedidos_sem_playlist(self):
        for i, query in enumerate(
            ["Rock A", "Rock B", "Pop A", "Pop A", "https://open.spotify.com/playlist/id"]
        ):
            self.b.receber(msg(seq=i, content="m!play " + query))
        with self.b.con:
            self.b.con.execute(
                "UPDATE classificacao_pedidos SET genero='Rock' WHERE chave LIKE 'rock%'"
            )
            self.b.con.execute(
                "UPDATE classificacao_pedidos SET genero='Pop' WHERE chave LIKE 'pop%'"
            )
        self.cog.links.titulo_seguro = AsyncMock(return_value=None)
        mais, genero, pendentes = await self.cog.resumo_musical(10)
        self.assertIn("Pop A", mais)
        self.assertEqual(genero, "Rock")  # Empate 2x2, primeiro gênero pedido.
        self.assertEqual(pendentes, 0)

    async def test_26_avisos_sem_mencao_e_sem_repetir(self):
        canal = NS(send=AsyncMock())
        self.cog.bot.get_channel = lambda _: canal
        self.cog.recuperando = False
        self.b.definir_estado("importacao_concluida", "1")
        self.b.receber(msg(), recompensar=True, avisar=True)
        await self.cog.enviar_avisos()
        await self.cog.enviar_avisos()
        self.assertEqual(canal.send.await_count, 1)
        args, kw = canal.send.call_args
        self.assertIn("Apelido 10", args[0])
        self.assertNotIn("<@", args[0])
        self.assertEqual(kw["allowed_mentions"].to_dict(), {"parse": []})

    async def test_27_comandos_anteriores_e_help_preservados(self):
        nomes = {c.name for c in m.Musicas.__cog_commands__ + m.Atividade.__cog_commands__}
        self.assertTrue(
            {"read", "scan", "wrapped", "exportar", "aleatoria", "perfil", "musicas", "tagarelas"}
            <= nomes
        )
        ctx = NS(send=AsyncMock())
        await m.Ajuda.ajuda.callback(m.Ajuda(None), ctx)
        texto = ctx.send.call_args.kwargs["embed"].description
        for reservado in ("give", "scan", "read", "exportar"):
            self.assertNotIn("mm!" + reservado, texto)
        for publico in ("mudae", "levels", "frase", "titulo", "favorita"):
            self.assertIn("mm!" + publico, texto)

    async def test_28_playlist_watch_list_e_album_fora_favorita(self):
        for consulta in (
            "https://youtube.com/watch?v=abc&list=PL123",
            "https://youtu.be/abc?list=PL123",
            "https://open.spotify.com/album/id",
            "https://youtube.com/playlist?list=PL123",
        ):
            self.assertIsNone(m.consulta_musical("m!p " + consulta))
        self.assertIsNotNone(m.consulta_musical("m!p https://youtube.com/watch?v=abc"))

    async def test_29_leitura_cruzando_meia_noite_nao_fecha_mes_incompleto(self):
        evento = msg(date="2025-12-31T23:59:00")
        self.cog.canais_legiveis = AsyncMock(return_value=([Canal(1, [evento])], []))
        corte = datetime(2025, 12, 31, 23, 59, 30, tzinfo=m.FUSO)
        with patch.object(m.discord.utils, "utcnow", return_value=corte):
            await self.cog.sincronizar()
        self.assertEqual(
            self.b.con.execute("SELECT COUNT(*) FROM periodos_fechados").fetchone()[0], 0
        )
        with patch.object(m.discord.utils, "utcnow", return_value=corte + timedelta(minutes=1)):
            await self.cog.sincronizar()
        self.assertEqual(
            self.b.con.execute("SELECT COUNT(*) FROM periodos_fechados").fetchone()[0], 2
        )

    async def test_30_reconexao_durante_sync_agenda_outra_passagem(self):
        iniciou, terminar = asyncio.Event(), asyncio.Event()
        chamadas = []

        async def sincronizar():
            chamadas.append(1)
            if len(chamadas) == 1:
                iniciou.set()
                await terminar.wait()

        self.cog.sincronizar = sincronizar
        self.cog.enviar_avisos = AsyncMock()
        self.cog.iniciar_sync()
        await iniciou.wait()
        self.cog.iniciar_sync()
        terminar.set()
        await self.cog._sync_task
        self.assertEqual(len(chamadas), 2)

    async def test_31_setup_discord_registra_todos_comandos(self):
        candidato = m.MeMiBot(
            command_prefix="mm!", help_command=None, intents=discord.Intents.default()
        )
        with patch.object(m, "ARQUIVO_BANCO", Path(self.temp.name) / "setup.db"):
            async with candidato:
                await candidato.setup_hook()
                self.assertTrue(
                    all(
                        candidato.get_command(nome)
                        for nome in ["perfil", "ranking", "scan", "give", "mudae", "titulos"]
                    )
                )
                self.assertEqual(candidato.get_command("csv"), candidato.get_command("exportar"))

    async def test_32_flags_genero_com_espacos_e_periodo(self):
        self.b.inserir([(sid(), 1, "Faixa", "Artista", "jockie")])
        self.b.salvar_cache_deezer(m.chave_deezer("Faixa Artista"), "", "Música Brasileira", 1)
        self.cog.deezer.info_seguro = AsyncMock(return_value=("", ""))
        ctx = NS(author=NS(id=10), send=AsyncMock())
        with patch.object(m, "intervalo", return_value=(0, "mês atual")):
            await m.Musicas.musicas.callback(self.cog, ctx, "genero", "musica", "brasileira", "mes")
        embed = ctx.send.call_args.kwargs["embed"]
        self.assertIn("Faixa", embed.description)
        self.assertIn("0 músicas sem gênero", embed.footer.text)

    async def test_33_favorita_avisa_capa_e_frase_limite(self):
        social = m.Atividade(NS(get_cog=lambda _: self.cog))

        class Typing:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

        ctx = NS(author=NS(id=10), send=AsyncMock(), typing=Typing)
        await m.Atividade.frase.callback(social, ctx, texto="x" * 101)
        self.assertEqual(self.b.perfil(10)["frase"], "")
        await m.Atividade.frase.callback(social, ctx, texto="x" * 100)
        self.assertEqual(len(self.b.perfil(10)["frase"]), 100)
        for capa in ("https://example.test/capa.jpg", ""):
            self.cog.deezer.info_seguro = AsyncMock(return_value=(capa, ""))
            await m.Atividade.favorita.callback(social, ctx, texto="Música - Banda")
            self.assertEqual(self.b.perfil(10)["capa"], capa)
            self.assertIn(
                "Capa encontrada" if capa else "Não encontrei", ctx.send.call_args.args[0]
            )

    async def test_34_descobre_voz_threads_arquivadas_e_forum(self):
        perm = NS(view_channel=True, read_message_history=True, manage_threads=False)
        canais = []
        for cid, classe in (
            (1, discord.TextChannel),
            (2, discord.VoiceChannel),
            (3, discord.ForumChannel),
            (4, discord.CategoryChannel),
        ):
            c = MagicMock(spec=classe)
            c.id = cid
            c.permissions_for.return_value = perm
            canais.append(c)
        threads = {cid: MagicMock(spec=discord.Thread) for cid in range(5, 9)}
        for cid, t in threads.items():
            t.id = cid
            t.permissions_for.return_value = perm

        async def arquivos_texto(**kwargs):
            yield threads[6 if kwargs.get("private") else 5]

        async def arquivos_forum(**kwargs):
            yield threads[7]

        canais[0].archived_threads = MagicMock(side_effect=arquivos_texto)
        canais[2].archived_threads = MagicMock(side_effect=arquivos_forum)
        guild = NS(
            channels=canais,
            threads=[],
            active_threads=AsyncMock(return_value=[threads[8]]),
            me=NS(id=99),
        )
        self.cog.guild = lambda: guild
        lidos, erros = await self.cog.canais_legiveis()
        self.assertEqual(erros, [])
        self.assertEqual({c.id for c in lidos}, {1, 2, 5, 6, 7, 8})
        self.assertIn(
            unittest.mock.call(limit=None, private=True, joined=True),
            canais[0].archived_threads.call_args_list,
        )

    async def test_35_evento_ao_vivo_espera_recuperacao(self):
        self.cog.guild_id = 1
        self.b.definir_estado("importacao_concluida", "1")
        self.cog.sincronizados = {1}
        pronto = asyncio.Event()
        self.cog._sync_task = asyncio.create_task(pronto.wait())
        self.cog.enviar_avisos = AsyncMock()
        listener = asyncio.create_task(self.cog.on_message(msg()))
        await asyncio.sleep(0)
        self.assertEqual(self.b.total_usuario(10, "mensagens"), 0)
        self.cog.recuperando = False
        pronto.set()
        await listener
        self.assertEqual(self.b.total_usuario(10, "mensagens"), 1)


if __name__ == "__main__":
    unittest.main()
