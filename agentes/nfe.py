"""Auditoria de NF-e de mercadoria (DANFE / XML modelo 55): chave, CNPJ, CFOP, alíquota interestadual, ICMS por item e totais."""
import xml.etree.ElementTree as ET

NS = {"n": "http://www.portalfiscal.inf.br/nfe"}
SUL_SUDESTE_SEM_ES = {"SP", "RJ", "MG", "PR", "SC", "RS"}


def brl(v):
    return "R$ " + f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _f(el, path):
    x = el.find(path, NS)
    return x.text.strip() if x is not None and x.text else None


def _n(el, path):
    v = _f(el, path)
    return float(v) if v else 0.0


def dv_chave(ch):
    pesos, s = [2, 3, 4, 5, 6, 7, 8, 9], 0
    for i, d in enumerate(reversed(ch[:43])):
        s += int(d) * pesos[i % 8]
    r = s % 11
    return 0 if r < 2 else 11 - r


def cnpj_ok(c):
    c = "".join(ch for ch in (c or "") if ch.isdigit())
    if len(c) != 14 or len(set(c)) == 1:
        return False
    for n in (12, 13):
        pesos = list(range(n - 7, 1, -1)) + list(range(9, 1, -1))
        s = sum(int(c[i]) * pesos[i] for i in range(n))
        if int(c[n]) != (0 if s % 11 < 2 else 11 - s % 11):
            return False
    return True


def aliq_interestadual(uf_o, uf_d, orig):
    if orig in ("1", "2", "3", "8"):
        return 4.0  # Resolução do Senado 13/2012 (importados)
    return 7.0 if uf_o in SUL_SUDESTE_SEM_ES and uf_d not in SUL_SUDESTE_SEM_ES else 12.0  # Resolução do Senado 22/1989


def eh_nfe(texto: str) -> bool:
    return "portalfiscal.inf.br/nfe" in texto[:3000] and "<infNFe" in texto


def auditar_nfe(texto: str, cnpj_empresa: str | None = None) -> dict:
    r = ET.fromstring(texto)
    inf = r.find(".//n:infNFe", NS)
    F = []
    add = lambda nivel, campo, msg, fonte="": F.append({"nivel": nivel, "campo": campo, "msg": msg, "fonte": fonte})
    chave = (inf.get("Id") or "").replace("NFe", "")
    uf_o, uf_d = _f(inf, "n:emit/n:enderEmit/n:UF"), _f(inf, "n:dest/n:enderDest/n:UF")
    id_dest, ind_ie = _f(inf, "n:ide/n:idDest"), _f(inf, "n:dest/n:indIEDest")
    emit, dest = _f(inf, "n:emit/n:CNPJ"), _f(inf, "n:dest/n:CNPJ")
    nota = {"origem": "NF-e (XML)", "numero": _f(inf, "n:ide/n:nNF"), "emissao": (_f(inf, "n:ide/n:dhEmi") or "")[:10],
            "prestador_cnpj": emit, "prestador_nome": _f(inf, "n:emit/n:xNome"), "tomador_cnpj": dest,
            "municipio": f"{uf_o} → {uf_d}", "valor_servicos": _n(inf, "n:total/n:ICMSTot/n:vNF"), "chave": chave}

    if len(chave) == 44 and chave.isdigit():
        if int(chave[-1]) == dv_chave(chave):
            add("ok", "Chave de acesso", "Dígito verificador da chave confere.", "Manual de Orientação do Contribuinte NF-e")
        else:
            add("erro", "Chave de acesso", "Dígito verificador da chave não confere.", "Manual de Orientação do Contribuinte NF-e")
    cst = _f(r, ".//n:protNFe/n:infProt/n:cStat")
    if cst == "100":
        add("ok", "Autorização", "NF-e autorizada pela SEFAZ (cStat 100).", "Protocolo de autorização")
    elif cst:
        add("erro", "Autorização", f"Situação {cst}: nota não autorizada ou cancelada. Consultar a chave na SEFAZ.", "Portal da NF-e")
    else:
        add("alerta", "Autorização", "XML sem protocolo de autorização (nfeProc).", "Portal da NF-e")
    for papel, c in (("Emitente", emit), ("Destinatário", dest)):
        if c and not cnpj_ok(c):
            add("erro", papel, f"CNPJ {c} com dígito verificador inválido.", "Receita Federal")
    if cnpj_empresa and dest and dest != "".join(ch for ch in cnpj_empresa if ch.isdigit()):
        add("alerta", "Destinatário", "Nota emitida para outro CNPJ, não para a empresa auditada.")

    soma_prod = soma_icms = 0.0
    cfops, erros_item = set(), 0
    for det in inf.findall("n:det", NS):
        n = det.get("nItem")
        cfop, ncm, orig = _f(det, "n:prod/n:CFOP"), _f(det, "n:prod/n:NCM"), _f(det, ".//n:ICMS/*/n:orig")
        cfops.add(cfop)
        soma_prod += _n(det, "n:prod/n:vProd")
        if not ncm or len(ncm) != 8:
            add("erro", f"Item {n}", f"NCM '{ncm}' inválido (precisa de 8 dígitos).", "Tabela NCM / TIPI")
            erros_item += 1
        if cfop and id_dest == "2" and not cfop.startswith("6"):
            add("erro", f"Item {n}", f"CFOP {cfop} é de operação interna, mas a nota é interestadual ({uf_o} → {uf_d}).", "Convênio s/nº de 1970 (tabela CFOP)")
        if cfop and id_dest == "1" and not cfop.startswith("5"):
            add("erro", f"Item {n}", f"CFOP {cfop} é interestadual, mas a nota é interna.", "Convênio s/nº de 1970 (tabela CFOP)")
        bc, p, v = _n(det, ".//n:ICMS/*/n:vBC"), _n(det, ".//n:ICMS/*/n:pICMS"), _n(det, ".//n:ICMS/*/n:vICMS")
        soma_icms += v
        if bc and p and abs(round(bc * p / 100, 2) - v) > 0.05:
            add("erro", f"Item {n}", f"ICMS {brl(v)} difere de base {brl(bc)} × {p:g}% = {brl(bc * p / 100)}.", "LC 87/1996, art. 13")
            erros_item += 1
        if p and id_dest == "2" and uf_o and uf_d:
            esp = aliq_interestadual(uf_o, uf_d, orig)
            if abs(p - esp) > 0.01:
                add("erro", f"Item {n}", f"Alíquota interestadual {p:g}% de {uf_o} para {uf_d}; o esperado é {esp:g}%.", "Resoluções do Senado 22/1989 e 13/2012")
                erros_item += 1
    if not erros_item:
        add("ok", "Itens", f"{len(inf.findall('n:det', NS))} item(ns): NCM, CFOP, base e valor do ICMS conferem.", "LC 87/1996")

    t = "n:total/n:ICMSTot/"
    if abs(soma_prod - _n(inf, t + "n:vProd")) > 0.05:
        add("erro", "Totais", f"Soma dos itens {brl(soma_prod)} difere do total de produtos {brl(_n(inf, t + 'n:vProd'))}.")
    vnf_calc = (_n(inf, t + "n:vProd") - _n(inf, t + "n:vDesc") + _n(inf, t + "n:vST") + _n(inf, t + "n:vFCPST") + _n(inf, t + "n:vFrete")
                + _n(inf, t + "n:vSeg") + _n(inf, t + "n:vOutro") + _n(inf, t + "n:vII") + _n(inf, t + "n:vIPI") + _n(inf, t + "n:vIPIDevol") - _n(inf, t + "n:vICMSDeson"))
    if abs(vnf_calc - _n(inf, t + "n:vNF")) > 0.05:
        add("erro", "Totais", f"Valor da nota {brl(_n(inf, t + 'n:vNF'))} difere da soma dos componentes {brl(vnf_calc)}.", "Manual de Orientação do Contribuinte NF-e")
    else:
        add("ok", "Totais", f"Valor da nota {brl(_n(inf, t + 'n:vNF'))} confere com produtos, descontos, frete e impostos.", "Manual de Orientação do Contribuinte NF-e")

    if id_dest == "2" and ind_ie == "9":
        if any(c in ("6102", "6101") for c in cfops):
            add("alerta", "DIFAL", f"Destinatário não contribuinte em operação interestadual com CFOP {', '.join(sorted(c for c in cfops if c in ('6101', '6102')))}: conferir se o certo é 6107/6108 e se o remetente recolheu o DIFAL para {uf_d}.", "EC 87/2015; LC 190/2022")
        else:
            add("info", "DIFAL", f"Venda interestadual a não contribuinte: o DIFAL é devido a {uf_d} pelo remetente.", "EC 87/2015; LC 190/2022")
    elif id_dest == "2" and ind_ie == "1":
        add("info", "DIFAL", f"Destinatário contribuinte: se o material for para uso e consumo ou ativo, o DIFAL é recolhido pelo destinatário em {uf_d}.", "LC 87/1996, art. 4º, §2º; LC 190/2022")

    nerr, nal = sum(f["nivel"] == "erro" for f in F), sum(f["nivel"] == "alerta" for f in F)
    ordem = {"erro": 0, "alerta": 1, "info": 2, "ok": 3}
    return {"nota": nota, "municipio": nota["municipio"], "achados": sorted(F, key=lambda f: ordem[f["nivel"]]), "erros": nerr, "alertas": nal,
            "decisao": "aprovar" if not nerr and not nal else "revisar" if not nerr else "devolver ao fornecedor", "tipo": "NF-e mercadoria"}
