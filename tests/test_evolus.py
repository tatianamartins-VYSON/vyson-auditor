"""Mascaramento antes de enviar ao Evolus e modo desligado sem chave."""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "agentes"))
import evolus  # noqa: E402


class Mascara(unittest.TestCase):
    def test_troca_cnpj_cpf_email_nome(self):
        t, rec = evolus.mascarar("ACME SERVICOS LTDA CNPJ 12.345.678/0001-90, sócio CPF 123.456.789-09, fiscal@acme.com.br; ACME SERVICOS LTDA", ["ACME SERVICOS LTDA"])
        for dado in ("12.345.678/0001-90", "123.456.789-09", "fiscal@acme.com.br", "ACME"):
            self.assertNotIn(dado, t)
        self.assertEqual(t.count("[NOME_1]"), 2)
        self.assertEqual(set(rec), {"[NOME_1]", "[CNPJ_1]", "[CPF_1]", "[EMAIL_1]"})
        self.assertEqual(len(rec["[CNPJ_1]"]), 64)

    def test_desligado_sem_chave(self):
        self.assertFalse(evolus.ligado())
        self.assertIsNone(evolus.encaminhar({"nota": {}, "achados": []}))


if __name__ == "__main__":
    unittest.main()
