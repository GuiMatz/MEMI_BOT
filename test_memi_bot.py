"""Testes locais: python -m unittest -v test_memi_bot.py (não conecta ao Discord)."""

import asyncio
import gzip
import json
import logging
import socket
import sqlite3
import time
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import discord
import estilo
import imagens
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
        self.assertEqual(self.b.xp_usuario(10), 1)
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

    def test_03_xp_do_banco_combina_as_tres_fontes(self):
        self.lote(10, 100)
        self.lote(10, 4, "pedidos")
        self.lote(10, 9, "mudae")
        self.assertEqual(self.b.xp_usuario(10), 100 + 4 * 25 + 4)
        self.assertEqual(self.b.xp_usuario(999), 0)
        self.assertEqual(m.xp_de({"mensagens": 100, "pedidos": 4, "mudae": 9}), 204)

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
        self.assertEqual(self.b.ranking_xp(eh_bot=True), [(5, 1000)])
        self.assertEqual(self.b.ranking_xp(), [(10, 1)])
        self.assertEqual(self.b.ranking_atividade(False), [(10, 1)])
        self.assertEqual(self.b.ranking_atividade(True), [(5, 1000)])
        self.assertEqual(self.b.itens(5, "titulo"), [])
        with self.assertRaises(ValueError):
            self.b.conceder_manual(5, "BONGAS", True)

    def test_06_titulos_mensagens_cada_limite(self):
        for i, (limite, item) in enumerate(m.tags.METAS_MENSAGENS):
            uid = 100 + i
            self.lote(uid, limite - 1, date=f"2026-09-{i+1:02}T00:00:00")
            self.b.reconciliar()
            self.assertFalse(self.possui(uid, item))
            self.b.receber(msg(uid, seq=i), recompensar=True)
            self.assertTrue(self.possui(uid, item))
            self.assertTrue(all(self.possui(uid, k) for _, k in m.tags.METAS_MENSAGENS[: i + 1]))

    def test_07_titulos_musicas_cada_limite(self):
        for i, (limite, item) in enumerate(m.tags.METAS_MUSICAS):
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
            self.b.conceder_manual(10, "Não existe")
        with self.assertRaises(ValueError):
            self.b.conceder_manual(10, "DJ da Call")  # automática: não se dá à mão
        self.assertTrue(self.b.conceder_manual(10, "BONGAS"))  # nome antigo ainda vale
        self.assertFalse(self.b.conceder_manual(10, "bongador"))
        self.assertEqual(dict(self.b.itens(10, "insignia")), {"bongas": 1})
        self.assertEqual(dict(self.b.itens(10, "titulo")), {"bongas": 1})
        self.assertEqual(self.b.con.execute("SELECT COUNT(*) FROM avisos").fetchone()[0], 0)

    def test_13_titulo_possuido_sem_acento(self):
        self.assertFalse(self.b.selecionar_titulo(10, "Brécagames"))
        self.b.conceder_manual(10, "bréca games")
        self.assertTrue(self.b.selecionar_titulo(10, "BRECAGAMES"))
        self.assertEqual(self.b.perfil(10)["titulo"], "breca")

    def test_13b_tags_da_pessoa_na_ordem_das_categorias_com_ultimo_periodo(self):
        self.b.conceder_manual(10, "Papagaio da Call")
        with self.b.con:
            self.b._conceder(10, "dj_mes", "2026-07")
            self.b._conceder(10, "dj_mes", "2026-08")
            self.b._conceder(10, "resenha_torta")
        self.assertEqual(
            self.b.tags_usuario(10),
            [("dj_mes", 2, "2026-08"), ("papagaio", 1, "manual"), ("resenha_torta", 1, "")],
        )

    def test_13c_patentes_sao_concedidas_ao_alcancar_o_nivel(self):
        self.lote(10, m.progressao.xp_minimo(20))
        self.b.reconciliar(avisar=True)
        tem = dict(self.b.itens(10))
        self.assertIn("patente_figurante", tem)
        self.assertIn("patente_ouvinte_call", tem)
        self.assertIn("patente_resenheiro", tem)
        self.assertNotIn("patente_veterano_call", tem)
        # patente não gera o aviso genérico de tag: o aviso de nível já a anuncia
        self.assertEqual(
            self.b.con.execute(
                "SELECT COUNT(*) FROM avisos WHERE item LIKE 'patente_%'"
            ).fetchone()[0],
            0,
        )

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
        with legado.con:
            legado.con.execute(
                "INSERT INTO pedidos (message_id, channel_id, usuario_id, conteudo) VALUES (?,?,?,?)",
                (sid(seq=1), 1, 10, "m!p faixa"),
            )
            legado.con.execute("INSERT INTO atividade VALUES (10, 0, 9000)")
            legado.con.execute("INSERT INTO atividade_scan VALUES (1, 123)")
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
            self.assertTrue(list(outro.parent.glob("backups/migracao_*/estilo.py")))
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

    def test_36_wal_com_synchronous_normal_evita_fsync_por_mensagem(self):
        self.assertEqual(self.b.con.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        self.assertEqual(self.b.con.execute("PRAGMA synchronous").fetchone()[0], 1)  # NORMAL

    def test_37_lider_respeita_desempate_e_membros_elegiveis(self):
        for item in [msg(20, seq=1), msg(10, seq=2), msg(10, seq=4), msg(20, seq=3)]:
            self.b.receber(item)
        self.assertEqual(
            self.b.lider("mensagens", eh_bot=False), 20
        )  # empate 2x2: última mais antiga
        self.assertEqual(self.b.lider("mensagens", eh_bot=False, elegiveis={10}), 10)
        self.assertIsNone(self.b.lider("mensagens", eh_bot=False, elegiveis=set()))
        self.assertIsNone(self.b.lider("pedidos", eh_bot=False))
        self.assertEqual(
            self.b.lider("mensagens", eh_bot=False),
            self.b.ranking_contagens("mensagens", eh_bot=False)[0][0],
        )

    def test_38_backup_diario_isolado_roda_em_outra_thread(self):
        self.b.receber(msg())
        resultado, erros = [], []

        def rodar():
            try:
                agora = datetime(2026, 9, 1, tzinfo=m.FUSO)
                resultado.append(self.b.backup_diario(agora, isolado=True))
            except Exception as erro:  # noqa: BLE001
                erros.append(erro)

        tarefa = threading.Thread(target=rodar)
        tarefa.start()
        tarefa.join()
        self.assertEqual(erros, [])
        restaurado = Path(self.temp.name) / "restaurado.db"
        restaurado.write_bytes(gzip.decompress(resultado[0].read_bytes()))
        con = sqlite3.connect(restaurado)
        try:
            self.assertEqual(con.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(con.execute("SELECT COUNT(*) FROM mensagens").fetchone()[0], 1)
        finally:
            con.close()

    def _preparar_dezembro(self):
        self.b.receber(msg(10, 1, date="2025-12-05T10:00:00", content="m!play a"))
        self.b.receber(msg(10, 2, date="2025-12-06T10:00:00"))
        self.b.receber(msg(10, 3, date="2025-12-07T10:00:00"))
        self.b.receber(msg(20, 4, date="2025-12-08T10:00:00", content="m!play b"))
        self.b.receber(msg(20, 5, date="2025-12-09T10:00:00", content="m!play b"))
        evento = msg(50, 6, bot=True, date="2025-12-10T10:00:00")
        evento.embeds = [
            NS(title="Started playing", description="[Faixa by Artista](https://x.test)")
        ]
        self.b.receber(evento)

    def test_40_rankings_de_musica_e_artista_respeitam_o_limite_superior(self):
        self.b.inserir(
            [
                (sid("2026-08-10T12:00:00"), 1, "A", "X", "j"),
                (sid("2026-09-10T12:00:00"), 1, "B", "X", "j"),
            ]
        )
        desde, ate = m.limites_periodo("mes", "2026-08")
        self.assertEqual([t for t, _, _ in self.b.ranking_musicas(desde, ate)], ["A"])
        self.assertEqual(tuple(self.b.ranking_artistas(desde, ate)[0][1:]), (1, 1))
        self.assertEqual(len(self.b.ranking_musicas(desde)), 2)

    def test_41_fechar_periodo_com_avisos_guarda_resumo(self):
        self._preparar_dezembro()
        self.b.fechar_periodos(datetime(2026, 1, 1, tzinfo=m.FUSO), avisar=True)
        resumos = {(t, p): d for t, p, d in self.b.resumos_pendentes()}
        self.assertEqual(set(resumos), {("mes", "2025-12"), ("ano", "2025")})
        mes = resumos[("mes", "2025-12")]
        self.assertEqual(mes["djs"][0], [20, 2])
        self.assertEqual(mes["tagarelas"][0], [10, 3])
        self.assertEqual((mes["mensagens"], mes["pedidos"]), (5, 3))
        self.assertEqual(mes["musicas"], [["Faixa", "Artista", 1]])
        self.assertEqual(mes["artistas"], [["Artista", 1, 1]])

    def test_42_fechamento_silencioso_ou_repetido_nao_gera_resumo(self):
        self._preparar_dezembro()
        self.b.fechar_periodos(datetime(2026, 1, 1, tzinfo=m.FUSO), avisar=False)
        self.assertEqual(self.b.resumos_pendentes(), [])
        # Período já fechado em silêncio não ganha resumo retroativo.
        self.b.fechar_periodos(datetime(2026, 2, 1, tzinfo=m.FUSO), avisar=True)
        self.assertEqual(self.b.resumos_pendentes(), [])

    def test_43_resumo_nao_duplica_e_respeita_quem_saiu(self):
        self._preparar_dezembro()
        self.b.fechar_periodos(datetime(2026, 1, 1, tzinfo=m.FUSO), avisar=True, elegiveis={10})
        self.b.fechar_periodos(datetime(2026, 3, 1, tzinfo=m.FUSO), avisar=True)
        pendentes = self.b.resumos_pendentes()
        self.assertEqual(len(pendentes), 2)
        mes = dict(((t, p), d) for t, p, d in pendentes)[("mes", "2025-12")]
        self.assertEqual(mes["djs"], [[10, 1]])
        self.assertEqual(mes["tagarelas"], [[10, 3]])

    def test_44_periodo_sem_atividade_humana_nao_gera_resumo(self):
        with self.b.con:
            self.b._guardar_resumo("mes", "2025-11", None)
        self.assertEqual(self.b.resumos_pendentes(), [])

    def test_45_ordem_e_estado_dos_resumos_pendentes(self):
        with self.b.con:
            for tipo, periodo in (
                ("ano", "2025"),
                ("mes", "2025-12"),
                ("mes", "2025-11"),
                ("mes", "2026-01"),
            ):
                self.b.con.execute(
                    "INSERT INTO resumos(tipo, periodo, dados) VALUES (?, ?, '{}')", (tipo, periodo)
                )
        ordem = [(t, p) for t, p, _ in self.b.resumos_pendentes()]
        self.assertEqual(
            ordem, [("mes", "2025-11"), ("mes", "2025-12"), ("ano", "2025"), ("mes", "2026-01")]
        )
        self.b.marcar_resumo("mes", "2025-11", "enviado")
        self.b.marcar_resumo("mes", "2025-12", "reservado")
        self.assertEqual(
            [(t, p) for t, p, _ in self.b.resumos_pendentes()],
            [("ano", "2025"), ("mes", "2026-01")],
        )

    def test_46_hall_lista_vencedores_do_mais_recente_ao_mais_antigo(self):
        self.b.receber(msg(10, 1, date="2025-11-05T10:00:00", content="m!play a"))
        self.b.receber(msg(20, 2, date="2025-11-06T10:00:00"))
        self.b.receber(msg(20, 3, date="2025-11-07T10:00:00"))
        self._preparar_dezembro()
        self.b.fechar_periodos(datetime(2026, 3, 1, tzinfo=m.FUSO))
        with self.b.con:  # posse sem período não entra no hall
            self.b._conceder(30, "dj_mes", "manual")
        meses = self.b.hall("mes")
        self.assertEqual([p for p, _, _ in meses], ["2025-12", "2025-11"])
        self.assertEqual(meses[0][1:], (20, 10))  # DJ 20, Tagarela 10
        self.assertEqual(meses[1][1:], (10, 20))
        self.assertEqual([p for p, _, _ in self.b.hall("ano")], ["2025"])

    def test_46b_mes_so_com_bots_nao_gera_resumo(self):
        evento = msg(50, 1, bot=True, date="2025-11-05T10:00:00")
        evento.embeds = [
            NS(title="Started playing", description="[Faixa by Artista](https://x.test)")
        ]
        self.b.receber(evento)
        self.b.fechar_periodos(datetime(2026, 1, 1, tzinfo=m.FUSO), avisar=True)
        self.assertEqual(self.b.resumos_pendentes(), [])

    def avisos_de_nivel(self):
        return [(uid, marco) for _, uid, marco in self.b.avisos_nivel_pendentes()]

    def marco_guardado(self, uid):
        linha = self.b.con.execute(
            "SELECT marco FROM niveis_marco WHERE usuario_id=?", (uid,)
        ).fetchone()
        return linha[0] if linha else None

    def test_47_primeira_avaliacao_so_grava_o_nivel(self):
        self.lote(10, 2000)  # nível 11 antes de existir controle de nível
        self.b.receber(msg(10, 1), recompensar=True, avisar=True)
        self.assertEqual(self.avisos_de_nivel(), [])
        self.assertEqual(self.marco_guardado(10), 11)

    def test_48_subir_de_nivel_avisa_uma_vez(self):
        self.lote(10, 2000)
        self.b.receber(msg(10, 1), recompensar=True, avisar=True)  # grava nível 11 em silêncio
        self.lote(10, 420, date="2026-08-01T00:00:00")  # 2.421 XP: nível 12
        self.b.receber(msg(10, 2), recompensar=True, avisar=True)
        self.b.receber(msg(10, 3), recompensar=True, avisar=True)  # mesmo nível: nada novo
        self.assertEqual(self.avisos_de_nivel(), [(10, 12)])

    def test_49_varios_niveis_de_uma_vez_geram_so_o_mais_alto(self):
        self.lote(10, 2000)
        self.b.receber(msg(10, 1), recompensar=True, avisar=True)  # nível 11 gravado
        self.lote(10, 2000, date="2026-08-01T00:00:00")  # 4.001 XP: nível 15
        self.b.receber(msg(10, 2), recompensar=True, avisar=True)
        self.assertEqual(self.avisos_de_nivel(), [(10, 15)])

    def test_49b_salto_que_cruza_patente_anuncia_a_troca_e_o_nivel_final(self):
        self.lote(10, 1000)  # nível 8
        self.b.receber(msg(10, 1), recompensar=True, avisar=True)
        self.lote(10, 7000, date="2026-08-01T00:00:00")  # 8.001 XP: nível 21
        self.b.receber(msg(10, 2), recompensar=True, avisar=True)
        self.assertEqual(self.avisos_de_nivel(), [(10, 10), (10, 20), (10, 21)])

    def test_50_sem_avisos_ou_bot_so_atualiza_ou_ignora(self):
        self.lote(10, 2000)
        self.b.receber(msg(10, 1), recompensar=True, avisar=True)
        self.lote(10, 2000, date="2026-08-01T00:00:00")
        self.b.receber(msg(10, 2), recompensar=True, avisar=False)  # importação silenciosa
        self.assertEqual(self.avisos_de_nivel(), [])
        self.assertEqual(self.marco_guardado(10), 15)
        self.lote(5, 1000, bot=True, date="2026-07-01T00:00:00")
        self.b.receber(msg(5, 3, bot=True), recompensar=True, avisar=True)
        self.assertEqual(self.avisos_de_nivel(), [])
        self.assertIsNone(self.marco_guardado(5))

    def test_51b_recuperacao_offline_repassa_mensagens_e_avisa_so_o_nivel_mais_alto(self):
        self.lote(10, 1999)  # nível 10
        self.b.receber(msg(10, 0), recompensar=True, avisar=True)
        for i in range(1, 450):  # cruza os níveis 11 e 12 uma mensagem por vez
            self.b.receber(msg(10, i), recompensar=True, avisar=True)
        self.assertEqual(self.marco_guardado(10), 12)
        self.assertEqual(self.avisos_de_nivel(), [(10, 12)])

    def test_51c_aviso_ja_reservado_nao_e_apagado_ao_subir_de_nivel(self):
        self.lote(10, 1999)
        self.b.receber(msg(10, 0), recompensar=True, avisar=True)  # 2.000 XP: nível 11
        for i in range(1, 30):
            self.b.receber(msg(10, i), recompensar=True, avisar=True)
        self.assertEqual(self.avisos_de_nivel(), [])
        self.lote(10, 400, date="2026-08-01T00:00:00")
        self.b.receber(msg(10, 500), recompensar=True, avisar=True)  # nível 12
        ((aviso_id, _, nivel),) = self.b.avisos_nivel_pendentes()
        self.assertEqual(nivel, 12)
        self.b.marcar_aviso_nivel(aviso_id, "enviado")
        self.lote(10, 500, date="2026-07-01T00:00:00")
        self.b.receber(msg(10, 501), recompensar=True, avisar=True)  # nível 13
        self.assertEqual(self.avisos_de_nivel(), [(10, 13)])
        self.assertEqual(self.b.con.execute("SELECT COUNT(*) FROM avisos_nivel").fetchone()[0], 2)

    def test_51_estado_do_aviso_de_nivel(self):
        self.lote(10, 2000)
        self.b.receber(msg(10, 1), recompensar=True, avisar=True)
        self.lote(10, 2000, date="2026-08-01T00:00:00")
        self.b.receber(msg(10, 2), recompensar=True, avisar=True)
        ((aviso_id, uid, nivel),) = self.b.avisos_nivel_pendentes()
        self.assertEqual((uid, nivel), (10, 15))
        self.b.marcar_aviso_nivel(aviso_id, "reservado")
        self.assertEqual(self.b.avisos_nivel_pendentes(), [])

    def test_51d_musicas_e_roletadas_tambem_sobem_de_nivel(self):
        self.lote(10, 2000)
        self.b.receber(msg(10, 1), recompensar=True, avisar=True)  # nível 11
        self.lote(10, 17, "pedidos", date="2026-08-01T00:00:00")  # +425 XP
        self.b.receber(msg(10, 2), recompensar=True, avisar=True)
        self.assertEqual(self.avisos_de_nivel(), [(10, 12)])

    def _banco_da_versao_anterior(self):
        """Banco como a versão 2 deixava: sem as tabelas/coluna novas e com user_version=2."""
        caminho = Path(self.temp.name) / "anterior.db"
        antigo = m.Banco(caminho)
        antigo.receber(msg(10, 1, content="m!play a"))
        antigo.salvar_perfil(10, frase="minha frase")
        antigo.con.executescript(
            "DROP TABLE resumos; DROP TABLE niveis_marco; DROP TABLE avisos_nivel;"
            "ALTER TABLE perfil_usuario DROP COLUMN cor; PRAGMA user_version=2;"
        )
        antigo.con.close()
        return caminho

    def test_52_atualizar_do_banco_da_versao_2_faz_backup_e_migra_sem_perder_dados(self):
        caminho = self._banco_da_versao_anterior()
        novo = m.Banco(caminho)
        try:
            self.assertEqual(novo.con.execute("PRAGMA user_version").fetchone()[0], 4)
            tabelas = {r[0] for r in novo.con.execute("SELECT name FROM sqlite_master")}
            self.assertTrue({"resumos", "niveis_marco", "avisos_nivel"} <= tabelas)
            self.assertEqual(novo.total_usuario(10, "mensagens"), 1)
            self.assertEqual(novo.perfil(10)["frase"], "minha frase")
            self.assertIsNone(novo.perfil(10)["cor"])
        finally:
            novo.con.close()
        (pasta,) = caminho.parent.glob("backups/migracao_*")
        restaurado = Path(self.temp.name) / "restaurado_antes.db"
        restaurado.write_bytes(gzip.decompress((pasta / "memi.db.gz").read_bytes()))
        con = sqlite3.connect(restaurado)
        try:
            tabelas = {r[0] for r in con.execute("SELECT name FROM sqlite_master")}
            self.assertNotIn("resumos", tabelas)  # o backup é o estado de antes da migração
            self.assertEqual(con.execute("SELECT COUNT(*) FROM mensagens").fetchone()[0], 1)
        finally:
            con.close()
        self.assertTrue((pasta / "memi_bot.py").exists() and (pasta / "estilo.py").exists())

    def test_53_banco_ja_atualizado_nao_gera_novo_backup(self):
        caminho = self._banco_da_versao_anterior()
        m.Banco(caminho).con.close()
        m.Banco(caminho).con.close()
        m.Banco(caminho).con.close()
        self.assertEqual(len(list(caminho.parent.glob("backups/migracao_*"))), 1)

    def test_54_banco_novo_nasce_na_versao_atual_sem_backup(self):
        caminho = Path(self.temp.name) / "novo.db"
        banco = m.Banco(caminho)
        try:
            self.assertEqual(banco.con.execute("PRAGMA user_version").fetchone()[0], 4)
        finally:
            banco.con.close()
        self.assertFalse(list(caminho.parent.glob("backups/migracao_*")))

    def test_55_migracao_da_versao_3_zera_niveis_unifica_tags_e_nao_avisa_nada(self):
        caminho = Path(self.temp.name) / "v3.db"
        antigo = m.Banco(caminho)
        antigo.receber(msg(10, 1))
        with antigo.con:
            antigo.con.execute("INSERT INTO niveis_marco VALUES (10, 27)")  # escala antiga
            antigo.con.execute("INSERT INTO avisos_nivel(usuario_id, marco) VALUES (10, 27)")
            antigo.con.execute(
                "INSERT INTO avisos_nivel(usuario_id, marco, estado) VALUES (10, 26, 'enviado')"
            )
            antigo.con.execute("INSERT INTO posses VALUES (10,'dj_piolho','',1,0,1)")
            antigo.con.execute("INSERT INTO posses VALUES (10,'bongas','manual',0,1,2)")
            antigo.con.execute("PRAGMA user_version=3")
        antigo.salvar_perfil(10, titulo="dj_piolho")
        antigo.con.close()
        novo = m.Banco(caminho)
        try:
            self.assertEqual(novo.con.execute("PRAGMA user_version").fetchone()[0], 4)
            self.assertEqual(novo.con.execute("SELECT COUNT(*) FROM niveis_marco").fetchone()[0], 0)
            self.assertEqual(novo.con.execute("SELECT COUNT(*) FROM avisos_nivel").fetchone()[0], 0)
            self.assertEqual(dict(novo.itens(10, "insignia")), {"dj_piolho": 1, "bongas": 1})
            self.assertEqual(dict(novo.itens(10, "titulo")), {"dj_piolho": 1, "bongas": 1})
            self.assertEqual(novo.perfil(10)["titulo"], "dj_piolho")
            novo.receber(msg(10, 2), recompensar=True, avisar=True)
            self.assertEqual(novo.avisos_nivel_pendentes(), [])  # nada retroativo
            self.assertEqual(
                novo.con.execute("SELECT marco FROM niveis_marco WHERE usuario_id=10").fetchone(),
                (1,),
            )
        finally:
            novo.con.close()
        self.assertEqual(len(list(caminho.parent.glob("backups/migracao_*"))), 1)


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


class CogBase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        fake = NS(get_guild=lambda _: None, intents=NS(members=False), get_channel=lambda _: None)
        with patch.object(m, "ARQUIVO_BANCO", Path(self.temp.name) / "memi.db"):
            self.cog = m.Musicas(fake)
        self.b = self.cog.banco

    async def asyncTearDown(self):
        await self.cog.cog_unload()
        self.temp.cleanup()


class IntegracaoTests(CogBase):
    pass

    async def test_21_permissoes_id_memi_read_scan_give(self):
        for command in (m.Musicas.read, m.Musicas.exportar, m.Atividade.scan, m.Atividade.give):
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
        self.assertIn("0/20 XP", str(paginas[0].to_dict()))
        self.assertNotIn("sem dados", str([x.to_dict() for x in paginas]))
        # Recurso ainda não implementado não aparece como campo vazio.
        self.assertNotIn("One Hit", str([x.to_dict() for x in paginas]))
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
        self.assertIn("Parabéns", args[0])
        self.assertIn("Nova tag: 💬 Resenhex do Clubex", args[0])
        self.assertIn("Top 1 Mensagens", args[0])  # como ganhou
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
        for publico in ("mudae", "levels", "frase", "titulo", "favorita", "ec"):
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
                self.assertEqual(candidato.get_command("ajuda"), candidato.get_command("help"))
                self.assertIsNotNone(candidato.get_command("ec"))

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
            self.assertIn("Capa encontrada" if capa else "Não encontrei", texto_enviado(ctx))

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


class CanalNegado(Canal):
    """Canal cujo histórico o Discord recusa (403)."""

    async def history(self, **kwargs):
        raise discord.Forbidden(NS(status=403, reason="Forbidden"), "sem acesso")
        yield  # pragma: no cover - torna a função um gerador assíncrono


class CanalComFalha(Canal):
    """Canal com erro passageiro do Discord (500)."""

    async def history(self, **kwargs):
        raise discord.HTTPException(NS(status=500, reason="Erro"), "falha passageira")
        yield  # pragma: no cover


class LeituraTests(CogBase):
    """Correções de robustez da leitura e da manutenção."""

    async def test_39_canal_sem_historico_e_ignorado_sem_bloquear(self):
        perm_sem = NS(view_channel=True, read_message_history=False, manage_threads=False)
        perm_ok = NS(view_channel=True, read_message_history=True, manage_threads=False)
        sem, ok = MagicMock(spec=discord.TextChannel), MagicMock(spec=discord.VoiceChannel)
        sem.id, ok.id = 1, 2
        sem.permissions_for.return_value, ok.permissions_for.return_value = perm_sem, perm_ok
        guild = NS(
            channels=[sem, ok],
            threads=[],
            active_threads=AsyncMock(return_value=[]),
            me=NS(id=99),
        )
        self.cog.guild = lambda: guild
        lidos, erros = await self.cog.canais_legiveis()
        self.assertEqual(erros, [])
        self.assertEqual({c.id for c in lidos}, {2})
        self.assertEqual(self.cog.canais_ignorados, {1})

    async def test_40_historico_recusado_403_nao_bloqueia_importacao(self):
        ok, negado = Canal(1, [msg(10, channel=1)]), CanalNegado(2, [])
        self.cog.canais_legiveis = AsyncMock(return_value=([ok, negado], []))
        status = NS(edit=AsyncMock())
        total, erros = await self.cog.sincronizar(status=status)
        self.assertEqual((total, erros), (1, []))
        self.assertEqual(self.b.estado("importacao_concluida"), "1")
        self.assertIn(2, self.cog.canais_ignorados)
        self.assertIn("ignorado", status.edit.call_args.kwargs["content"])

    async def test_41_erro_passageiro_no_historico_continua_bloqueando(self):
        ok, falha = Canal(1, [msg(10, channel=1)]), CanalComFalha(2, [])
        self.cog.canais_legiveis = AsyncMock(return_value=([ok, falha], []))
        total, erros = await self.cog.sincronizar()
        self.assertEqual(len(erros), 1)
        self.assertNotEqual(self.b.estado("importacao_concluida"), "1")
        self.assertNotIn(2, self.cog.canais_ignorados)

    async def _preparar_manutencao(self):
        self.b.definir_estado("importacao_concluida", "1")
        self.cog.recuperando = False
        self.cog.enviar_avisos = AsyncMock()

    async def test_42_manutencao_nao_repete_sincronizacao_com_erro(self):
        await self._preparar_manutencao()
        self.cog.iniciar_sync = MagicMock()
        self.cog.sincronizar = AsyncMock(return_value=(0, ["falha"]))
        relogio = [1000.0]
        self.cog._relogio = lambda: relogio[0]
        await self.cog.ciclo_manutencao()
        await self.cog.ciclo_manutencao()
        self.assertEqual(self.cog.sincronizar.await_count, 1)
        relogio[0] += m.SYNC_REPETIR_APOS_ERRO + 1
        await self.cog.ciclo_manutencao()
        self.assertEqual(self.cog.sincronizar.await_count, 2)

    async def test_43_sync_periodico_pula_threads_arquivadas(self):
        await self._preparar_manutencao()
        self.cog._ultimo_fechamento = datetime.now(m.FUSO).strftime("%Y-%m")
        self.cog._relogio = lambda: 100_000.0
        self.cog._ultimo_sync = 0.0
        self.cog.canais_legiveis = AsyncMock(return_value=([], []))
        await self.cog.ciclo_manutencao()
        await self.cog._sync_task
        self.cog.canais_legiveis.assert_awaited_once_with(arquivadas=False)

    async def test_44_canais_legiveis_sem_arquivadas_nao_consulta_a_api(self):
        perm = NS(view_channel=True, read_message_history=True, manage_threads=False)
        canal = MagicMock(spec=discord.TextChannel)
        canal.id = 1
        canal.permissions_for.return_value = perm
        canal.archived_threads = MagicMock()
        guild = NS(
            channels=[canal], threads=[], active_threads=AsyncMock(return_value=[]), me=NS(id=99)
        )
        self.cog.guild = lambda: guild
        lidos, erros = await self.cog.canais_legiveis(arquivadas=False)
        self.assertEqual(({c.id for c in lidos}, erros), ({1}, []))
        canal.archived_threads.assert_not_called()

    async def test_45_read_e_scan_compartilham_a_mesma_leitura(self):
        ctx = NS(send=AsyncMock(return_value=NS(edit=AsyncMock())))
        self.cog.sincronizar = AsyncMock(return_value=(0, []))
        social = m.Atividade(NS(get_cog=lambda _: self.cog))
        await m.Atividade.scan.callback(social, ctx)
        kw = self.cog.sincronizar.await_args.kwargs
        self.assertEqual((kw["completo"], kw["silencioso"], kw["canais"]), (True, True, None))
        self.cog.sincronizar.reset_mock()
        await self.cog.lock.acquire()
        try:
            await self.cog.iniciar_leitura(ctx, "abertura", completo=True)
        finally:
            self.cog.lock.release()
        self.cog.sincronizar.assert_not_awaited()
        self.assertIn("Já tem uma leitura", texto_enviado(ctx))

    async def test_46_ranking_view_nao_exige_id_do_autor(self):
        view = m.RankingView("Título", ["a", "b"], "rodapé")
        self.assertIn("a", view.montar_embed().description)

    async def test_47_cog_check_distingue_outro_servidor_de_identificacao_pendente(self):
        self.cog.guild_id = 0
        with self.assertRaises(m.commands.CheckFailure) as pendente:
            await self.cog.cog_check(NS(guild=NS(id=2)))
        self.assertIn("identificando", str(pendente.exception))
        self.cog.guild_id = 1
        with self.assertRaises(m.commands.CheckFailure) as outro:
            await self.cog.cog_check(NS(guild=NS(id=2)))
        self.assertIn("outro servidor", str(outro.exception))
        self.assertTrue(await self.cog.cog_check(NS(guild=NS(id=1))))


class Typing:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None


def pessoa(uid=10, nome=None, bot=False):
    return NS(
        id=uid,
        bot=bot,
        display_name=nome or f"Apelido {uid}",
        display_avatar=NS(with_size=lambda _: NS(url="https://example.test/avatar.png")),
    )


def embed_enviado(ctx):
    return ctx.send.call_args.kwargs["embed"]


def texto_enviado(ctx):
    """Texto da última resposta: o embed de feedback (descrição) ou o texto simples."""
    args, kwargs = ctx.send.call_args
    return args[0] if args else kwargs["embed"].description


class RankingsPerfilTests(CogBase):
    """Nomes em negrito, quem saiu, perfil, listas vazias e cor do perfil."""

    def social(self):
        return m.Atividade(NS(get_cog=lambda _: self.cog))

    def servidor_com(self, ids):
        membros = [pessoa(i) for i in ids]
        guild = NS(
            chunked=True,
            members=membros,
            get_member=lambda uid: next((x for x in membros if x.id == uid), None),
        )
        self.cog.bot.intents = NS(members=True)
        self.cog.guild = lambda: guild

    def registrar_dois(self, data=None):
        extra = {"date": data} if data else {}
        self.b.receber(msg(10, 1, content="m!play a", **extra))
        self.b.receber(msg(20, 2, content="m!play b", **extra))
        self.b.receber(msg(20, 3, content="m!play b", **extra))
        self.b.receber(msg(10, 4, content="$w", **extra))
        self.b.receber(msg(20, 5, content="$w", **extra))
        self.b.receber(msg(20, 6, content="$w", **extra))

    async def descricoes_dos_rankings(self):
        social, saidas = self.social(), []
        for comando, alvo, args in (
            (m.Atividade.tagarelas, social, ()),
            (m.Atividade.levels, social, ()),
            (m.Atividade.mudae, social, ()),
            (m.Musicas.musicas, self.cog, ("ios",)),
        ):
            ctx = NS(send=AsyncMock(), author=pessoa(10))
            await comando.callback(alvo, ctx, *args)
            saidas.append(embed_enviado(ctx).description)
        return saidas

    async def test_52_rankings_mostram_so_o_nome_em_negrito_sem_mencao(self):
        self.registrar_dois()
        for descricao in await self.descricoes_dos_rankings():
            self.assertIn("**Apelido 10**", descricao)
            self.assertNotIn("<@", descricao)

    async def test_53_nome_de_pessoa_usa_apelido_atual_e_escapa_markdown(self):
        evento = msg(30, 3)
        evento.author.display_name = "a*b_c"
        self.b.receber(evento)
        self.assertEqual(self.cog.nome_pessoa(30), "a\\*b\\_c")
        self.cog.guild = lambda: NS(
            get_member=lambda uid: pessoa(30, "Novo Apelido") if uid == 30 else None
        )
        self.assertEqual(self.cog.nome_pessoa(30), "Novo Apelido")
        self.assertEqual(self.cog.nome_pessoa(99), "99")  # desconhecido: só o ID

    async def test_54_quem_saiu_nao_aparece_em_nenhum_ranking(self):
        self.registrar_dois()
        self.servidor_com([10])  # 20 saiu do servidor
        for descricao in await self.descricoes_dos_rankings():
            self.assertIn("Apelido 10", descricao)
            self.assertNotIn("Apelido 20", descricao)
        p1 = self.cog.paginas_perfil(pessoa(10))[0]
        posicoes = {f.name: f.value for f in p1.fields}
        self.assertIn("#1** de 1", posicoes["🎧 Música"])
        self.assertIn("#1** de 1", posicoes["💬 Mensagens"])

    async def test_55_wrapped_ignora_quem_saiu_na_posicao(self):
        ontem = (datetime.now(m.FUSO) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S")
        self.registrar_dois(ontem)
        self.servidor_com([10])
        self.cog.deezer.info_seguro = AsyncMock(return_value=("", ""))
        self.cog.links.titulo_seguro = AsyncMock(return_value=None)
        ctx = NS(send=AsyncMock(), typing=Typing, author=pessoa(10))
        await m.Musicas.wrapped.callback(self.cog, ctx)
        posicao = next(f.value for f in embed_enviado(ctx).fields if "Posição" in f.name)
        self.assertIn("#1** de 1", posicao)

    def test_56_intent_de_membros_vem_ligada_por_padrao(self):
        self.assertTrue(m.intent_membros(None))
        self.assertTrue(m.intent_membros("1"))
        self.assertFalse(m.intent_membros("0"))
        self.assertEqual(m.bot.intents.members, m.MEMBERS_INTENT)

    async def test_57_frase_em_italico_sem_rotulo(self):
        self.assertIsNone(self.cog.paginas_perfil(pessoa(10))[0].description)
        self.b.salvar_perfil(10, frase="oi *mundo*")
        p1 = self.cog.paginas_perfil(pessoa(10))[0]
        self.assertEqual(p1.description, "*oi \\*mundo\\**")
        self.assertNotIn("Frase", [f.name for f in p1.fields])

    async def test_58_ajuda_sem_semana_e_com_genero_como_flag(self):
        ctx = NS(send=AsyncMock())
        await m.Ajuda.ajuda.callback(m.Ajuda(None), ctx)
        texto = embed_enviado(ctx).description
        self.assertNotIn("semana", texto)
        self.assertNotIn("mm!musicas genero", texto)
        linha = next(x for x in texto.splitlines() if x.startswith("`mm!musicas`"))
        self.assertIn("genero", linha)
        self.assertIn("mm!ec", texto)

    async def test_59_ranking_semanal_deixou_de_existir(self):
        self.assertEqual(m.intervalo("semana"), (0, ""))
        for args in (("semana",), ("genero", "rock", "semana"), ("artista", "semana")):
            ctx = NS(send=AsyncMock())
            await m.Musicas.musicas.callback(self.cog, ctx, *args)
            self.assertEqual(embed_enviado(ctx).color.value, estilo.COR_AVISO, args)
            self.assertIn("mes", texto_enviado(ctx))
            self.assertNotIn("semana|", texto_enviado(ctx))

    async def test_60_sem_tags_mostra_aviso(self):
        alvo, social = pessoa(10), self.social()
        self.assertEqual(
            self.cog.paginas_perfil(alvo)[1].description, "NÃO POSSUI TÍTULOS OU INSÍGNIAS"
        )
        ctx = NS(send=AsyncMock(), author=alvo)
        await m.Atividade.tags.callback(social, ctx, None)
        self.assertEqual(embed_enviado(ctx).description, "NÃO POSSUI TÍTULOS OU INSÍGNIAS")
        self.assertIn("mm!tags todos", embed_enviado(ctx).footer.text)

    async def test_61_tags_listam_por_categoria_com_como_ganhou(self):
        alvo, social = pessoa(10), self.social()
        self.b.conceder_manual(10, "BONGAS")
        with self.b.con:
            self.b._conceder(10, "dj_mes", "2026-07")
            self.b._conceder(10, "dj_mes", "2026-08")
        p2 = self.cog.paginas_perfil(alvo)[1].description
        self.assertEqual(
            p2,
            "**DJs e Resenhas**\n🟣 **DJ do Mês** — Top 1 Músicas do mês · ×2 · último: ago/2026"
            "\n\n**Eventos e Comunidade**\n🫏 **Bongador** — Participar do BONGAS",
        )
        ctx = NS(send=AsyncMock(), author=alvo)
        await m.Atividade.tags.callback(social, ctx, None)
        embed = embed_enviado(ctx)
        self.assertEqual(embed.title, "🏷️ Tags de Apelido 10")
        self.assertEqual(embed.description, p2)
        self.assertEqual(embed.footer.text, "MeMi BOT · 2 tags · 3 no total · mm!tags todos")

    async def test_61b_tags_todos_mostra_o_catalogo_por_categoria(self):
        alvo, social = pessoa(10), self.social()
        self.b.conceder_manual(10, "Papagaio da Call")
        for chamada in (
            lambda ctx: m.Atividade.tags.callback(social, ctx, None, modo="todos"),
            lambda ctx: m.Atividade.th.callback(social, ctx),
        ):
            ctx = NS(send=AsyncMock(), author=alvo)
            await chamada(ctx)
            view = ctx.send.call_args.kwargs["view"]
            self.assertEqual(len(view.paginas), len(m.tags.CATEGORIAS))
            eventos = next(x for x in view.paginas if "Eventos" in x.title)
            self.assertIn(
                "✅ 🦜 **Papagaio da Call** — Ser Papagaio · dada pelo dono", eventos.description
            )
            self.assertIn("▫️ 🎨 **Pintador**", eventos.description)

    async def test_61c_titulos_e_insignias_viraram_atalhos_de_tags(self):
        self.assertIn("titulos", m.Atividade.tags.aliases)
        self.assertIn("insignias", m.Atividade.tags.aliases)
        self.assertIn("i", m.Atividade.tags.aliases)
        self.assertIn("t", m.Atividade.tags.aliases)

    async def test_61d_give_aceita_o_formato_novo_e_o_antigo(self):
        social = self.social()
        alvo = pessoa(10)
        ctx = NS(
            send=AsyncMock(),
            author=NS(id=m.MEMI_ID),
            guild=NS(get_member=lambda uid: alvo if uid == 10 else None),
        )
        await m.Atividade.give.callback(social, ctx, texto="<@10> papagaio da call")
        self.assertIn("concedida", texto_enviado(ctx))
        await m.Atividade.give.callback(social, ctx, texto="titulo <@10> BONGAS")
        self.assertEqual(dict(self.b.itens(10)), {"papagaio": 1, "bongas": 1})
        await m.Atividade.give.callback(social, ctx, texto="<@10> bongador")
        self.assertIn("já possui", texto_enviado(ctx))
        await m.Atividade.give.callback(social, ctx, texto="<@10> DJ da Call")
        self.assertIn("Tags manuais", texto_enviado(ctx))
        await m.Atividade.give.callback(social, ctx, texto="papagaio")
        self.assertIn("mm!give @pessoa TAG", texto_enviado(ctx))

    async def test_62_ec_troca_a_cor_do_perfil(self):
        alvo, social = pessoa(10), self.social()
        cores = lambda: [x.color.value for x in self.cog.paginas_perfil(alvo)]
        self.assertEqual(cores(), [0x5865F2] * 3)
        ctx = NS(send=AsyncMock(), author=alvo)
        await m.Atividade.ec.callback(social, ctx, texto="#ff8800")
        self.assertEqual(self.b.perfil(10)["cor"], 0xFF8800)
        self.assertEqual(cores(), [0xFF8800] * 3)
        await m.Atividade.ec.callback(social, ctx, texto="corzinha")
        self.assertIn("mm!ec", texto_enviado(ctx))
        self.assertEqual(self.b.perfil(10)["cor"], 0xFF8800)  # inválida não altera
        await m.Atividade.ec.callback(social, ctx, texto="")
        self.assertIn("mm!ec", texto_enviado(ctx))
        await m.Atividade.ec.callback(social, ctx, texto="Padrão")
        self.assertIsNone(self.b.perfil(10)["cor"])
        self.assertEqual(cores(), [0x5865F2] * 3)


class CorTests(unittest.TestCase):
    def test_63_interpretar_cor_aceita_hex_e_nomes(self):
        for texto in ("#FF8800", "ff8800", "0xFF8800", "#f80", " #ff8800 "):
            self.assertEqual(m.interpretar_cor(texto), 0xFF8800, texto)
        self.assertEqual(m.interpretar_cor("Vermelho"), m.CORES["vermelho"])
        self.assertEqual(m.interpretar_cor("ROSA"), m.CORES["rosa"])
        self.assertNotEqual(m.interpretar_cor("preto"), 0)  # 0 o Discord trata como "sem cor"
        self.assertNotEqual(m.interpretar_cor("#000000"), 0)
        for texto in ("padrao", "padrão", "PADRAO", "resetar"):
            self.assertIsNone(m.interpretar_cor(texto))
        for invalida in ("", "xyz", "#12345", "#gggggg", "#1234567"):
            with self.assertRaises(ValueError, msg=invalida):
                m.interpretar_cor(invalida)

    def test_64_banco_antigo_ganha_a_coluna_de_cor_sem_perder_perfil(self):
        with tempfile.TemporaryDirectory() as pasta:
            caminho = Path(pasta) / "memi.db"
            con = sqlite3.connect(caminho)
            con.executescript("""
                CREATE TABLE perfil_usuario (
                    usuario_id INTEGER PRIMARY KEY, frase TEXT NOT NULL DEFAULT '',
                    favorita TEXT NOT NULL DEFAULT '', capa TEXT NOT NULL DEFAULT '',
                    titulo TEXT NOT NULL DEFAULT '');
                INSERT INTO perfil_usuario(usuario_id, frase) VALUES (10, 'antiga');
                PRAGMA user_version = 2;
                """)
            con.close()
            banco = m.Banco(caminho)
            try:
                self.assertEqual(banco.perfil(10)["frase"], "antiga")
                self.assertIsNone(banco.perfil(10)["cor"])
                banco.salvar_perfil(10, cor=0x123456)
                self.assertEqual(banco.perfil(10)["cor"], 0x123456)
            finally:
                banco.con.close()


class VisualTests(CogBase):
    """Aparência dos rankings (Fase 1)."""

    def social(self):
        return m.Atividade(NS(get_cog=lambda _: self.cog))

    async def test_65_ranking_mostra_sua_posicao_quando_fora_da_pagina(self):
        for uid in range(20, 31):  # 11 pessoas com 2 mensagens cada
            self.b.receber(msg(uid, seq=uid))
            self.b.receber(msg(uid, seq=uid + 100))
        self.b.receber(msg(10, seq=500))  # 10 fica em último, na 2ª página
        ctx = NS(send=AsyncMock(), author=pessoa(10))
        await m.Atividade.tagarelas.callback(self.social(), ctx)
        self.assertIn("📍 Sua posição: **#12** de 12", embed_enviado(ctx).description)
        ctx = NS(send=AsyncMock(), author=pessoa(20))  # 20 é o 1º: já está na página
        await m.Atividade.tagarelas.callback(self.social(), ctx)
        self.assertNotIn("Sua posição", embed_enviado(ctx).description)
        ctx = NS(send=AsyncMock(), author=pessoa(999))  # quem nunca falou não tem posição
        await m.Atividade.tagarelas.callback(self.social(), ctx)
        self.assertNotIn("Sua posição", embed_enviado(ctx).description)

    async def test_66_ranking_vazio_mostra_mensagem(self):
        ctx = NS(send=AsyncMock(), author=pessoa(10))
        await m.Atividade.mudae.callback(self.social(), ctx)
        self.assertIn("Ninguém no ranking ainda", embed_enviado(ctx).description)

    async def test_67_musicas_usa_subtitulo_do_periodo_e_linha_enxuta(self):
        self.b.inserir([(sid(), 1, "Faixa", "Artista", "jockie")])
        self.cog.deezer.info_seguro = AsyncMock(return_value=("", ""))
        ctx = NS(send=AsyncMock(), author=pessoa(10))
        with patch.object(m, "intervalo", return_value=(0, "mês atual")):
            await m.Musicas.musicas.callback(self.cog, ctx, "mes")
        embed = embed_enviado(ctx)
        self.assertTrue(embed.description.startswith("*mês atual*\n\n"))
        self.assertIn("🥇 **Faixa** · Artista · 1x", embed.description)
        self.assertNotIn("—", embed.title)
        self.assertIn("1 músicas", embed.footer.text)

    async def test_68_niveis_e_mudae_usam_ponto_medio_e_nome_em_negrito(self):
        self.b.receber(msg(10, 1, content="$w"))
        ctx = NS(send=AsyncMock(), author=pessoa(10))
        await m.Atividade.levels.callback(self.social(), ctx)
        self.assertIn(
            "🥇 **Apelido 10** · Nv. 1 · 🎖️ Figurante · 1 XP", embed_enviado(ctx).description
        )
        ctx = NS(send=AsyncMock(), author=pessoa(10))
        await m.Atividade.mudae.callback(self.social(), ctx)
        self.assertIn("🥇 **Apelido 10** · 1 roletadas", embed_enviado(ctx).description)

    def todos_os_campos(self, paginas):
        return [(i, f.name, f.value) for i, p in enumerate(paginas) for f in p.fields]

    async def test_69_perfil_vazio_nao_tem_campos_vazios(self):
        paginas = self.cog.paginas_perfil(pessoa(10))
        for pagina, nome, valor in self.todos_os_campos(paginas):
            self.assertTrue(valor.strip(), (pagina, nome))
            self.assertNotEqual(valor, "​", (pagina, nome))
        nomes = [n for _, n, _ in self.todos_os_campos(paginas[:1])]
        self.assertNotIn("🏅 Insígnias", nomes)
        self.assertIn("Nível 1 · Figurante", nomes)

    async def test_70_perfil_completo_tem_hierarquia_e_rodape_curto(self):
        self.b.receber(msg(10, 1, content="m!play a"))
        self.b.salvar_perfil(10, frase="Pain.", favorita="Música - Banda")
        self.b.conceder_manual(10, "BONGAS")
        p1 = self.cog.paginas_perfil(
            pessoa(10), mais="[Faixa](https://x.test) — 6x", genero="Dance", pendentes=260
        )[0]
        nomes = [f.name for f in p1.fields]
        self.assertEqual(p1.description, "*Pain.*")
        self.assertEqual(nomes[:3], ["🎧 Música", "💬 Mensagens", "Nível 2 · Figurante"])
        self.assertIn("🎵 Música favorita", nomes)
        self.assertIn("🔥 Música mais colocada", nomes)
        self.assertIn("🎼 Gênero favorito", nomes)
        self.assertIn("📊 Atividade", nomes)
        self.assertIn("🏅 Insígnias", nomes)
        self.assertEqual(
            p1.footer.text, "MeMi BOT · 1/3 · 🎼 260 sem gênero · ⏳ importando histórico"
        )
        self.b.definir_estado("importacao_concluida", "1")
        p1 = self.cog.paginas_perfil(pessoa(10))[0]
        self.assertEqual(p1.footer.text, "MeMi BOT · 1/3")

    async def test_71_barra_de_nivel_no_perfil_e_nivel_maximo(self):
        p1 = self.cog.paginas_perfil(pessoa(10))[0]
        nivel = next(f for f in p1.fields if f.name.startswith("Nível"))
        self.assertEqual(nivel.value, "▱▱▱▱▱▱▱▱▱▱ 0/20 XP")
        with self.b.con:
            self.b.con.execute(
                "INSERT OR REPLACE INTO totais(usuario_id, mensagens) VALUES (10, 250000)"
            )
        p1 = self.cog.paginas_perfil(pessoa(10))[0]
        nivel = next(f for f in p1.fields if f.name.startswith("Nível"))
        self.assertEqual(nivel.name, "Nível 100 · Demiurgo Supremo")
        self.assertEqual(nivel.value, "▰▰▰▰▰▰▰▰▰▰ nível máximo")

    async def test_72_perfil_no_pior_caso_respeita_os_limites_do_discord(self):
        self.b.salvar_perfil(10, frase="f" * 100, favorita="x" * 1500, capa="")
        alvo = pessoa(10, "N" * 60)
        paginas = self.cog.paginas_perfil(alvo, mais="y" * 1500, genero="z" * 1500, pendentes=99999)
        for pagina in paginas:
            self.assertLessEqual(len(pagina), 6000)
            self.assertLessEqual(len(pagina.fields), 25)
            for f in pagina.fields:
                self.assertLessEqual(len(f.value), 1024)
                self.assertLessEqual(len(f.name), 256)
            self.assertLessEqual(len(pagina.footer.text), 2048)

    async def test_73_ajuda_agrupada_em_categorias(self):
        ctx = NS(send=AsyncMock())
        await m.Ajuda.ajuda.callback(m.Ajuda(None), ctx)
        texto = embed_enviado(ctx).description
        secoes = ("🏆 Rankings", "🎵 Música", "👤 Perfil e conquistas")
        posicoes = [texto.index(f"**{s}**") for s in secoes]
        self.assertEqual(posicoes, sorted(posicoes))
        for comando in (
            "mm!tagarelas",
            "mm!musicas",
            "mm!perfil [@pessoa]",
            "mm!ec COR",
            "mm!wrapped",
        ):
            self.assertIn(f"`{comando}`", texto)
        for oculto in ("mm!give", "mm!scan", "mm!read", "mm!exportar"):
            self.assertNotIn(oculto, texto)
        self.assertLessEqual(len(embed_enviado(ctx)), 6000)

    async def test_76_frase_com_varias_linhas_continua_em_italico(self):
        self.b.salvar_perfil(10, frase="linha 1\nlinha   2\n\nlinha 3")
        p1 = self.cog.paginas_perfil(pessoa(10))[0]
        self.assertEqual(p1.description, "*linha 1 linha 2 linha 3*")

    async def test_77_perfil_de_bot_nao_tem_campos_vazios(self):
        self.b.receber(msg(5, 1, bot=True))
        paginas = self.cog.paginas_perfil(pessoa(5, "Jockie", bot=True))
        for pagina, nome, valor in self.todos_os_campos(paginas):
            self.assertTrue(valor.strip(), (pagina, nome))
        posicoes = {f.name: f.value for f in paginas[0].fields}
        self.assertIn("#1** de 1", posicoes["💬 Mensagens"])

    async def test_79_tags_nao_tem_medalha_e_titulo_escolhido_aparece_no_perfil(self):
        self.b.conceder_manual(10, "BONGAS")
        self.b.conceder_manual(10, "Bréca Games")
        ctx = NS(send=AsyncMock(), author=pessoa(10))
        await m.Atividade.tags.callback(self.social(), ctx, None)
        self.assertNotIn("🥇", embed_enviado(ctx).description)
        self.b.selecionar_titulo(10, "bongador")
        self.b.salvar_perfil(10, frase="oi")
        self.assertEqual(
            self.cog.paginas_perfil(pessoa(10))[0].description, "🫏 **Bongador**\n*oi*"
        )

    def _fechar_dezembro(self):
        self.b.receber(msg(10, 1, date="2025-12-05T10:00:00", content="m!play a"))
        self.b.receber(msg(10, 2, date="2025-12-06T10:00:00"))
        self.b.receber(msg(20, 3, date="2025-12-08T10:00:00", content="m!play b"))
        faixa = msg(50, 4, bot=True, date="2025-12-10T10:00:00")
        faixa.embeds = [
            NS(title="Started playing", description="[Faixa by Artista](https://x.test)")
        ]
        self.b.receber(faixa)
        self.b.fechar_periodos(datetime(2026, 1, 1, tzinfo=m.FUSO), avisar=True)

    def _pronto_para_enviar(self):
        canal = NS(send=AsyncMock())
        self.cog.bot.get_channel = lambda _: canal
        self.cog.recuperando = False
        self.cog.deezer.info_seguro = AsyncMock(return_value=("", ""))
        self.b.definir_estado("importacao_concluida", "1")
        return canal

    async def test_80_resumos_saem_em_ordem_uma_vez_e_sem_mencao(self):
        self._fechar_dezembro()
        canal = self._pronto_para_enviar()
        with patch.object(imagens, "disponivel", return_value=False):  # embed completo
            await self.cog.enviar_resumos()
            await self.cog.enviar_resumos()
        self.assertEqual(canal.send.await_count, 2)
        titulos = [c.kwargs["embed"].title for c in canal.send.call_args_list]
        self.assertEqual(titulos, ["📅 Resumo de dezembro de 2025", "🏁 Resumo de 2025"])
        for chamada in canal.send.call_args_list:
            self.assertEqual(chamada.kwargs["allowed_mentions"].to_dict(), {"parse": []})
        self.assertIn("**Apelido 10**", str(canal.send.call_args_list[0].kwargs["embed"].to_dict()))

    async def test_81_resumos_esperam_recuperacao_e_importacao(self):
        self._fechar_dezembro()
        canal = self._pronto_para_enviar()
        self.cog.recuperando = True
        await self.cog.enviar_resumos()
        self.cog.recuperando = False
        self.b.definir_estado("importacao_concluida", "0")
        await self.cog.enviar_resumos()
        canal.send.assert_not_awaited()

    async def test_82_sem_permissao_volta_a_pendente_e_erro_incerto_nao_reenvia(self):
        self._fechar_dezembro()
        canal = self._pronto_para_enviar()
        canal.send = AsyncMock(side_effect=discord.Forbidden(NS(status=403, reason="x"), "sem"))
        with patch.object(imagens, "disponivel", return_value=False):
            await self.cog.enviar_resumos()
        self.assertEqual(len(self.b.resumos_pendentes()), 2)  # nada perdido
        canal.send = AsyncMock(
            side_effect=discord.HTTPException(NS(status=500, reason="x"), "erro")
        )
        with (
            self.assertLogs(level="ERROR"),
            patch.object(imagens, "disponivel", return_value=False),
        ):
            await self.cog.enviar_resumos()
        # Só o item em voo fica 'reservado' (não será repetido); o resto do lote continua pendente.
        self.assertEqual(self.estados_dos_resumos(), {"2025-12": "reservado", "2025": "pendente"})
        self.assertEqual(canal.send.await_count, 1)

    def estados_dos_resumos(self):
        return dict(self.b.con.execute("SELECT periodo, estado FROM resumos").fetchall())

    async def test_93_falha_de_conexao_certa_nao_perde_nenhum_resumo(self):
        self._fechar_dezembro()
        canal = self._pronto_para_enviar()
        conexao = aiohttp.ClientConnectorError(
            NS(host="discord.com", port=443, is_ssl=True, ssl=None), OSError(11001, "sem DNS")
        )
        canal.send = AsyncMock(side_effect=conexao)
        await self.cog.enviar_resumos()
        self.assertEqual(self.estados_dos_resumos(), {"2025-12": "pendente", "2025": "pendente"})
        self.assertEqual(canal.send.await_count, 1)  # para o lote na primeira falha

    async def test_94_recuperacao_iniciada_durante_a_capa_cancela_o_envio(self):
        self._fechar_dezembro()
        canal = self._pronto_para_enviar()

        async def capa_lenta(*args, **kwargs):
            self.cog.recuperando = True  # uma reconexão começou enquanto buscava a capa
            return "", ""

        self.cog.deezer.info_seguro = capa_lenta
        with patch.object(imagens, "disponivel", return_value=False):
            await self.cog.enviar_resumos()
        canal.send.assert_not_awaited()
        self.assertEqual(self.estados_dos_resumos(), {"2025-12": "pendente", "2025": "pendente"})

    async def test_95_recuperacao_de_historico_tambem_envia_os_resumos(self):
        self.cog.sincronizar = AsyncMock(return_value=(0, []))
        self.cog.enviar_avisos = AsyncMock()
        self.cog.enviar_resumos = AsyncMock()
        self.cog.iniciar_sync()
        await self.cog._sync_task
        self.cog.enviar_resumos.assert_awaited_once()

    async def test_96_hall_com_nome_enorme_respeita_o_limite_e_nao_marca_ninguem(self):
        evento = msg(10, 1, date="2025-12-05T10:00:00", content="m!play a")
        evento.author.display_name = "N" * 300
        self.b.receber(evento)
        self.b.fechar_periodos(datetime(2026, 3, 1, tzinfo=m.FUSO))
        ctx = NS(send=AsyncMock(), author=pessoa(10))
        await m.Atividade.hall.callback(self.social(), ctx)
        embed = embed_enviado(ctx)
        self.assertLessEqual(len(embed), 6000)
        self.assertNotIn("<@", embed.description)

    async def test_83_manutencao_chama_o_envio_de_resumos(self):
        self.b.definir_estado("importacao_concluida", "1")
        self.cog.recuperando = False
        self.cog.enviar_avisos = AsyncMock()
        self.cog.enviar_resumos = AsyncMock()
        self.cog._ultimo_fechamento = datetime.now(m.FUSO).strftime("%Y-%m")
        self.cog._relogio = lambda: 0.0
        await self.cog.ciclo_manutencao()
        self.cog.enviar_resumos.assert_awaited_once()

    async def test_84_hall_mostra_meses_anos_e_avisa_quando_vazio(self):
        ctx = NS(send=AsyncMock(), author=pessoa(10))
        await m.Atividade.hall.callback(self.social(), ctx)
        self.assertIn("Ninguém no ranking ainda", embed_enviado(ctx).description)
        self.b.receber(msg(10, 1, date="2025-12-05T10:00:00", content="m!play a"))
        self.b.receber(msg(20, 2, date="2025-12-06T10:00:00"))
        self.b.receber(msg(20, 3, date="2025-12-07T10:00:00"))
        self.b.fechar_periodos(datetime(2026, 3, 1, tzinfo=m.FUSO))
        ctx = NS(send=AsyncMock(), author=pessoa(10))
        await m.Atividade.hall.callback(self.social(), ctx)
        embed = embed_enviado(ctx)
        self.assertIn("**dezembro de 2025** · 🎧 Apelido 10 · 💬 Apelido 20", embed.description)
        ctx = NS(send=AsyncMock(), author=pessoa(10))
        await m.Atividade.hall.callback(self.social(), ctx, "ano")
        self.assertIn("**2025** · 🎧 ", embed_enviado(ctx).description)
        ctx = NS(send=AsyncMock(), author=pessoa(10))
        await m.Atividade.hall.callback(self.social(), ctx, "semana")
        self.assertEqual(embed_enviado(ctx).color.value, estilo.COR_AVISO)

    def _com_aviso_de_nivel(self, mensagens=3950):
        """Nível 10 gravado em silêncio e depois um salto (padrão: nível 15, múltiplo de 5)."""
        with self.b.con:
            self.b.con.execute(
                "INSERT OR REPLACE INTO totais(usuario_id, mensagens) VALUES (10, 1700)"
            )
        self.b.receber(msg(10, 1), recompensar=True, avisar=True)  # grava o nível 10
        with self.b.con:
            self.b.con.execute(
                "INSERT OR REPLACE INTO totais(usuario_id, mensagens) VALUES (10, ?)", (mensagens,)
            )
        self.b.receber(msg(10, 2), recompensar=True, avisar=True)
        canal = NS(send=AsyncMock())
        self.cog.bot.get_channel = lambda _: canal
        self.cog.recuperando = False
        self.b.definir_estado("importacao_concluida", "1")
        return canal

    async def test_85_aviso_de_nivel_sai_uma_vez_sem_mencao(self):
        canal = self._com_aviso_de_nivel()
        with patch.object(imagens, "disponivel", return_value=False):  # texto completo
            await self.cog.enviar_avisos_nivel()
            await self.cog.enviar_avisos_nivel()
        self.assertEqual(canal.send.await_count, 1)
        self.assertNotIn("file", canal.send.call_args.kwargs)
        embed = canal.send.call_args.kwargs["embed"]
        self.assertEqual(embed.title, "⬆️ Nível 15")
        self.assertIn("**Apelido 10**", embed.description)
        self.assertIn("Ouvinte da Call", embed.description)
        self.assertEqual(embed.fields[0].name, "Nível 15")
        self.assertEqual(canal.send.call_args.kwargs["allowed_mentions"].to_dict(), {"parse": []})

    async def test_85b_nivel_fora_dos_multiplos_de_5_sai_so_em_texto(self):
        canal = self._com_aviso_de_nivel(mensagens=2100)  # nível 11
        gerar = MagicMock(return_value=b"PNG")
        with (
            patch.object(imagens, "disponivel", return_value=True),
            patch.object(imagens, "gerar_nivel", gerar),
        ):
            await self.cog.enviar_avisos_nivel()
        gerar.assert_not_called()
        kw = canal.send.call_args.kwargs
        self.assertNotIn("file", kw)
        self.assertEqual(kw["embed"].title, "⬆️ Nível 11")

    async def test_85c_troca_de_patente_sai_com_imagem_e_destaque(self):
        canal = self._com_aviso_de_nivel(mensagens=7300)  # nível 20: Resenheiro
        gerar = MagicMock(return_value=b"PNG")
        with (
            patch.object(imagens, "disponivel", return_value=True),
            patch.object(imagens, "gerar_nivel", gerar),
        ):
            await self.cog.enviar_avisos_nivel()
        dados = gerar.call_args.args[0]
        self.assertEqual(
            (dados["nivel"], dados["patente"], dados["trocou"]), (20, "Resenheiro", True)
        )
        embed = canal.send.call_args.kwargs["embed"]
        self.assertEqual(embed.title, "🎖️ Nova patente: Resenheiro")
        self.assertEqual(embed.color.value, 0xCD7F32)

    @unittest.skipUnless(imagens.disponivel(), "Pillow não instalado")
    async def test_107_aviso_de_nivel_anexa_a_imagem_e_enxuga_o_embed(self):
        canal = self._com_aviso_de_nivel()
        self.b.salvar_perfil(10, cor=0x2255FF)
        await self.cog.enviar_avisos_nivel()
        kw = canal.send.call_args.kwargs
        self.assertEqual(kw["file"].filename, "nivel.png")
        self.assertEqual(kw["embed"].image.url, "attachment://nivel.png")
        self.assertEqual(kw["embed"].title, "⬆️ Nível 15")
        self.assertEqual(len(kw["embed"].fields), 0)
        self.assertEqual(kw["allowed_mentions"].to_dict(), {"parse": []})

    async def test_108_aviso_de_nivel_usa_nome_sem_escape_e_a_cor_da_pessoa(self):
        canal = self._com_aviso_de_nivel()
        self.b.salvar_perfil(10, cor=0x2255FF)
        self.b.con.execute("UPDATE autores SET nome='a*b_c' WHERE usuario_id=10")
        gerar = MagicMock(return_value=b"PNG")
        with (
            patch.object(imagens, "disponivel", return_value=True),
            patch.object(imagens, "gerar_nivel", gerar),
        ):
            await self.cog.enviar_avisos_nivel()
        dados = gerar.call_args.args[0]
        self.assertEqual(
            (dados["nome"], dados["nivel"], dados["atual"], dados["cor"]),
            ("a*b_c", 15, 15, 0x2255FF),
        )
        self.assertIn("a\\*b\\_c", canal.send.call_args.kwargs["embed"].description)  # texto escapa

    async def test_109_falha_na_imagem_nao_impede_o_aviso(self):
        canal = self._com_aviso_de_nivel()
        with (
            patch.object(imagens, "disponivel", return_value=True),
            patch.object(imagens, "gerar_nivel", side_effect=RuntimeError("boom")),
            self.assertLogs(level="ERROR"),
        ):
            await self.cog.enviar_avisos_nivel()
        kw = canal.send.call_args.kwargs
        self.assertNotIn("file", kw)
        self.assertEqual(kw["embed"].fields[0].name, "Nível 15")  # embed completo como reserva

    async def test_110_reenvio_apos_falta_de_permissao_gera_arquivo_novo(self):
        canal = self._com_aviso_de_nivel()
        with (
            patch.object(imagens, "disponivel", return_value=True),
            patch.object(imagens, "gerar_nivel", MagicMock(return_value=b"PNG")),
        ):
            canal.send = AsyncMock(side_effect=discord.Forbidden(NS(status=403, reason="x"), "sem"))
            await self.cog.enviar_avisos_nivel()
            canal.send = AsyncMock()
            await self.cog.enviar_avisos_nivel()
        self.assertEqual(canal.send.await_count, 1)
        self.assertEqual(canal.send.call_args.kwargs["file"].fp.read(), b"PNG")

    async def test_86_aviso_de_nivel_espera_e_respeita_permissao(self):
        canal = self._com_aviso_de_nivel()
        self.cog.recuperando = True
        await self.cog.enviar_avisos_nivel()
        canal.send.assert_not_awaited()
        self.cog.recuperando = False
        canal.send = AsyncMock(side_effect=discord.Forbidden(NS(status=403, reason="x"), "sem"))
        await self.cog.enviar_avisos_nivel()
        self.assertEqual(len(self.b.avisos_nivel_pendentes()), 1)  # continua pendente

    async def test_87_manutencao_chama_o_envio_de_avisos_de_nivel(self):
        self.b.definir_estado("importacao_concluida", "1")
        self.cog.recuperando = False
        self.cog.enviar_avisos = AsyncMock()
        self.cog.enviar_resumos = AsyncMock()
        self.cog.enviar_avisos_nivel = AsyncMock()
        self.cog._ultimo_fechamento = datetime.now(m.FUSO).strftime("%Y-%m")
        self.cog._relogio = lambda: 0.0
        await self.cog.ciclo_manutencao()
        self.cog.enviar_avisos_nivel.assert_awaited_once()

    async def test_88_recuperacao_de_historico_tambem_envia_avisos_de_nivel(self):
        self.cog.sincronizar = AsyncMock(return_value=(0, []))
        self.cog.enviar_avisos = AsyncMock()
        self.cog.enviar_resumos = AsyncMock()
        self.cog.enviar_avisos_nivel = AsyncMock()
        self.cog.iniciar_sync()
        await self.cog._sync_task
        self.cog.enviar_avisos_nivel.assert_awaited_once()

    @unittest.skipUnless(imagens.disponivel(), "Pillow não instalado")
    async def test_102_erros_de_rede_ao_baixar_o_avatar_nao_impedem_o_cartao(self):
        for erro in (aiohttp.ClientConnectionError(), asyncio.TimeoutError()):
            ctx = self._ctx_do_cartao()
            ctx.author.display_avatar = NS(
                with_size=lambda _: NS(url="https://example.test/a.png"),
                replace=lambda **kw: NS(read=AsyncMock(side_effect=erro)),
            )
            await m.Atividade.cartao.callback(self.social(), ctx, None)
            self.assertEqual(ctx.send.call_args.kwargs["file"].filename, "cartao.png")

    async def test_103_gerar_imagem_devolve_arquivo_ou_none_sem_quebrar_a_mensagem(self):
        with patch.object(imagens, "disponivel", return_value=True):
            arquivo = await self.cog.gerar_imagem(lambda: b"PNG", "x.png")
        self.assertEqual(arquivo.filename, "x.png")
        self.assertEqual(arquivo.fp.read(), b"PNG")
        with patch.object(imagens, "disponivel", return_value=False):
            self.assertIsNone(await self.cog.gerar_imagem(lambda: b"PNG", "x.png"))
        with patch.object(imagens, "disponivel", return_value=True):
            with self.assertLogs(level="ERROR"):
                self.assertIsNone(await self.cog.gerar_imagem(self._quebra, "x.png"))
            with patch.object(m, "IMAGEM_TIMEOUT", 0.05), self.assertLogs(level="ERROR"):
                self.assertIsNone(await self.cog.gerar_imagem(self._lenta, "x.png"))

    @staticmethod
    def _quebra():
        raise RuntimeError("boom")

    @staticmethod
    def _lenta():
        time.sleep(0.4)
        return b"tarde demais"

    async def test_104_baixar_avatar_devolve_bytes_ou_none(self):
        def avatar_com(leitura):
            return NS(display_avatar=NS(replace=lambda **kw: NS(read=leitura)))

        self.assertEqual(
            await self.cog.baixar_avatar(avatar_com(AsyncMock(return_value=b"png"))), b"png"
        )
        for erro in (aiohttp.ClientConnectionError(), asyncio.TimeoutError()):
            self.assertIsNone(await self.cog.baixar_avatar(avatar_com(AsyncMock(side_effect=erro))))
        self.assertIsNone(await self.cog.baixar_avatar(None))
        self.assertIsNone(await self.cog.baixar_avatar(NS()))  # sem display_avatar

    async def test_97_dados_do_cartao_reunem_nivel_posicoes_e_cor(self):
        self.b.receber(msg(10, 1, content="m!play a"))
        self.b.salvar_perfil(10, cor=0xFF8800)
        dados = self.cog.dados_cartao(pessoa(10))
        self.assertEqual(dados["nome"], "Apelido 10")
        self.assertEqual((dados["mensagens"], dados["pedidos"], dados["roletadas"]), (1, 1, 0))
        self.assertEqual((dados["nivel"], dados["avanco"], dados["meta"]), (2, 6, 60))
        self.assertEqual((dados["xp"], dados["patente"]), (26, "Figurante"))
        self.assertIsNone(dados["insignia"])  # sem título escolhido
        self.b.conceder_manual(10, "Papagaio da Call")
        self.b.selecionar_titulo(10, "papagaio da call")
        dados = self.cog.dados_cartao(pessoa(10))
        self.assertEqual(dados["titulo"], "Papagaio da Call")
        self.assertTrue(dados["insignia"].startswith(b"\x89PNG"))
        self.assertEqual(dados["pos_mensagens"], "#1 de 1")
        self.assertEqual(dados["pos_mudae"], "")
        self.assertEqual(dados["cor"], 0xFF8800)

    def _ctx_do_cartao(self):
        alvo = pessoa(10)
        alvo.display_avatar = NS(
            with_size=lambda _: NS(url="https://example.test/avatar.png"),
            replace=lambda **kw: NS(read=AsyncMock(return_value=b"lixo")),
        )
        return NS(send=AsyncMock(), author=alvo, typing=Typing)

    @unittest.skipUnless(imagens.disponivel(), "Pillow não instalado")
    async def test_98_comando_cartao_envia_a_imagem_dentro_de_um_embed(self):
        ctx = self._ctx_do_cartao()
        await m.Atividade.cartao.callback(self.social(), ctx, None)
        kw = ctx.send.call_args.kwargs
        self.assertEqual(kw["file"].filename, "cartao.png")
        self.assertEqual(kw["embed"].image.url, "attachment://cartao.png")
        self.assertEqual(kw["allowed_mentions"].to_dict(), {"parse": []})

    async def test_99_sem_pillow_o_cartao_cai_no_perfil_com_aviso(self):
        ctx = self._ctx_do_cartao()
        with patch.object(imagens, "disponivel", return_value=False):
            await m.Atividade.cartao.callback(self.social(), ctx, None)
        primeira, segunda = ctx.send.call_args_list
        self.assertEqual(primeira.kwargs["embed"].color.value, estilo.COR_AVISO)
        self.assertIn("Pillow", primeira.kwargs["embed"].description)
        self.assertIn("view", segunda.kwargs)  # o perfil paginado

    async def test_100_cartao_com_falha_vira_erro_amigavel(self):
        ctx = self._ctx_do_cartao()
        with (
            patch.object(imagens, "disponivel", return_value=True),
            patch.object(imagens, "gerar_cartao", side_effect=RuntimeError("boom")),
        ):
            with self.assertLogs(level="ERROR"):
                await m.Atividade.cartao.callback(self.social(), ctx, None)
        self.assertEqual(embed_enviado(ctx).color.value, estilo.COR_ERRO)

    async def test_101_cartao_tem_cooldown_aliases_e_aparece_na_ajuda(self):
        comando = m.Atividade.cartao
        self.assertIsNotNone(comando._buckets._cooldown)
        self.assertIn("cartão", comando.aliases)
        ctx = NS(send=AsyncMock())
        await m.Ajuda.ajuda.callback(m.Ajuda(None), ctx)
        self.assertIn("`mm!cartao [@pessoa]`", embed_enviado(ctx).description)

    def _ajuda(self):
        return m.Ajuda(NS(get_cog=lambda _: self.cog))

    @unittest.skipUnless(imagens.disponivel(), "Pillow não instalado")
    async def test_105_ajuda_anexa_o_banner(self):
        ctx = NS(send=AsyncMock())
        await m.Ajuda.ajuda.callback(self._ajuda(), ctx)
        kw = ctx.send.call_args.kwargs
        self.assertEqual(kw["file"].filename, "ajuda.png")
        self.assertEqual(kw["embed"].image.url, "attachment://ajuda.png")
        self.assertIn("`mm!levels`", kw["embed"].description)  # o texto continua completo

    async def test_106_ajuda_sem_pillow_ou_com_falha_sai_so_com_texto(self):
        with patch.object(imagens, "disponivel", return_value=False):
            ctx = NS(send=AsyncMock())
            await m.Ajuda.ajuda.callback(self._ajuda(), ctx)
        self.assertNotIn("file", ctx.send.call_args.kwargs)
        self.assertIsNone(embed_enviado(ctx).image.url)
        with (
            patch.object(imagens, "disponivel", return_value=True),
            patch.object(imagens, "gerar_ajuda", side_effect=RuntimeError("boom")),
            self.assertLogs(level="ERROR"),
        ):
            ctx = NS(send=AsyncMock())
            await m.Ajuda.ajuda.callback(self._ajuda(), ctx)
        self.assertNotIn("file", ctx.send.call_args.kwargs)
        self.assertIn("`mm!levels`", embed_enviado(ctx).description)

    async def test_111_baixar_bytes_da_capa_com_limites(self):
        def sessao(status=200, dados=b"capa", erro=None):
            class Resposta:
                def __init__(self):
                    self.status = status
                    self.content = NS(read=AsyncMock(return_value=dados))

                async def __aenter__(self):
                    if erro:
                        raise erro
                    return self

                async def __aexit__(self, *args):
                    return None

            return NS(closed=False, get=lambda url: Resposta())

        deezer = self.cog.deezer
        deezer.session = sessao()
        self.assertEqual(await deezer.baixar_bytes("https://x.test/capa.jpg"), b"capa")
        self.assertIsNone(await deezer.baixar_bytes(""))
        deezer.session = sessao(status=404)
        self.assertIsNone(await deezer.baixar_bytes("https://x.test/capa.jpg"))
        deezer.session = sessao(erro=aiohttp.ClientConnectionError())
        self.assertIsNone(await deezer.baixar_bytes("https://x.test/capa.jpg"))
        deezer.session = sessao(dados=b"x" * 2_000_001)  # grande demais para uma capa
        self.assertIsNone(await deezer.baixar_bytes("https://x.test/capa.jpg"))
        deezer.session = None  # o teardown fecha a sessão real, não a simulada

    async def test_112_resumos_anexam_a_imagem_e_enxugam_o_embed(self):
        self._fechar_dezembro()
        canal = self._pronto_para_enviar()
        self.cog.deezer.info_seguro = AsyncMock(return_value=("https://x.test/capa.jpg", ""))
        self.cog.deezer.baixar_bytes = AsyncMock(return_value=b"capa")
        gerar = MagicMock(return_value=b"PNG")
        with (
            patch.object(imagens, "disponivel", return_value=True),
            patch.object(imagens, "gerar_resumo", gerar),
        ):
            await self.cog.enviar_resumos()
        mes, ano = gerar.call_args_list
        dados = mes.args[0]
        self.assertEqual(
            (dados["tipo"], dados["titulo"], dados["ano"]), ("mes", "Dezembro", "2025")
        )
        self.assertEqual(dados["dj"], ("Apelido 10", "1 pedido"))
        self.assertEqual(dados["tagarela"], ("Apelido 10", "2 mensagens"))
        self.assertEqual((dados["mensagens"], dados["pedidos"]), ("3", "2"))
        self.assertEqual(dados["musica"], ("Faixa", "Artista", 1))
        self.assertEqual(mes.args[3], b"capa")
        self.assertEqual((ano.args[0]["tipo"], ano.args[0]["titulo"]), ("ano", "2025"))
        for chamada in canal.send.call_args_list:
            self.assertEqual(chamada.kwargs["file"].filename, "resumo.png")
            self.assertEqual(chamada.kwargs["embed"].image.url, "attachment://resumo.png")
            self.assertIsNone(chamada.kwargs["embed"].thumbnail.url)  # a capa está na imagem
            self.assertEqual(chamada.kwargs["allowed_mentions"].to_dict(), {"parse": []})
        self.assertEqual(len(canal.send.call_args_list[0].kwargs["embed"].fields), 0)

    async def test_113_falha_na_imagem_do_resumo_mantem_o_embed_completo(self):
        self._fechar_dezembro()
        canal = self._pronto_para_enviar()
        self.cog.deezer.info_seguro = AsyncMock(return_value=("https://x.test/capa.jpg", ""))
        self.cog.deezer.baixar_bytes = AsyncMock(return_value=None)
        with (
            patch.object(imagens, "disponivel", return_value=True),
            patch.object(imagens, "gerar_resumo", side_effect=RuntimeError("boom")),
            self.assertLogs(level="ERROR"),
        ):
            await self.cog.enviar_resumos()
        self.assertEqual(canal.send.await_count, 2)
        for chamada in canal.send.call_args_list:
            self.assertNotIn("file", chamada.kwargs)
        primeiro = canal.send.call_args_list[0].kwargs["embed"]
        self.assertEqual(primeiro.thumbnail.url, "https://x.test/capa.jpg")
        self.assertIn("🎧 DJ do Mês", [f.name for f in primeiro.fields])

    def _wrapped_com_dois_pedidos(self):
        ontem = (datetime.now(m.FUSO) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S")
        self.b.receber(msg(10, 1, content="m!play a", date=ontem))
        self.b.receber(msg(10, 2, content="m!play b", date=ontem))
        self.cog.deezer.info_seguro = AsyncMock(return_value=("", "Dance"))
        self.cog.links.titulo_seguro = AsyncMock(return_value=None)
        return NS(send=AsyncMock(), typing=Typing, author=pessoa(10))

    async def test_114_wrapped_anexa_a_imagem_e_troca_o_grafico_em_texto(self):
        ctx = self._wrapped_com_dois_pedidos()
        self.b.salvar_perfil(10, cor=0x2255FF)
        gerar = MagicMock(return_value=b"PNG")
        with (
            patch.object(imagens, "disponivel", return_value=True),
            patch.object(imagens, "gerar_wrapped", gerar),
        ):
            await m.Musicas.wrapped.callback(self.cog, ctx)
        dados = gerar.call_args.args[0]
        self.assertEqual((dados["nome"], dados["pedidos"], dados["dias"]), ("Apelido 10", 2, 1))
        self.assertEqual((len(dados["meses"]), sum(dados["meses"])), (12, 2))
        self.assertEqual((dados["genero"], dados["cor"]), ("Dance", 0x2255FF))
        self.assertEqual({t for t, _ in dados["top"]}, {"a", "b"})
        kw = ctx.send.call_args.kwargs
        self.assertEqual(kw["file"].filename, "wrapped.png")
        embed = kw["embed"]
        self.assertEqual(embed.image.url, "attachment://wrapped.png")
        self.assertNotIn("📈 Mês a mês", [f.name for f in embed.fields])
        self.assertIn("🎧 Pedidos", [f.name for f in embed.fields])

    async def test_115_wrapped_sem_imagem_ou_com_falha_mantem_o_grafico_em_texto(self):
        for cenario in ("sem_pillow", "falha"):
            ctx = self._wrapped_com_dois_pedidos()
            if cenario == "sem_pillow":
                with patch.object(imagens, "disponivel", return_value=False):
                    await m.Musicas.wrapped.callback(self.cog, ctx)
            else:
                with (
                    patch.object(imagens, "disponivel", return_value=True),
                    patch.object(imagens, "gerar_wrapped", side_effect=RuntimeError("boom")),
                    self.assertLogs(level="ERROR"),
                ):
                    await m.Musicas.wrapped.callback(self.cog, ctx)
            kw = ctx.send.call_args.kwargs
            self.assertNotIn("file", kw, cenario)
            self.assertIn("📈 Mês a mês", [f.name for f in kw["embed"].fields], cenario)

    @staticmethod
    def canal_sem_anexo():
        """Canal em que o bot pode enviar mensagens, mas não anexar arquivos."""
        return NS(
            permissions_for=lambda membro: NS(attach_files=False),
            guild=NS(me=NS()),
            send=AsyncMock(),
        )

    async def test_116_ajuda_e_wrapped_sem_permissao_de_anexo_saem_so_com_texto(self):
        gerar = MagicMock(return_value=b"PNG")
        with (
            patch.object(imagens, "disponivel", return_value=True),
            patch.object(imagens, "gerar_ajuda", gerar),
            patch.object(imagens, "gerar_wrapped", gerar),
        ):
            ctx = NS(send=AsyncMock(), channel=self.canal_sem_anexo())
            await m.Ajuda.ajuda.callback(self._ajuda(), ctx)
            self.assertNotIn("file", ctx.send.call_args.kwargs)
            self.assertIn("`mm!levels`", embed_enviado(ctx).description)
            ctx = self._wrapped_com_dois_pedidos()
            ctx.channel = self.canal_sem_anexo()
            await m.Musicas.wrapped.callback(self.cog, ctx)
            kw = ctx.send.call_args.kwargs
            self.assertNotIn("file", kw)
            self.assertIn("📈 Mês a mês", [f.name for f in kw["embed"].fields])
        gerar.assert_not_called()  # nem gasta CPU gerando uma imagem que não poderia sair

    async def test_117_avisos_em_canal_sem_anexo_saem_completos_e_sem_gerar_imagem(self):
        gerar = MagicMock(return_value=b"PNG")
        self._fechar_dezembro()
        self.b.receber(msg(10, 90), recompensar=True, avisar=True)
        canal = self._pronto_para_enviar()
        canal.permissions_for = lambda membro: NS(attach_files=False)
        canal.guild = NS(me=NS())
        with (
            patch.object(imagens, "disponivel", return_value=True),
            patch.object(imagens, "gerar_resumo", gerar),
            patch.object(imagens, "gerar_nivel", gerar),
        ):
            await self.cog.enviar_resumos()
            with self.b.con:
                self.b.con.execute("INSERT INTO avisos_nivel(usuario_id, marco) VALUES (10, 6)")
            await self.cog.enviar_avisos_nivel()
        gerar.assert_not_called()
        self.assertEqual(canal.send.await_count, 3)  # 2 resumos + 1 aviso, todos em texto
        for chamada in canal.send.call_args_list:
            self.assertNotIn("file", chamada.kwargs)
        self.assertTrue(canal.send.call_args_list[0].kwargs["embed"].fields)  # embed completo

    async def test_118_cartao_sem_permissao_de_anexo_cai_no_perfil_comum(self):
        ctx = self._ctx_do_cartao()
        ctx.channel = self.canal_sem_anexo()
        with patch.object(imagens, "disponivel", return_value=True):
            await m.Atividade.cartao.callback(self.social(), ctx, None)
        primeira, segunda = ctx.send.call_args_list
        self.assertEqual(primeira.kwargs["embed"].color.value, estilo.COR_AVISO)
        self.assertIn("anexar", primeira.kwargs["embed"].description)
        self.assertIn("view", segunda.kwargs)

    async def test_119_nome_alternativo_e_cor_padrao_em_todas_as_imagens(self):
        alvo = pessoa(10, "田中")
        alvo.name = "tanaka"
        self.assertEqual(self.cog.dados_cartao(alvo)["nome_alt"], "tanaka")
        self.assertIsNone(self.cog.dados_cartao(alvo)["cor"])  # sem mm!ec: coral em todas
        ctx = self._wrapped_com_dois_pedidos()
        ctx.author = alvo
        gerar = MagicMock(return_value=b"PNG")
        with (
            patch.object(imagens, "disponivel", return_value=True),
            patch.object(imagens, "gerar_wrapped", gerar),
        ):
            await m.Musicas.wrapped.callback(self.cog, ctx)
        self.assertEqual(gerar.call_args.args[0]["nome_alt"], "tanaka")
        self.assertIsNone(gerar.call_args.args[0]["cor"])

    async def test_120_wrapped_tem_cooldown(self):
        self.assertIsNotNone(m.Musicas.wrapped._buckets._cooldown)
        self.assertEqual(m.Musicas.wrapped._buckets._cooldown.rate, 1)

    async def test_78_ios_tambem_mostra_sua_posicao(self):
        for uid in range(20, 31):  # 11 pessoas com 2 pedidos
            self.b.receber(msg(uid, seq=uid, content="m!play x"))
            self.b.receber(msg(uid, seq=uid + 100, content="m!play y"))
        self.b.receber(msg(10, seq=500, content="m!play z"))
        ctx = NS(send=AsyncMock(), author=pessoa(10))
        await m.Musicas.musicas.callback(self.cog, ctx, "ios")
        self.assertIn("📍 Sua posição: **#12** de 12", embed_enviado(ctx).description)

    async def test_74_confirmacoes_e_erros_usam_embeds_coloridos(self):
        social = self.social()
        ctx = NS(send=AsyncMock(), author=pessoa(10))
        await m.Atividade.frase.callback(social, ctx, texto="ok")
        self.assertEqual(embed_enviado(ctx).color.value, estilo.COR_SUCESSO)
        self.assertEqual(embed_enviado(ctx).description, "✅ Frase salva.")
        await m.Atividade.frase.callback(social, ctx, texto="x" * 101)
        self.assertEqual(embed_enviado(ctx).color.value, estilo.COR_AVISO)
        await m.Atividade.ec.callback(social, ctx, texto="corzinha")
        self.assertEqual(embed_enviado(ctx).color.value, estilo.COR_ERRO)
        await m.Atividade.titulo.callback(social, ctx, nome="inexistente")
        self.assertEqual(embed_enviado(ctx).color.value, estilo.COR_AVISO)
        for chamada in ctx.send.call_args_list:
            self.assertEqual(chamada.kwargs["allowed_mentions"].to_dict(), {"parse": []})

    async def test_75_erros_de_comando_saem_padronizados(self):
        bot = m.MeMiBot(command_prefix="mm!", help_command=None, intents=discord.Intents.default())
        ctx = NS(send=AsyncMock(), command=NS(qualified_name="perfil", signature="[pessoa]"))
        casos = (
            (m.commands.NoPrivateMessage(), estilo.COR_AVISO),
            (
                m.commands.CommandOnCooldown(None, 12.0, m.commands.BucketType.user),
                estilo.COR_AVISO,
            ),
            (m.commands.CheckFailure("Só o Memi pode usar esse comando."), estilo.COR_ERRO),
            (m.commands.BadArgument("x"), estilo.COR_AVISO),
            (RuntimeError("boom"), estilo.COR_ERRO),
        )
        with self.assertLogs(level="ERROR"):  # o erro genérico é registrado no log
            for erro, cor in casos:
                ctx.send.reset_mock()
                await bot.on_command_error(ctx, erro)
                self.assertEqual(embed_enviado(ctx).color.value, cor, repr(erro))
        ctx.send.reset_mock()
        await bot.on_command_error(ctx, m.commands.CommandNotFound("x"))
        ctx.send.assert_not_called()


class ChangelogComandoTests(unittest.IsolatedAsyncioTestCase):
    """mm!changelog: versão mais recente, versões passadas por menu ou por argumento."""

    async def enviar(self, *args):
        ctx = NS(send=AsyncMock())
        ctx.send.return_value = NS()
        await m.Ajuda.changelog.callback(m.Ajuda(None), ctx, *args)
        return ctx

    async def test_sem_argumento_mostra_a_versao_mais_recente_com_menu(self):
        ctx = await self.enviar()
        kw = ctx.send.call_args.kwargs
        self.assertIn(m.versoes_bot.VERSAO_ATUAL, kw["embed"].title)
        opcoes = kw["view"].select.options
        self.assertEqual(len(opcoes), len(m.versoes_bot.VERSOES))
        self.assertEqual([o.default for o in opcoes], [True] + [False] * (len(opcoes) - 1))
        self.assertEqual(kw["allowed_mentions"].to_dict(), {"parse": []})

    async def test_argumento_abre_a_versao_pedida_e_marca_no_menu(self):
        ctx = await self.enviar("v2.0.0")
        kw = ctx.send.call_args.kwargs
        self.assertIn("2.0.0", kw["embed"].title)
        self.assertEqual([o.default for o in kw["view"].select.options][-1], True)

    async def test_versao_inexistente_lista_as_disponiveis(self):
        ctx = await self.enviar("9.9.9")
        embed = embed_enviado(ctx)
        self.assertEqual(embed.color.value, estilo.COR_AVISO)
        for versao in m.versoes_bot.VERSOES:
            self.assertIn(versao["versao"], embed.description)

    async def test_menu_troca_a_versao_no_mesmo_menu(self):
        view = m.ChangelogView(0)
        interacao = NS(response=NS(edit_message=AsyncMock()))
        await view.trocar(interacao, len(m.versoes_bot.VERSOES) - 1)
        kw = interacao.response.edit_message.call_args.kwargs
        self.assertIn("2.0.0", kw["embed"].title)
        self.assertEqual(kw["view"], view)
        self.assertEqual(view.select.options[-1].default, True)
        self.assertEqual(view.select.options[0].default, False)
        await view.on_timeout()
        self.assertTrue(view.select.disabled)

    async def test_changelog_aparece_na_ajuda_e_tem_aliases(self):
        self.assertIn("novidades", m.Ajuda.changelog.aliases)
        ctx = NS(send=AsyncMock())
        await m.Ajuda.ajuda.callback(m.Ajuda(None), ctx)
        self.assertIn("`mm!changelog`", embed_enviado(ctx).description)


class ExecutarTests(unittest.TestCase):
    def test_ctrl_c_encerra_sem_traceback(self):
        async def interrompido():
            raise KeyboardInterrupt

        with patch.object(m, "conectar", interrompido):
            m.executar()  # não deve levantar

    def test_recusa_da_message_content_vira_mensagem_clara(self):
        async def recusado():
            raise discord.PrivilegedIntentsRequired(None)

        with patch.object(m, "conectar", recusado), self.assertLogs(level="ERROR"):
            with self.assertRaises(SystemExit) as saida:
                m.executar()
        self.assertIn("Message Content", str(saida.exception))


class BotFalso:
    """Bot mínimo para testar a conexão sem falar com o Discord."""

    def __init__(self, membros, erro=None):
        self.intents = NS(members=membros, message_content=True)
        self.erro, self.iniciado = erro, False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def start(self, token):
        if self.erro:
            raise self.erro
        self.iniciado = True


class ConexaoTests(unittest.IsolatedAsyncioTestCase):
    """A atualização não pode exigir passo manual no Developer Portal."""

    def test_criar_bot_liga_ou_desliga_so_a_intent_de_membros(self):
        com, sem = m.criar_bot(True), m.criar_bot(False)
        self.assertEqual((com.intents.members, sem.intents.members), (True, False))
        self.assertTrue(com.intents.message_content and sem.intents.message_content)
        self.assertEqual(m.bot.intents.members, m.MEMBERS_INTENT)

    async def test_intent_de_membros_recusada_faz_o_bot_seguir_sem_ela(self):
        recusa = discord.PrivilegedIntentsRequired(None)
        primeiro, segundo = BotFalso(True, recusa), BotFalso(False)
        criar = MagicMock(return_value=segundo)
        with patch.object(m, "bot", primeiro), patch.object(m, "criar_bot", criar):
            with self.assertLogs(level="WARNING") as registro:
                await m.conectar()
            self.assertIs(m.bot, segundo)
        criar.assert_called_once_with(False)
        self.assertTrue(segundo.iniciado)
        self.assertIn("Server Members Intent", "\n".join(registro.output))

    async def test_sem_a_intent_de_membros_a_recusa_e_repassada(self):
        recusa = discord.PrivilegedIntentsRequired(None)
        criar = MagicMock()
        with patch.object(m, "bot", BotFalso(False, recusa)), patch.object(m, "criar_bot", criar):
            with self.assertRaises(discord.PrivilegedIntentsRequired):
                await m.conectar()
        criar.assert_not_called()

    async def test_conexao_normal_nao_recria_o_bot(self):
        normal = BotFalso(True)
        criar = MagicMock()
        with patch.object(m, "bot", normal), patch.object(m, "criar_bot", criar):
            await m.conectar()
        self.assertTrue(normal.iniciado)
        criar.assert_not_called()


class InfraTests(unittest.TestCase):
    def test_48_config_de_exemplo_so_tem_chaves_usadas(self):
        exemplo = json.loads((Path(m.__file__).parent / "config.example.json").read_text("utf-8"))
        self.assertEqual(set(exemplo), {"owner_id", "notice_channel_id", "guild_id"})

    def test_49_configurar_log_grava_em_arquivo_rotativo(self):
        raiz = logging.getLogger()
        nivel = raiz.level
        with tempfile.TemporaryDirectory() as pasta:
            arquivo = Path(pasta) / "teste.log"
            handler = m.configurar_log(arquivo)
            try:
                logging.getLogger("memi.teste").info("olá log")
                handler.flush()
                self.assertIn("olá log", arquivo.read_text(encoding="utf-8"))
            finally:
                raiz.removeHandler(handler)
                handler.close()
                raiz.setLevel(nivel)

    def test_50_travar_instancia_impede_segunda_copia(self):
        livre = socket.socket()
        livre.bind(("127.0.0.1", 0))
        porta = livre.getsockname()[1]
        livre.close()
        primeira = m.travar_instancia(porta)
        self.assertIsNotNone(primeira)
        try:
            self.assertIsNone(m.travar_instancia(porta))
        finally:
            primeira.close()
        outra = m.travar_instancia(porta)
        self.assertIsNotNone(outra)
        outra.close()

    def test_51_fluxo_log_converte_saida_em_registros_por_linha(self):
        with self.assertLogs("memi.stdout", level="ERROR") as capturado:
            fluxo = m.FluxoLog("memi.stdout", logging.ERROR)
            fluxo.write("linha 1\nlinha")
            fluxo.write(" 2\n")
            fluxo.flush()
        self.assertEqual([r.getMessage() for r in capturado.records], ["linha 1", "linha 2"])


if __name__ == "__main__":
    unittest.main()
