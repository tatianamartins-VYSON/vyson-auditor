"""Prova com erros plantados: gera notas fictícias corretas e com UM erro conhecido, roda o Auditor
(só as regras, sem rede e sem modelo) e mede quantos erros foram pegos e quantos alarmes falsos houve.

    python avaliacao_erros/plantar.py [pasta_dos_agentes]

Os dados são inventados (CNPJ e TRN fictícios). A semente é fixa e o conjunto é congelado com SHA-256.
"""
import hashlib, json, random, sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent
AG = Path(sys.argv[1]) if len(sys.argv) > 1 else AQUI.parent / "agentes"
sys.path.insert(0, str(AG))
import agentes  # noqa: E402
import internacional as I  # noqa: E402

agentes.ag_cnpj = lambda s, **_: {"pulado": "sem rede na prova"}
agentes.AGENTES["cnpj"] = agentes.ag_cnpj
random.seed(2026)
BASE = AG.parent / "data" if (AG.parent / "data" / "maceio.json").exists() else AG.parent / "public" / "fiscal"
MCZ = [x for x in json.loads((BASE / "maceio.json").read_text(encoding="utf-8"))["itens"] if not x.get("fim") and x.get("a")]


def nfse(n, item, aliq, iss, base=None):
    base = base or 8000.0
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<CompNfse><Nfse><InfNfse><Numero>{n}</Numero><DataEmissao>2026-10-12</DataEmissao>
<PrestadorServico><IdentificacaoPrestador><Cnpj>11444777000161</Cnpj></IdentificacaoPrestador><RazaoSocial>Prestador Fictício {n} Ltda</RazaoSocial></PrestadorServico>
<Servico><Valores><ValorServicos>{base:.2f}</ValorServicos><IssRetido>2</IssRetido><ValorIss>{iss:.2f}</ValorIss><Aliquota>{aliq / 100:.4f}</Aliquota></Valores>
<ItemListaServico>{item['i']}</ItemListaServico><CodigoTributacaoMunicipio>{item['c']}</CodigoTributacaoMunicipio><Discriminacao>{item['d'][:120]}</Discriminacao><CodigoMunicipio>2704302</CodigoMunicipio></Servico>
</InfNfse></Nfse></CompNfse>""".encode()


def casos():
    out = []
    for k, it in enumerate(random.sample(MCZ, 200)):
        a, base = it["a"], random.choice([1500.0, 4000.0, 8000.0, 23500.0])
        tipo = ["ok", "aliquota", "valor_iss", "item_trocado"][k % 4]
        if tipo == "ok":
            out.append(("nfse", tipo, f"{3000 + k}.xml", nfse(3000 + k, it, a, base * a / 100, base)))
        elif tipo == "aliquota":
            b = random.choice([x for x in (2.0, 3.0, 4.0, 5.0) if abs(x - a) > 0.01])
            out.append(("nfse", tipo, f"{3000 + k}.xml", nfse(3000 + k, it, b, base * b / 100, base)))
        elif tipo == "valor_iss":
            out.append(("nfse", tipo, f"{3000 + k}.xml", nfse(3000 + k, it, a, base * a / 100 * random.choice([0.5, 0.9, 1.1, 2]), base)))
        else:  # item e código de outro grupo da LC 116, com a descrição do serviço original
            outro = random.choice([x for x in MCZ if x["i"].split(".")[0] != it["i"].split(".")[0] and abs(x["a"] - a) < 0.01] or [it])
            out.append(("nfse", tipo, f"{3000 + k}.xml", nfse(3000 + k, {**outro, "d": it["d"]}, a, base * a / 100, base)))
    ok = (AQUI.parent / "web" / "exemplos" / "fatura_dubai_ok.xml").read_text(encoding="utf-8")
    muta = {"vat": lambda t: t.replace('<cbc:TaxAmount currencyID="AED">1000.00</cbc:TaxAmount>', '<cbc:TaxAmount currencyID="AED">800.00</cbc:TaxAmount>'),
            "trn": lambda t: t.replace("<cbc:CompanyID>100123456700003</cbc:CompanyID>", "<cbc:CompanyID>20012345670003</cbc:CompanyID>"),
            "linha": lambda t: t.replace('<cbc:LineExtensionAmount currencyID="AED">15000.00', '<cbc:LineExtensionAmount currencyID="AED">16000.00'),
            "endereco": lambda t: t.split("<cac:PostalAddress>")[0] + t.split("</cac:PostalAddress>")[1],
            "categoria": lambda t: t.replace("<cbc:Percent>5</cbc:Percent><cac:TaxScheme><cbc:ID>VAT</cbc:ID></cac:TaxScheme></cac:TaxCategory></cac:TaxSubtotal>",
                                             "<cbc:Percent>10</cbc:Percent><cac:TaxScheme><cbc:ID>VAT</cbc:ID></cac:TaxScheme></cac:TaxCategory></cac:TaxSubtotal>")}
    for k in range(60):
        tipo = ["ok"] + list(muta)
        tipo = tipo[k % len(tipo)]
        t = ok.replace("INV-2026-0451", f"INV-2026-{1000 + k}")
        out.append(("uae", "uae_" + tipo, f"INV-{1000 + k}.xml", (muta[tipo](t) if tipo != "ok" else t).encode()))
    return out


ESPERADO = {"item_trocado": "tem", "aliquota": "Alíquota", "valor_iss": "Valor ISS", "uae_vat": "Valor do VAT", "uae_trn": "TRN", "uae_linha": "Item",
            "uae_endereco": "Endereço", "uae_categoria": "Alíquota VAT"}


def main():
    cs = casos()
    h = hashlib.sha256(b"".join(c[3] for c in cs)).hexdigest()
    res = {}
    for _, tipo, nome, dados in cs:
        r = agentes.coordenar(nome, dados)
        erros = [a for a in r["achados"] if a["nivel"] == "erro"]
        if tipo.endswith("ok"):
            ok = not erros
        else:
            ok = r["decisao"] != "aprovar" and any(ESPERADO[tipo] in a["campo"].lower() or ESPERADO[tipo] in a["campo"] for a in r["achados"] if a["nivel"] in ("erro", "alerta"))
        d = res.setdefault(tipo, [0, 0])
        d[0] += ok; d[1] += 1
    planted = {k: v for k, v in res.items() if not k.endswith("ok")}
    pegos, total = sum(v[0] for v in planted.values()), sum(v[1] for v in planted.values())
    limpos = [v for k, v in res.items() if k.endswith("ok")]
    sem_falso, n_ok = sum(v[0] for v in limpos), sum(v[1] for v in limpos)
    rel = {"conjunto_sha256": h, "notas": len(cs), "erros_plantados": total, "erros_pegos": pegos, "taxa_pegos": round(pegos / total, 4),
           "notas_corretas": n_ok, "corretas_sem_erro_falso": sem_falso, "por_tipo": {k: f"{v[0]}/{v[1]}" for k, v in res.items()}}
    (AQUI / "relatorio.json").write_text(json.dumps(rel, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(rel, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
