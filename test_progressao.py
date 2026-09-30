"""Testes da progressão: XP, níveis de 1 a 100 e patentes."""

import unittest

import progressao as p


class XpTests(unittest.TestCase):
    def test_xp_combina_mensagens_musicas_e_roletadas(self):
        self.assertEqual(p.xp(0, 0, 0), 0)
        self.assertEqual(p.xp(10, 0, 0), 10)
        self.assertEqual(p.xp(0, 2, 0), 50)  # 25 por música pedida
        self.assertEqual(p.xp(0, 0, 5), 2)  # meia por roletada, arredondada para baixo
        self.assertEqual(p.xp(75000, 384, 51401), 75000 + 9600 + 25700)

    def test_xp_ignora_valores_negativos(self):
        self.assertEqual(p.xp(-5, -1, -9), 0)


class NivelTests(unittest.TestCase):
    def test_xp_minimo_de_cada_nivel(self):
        self.assertEqual(p.xp_minimo(1), 0)
        self.assertEqual(p.xp_minimo(2), 20)
        self.assertEqual(p.xp_minimo(10), 1620)
        self.assertEqual(p.xp_minimo(100), 196020)

    def test_nivel_em_cada_limite(self):
        for n in range(1, 101):
            with self.subTest(n=n):
                self.assertEqual(p.nivel(p.xp_minimo(n)), n)
                if n > 1:
                    self.assertEqual(p.nivel(p.xp_minimo(n) - 1), n - 1)

    def test_nivel_maximo_e_100(self):
        self.assertEqual(p.nivel(10**9), 100)
        self.assertEqual(p.nivel(-10), 1)

    def test_progresso_dentro_do_nivel(self):
        self.assertEqual(p.progresso(0), (1, 0, 20))
        self.assertEqual(p.progresso(1630), (10, 10, 380))
        self.assertEqual(p.progresso(p.xp_minimo(100)), (100, 1, 1))
        self.assertEqual(p.progresso(10**9), (100, 1, 1))

    def test_subir_fica_mais_lento_a_cada_nivel(self):
        custos = [p.xp_minimo(n + 1) - p.xp_minimo(n) for n in range(1, 100)]
        self.assertEqual(custos, sorted(custos))
        self.assertLess(custos[0], custos[-1])


class PatenteTests(unittest.TestCase):
    def test_onze_patentes_em_ordem(self):
        self.assertEqual(len(p.PATENTES), 11)
        minimos = [x["minimo"] for x in p.PATENTES]
        self.assertEqual(minimos, [1, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100])
        self.assertEqual(len({x["id"] for x in p.PATENTES}), 11)

    def test_patente_de_cada_nivel(self):
        self.assertEqual(p.patente(1)["nome"], "Figurante")
        self.assertEqual(p.patente(9)["nome"], "Figurante")
        self.assertEqual(p.patente(10)["nome"], "Ouvinte da Call")
        self.assertEqual(p.patente(99)["nome"], "Divindade")
        self.assertEqual(p.patente(100)["nome"], "Demiurgo Supremo")
        self.assertEqual(p.patente(0)["nome"], "Figurante")
        self.assertEqual(p.patente(500)["nome"], "Demiurgo Supremo")

    def test_troca_de_patente(self):
        self.assertTrue(p.trocou_patente(10))
        self.assertTrue(p.trocou_patente(100))
        self.assertFalse(p.trocou_patente(11))
        self.assertFalse(p.trocou_patente(1))

    def test_patentes_alcancadas(self):
        self.assertEqual([x["id"] for x in p.patentes_ate(1)], ["figurante"])
        self.assertEqual(len(p.patentes_ate(35)), 4)
        self.assertEqual(len(p.patentes_ate(100)), 11)

    def test_toda_patente_tem_cor_rgb(self):
        for x in p.PATENTES:
            self.assertTrue(0 <= x["cor"] <= 0xFFFFFF)


if __name__ == "__main__":
    unittest.main()
