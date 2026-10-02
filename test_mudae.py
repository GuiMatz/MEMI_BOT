"""Testes do Mudae tracker: leitura das mensagens do Mudae, atribuição e estatísticas."""

import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

import discord

import mudae

FUSO = timezone(timedelta(hours=-3))
KAKERA = "<:kakera:469835869059153940>"


def sid(segundos=0, seq=0):
    base = datetime(2026, 9, 15, 12, 0, 0, tzinfo=FUSO) + timedelta(seconds=segundos)
    return discord.utils.time_snowflake(base) + seq


def autor(uid, nome, bot=False, global_name=None, display_name=None):
    return NS(
        id=uid,
        name=nome,
        bot=bot,
        global_name=global_name,
        display_name=display_name or global_name or nome,
    )


MUDAE = autor(mudae.MUDAE_ID, "Mudae", bot=True)


def embed_roll(personagem="Feng Xiao", serie="Wu Shuang", claims="40.141", kakera="37", dono=""):
    e = discord.Embed(
        description=f"{serie}\nClaims: #{claims}\n**{kakera}**{KAKERA}\n"
        + ("" if dono else "Reaja com qualquer emoji para casar!"),
        color=0xFF9D2C,
    )
    e.set_author(name=personagem)
    e.set_image(url="https://mudae.net/uploads/x.png")
    if dono:
        e.set_footer(
            text=f"⚠️ 2 ROLLS RESTANTES ⚠️ · Pertence a {dono}.", icon_url="https://x/a.png"
        )
    return e


def mensagem(mid, autor_, content="", embeds=(), canal=1, interacao=None):
    return NS(
        id=mid,
        author=autor_,
        content=content,
        embeds=list(embeds),
        channel=NS(id=canal),
        interaction_metadata=NS(user=NS(id=interacao)) if interacao else None,
        interaction=None,
    )


class LeituraTests(unittest.TestCase):
    def test_roll_livre(self):
        roll = mudae.ler_roll(mensagem(1, MUDAE, embeds=[embed_roll()]))
        self.assertEqual(
            roll,
            {
                "personagem": "Feng Xiao",
                "serie": "Wu Shuang",
                "claims": 40141,
                "likes": None,
                "kakera": 37,
                "livre": True,
                "dono": "",
            },
        )

    def test_roll_com_dono(self):
        roll = mudae.ler_roll(
            mensagem(1, MUDAE, embeds=[embed_roll("Sinbad", "Magi", "2.327", "157", "memii")])
        )
        self.assertEqual(
            (roll["personagem"], roll["kakera"], roll["claims"]), ("Sinbad", 157, 2327)
        )
        self.assertEqual(roll["dono"], "memii")
        self.assertFalse(roll["livre"])

    def test_roll_em_ingles_com_likes_e_sem_kakera(self):
        e = discord.Embed(description="Naruto\nLikes: #1,204\nReact with any emoji to claim!")
        e.set_author(name="Hinata Hyuuga")
        e.set_image(url="https://x/y.png")
        roll = mudae.ler_roll(mensagem(1, MUDAE, embeds=[e]))
        self.assertEqual((roll["likes"], roll["kakera"], roll["livre"]), (1204, None, True))

    def test_im_e_outros_embeds_nao_sao_roll(self):
        im = embed_roll()
        im.set_footer(text="1 / 12")
        sem_imagem = discord.Embed(description="lista")
        sem_imagem.set_author(name="Harem")
        miniatura = embed_roll()
        miniatura.set_thumbnail(url="https://x/t.png")
        for embeds in ([im], [sem_imagem], [miniatura], [], [embed_roll(), embed_roll()]):
            self.assertIsNone(mudae.ler_roll(mensagem(1, MUDAE, embeds=embeds)))
        humano = mensagem(1, autor(10, "fulano"), embeds=[embed_roll()])
        self.assertIsNone(mudae.ler_roll(humano))

    def test_casamento_em_portugues_ingles_e_espanhol(self):
        casos = {
            "💖 **memii** e **Sinbad** agora são casados! 💖": ("memii", "Sinbad"),
            "💖 **memii** e **Sinbad** estão agora casados! 💖": ("memii", "Sinbad"),
            "💖 **ana** and **Hinata Hyuuga** are now married! 💖": ("ana", "Hinata Hyuuga"),
            "💖 **ana** y **Rem** ahora están casados! 💖": ("ana", "Rem"),
        }
        for texto, esperado in casos.items():
            with self.subTest(texto):
                self.assertEqual(mudae.ler_casamento(texto), esperado)
        self.assertIsNone(mudae.ler_casamento("oi gente"))
        self.assertIsNone(mudae.ler_casamento("> 💖 **a** e **b** agora são casados! 💖"))

    def test_kakera(self):
        texto = "<:kakeraY:6090> **memii +401** ($k)\n<:kakeraP:1> **ana +100** ($k)"
        self.assertEqual(
            mudae.ler_kakera(texto), [("memii", "kakeraY", 401), ("ana", "kakeraP", 100)]
        )
        luz = "<:kakeraL:1> breaks down into <:kakera:2> + <:kakeraT:3> => **bia +1.230** ($k)"
        self.assertEqual(mudae.ler_kakera(luz), [("bia", "kakeraL", 1230)])
        self.assertEqual(mudae.ler_kakera("sem kakera"), [])

    def test_recusa_nomeia_a_pessoa(self):
        self.assertEqual(
            mudae.nome_da_recusa("**memii**, a roleta está limitada a 10 usos por hora."), "memii"
        )
        self.assertIsNone(mudae.nome_da_recusa("💖 **a** e **b** agora são casados! 💖"))
        self.assertIsNone(mudae.nome_da_recusa("oi"))

    def test_quem_e_o_mudae(self):
        self.assertTrue(mudae.eh_do_mudae(mensagem(1, MUDAE)))
        self.assertTrue(mudae.eh_do_mudae(mensagem(1, autor(5, "Mudae", bot=True))))
        self.assertFalse(mudae.eh_do_mudae(mensagem(1, autor(5, "Mudae"))))  # pessoa
        self.assertFalse(mudae.eh_do_mudae(mensagem(1, autor(5, "jockie", bot=True))))


class BancoMudae:
    """Banco mínimo: as tabelas do Mudae e as que o tracker consulta (comandos e mensagens)."""

    def __init__(self):
        self.con = sqlite3.connect(":memory:")
        self.con.executescript(
            "CREATE TABLE mensagens (message_id INTEGER PRIMARY KEY, autor_id INTEGER, canal_id INTEGER, eh_bot INTEGER);"
            "CREATE TABLE mudae (message_id INTEGER PRIMARY KEY, usuario_id INTEGER NOT NULL);"
            "CREATE TABLE autores (usuario_id INTEGER PRIMARY KEY, eh_bot INTEGER NOT NULL DEFAULT 0, nome TEXT NOT NULL DEFAULT '');"
        )
        mudae.criar_tabelas(self.con)

    def comando(self, mid, uid, canal=1, nome=None):
        with self.con:
            self.con.execute("INSERT INTO mensagens VALUES (?,?,?,0)", (mid, uid, canal))
            self.con.execute("INSERT INTO mudae VALUES (?,?)", (mid, uid))
            mudae.registrar_nomes(self.con, autor(uid, nome or f"u{uid}"))

    def ler(self, msg):
        with self.con:
            return mudae.registrar(self.con, msg)


class AtribuicaoTests(unittest.TestCase):
    def setUp(self):
        self.b = BancoMudae()

    def roll(self, segundos, personagem="Feng Xiao", canal=1, **kw):
        return mensagem(sid(segundos, 5), MUDAE, embeds=[embed_roll(personagem, **kw)], canal=canal)

    def roletador(self, mid):
        return self.b.con.execute(
            "SELECT roletador_id FROM mudae_rolls WHERE message_id=?", (mid,)
        ).fetchone()[0]

    def test_roll_vai_para_quem_usou_o_comando(self):
        self.b.comando(sid(0), 10)
        r = self.roll(1)
        self.assertEqual(self.b.ler(r), "roll")
        self.assertEqual(self.roletador(r.id), 10)

    def test_varias_pessoas_ao_mesmo_tempo_na_ordem_de_chegada(self):
        self.b.comando(sid(0, 1), 10)
        self.b.comando(sid(0, 2), 20)
        a, b = self.roll(1, "A"), self.roll(2, "B")
        self.b.ler(a)
        self.b.ler(b)
        self.assertEqual((self.roletador(a.id), self.roletador(b.id)), (10, 20))

    def test_comando_de_outro_canal_ou_velho_demais_nao_conta(self):
        self.b.comando(sid(0), 10, canal=2)
        self.b.comando(sid(-60), 20)
        r = self.roll(1)
        self.b.ler(r)
        self.assertIsNone(self.roletador(r.id))

    def test_comando_de_barra_usa_quem_interagiu(self):
        r = mensagem(sid(1), MUDAE, embeds=[embed_roll()], interacao=30)
        self.b.ler(r)
        self.assertEqual(self.roletador(r.id), 30)

    def test_recusa_consome_o_comando_de_quem_foi_recusado(self):
        self.b.comando(sid(0, 1), 10, nome="memii")
        self.b.comando(sid(0, 2), 20, nome="ana")
        recusa = mensagem(sid(1), MUDAE, "**memii**, a roleta está limitada a 10 usos por hora.")
        self.assertEqual(self.b.ler(recusa), "recusa")
        r = self.roll(2)
        self.b.ler(r)
        self.assertEqual(self.roletador(r.id), 20)

    def test_ler_de_novo_nao_duplica_nem_consome_outro_comando(self):
        self.b.comando(sid(0, 1), 10)
        self.b.comando(sid(0, 2), 20)
        r = self.roll(1)
        self.b.ler(r)
        self.assertIsNone(self.b.ler(r))
        r2 = self.roll(2, "Outro")
        self.b.ler(r2)
        self.assertEqual(self.roletador(r2.id), 20)
        self.assertEqual(self.b.con.execute("SELECT COUNT(*) FROM mudae_rolls").fetchone()[0], 2)

    def test_casamento_liga_ao_roll_e_detecta_snipe(self):
        self.b.comando(sid(0), 10, nome="memii")
        self.b.comando(sid(0, 1), 20, nome="ana")
        self.b.ler(self.roll(1, "Sinbad", kakera="157"))
        casou = mensagem(sid(5), MUDAE, "💖 **ana** e **Sinbad** agora são casados! 💖")
        self.assertEqual(self.b.ler(casou), "casamento")
        linha = self.b.con.execute(
            "SELECT usuario_id, roletador_id, kakera, personagem FROM mudae_casamentos"
        ).fetchone()
        self.assertEqual(linha, (20, 10, 157, "Sinbad"))
        self.assertEqual(self.b.con.execute("SELECT dono FROM mudae_rolls").fetchone()[0], "ana")

    def test_casamento_sem_roll_conhecido_ainda_conta(self):
        self.b.comando(sid(0), 10, nome="memii")
        self.b.ler(mensagem(sid(5), MUDAE, "💖 **memii** e **Rem** agora são casados! 💖"))
        self.assertEqual(
            self.b.con.execute("SELECT usuario_id, roll_id FROM mudae_casamentos").fetchone(),
            (10, None),
        )

    def test_nome_ambiguo_ou_desconhecido_fica_sem_id(self):
        self.b.comando(sid(0), 10, nome="ana")
        self.b.comando(sid(0, 1), 20, nome="ana")
        self.b.ler(mensagem(sid(5), MUDAE, "💖 **ana** e **Rem** agora são casados! 💖"))
        self.b.ler(mensagem(sid(6), MUDAE, "💖 **zé** e **Emilia** agora são casados! 💖"))
        ids = [r[0] for r in self.b.con.execute("SELECT usuario_id FROM mudae_casamentos")]
        self.assertEqual(ids, [None, None])

    def test_nome_de_exibicao_e_nome_global_tambem_resolvem(self):
        with self.b.con:
            mudae.registrar_nomes(
                self.b.con, autor(40, "joao123", global_name="João", display_name="Jão do Clubex")
            )
        for nome in ("joao123", "JOÃO", "jão do clubex"):
            self.assertEqual(mudae.resolver_nome(self.b.con, nome), 40)

    def test_nome_visto_nas_mensagens_vale_antes_do_apelido_antigo(self):
        self.b.comando(sid(0), 10, nome="ana")
        with self.b.con:
            self.b.con.execute("INSERT INTO autores VALUES (20, 0, 'Ana')")
        self.assertEqual(mudae.resolver_nome(self.b.con, "ana"), 10)
        with self.b.con:
            self.b.con.execute("INSERT INTO autores VALUES (30, 0, 'Bia')")
        self.assertEqual(mudae.resolver_nome(self.b.con, "BIA"), 30)  # sem nome visto: apelido

    def test_kakera_coletado(self):
        self.b.comando(sid(0), 10, nome="memii")
        texto = "<:kakeraY:6090> **memii +401** ($k)"
        self.assertEqual(self.b.ler(mensagem(sid(3), MUDAE, texto)), "kakera")
        self.assertEqual(
            self.b.con.execute("SELECT usuario_id, tipo, valor FROM mudae_kakera").fetchone(),
            (10, "kakeraY", 401),
        )

    def test_edicao_do_roll_atualiza_o_dono(self):
        r = self.roll(1, "Rem")
        self.b.ler(r)
        editado = mensagem(r.id, MUDAE, embeds=[embed_roll("Rem", dono="ana")])
        with self.b.con:
            self.assertTrue(mudae.atualizar_roll(self.b.con, editado))
        self.assertEqual(self.b.con.execute("SELECT dono FROM mudae_rolls").fetchone()[0], "ana")

    def test_mensagem_qualquer_do_mudae_e_ignorada(self):
        self.assertIsNone(self.b.ler(mensagem(sid(1), MUDAE, "Wishlist atualizada.")))


class EstatisticasTests(unittest.TestCase):
    def setUp(self):
        self.b = BancoMudae()
        # 10 rola Rem duas vezes e Emilia uma; 20 rola Rem uma vez e casa (snipe) com a Rem do 10
        eventos = [
            (0, 10, "Rem", "Re:Zero", "300"),
            (20, 10, "Emilia", "Re:Zero", "90"),
            (40, 20, "Rem", "Re:Zero", "310"),
            (60, 10, "Rem", "Re:Zero", "320"),
        ]
        for i, (t, uid, personagem, serie, kakera) in enumerate(eventos):
            self.b.comando(sid(t, i), uid, nome=f"u{uid}")
            self.b.ler(
                mensagem(
                    sid(t + 1, i), MUDAE, embeds=[embed_roll(personagem, serie, kakera=kakera)]
                )
            )
        self.b.ler(mensagem(sid(65), MUDAE, "💖 **u20** e **Rem** agora são casados! 💖"))
        self.b.ler(mensagem(sid(80), MUDAE, "<:kakeraY:1> **u10 +500** ($k)"))

    def test_panorama_usa_indice_para_buscar_casamentos_por_roll(self):
        plano = self.b.con.execute(
            "EXPLAIN QUERY PLAN SELECT 1 FROM mudae_rolls r "
            "WHERE NOT EXISTS (SELECT 1 FROM mudae_casamentos c WHERE c.roll_id=r.message_id)"
        ).fetchall()
        self.assertTrue(any("idx_mudae_casamentos_roll_id" in linha[3] for linha in plano))

    def test_panorama(self):
        p = mudae.panorama(self.b.con)
        self.assertEqual((p["rolls"], p["casamentos"], p["kakera"]), (4, 1, 500))
        self.assertEqual(p["personagens"][0], ("Rem", 3))
        self.assertEqual(p["series"][0], ("Re:Zero", 4))
        self.assertEqual(p["maior_casamento"], ("Rem", 20, 320))
        self.assertEqual(p["escapou"], ("Rem", 310))  # a maior kakera que ninguém casou
        self.assertEqual(p["taxa"], 25)  # 1 casamento em 4 rolls livres
        self.assertEqual(len(p["horas"]), 24)

    def test_perfil_da_pessoa(self):
        dez = mudae.perfil(self.b.con, 10)
        self.assertEqual((dez["rolls"], dez["casamentos"], dez["kakera"]), (3, 0, 500))
        self.assertEqual(dez["favorito"], ("Rem", 2))
        vinte = mudae.perfil(self.b.con, 20)
        self.assertEqual((vinte["casamentos"], vinte["snipes"]), (1, 1))
        self.assertEqual(vinte["maior_casamento"], ("Rem", 320))

    def test_rankings(self):
        self.assertEqual(mudae.ranking(self.b.con, "casamentos"), [(20, 1)])
        self.assertEqual(mudae.ranking(self.b.con, "kakera"), [(10, 500)])
        self.assertEqual(mudae.ranking(self.b.con, "snipers"), [(20, 1)])
        self.assertEqual(mudae.ranking(self.b.con, "ios"), [(10, 3), (20, 1)])
        self.assertEqual(mudae.ranking(self.b.con, "personagens")[0], ("Rem", "Re:Zero", 3))
        self.assertEqual(mudae.ranking(self.b.con, "series"), [("Re:Zero", 4)])
        self.assertEqual(mudae.ranking(self.b.con, "azarados", minimo=1)[0], (10, 3))

    def test_rankings_de_rolls_ignoram_respostas_sem_comando(self):
        self.b.con.execute(
            "INSERT INTO mudae_rolls VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (sid(100), 1, None, None, "Hors-série", "hors-série", "Extra", 1, None, 10, 1, ""),
        )
        self.assertNotIn(("Hors-série", "Extra", 1), mudae.ranking(self.b.con, "personagens"))
        self.assertNotIn(("Extra", 1), mudae.ranking(self.b.con, "series"))
        self.assertEqual(mudae.ranking(self.b.con, "personagens", limite=1)[0][0], "Rem")

    def test_ranking_por_periodo(self):
        depois = sid(30)
        self.assertEqual(mudae.ranking(self.b.con, "personagens", desde=depois)[0][2], 2)

    def test_personagem(self):
        info = mudae.personagem(self.b.con, "rem")
        self.assertEqual(info["nome"], "Rem")
        self.assertEqual((info["vezes"], info["serie"], info["maior_kakera"]), (3, "Re:Zero", 320))
        self.assertEqual(info["roletadores"][0], (10, 2))
        self.assertEqual(info["casamentos"], [(20, "u20")])
        self.assertIsNone(mudae.personagem(self.b.con, "ninguém"))
        self.assertEqual(mudae.personagem(self.b.con, "emi")["nome"], "Emilia")  # começo do nome

    def test_resumo_do_periodo(self):
        r = mudae.resumo(self.b.con, 0, sid(3600))
        self.assertEqual((r["rolls"], r["casamentos"]), (4, 1))
        self.assertEqual(r["personagem"], ["Rem", 3])


if __name__ == "__main__":
    unittest.main()
