"""Testes do catálogo de tags (títulos e insígnias unificados) e dos ícones."""

import unittest
from pathlib import Path

import progressao
import tags


class CatalogoTests(unittest.TestCase):
    def test_toda_tag_tem_os_campos_obrigatorios(self):
        for ident, info in tags.CATALOGO.items():
            with self.subTest(ident):
                self.assertTrue(info["nome"])
                self.assertIn(info["categoria"], tags.CATEGORIAS)
                self.assertTrue(info["como"])
                self.assertTrue(info["emoji"])
                self.assertTrue(info["titulo"] and info["insignia"])  # tudo é título e insígnia
                self.assertRegex(ident, r"^[a-z0-9_]{2,29}$")  # vira nome de emoji: mm_<id>

    def test_identificadores_antigos_continuam_existindo(self):
        antigos = {
            "dj_call", "dj_mes", "dj_ano", "tagarela_chat", "tagarela_mes", "tagarela_ano",
            "roletador", "cartola", "wplace", "bongas", "breca", "resenha_torta", "resenha_reta",
            "resenhudo", "cafetao_resenhas", "rei_resenha", "demiurgo", "dj_piolho", "dj_overload",
            "dj_zettabytes", "dj_pancaked", "dj_cupcake", "dj_roger",
        }  # fmt: skip
        self.assertLessEqual(antigos, set(tags.CATALOGO))

    def test_nomes_seguem_a_lista_nova(self):
        c = tags.CATALOGO
        self.assertEqual(c["tagarela_chat"]["nome"], "Resenhex do Clubex")
        self.assertEqual(c["tagarela_mes"]["nome"], "Resenhex do Mêsex")
        self.assertEqual(c["tagarela_ano"]["nome"], "Resenhex do Anex")
        self.assertEqual(c["dj_cupcake"]["nome"], "DJ Cupcake Party")
        self.assertEqual(c["bongas"]["nome"], "Bongador")
        self.assertEqual(c["dj_call"]["como"], "Top 1 Músicas")

    def test_tags_manuais_novas(self):
        for ident in (
            "premier_league_ios",
            "copa_ios",
            "pintos_corridos",
            "cartoleiro_ouro",
            "ios_world_cup",
            "ios_club_wc",
            "papagaio",
        ):
            self.assertTrue(tags.CATALOGO[ident]["manual"], ident)

    def test_patentes_sao_tags_de_level(self):
        for item in progressao.PATENTES:
            info = tags.CATALOGO["patente_" + item["id"]]
            self.assertEqual(info["categoria"], "Level")
            self.assertEqual(info["nome"], item["nome"])
            self.assertFalse(info["manual"])

    def test_imagens_existem_para_quem_declara(self):
        for ident, info in tags.CATALOGO.items():
            if info.get("imagem"):
                with self.subTest(ident):
                    caminho = tags.PASTA_INSIGNIAS / info["imagem"]
                    self.assertTrue(caminho.exists(), caminho)
                    self.assertLess(caminho.stat().st_size, 256 * 1024)  # limite de emoji

    def test_ordem_das_categorias(self):
        self.assertEqual(
            tags.CATEGORIAS,
            (
                "DJs e Resenhas",
                "Roleta",
                "Cartola",
                "Eventos e Comunidade",
                "Mensagens",
                "Músicas",
                "Level",
            ),
        )


class BuscaTests(unittest.TestCase):
    def test_busca_por_nome_id_ou_nome_antigo_sem_acento(self):
        self.assertEqual(tags.buscar("resenhex do mesex"), "tagarela_mes")
        self.assertEqual(tags.buscar("DJ DA CALL"), "dj_call")
        self.assertEqual(tags.buscar("dj_call"), "dj_call")
        self.assertEqual(tags.buscar("Tagarela do Chat"), "tagarela_chat")  # nome antigo
        self.assertEqual(tags.buscar("BONGAS"), "bongas")
        self.assertEqual(tags.buscar("cartola"), "cartola")
        self.assertIsNone(tags.buscar("não existe"))
        self.assertIsNone(tags.buscar(""))

    def test_manuais_lista_so_as_concedidas_a_mao(self):
        manuais = tags.manuais()
        self.assertIn("papagaio", manuais)
        self.assertNotIn("dj_call", manuais)


class IconeTests(unittest.TestCase):
    def tearDown(self):
        tags.ICONES.clear()

    def test_sem_emoji_personalizado_usa_o_padrao(self):
        self.assertEqual(tags.icone("dj_call"), tags.CATALOGO["dj_call"]["emoji"])
        self.assertEqual(tags.icone("inexistente"), "🏷️")

    def test_emoji_personalizado_substitui_o_padrao(self):
        tags.ICONES["dj_call"] = "<:mm_dj_call:123>"
        self.assertEqual(tags.icone("dj_call"), "<:mm_dj_call:123>")

    def test_rotulo_junta_icone_e_nome(self):
        self.assertEqual(tags.rotulo("dj_call"), f"{tags.icone('dj_call')} DJ da Call")


if __name__ == "__main__":
    unittest.main()
