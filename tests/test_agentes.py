"""Run: python -m pytest tests  (or python tests/test_agentes.py)"""
import pathlib
import sys

R = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(R / "agentes"))
import agentes  # noqa: E402
import ariba  # noqa: E402
import edital  # noqa: E402

EX = R / "web" / "exemplos"


def test_nfse_ok():
    assert agentes.coordenar("nota_ok.xml", (EX / "nota_ok.xml").read_bytes())["decisao"] == "aprovar"


def test_nfse_wrong_rate_returned_to_supplier():
    r = agentes.coordenar("nota_erro.xml", (EX / "nota_erro.xml").read_bytes())
    assert r["decisao"] == "devolver ao fornecedor" and r["email"]


def test_construction_pdf_flags_inss_and_stf_precedent():
    r = agentes.coordenar("nota_obra.pdf", (EX / "nota_obra.pdf").read_bytes())
    assert r["decisao"] == "revisar" and any("247" in j["numero"] for j in r["jurisprudencia"])


def test_ariba_event_pricing():
    r = ariba.preparar("evento_ariba.xlsx", (EX / "evento_ariba.xlsx").read_bytes())
    assert len(r["itens"]) == 3 and abs(r["total"] - 149131.22) < 0.05


def test_tender_analysis():
    a = edital.analisar("edital_exemplo.pdf", (EX / "edital_exemplo.pdf").read_bytes())
    assert a["cabecalho"]["numero"] == "045/2026" and len(a["exigencias"]) >= 6


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
            print("OK", n)
