"""Testes do histórico de versões (mm!changelog): dados válidos e embeds dentro dos limites."""

import re
import unittest

import changelog
import estilo


def numeros(versao):
    return tuple(int(x) for x in versao.split("."))


class DadosTests(unittest.TestCase):
    def test_versoes_do_mais_novo_para_o_mais_antigo_sem_repetir(self):
        ids = [v["versao"] for v in changelog.VERSOES]
        self.assertEqual(len(ids), len(set(ids)))
        for versao in ids:
            self.assertRegex(versao, r"^\d+\.\d+\.\d+$")
        self.assertEqual(ids, sorted(ids, key=numeros, reverse=True))
        self.assertEqual(changelog.VERSAO_ATUAL, ids[0])

    def test_cada_versao_tem_titulo_e_ao_menos_uma_secao_bem_formada(self):
        for entrada in changelog.VERSOES:
            with self.subTest(entrada["versao"]):
                self.assertTrue(entrada["titulo"].strip())
                self.assertLessEqual(len(entrada["titulo"]), 80)
                secoes = [k for k in changelog.SECOES if entrada.get(k)]
                self.assertTrue(secoes)
                desconhecidas = set(entrada) - set(changelog.SECOES) - {"versao", "data", "titulo"}
                self.assertEqual(desconhecidas, set())
                for chave in secoes:
                    for item in entrada[chave]:
                        self.assertTrue(item.strip())
                        self.assertLessEqual(len(item), 320, item[:40])

    def test_buscar_aceita_v_maiuscula_e_prefixo_unico(self):
        self.assertEqual(changelog.buscar(changelog.VERSAO_ATUAL), 0)
        self.assertEqual(changelog.buscar("v" + changelog.VERSAO_ATUAL), 0)
        self.assertEqual(changelog.buscar("V2.0.0"), len(changelog.VERSOES) - 1)
        self.assertEqual(changelog.buscar(" 2.0 "), len(changelog.VERSOES) - 1)
        self.assertIsNone(changelog.buscar("9.9.9"))
        self.assertIsNone(changelog.buscar("2"))  # prefixo ambíguo (2.1.0 e 2.0.0)
        self.assertIsNone(changelog.buscar(""))
        self.assertIsNone(changelog.buscar("abc"))


class EmbedTests(unittest.TestCase):
    def test_estrutura_da_versao_mais_recente(self):
        entrada = changelog.VERSOES[0]
        embed = estilo.embed_changelog(entrada, 0, len(changelog.VERSOES))
        self.assertEqual(embed.title, f"📋 MeMi BOT · versão {entrada['versao']}")
        self.assertIn(entrada["titulo"], embed.description)
        nomes = [f.name for f in embed.fields]
        esperados = [estilo.SECOES_CHANGELOG[k] for k in changelog.SECOES if entrada.get(k)]
        self.assertEqual([n.replace(" (cont.)", "") for n in nomes], esperados)
        for campo in embed.fields:
            self.assertTrue(all(linha.startswith("• ") for linha in campo.value.split("\n")))
        self.assertIn(f"versão 1 de {len(changelog.VERSOES)}", embed.footer.text)

    def test_todas_as_versoes_cabem_nos_limites_do_discord(self):
        for i, entrada in enumerate(changelog.VERSOES):
            with self.subTest(entrada["versao"]):
                embed = estilo.embed_changelog(entrada, i, len(changelog.VERSOES))
                self.assertLessEqual(len(embed), estilo.LIMITE_EMBED)
                self.assertLessEqual(len(embed.fields), 25)
                for campo in embed.fields:
                    self.assertLessEqual(len(campo.value), estilo.LIMITE_CAMPO)

    def test_lista_longa_e_dividida_em_campos_continuados(self):
        entrada = {
            "versao": "9.9.9",
            "data": "01/01/2030",
            "titulo": "Teste",
            "novidades": [f"Item {i} " + "x" * 90 for i in range(30)],
        }
        embed = estilo.embed_changelog(entrada, 0, 1)
        self.assertGreater(len(embed.fields), 1)
        self.assertTrue(all(f.name.startswith("✨ Novidades") for f in embed.fields))
        self.assertIn("(cont.)", embed.fields[1].name)
        self.assertTrue(all(len(f.value) <= 1024 for f in embed.fields))
        itens = sum(len(f.value.split("\n")) for f in embed.fields)
        self.assertEqual(itens, 30)  # nenhum item se perde na divisão
        self.assertRegex(embed.footer.text, re.compile("MeMi BOT"))


if __name__ == "__main__":
    unittest.main()
