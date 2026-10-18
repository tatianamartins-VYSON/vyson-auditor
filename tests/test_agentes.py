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
    # Correct 2025-style NFS-e: no errors; the only alert is the 2026 tax-reform fields (NBS, cClassTrib, IBS/CBS) now required.
    r = agentes.coordenar("nota_ok.xml", (EX / "nota_ok.xml").read_bytes())
    alertas = [x["campo"] for x in r["achados"] if x["nivel"] == "alerta"]
    assert r["erros"] == 0 and alertas == ["Reforma tributária"]


def test_italy_fatturapa():
    ok = agentes.coordenar("fattura_italia_ok.xml", (EX / "fattura_italia_ok.xml").read_bytes())
    bad = agentes.coordenar("fattura_italia_erro.xml", (EX / "fattura_italia_erro.xml").read_bytes())
    assert ok["erros"] == 0 and bad["decisao"] == "devolver ao fornecedor"


def test_single_model_call_with_citation_lock(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "http://x")
    monkeypatch.setenv("LLM_MODEL", "Gemma-4-31B-IT")
    calls = []
    monkeypatch.setattr(agentes, "_llm", lambda m, e=60: calls.append(1) or {"content": "Devolver [Lei 99.999/2099]."})
    r = agentes.coordenar("nota_erro.xml", (EX / "nota_erro.xml").read_bytes())
    assert len(calls) == 1 and r["modo"] == "regras" and "trava" in r["aviso_llm"]


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
