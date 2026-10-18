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


# Cesta Básica Nacional com redução de 100% de IBS/CBS (LC 214/2025, art. 125 e Anexo I) - prefixos de NCM
CESTA = ("1006", "0713", "040110", "040120", "040210", "040221", "0405", "0406", "1101", "1102", "1103", "1104", "1902", "2101", "1507", "0201", "0202", "0203",
         "0204", "0206", "0207", "0209", "0210", "0302", "0303", "0304", "2501", "1701", "0901", "0409")
# PIS/COFINS monofásico (Leis 10.147/2000, 10.485/2002, 13.097/2015)
MONO = ("2201", "2202", "2203", "2204", "2205", "2206", "2207", "2208", "3003", "3004", "3303", "3304", "3305", "3306", "3307", "3401", "2710", "4011")
# PIS/COFINS alíquota zero da cesta básica (Lei 10.925/2004, art. 1º)
ZERO = ("0201", "0202", "0203", "0204", "0206", "0207", "0210", "0302", "0303", "0304", "040110", "040120", "040210", "040221", "0406", "0407", "0713", "1006", "1507",
        "2101", "0901", "0701", "0702", "0703", "0704", "0705", "0706", "0707", "0708", "0709", "0803", "0804", "0805", "0806", "0807", "0808", "4818", "1902", "1101")
CST_ST = {"10", "30", "60", "70", "201", "202", "203", "500"}
CRT = {"1": "Simples Nacional", "2": "Simples Nacional com excesso de sublimite", "3": "regime normal", "4": "MEI"}


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
    classes, st_val, mono_val, sem_benef, itens_ficha = {}, 0.0, 0.0, [], []
    add("info", "Emitente", f"Fornecedor do {CRT.get(_f(inf, 'n:emit/n:CRT') or '', 'regime não informado')} (CRT {_f(inf, 'n:emit/n:CRT')}).", "Manual de Orientação do Contribuinte NF-e")
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
        nome = (_f(det, "n:prod/n:xProd") or "")[:40]
        cst_i = _f(det, ".//n:ICMS/*/n:CST") or _f(det, ".//n:ICMS/*/n:CSOSN") or ""
        if cfop in ("5405", "6404", "5403", "6403") and cst_i not in CST_ST:
            add("erro", f"Item {n} · {nome}", f"CFOP {cfop} (mercadoria com substituição tributária) com CST {cst_i}; o esperado é CST 60 (ou 10/70).", "Ajuste SINIEF 07/2005 (tabela CST) e tabela CFOP")
        if cst_i == "60" and cfop in ("5102", "6102"):
            add("erro", f"Item {n} · {nome}", f"CST 60 (ICMS já retido por ST) com CFOP {cfop}; o esperado é 5405/6404.", "Tabela CFOP")
        if cst_i in CST_ST and not _f(det, "n:prod/n:CEST"):
            add("erro", f"Item {n} · {nome}", "Mercadoria com substituição tributária sem o código CEST.", "Convênio ICMS 142/2018, cláusula 3ª")
        if cst_i in ("20", "30", "40", "41", "50", "51", "70", "90") and not _f(det, "n:prod/n:cBenef"):
            sem_benef.append(f"{n} ({nome.split()[0] if nome else ''}, CST {cst_i})")
        pst = _f(det, ".//n:PIS/*/n:CST") or ""
        pb, pp, pv = _n(det, ".//n:PIS/*/n:vBC"), _n(det, ".//n:PIS/*/n:pPIS"), _n(det, ".//n:PIS/*/n:vPIS")
        if pb and pp and abs(round(pb * pp / 100, 2) - pv) > 0.05:
            add("erro", f"Item {n} · {nome}", f"PIS {brl(pv)} difere de base {brl(pb)} × {pp:g}%.", "Leis 10.637/2002 e 9.718/1998")
        cb, cp, cv = _n(det, ".//n:COFINS/*/n:vBC"), _n(det, ".//n:COFINS/*/n:pCOFINS"), _n(det, ".//n:COFINS/*/n:vCOFINS")
        if cb and cp and abs(round(cb * cp / 100, 2) - cv) > 0.05:
            add("erro", f"Item {n} · {nome}", f"COFINS {brl(cv)} difere de base {brl(cb)} × {cp:g}%.", "Leis 10.833/2003 e 9.718/1998")
        nc = ncm or ""
        if pst in ("01", "02") and nc.startswith(MONO):
            add("alerta", f"Item {n} · {nome}", f"NCM {nc} costuma ter PIS/COFINS monofásico (CST 04 na revenda); veio CST {pst}.", "Leis 10.147/2000, 10.485/2002 e 13.097/2015")
        elif pst == "06" and nc.startswith(MONO) and not nc.startswith("4818"):
            add("alerta", f"Item {n} · {nome}", f"NCM {nc} veio com alíquota zero (CST 06), mas costuma ser monofásico (CST 04).", "Lei 10.147/2000")
        elif pst == "01" and nc.startswith(ZERO):
            add("alerta", f"Item {n} · {nome}", f"NCM {nc} pode ter PIS/COFINS com alíquota zero da cesta básica; veio tributado (CST 01).", "Lei 10.925/2004, art. 1º")
        ib = det.find(".//n:IBSCBS", NS)
        cct = _f(det, ".//n:IBSCBS/n:cClassTrib")
        if ib is not None and cct and nc.startswith(CESTA) and cct != "200003":
            add("alerta", f"Item {n} · {nome}", f"NCM {nc} parece Cesta Básica Nacional (redução de 100%, cClassTrib 200003); veio {cct}.", "LC 214/2025, art. 125 e Anexo I")
        chave_p = nc
        classes.setdefault(chave_p, set()).add(f"ICMS {cst_i} · IBS/CBS {cct or '-'}")
        st_val += _n(det, "n:prod/n:vProd") if cst_i in CST_ST else 0
        mono_val += _n(det, "n:prod/n:vProd") if pst == "04" else 0
        bc, p, v = _n(det, ".//n:ICMS/*/n:vBC"), _n(det, ".//n:ICMS/*/n:pICMS"), _n(det, ".//n:ICMS/*/n:vICMS")
        antes = len(F)
        itens_ficha.append({"n": n, "produto": nome, "ncm": nc, "cfop": cfop, "cst": cst_i, "vprod": _n(det, "n:prod/n:vProd"),
                            "bc_icms": bc, "p_icms": p, "icms": v, "st": _n(det, ".//n:ICMS/*/n:vICMSSTRet") or _n(det, ".//n:ICMS/*/n:vICMSST"),
                            "pis_cst": pst, "pis": pv, "cofins": cv, "cclass": cct or "", "ibs": _n(det, ".//n:IBSCBS//n:vIBS"), "cbs": _n(det, ".//n:IBSCBS//n:vCBS"),
                            "obs": [f["msg"] for f in F if f["campo"].startswith(f"Item {n} ")]})
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

    for k, v in classes.items():
        if len(v) > 1:
            add("alerta", "Classificação", f"Produtos com o mesmo NCM {k} e tributação diferente na mesma nota: {' e '.join(sorted(v))}. Um dos dois pode estar errado.", "Coerência de cadastro fiscal")
    if sem_benef:
        add("alerta", "cBenef", f"{len(sem_benef)} item(ns) com isenção ou redução de ICMS sem o código do benefício fiscal (cBenef). Verifique se a SEFAZ de {uf_o} exige: itens " + ", ".join(sem_benef[:8]) + ("…" if len(sem_benef) > 8 else "") + ".", "Ajuste SINIEF 16/2018 e norma estadual")
    add("info", "Comprador do Simples Nacional", "Se a compradora é do Simples, ICMS, PIS e COFINS destacados não geram crédito e entram no custo (LC 123/2006, art. 23)."
        + (f" Na revenda, segregue no PGDAS-D a receita de mercadorias com ICMS-ST ({brl(st_val)} nesta nota)" if st_val else "")
        + (f" e de monofásicos de PIS/COFINS ({brl(mono_val)})" if mono_val else "")
        + (". Restaurante que prepara refeições não segrega os ingredientes." if (st_val or mono_val) else ""), "LC 123/2006, arts. 18, §4º-A, e 23")
    nota["assinatura"] = (emit, round(_n(inf, "n:total/n:ICMSTot/n:vNF"), 2), tuple((_f(d, "n:prod/n:xProd"), _f(d, "n:prod/n:vProd")) for d in inf.findall("n:det", NS)))
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

    t = "n:total/n:ICMSTot/"
    def soma(path):
        return round(sum(_n(d, path) for d in inf.findall("n:det", NS)), 2)
    def recalc(vb, pa):
        return round(sum(round(_n(d, vb) * _n(d, pa) / 100, 2) for d in inf.findall("n:det", NS)), 2)
    T = lambda k: _n(inf, t + k)
    tib = inf.find("n:total/n:IBSCBSTot", NS)
    ficha = []
    LEI = {"ICMS próprio": "LC 87/1996, arts. 12 e 13; regulamento do ICMS do estado", "FCP": "ADCT, arts. 82 e 83; lei estadual do Fundo de Combate à Pobreza",
           "ICMS-ST": "LC 87/1996, arts. 6º a 10; Convênio ICMS 142/2018", "IPI": "Decreto 7.212/2010 (RIPI); TIPI (Decreto 11.158/2022)",
           "PIS": "Leis 10.637/2002 e 9.718/1998; Lei 10.925/2004 (alíquota zero); STF Tema 69", "COFINS": "Leis 10.833/2003 e 9.718/1998; Lei 10.925/2004; STF Tema 69",
           "IBS (2026)": "LC 214/2025, arts. 125 (cesta básica) e 343 a 346", "CBS (2026)": "LC 214/2025, arts. 125 e 343 a 346"}

    def lin(trib, regra, esp, nota_v):
        ficha.append({"tributo": trib, "regra": regra, "aliquota": "por item", "esperado": esp, "nota": nota_v, "lei": LEI.get(trib, ""),
                      "situacao": "confere" if esp is not None and nota_v is not None and abs(esp - nota_v) < 0.05 else "conferir"})
    lin("ICMS próprio", "Base × alíquota de cada item (LC 87/1996, art. 13)", recalc(".//n:ICMS/*/n:vBC", ".//n:ICMS/*/n:pICMS"), T("n:vICMS"))
    lin("FCP", "Fundo de Combate à Pobreza do estado", recalc(".//n:ICMS/*/n:vBC", ".//n:ICMS/*/n:pFCP"), T("n:vFCP"))
    lin("ICMS-ST", "Substituição tributária retida nesta nota", soma(".//n:ICMS/*/n:vICMSST"), T("n:vST"))
    lin("IPI", "Base × alíquota da TIPI", recalc(".//n:IPI/*/n:vBC", ".//n:IPI/*/n:pIPI"), T("n:vIPI"))
    lin("PIS", "Base (sem ICMS) × alíquota; monofásico e alíquota zero sem valor", recalc(".//n:PIS/*/n:vBC", ".//n:PIS/*/n:pPIS"), T("n:vPIS"))
    lin("COFINS", "Base (sem ICMS) × alíquota", recalc(".//n:COFINS/*/n:vBC", ".//n:COFINS/*/n:pCOFINS"), T("n:vCOFINS"))
    if tib is not None:
        lin("IBS (2026)", "0,1% ano-teste; cesta básica com redução", soma(".//n:IBSCBS//n:vIBS"), _n(tib, ".//n:gIBS/n:vIBS"))
        lin("CBS (2026)", "0,9% ano-teste; cesta básica com redução", soma(".//n:IBSCBS//n:vCBS"), _n(tib, ".//n:gCBS/n:vCBS"))
    nerr, nal = sum(f["nivel"] == "erro" for f in F), sum(f["nivel"] == "alerta" for f in F)
    ordem = {"erro": 0, "alerta": 1, "info": 2, "ok": 3}
    for it in itens_ficha:
        it["obs"] += [f["msg"] for f in F if f["campo"] == "Classificação" and f"NCM {it['ncm']} " in f["msg"]]
    return {"ficha": ficha, "itens": itens_ficha, "nota": nota, "municipio": nota["municipio"], "achados": sorted(F, key=lambda f: ordem[f["nivel"]]), "erros": nerr, "alertas": nal,
            "decisao": "aprovar" if not nerr and not nal else "revisar" if not nerr else "devolver ao fornecedor", "tipo": "NF-e mercadoria"}
