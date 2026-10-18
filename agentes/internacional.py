"""Agentes internacionais: auditoria de faturas dos Emirados Árabes (VAT, Dubai) e da Índia (GST).

Emirados (Federal Decree-Law 8/2017 e Executive Regulation, Cabinet Decision 52/2017):
  - fatura eletrônica no padrão PINT AE (UBL 2.1), base do e-invoicing dos Emirados
  - TRN de 15 dígitos; VAT padrão de 5% (categoria S), alíquota zero (Z: exportação, transporte internacional),
    isento (E: serviços financeiros, imóvel residencial); valor do imposto em AED; campos obrigatórios da fatura (art. 59)
Índia (CGST Act 2017, IGST Act 2017, CGST Rules, regra 46):
  - e-invoice no formato JSON do IRP (INV-01)
  - GSTIN de 15 caracteres com dígito verificador; operação dentro do estado = CGST + SGST, entre estados = IGST
  - alíquotas após a reforma GST 2.0 (22/09/2025): 0%, 5%, 18% e 40% (0,25% e 3% para pedras e metais preciosos)
"""
import json
import re
import xml.etree.ElementTree as ET
from datetime import date

UBL = {"cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
       "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"}
CAT_UAE = {"AE": (None, "Inversão do contribuinte (reverse charge): o VAT é declarado pelo comprador"), "S": (5.0, "Taxa padrão de 5%"), "Z": (0.0, "Alíquota zero (exportação, transporte internacional, saúde e educação qualificadas)"),
           "E": (None, "Isento (serviços financeiros, imóvel residencial, transporte local)"), "O": (None, "Fora do escopo do VAT")}
UF_IN = {"27": "Maharashtra", "29": "Karnataka", "07": "Delhi", "09": "Uttar Pradesh", "33": "Tamil Nadu", "24": "Gujarat", "36": "Telangana", "19": "West Bengal"}
GST_NOVAS = {0, 0.25, 3, 5, 18, 40}
GST_ANTIGAS = {0, 0.25, 3, 5, 12, 18, 28}
FONTE_UAE = "Federal Decree-Law 8/2017 (VAT) e Executive Regulation, art. 59"
FONTE_IN = "CGST Act 2017, IGST Act 2017 e CGST Rules, regra 46"


def _r(x):
    return round(float(x or 0), 2)


# ---------------- Emirados Árabes ----------------
def eh_uae(texto):
    return "urn:oasis:names:specification:ubl" in texto[:4000] and ("AED" in texto or "pint_ae" in texto.lower() or "TRN" in texto)


def auditar_uae(texto):
    r = ET.fromstring(texto)
    g = lambda p: (r.find(p, UBL).text.strip() if r.find(p, UBL) is not None and r.find(p, UBL).text else None)
    F = []
    add = lambda n, c, m, f=FONTE_UAE: F.append({"nivel": n, "campo": c, "msg": m, "fonte": f})
    trn_v = g("cac:AccountingSupplierParty/cac:Party/cac:PartyTaxScheme/cbc:CompanyID")
    trn_c = g("cac:AccountingCustomerParty/cac:Party/cac:PartyTaxScheme/cbc:CompanyID")
    moeda = g("cbc:DocumentCurrencyCode")
    nota = {"origem": "Fatura eletrônica UAE (UBL/PINT AE)", "numero": g("cbc:ID"), "emissao": g("cbc:IssueDate"),
            "prestador_cnpj": trn_v, "prestador_nome": g("cac:AccountingSupplierParty/cac:Party/cac:PartyLegalEntity/cbc:RegistrationName"),
            "tomador_cnpj": trn_c, "municipio": "Dubai, Emirados Árabes", "valor_servicos": _r(g("cac:LegalMonetaryTotal/cbc:TaxExclusiveAmount"))}
    for papel, t in (("Fornecedor", trn_v), ("Cliente", trn_c)):
        if not t:
            if papel == "Fornecedor":
                add("erro", "TRN", "Fatura sem o TRN do fornecedor: obrigatório na fatura fiscal (tax invoice).")
        elif not re.fullmatch(r"100\d{12}", t):
            add("erro", "TRN", f"TRN do {papel.lower()} '{t}' inválido: são 15 dígitos começando por 100.", "Federal Tax Authority (FTA)")
        else:
            add("ok", "TRN", f"TRN do {papel.lower()} {t} no formato da FTA.", "Federal Tax Authority (FTA)")
    if moeda and moeda != "AED" and not r.find("cbc:TaxCurrencyCode", UBL) is not None:
        add("erro", "Moeda", f"Fatura em {moeda} sem o valor do VAT convertido para AED.", "Executive Regulation, art. 59(1)(k)")
    base_tot = imp_tot = 0.0
    for st in r.findall("cac:TaxTotal/cac:TaxSubtotal", UBL):
        base = _r(st.findtext("cbc:TaxableAmount", namespaces=UBL))
        imp = _r(st.findtext("cbc:TaxAmount", namespaces=UBL))
        cat = (st.findtext("cac:TaxCategory/cbc:ID", namespaces=UBL) or "").strip()
        pct = st.findtext("cac:TaxCategory/cbc:Percent", namespaces=UBL)
        pct = float(pct) if pct else None
        base_tot += base
        imp_tot += imp
        if cat not in CAT_UAE:
            add("erro", "Categoria", f"Categoria de VAT '{cat}' desconhecida (use S, Z, E ou O).")
            continue
        esp, desc = CAT_UAE[cat]
        if esp is not None and pct is not None and abs(pct - esp) > 0.001:
            add("erro", "Alíquota VAT", f"Categoria {cat} ({desc}) exige {esp:g}%, mas a fatura usa {pct:g}%.", "Federal Decree-Law 8/2017, arts. 3 e 45")
        elif esp is not None:
            add("ok", "Alíquota VAT", f"Categoria {cat}: {desc}.", "Federal Decree-Law 8/2017, arts. 3 e 45")
        else:
            add("info", "Alíquota VAT", f"Categoria {cat}: {desc}. Conferir se a operação se enquadra.", "Federal Decree-Law 8/2017, art. 46")
        if esp is not None and abs(base * esp / 100 - imp) > 0.05:
            add("erro", "Valor do VAT", f"VAT de AED {imp:,.2f} difere de AED {base:,.2f} × {esp:g}% = AED {base * esp / 100:,.2f}.")
        elif esp:
            add("ok", "Valor do VAT", f"VAT de AED {imp:,.2f} = AED {base:,.2f} × {esp:g}%.")
        if cat == "Z" and not re.search(r"export|outside the uae|designated zone", texto, re.I):
            add("alerta", "Alíquota zero", "Alíquota zero sem indicação de exportação ou destino fora dos Emirados: guarde a prova de exportação.", "Executive Regulation, art. 30")
    tot = _r(g("cac:TaxTotal/cbc:TaxAmount"))
    if tot and abs(tot - imp_tot) > 0.05:
        add("erro", "Totais", f"Total de VAT AED {tot:,.2f} difere da soma das categorias AED {imp_tot:,.2f}.")
    payable = _r(g("cac:LegalMonetaryTotal/cbc:TaxInclusiveAmount"))
    if payable and abs(payable - (base_tot + imp_tot)) > 0.05:
        add("erro", "Totais", f"Valor com VAT AED {payable:,.2f} difere de base + VAT = AED {base_tot + imp_tot:,.2f}.")
    elif payable:
        add("ok", "Totais", f"Total com VAT AED {payable:,.2f} confere.")
    for campo, caminho in (("Número da fatura", "cbc:ID"), ("Data", "cbc:IssueDate"), ("Nome do fornecedor", "cac:AccountingSupplierParty/cac:Party/cac:PartyLegalEntity/cbc:RegistrationName")):
        if not g(caminho):
            add("erro", campo, f"{campo} ausente: campo obrigatório da fatura fiscal.", "Executive Regulation, art. 59")
    # ---- campos obrigatórios da fatura fiscal (Executive Regulation, art. 59(1)) ----
    if not re.search(r"tax invoice", texto, re.I) and any(st.findtext("cac:TaxCategory/cbc:ID", namespaces=UBL) == "S" for st in r.findall("cac:TaxTotal/cac:TaxSubtotal", UBL)):
        add("alerta", "Título", "A fatura com VAT de 5% deve trazer a expressão 'Tax Invoice' em destaque.", "Executive Regulation, art. 59(1)(a)")
    if r.find("cac:AccountingSupplierParty/cac:Party/cac:PostalAddress", UBL) is None:
        add("erro", "Endereço do fornecedor", "Fatura sem o endereço do fornecedor: campo obrigatório da fatura fiscal.", "Executive Regulation, art. 59(1)(b)")
    linhas = r.findall("cac:InvoiceLine", UBL)
    if not linhas:
        add("alerta", "Itens", "Fatura sem linhas de itens: a fatura fiscal deve descrever cada bem ou serviço, com quantidade, preço unitário e VAT.", "Executive Regulation, art. 59(1)(f)-(j)")
    for k, ln in enumerate(linhas, 1):
        nome = ln.findtext("cac:Item/cbc:Name", namespaces=UBL)
        qtd, preco, val = (_r(ln.findtext(x, namespaces=UBL)) for x in ("cbc:InvoicedQuantity", "cac:Price/cbc:PriceAmount", "cbc:LineExtensionAmount"))
        if not nome:
            add("erro", f"Item {k}", "Item sem descrição do bem ou serviço.", "Executive Regulation, art. 59(1)(f)")
        if qtd and preco and abs(qtd * preco - val) > 0.05:
            add("erro", f"Item {k}", f"Valor da linha AED {val:,.2f} difere de {qtd:g} × AED {preco:,.2f} = AED {qtd * preco:,.2f}.", "Executive Regulation, art. 59(1)(g)-(i)")
    # ---- prazo de emissão: 14 dias da data do fornecimento ----
    entrega = g("cac:Delivery/cbc:ActualDeliveryDate")
    try:
        if entrega and g("cbc:IssueDate") and (date.fromisoformat(g("cbc:IssueDate")) - date.fromisoformat(entrega)).days > 14:
            add("alerta", "Prazo", f"Fatura emitida mais de 14 dias depois do fornecimento ({entrega}).", "Federal Decree-Law 8/2017, art. 67")
    except ValueError:
        pass
    # ---- fatura simplificada e reverse charge ----
    if (payable or 0) and payable <= 10000 and not trn_c:
        add("info", "Fatura simplificada", "Total até AED 10.000 e cliente sem TRN: pode ser emitida fatura simplificada (simplified tax invoice).", "Executive Regulation, art. 60")
    if any(st.findtext("cac:TaxCategory/cbc:ID", namespaces=UBL) == "AE" for st in r.findall("cac:TaxTotal/cac:TaxSubtotal", UBL)) and not re.search(r"reverse charge", texto, re.I):
        add("erro", "Reverse charge", "Categoria AE sem a menção 'reverse charge' na fatura: o comprador precisa saber que declara o VAT.", "Executive Regulation, art. 59(1)(l); Decree-Law 8/2017, art. 48")
    # ---- e-invoicing PINT AE ----
    cust = (g("cbc:CustomizationID") or "").lower()
    if "pint" in cust and "ae" in cust:
        add("ok", "PINT AE", "Fatura no padrão PINT AE (Peppol) do e-invoicing dos Emirados.", "Ministério das Finanças dos Emirados (e-Invoicing)")
        if g("cbc:InvoiceTypeCode") not in ("380", "381", "386", "389", "480"):
            add("alerta", "PINT AE", f"Tipo de documento '{g('cbc:InvoiceTypeCode')}' fora dos usuais (380 fatura, 381 nota de crédito).", "PINT AE (Peppol International)")
        if r.find("cac:AccountingSupplierParty/cac:Party/cbc:EndpointID", UBL) is None or r.find("cac:AccountingCustomerParty/cac:Party/cbc:EndpointID", UBL) is None:
            add("alerta", "PINT AE", "Falta o EndpointID (endereço Peppol) do fornecedor ou do cliente: sem ele a fatura não trafega pela rede do e-invoicing.", "PINT AE (Peppol International)")
    else:
        add("alerta", "PINT AE", "Fatura fora do padrão PINT AE: com o e-invoicing obrigatório, ela precisará ser emitida pelo provedor credenciado (ASP) nesse padrão.", "Ministério das Finanças dos Emirados (e-Invoicing)")
    add("info", "e-Invoicing", "Cronograma divulgado pelo Ministério das Finanças: piloto a partir de 1º/07/2026; empresas com receita a partir de AED 50 milhões, obrigatório a partir de 1º/01/2027; demais empresas, a partir de 1º/07/2027. Confirme a fase da sua empresa no site do MoF.", "Decisão Ministerial 244/2025 (Emirados)")
    if any(f["nivel"] == "erro" for f in F):
        add("alerta", "Multa", "Fatura fiscal emitida sem os requisitos pode gerar multa administrativa da FTA (por exemplo, AED 5.000 por fatura não emitida corretamente). Corrija antes de declarar o VAT.", "Cabinet Decision 49/2021 (multas administrativas)")
    return _fecha(F, nota, "Fatura UAE (VAT)", "Dubai, Emirados Árabes")


# ---------------- Índia ----------------
def gstin_ok(g):
    if not re.fullmatch(r"\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]", g or ""):
        return False
    al = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    s = 0
    for i, ch in enumerate(g[:14]):
        p = al.index(ch) * (1 if i % 2 == 0 else 2)
        s += p // 36 + p % 36
    return al[(36 - s % 36) % 36] == g[14]


def eh_india(texto):
    return '"SellerDtls"' in texto[:6000] and '"ItemList"' in texto


def auditar_india(texto):
    j = json.loads(texto)
    F = []
    add = lambda n, c, m, f=FONTE_IN: F.append({"nivel": n, "campo": c, "msg": m, "fonte": f})
    v, b, doc = j.get("SellerDtls", {}), j.get("BuyerDtls", {}), j.get("DocDtls", {})
    gv, gb = v.get("Gstin"), b.get("Gstin")
    pos = str(b.get("Pos") or (gb or "")[:2])
    est_v = (gv or "")[:2]
    nota = {"origem": "e-Invoice Índia (JSON do IRP)", "numero": doc.get("No"), "emissao": doc.get("Dt"), "prestador_cnpj": gv,
            "prestador_nome": v.get("LglNm"), "tomador_cnpj": gb, "municipio": f"{UF_IN.get(est_v, est_v)} → {UF_IN.get(pos, pos)}",
            "valor_servicos": _r(j.get("ValDtls", {}).get("TotInvVal"))}
    for papel, gg in (("Fornecedor", gv), ("Cliente", gb)):
        if gg and gg != "URP":
            add("ok" if gstin_ok(gg) else "erro", "GSTIN", f"GSTIN do {papel.lower()} {gg} " + ("válido (dígito verificador confere)." if gstin_ok(gg) else "inválido (formato ou dígito verificador)."), "CGST Rules, regra 10")
    intra = est_v == pos
    add("info", "Tipo de operação", f"{'Dentro do estado' if intra else 'Entre estados'} ({UF_IN.get(est_v, est_v)} → {UF_IN.get(pos, pos)}): " + ("CGST + SGST, metade cada." if intra else "IGST integral."), "IGST Act 2017, arts. 7 e 8")
    try:
        d = date(*map(int, reversed((doc.get("Dt") or "01/01/2026").split("/"))))
    except ValueError:
        d = date(2026, 1, 1)
    validas = GST_NOVAS if d >= date(2025, 9, 22) else GST_ANTIGAS
    for it in j.get("ItemList", []):
        n, rt, base = it.get("SlNo"), float(it.get("GstRt") or 0), _r(it.get("AssAmt"))
        ig, cg, sg = _r(it.get("IgstAmt")), _r(it.get("CgstAmt")), _r(it.get("SgstAmt"))
        hsn = str(it.get("HsnCd") or "")
        if rt not in validas:
            add("erro", f"Item {n}", f"Alíquota de GST {rt:g}% não existe {'depois da reforma GST 2.0 (22/09/2025)' if validas is GST_NOVAS else 'na tabela vigente'}; as válidas são {', '.join(f'{x:g}%' for x in sorted(validas))}.", "Notificações do GST Council (GST 2.0)")
        if it.get("IsServc") == "Y" and not hsn.startswith("99"):
            add("erro", f"Item {n}", f"Serviço com código {hsn}: serviços usam SAC do capítulo 99.", "CGST Rules, regra 46(g)")
        esp = base * rt / 100
        if intra:
            if ig:
                add("erro", f"Item {n}", f"Operação dentro do estado com IGST de ₹{ig:,.2f}: o certo é CGST + SGST.", "IGST Act 2017, art. 8")
            elif abs(cg + sg - esp) > 0.05 or abs(cg - sg) > 0.05:
                add("erro", f"Item {n}", f"CGST ₹{cg:,.2f} + SGST ₹{sg:,.2f} deveria ser ₹{esp / 2:,.2f} + ₹{esp / 2:,.2f} ({rt:g}% de ₹{base:,.2f}).")
            else:
                add("ok", f"Item {n}", f"CGST + SGST de ₹{esp:,.2f} ({rt:g}%) conferem.")
        else:
            if cg or sg:
                add("erro", f"Item {n}", "Operação entre estados com CGST/SGST: o certo é IGST.", "IGST Act 2017, art. 7")
            elif abs(ig - esp) > 0.05:
                add("erro", f"Item {n}", f"IGST de ₹{ig:,.2f} deveria ser ₹{esp:,.2f} ({rt:g}% de ₹{base:,.2f}).")
            else:
                add("ok", f"Item {n}", f"IGST de ₹{ig:,.2f} ({rt:g}%) confere.")
    if not j.get("Irn"):
        add("alerta", "IRN", "Sem IRN (número de registro no portal de e-invoice): obrigatório para empresas com faturamento acima de ₹5 crore.", "CGST Rules, regra 48(4)")
    if any(it.get("IsServc") == "Y" for it in j.get("ItemList", [])) and gb:
        add("info", "TDS", "Serviço profissional pago por empresa pode ter retenção de imposto de renda (TDS) de 10% na seção 194J; serviços de contrato, 1% ou 2% na seção 194C.", "Income-tax Act 1961, s. 194J e 194C")
    return _fecha(F, nota, "e-Invoice Índia (GST)", nota["municipio"])


def _fecha(F, nota, tipo, mun):
    nerr, nal = sum(f["nivel"] == "erro" for f in F), sum(f["nivel"] == "alerta" for f in F)
    ordem = {"erro": 0, "alerta": 1, "info": 2, "ok": 3}
    return {"nota": nota, "municipio": mun, "tipo": tipo, "achados": sorted(F, key=lambda f: ordem[f["nivel"]]), "erros": nerr, "alertas": nal,
            "decisao": "aprovar" if not nerr and not nal else "revisar" if not nerr else "devolver ao fornecedor"}


# ---------------- Itália (FatturaPA) ----------------
# DPR 633/1972 (IVA), DPR 642/1972 (imposta di bollo), Provvedimento AdE 30/04/2018 e especificações técnicas da FatturaPA 1.2
FONTE_IT = "DPR 633/1972 (IVA) e especificações técnicas FatturaPA (Agenzia delle Entrate)"
IVA_IT = {22.0, 10.0, 5.0, 4.0}
NATURA_IT = {"N1": "Excluída (art. 15)", "N2": "Não sujeita", "N3": "Não tributável (exportação, intra-UE)", "N4": "Isenta (art. 10)",
             "N5": "Regime do margem", "N6": "Reverse charge (inversão do contribuinte)", "N7": "IVA pago em outro país da UE"}
TIPO_IT = {"TD01": "fatura", "TD02": "adiantamento", "TD04": "nota de crédito", "TD05": "nota de débito", "TD06": "honorários", "TD16": "integração reverse charge interno",
           "TD17": "autofatura serviços do exterior", "TD18": "compra intra-UE de bens", "TD19": "compra de bens art. 17 c.2", "TD24": "fatura diferida", "TD25": "fatura diferida art. 21 c.4", "TD26": "venda de bens instrumentais", "TD27": "autoconsumo"}


def eh_italia(texto):
    return "FatturaElettronica" in texto[:3000] and "CedentePrestatore" in texto


def piva_ok(p):
    """Dígito verificador da Partita IVA italiana (11 dígitos)."""
    if not re.fullmatch(r"\d{11}", p or ""):
        return False
    s = 0
    for i, ch in enumerate(p[:10]):
        d = int(ch)
        if i % 2:
            d *= 2
            d = d - 9 if d > 9 else d
        s += d
    return (10 - s % 10) % 10 == int(p[10])


def _vies(pais, numero):
    """Consulta oficial da Comissão Europeia (VIES). None se o serviço não responder a tempo."""
    import urllib.request
    try:
        with urllib.request.urlopen(f"https://ec.europa.eu/taxation_customs/vies/rest-api/ms/{pais}/vat/{numero}", timeout=4) as r:
            return json.loads(r.read())
    except Exception:  # noqa: BLE001
        return None


def auditar_italia(texto, vies=True):
    r = ET.fromstring(texto)
    for el in r.iter():  # a FatturaPA usa namespace só na raiz; os filhos vêm sem prefixo
        el.tag = el.tag.split("}")[-1]
    g = lambda p, base=r: (base.findtext(p) or "").strip() or None
    F = []
    add = lambda n, c, m, f=FONTE_IT: F.append({"nivel": n, "campo": c, "msg": m, "fonte": f})
    ced = "FatturaElettronicaHeader/CedentePrestatore/DatiAnagrafici"
    ces = "FatturaElettronicaHeader/CessionarioCommittente/DatiAnagrafici"
    body = r.find("FatturaElettronicaBody")
    if body is None:
        add("erro", "Estrutura", "Arquivo sem o corpo da fatura (FatturaElettronicaBody).", "Especificações técnicas FatturaPA")
        return _fecha(F, {"origem": "FatturaPA (Itália)"}, "Fattura elettronica (Itália)", "Itália")
    dg = "DatiGenerali/DatiGeneraliDocumento"
    nome = lambda b: g(b + "/Anagrafica/Denominazione") or " ".join(filter(None, (g(b + "/Anagrafica/Nome"), g(b + "/Anagrafica/Cognome")))) or None
    pais_v, piva_v = g(ced + "/IdFiscaleIVA/IdPaese"), g(ced + "/IdFiscaleIVA/IdCodice")
    pais_c, piva_c = g(ces + "/IdFiscaleIVA/IdPaese"), g(ces + "/IdFiscaleIVA/IdCodice")
    moeda = g(dg + "/Divisa", body)
    nota = {"origem": "Fattura elettronica (FatturaPA, Itália)", "numero": g(dg + "/Numero", body), "emissao": g(dg + "/Data", body),
            "prestador_cnpj": f"{pais_v or ''}{piva_v or ''}", "prestador_nome": nome(ced), "tomador_cnpj": f"{pais_c or ''}{piva_c or ''}",
            "tomador_nome": nome(ces), "municipio": "Itália", "valor_servicos": _r(g(dg + "/ImportoTotaleDocumento", body))}
    try:
        if nota["emissao"] and date.fromisoformat(nota["emissao"][:10]) > date.today():
            add("erro", "Data", f"Data de emissão {nota['emissao']} no futuro.", "DPR 633/1972, art. 21, c.4")
    except ValueError:
        add("erro", "Data", f"Data de emissão '{nota['emissao']}' em formato inválido (use AAAA-MM-DD).", "Especificações técnicas FatturaPA")
    tipo = g(dg + "/TipoDocumento", body)
    if tipo in TIPO_IT:
        add("ok", "Tipo de documento", f"{tipo}: {TIPO_IT[tipo]}.", "Especificações técnicas FatturaPA")
    else:
        add("erro", "Tipo de documento", f"Tipo '{tipo}' fora da tabela da FatturaPA (TD01 a TD29).", "Especificações técnicas FatturaPA")
    for campo, val in (("Número", nota["numero"]), ("Data", nota["emissao"]), ("Fornecedor (cedente)", nota["prestador_nome"])):
        if not val:
            add("erro", campo, f"{campo} ausente: dado obrigatório da fatura.", "DPR 633/1972, art. 21")
    if moeda and moeda != "EUR":
        add("alerta", "Moeda", f"Fatura em {moeda}: a IVA deve ser expressa também em euro.", "DPR 633/1972, art. 21, c.2")
    for papel, pais, piva in (("fornecedor", pais_v, piva_v), ("cliente", pais_c, piva_c)):
        if not piva:
            if papel == "fornecedor":
                add("erro", "Partita IVA", "Fatura sem a Partita IVA do fornecedor.", "DPR 633/1972, art. 21, c.2")
            continue
        if pais == "IT" and not piva_ok(piva):
            add("erro", "Partita IVA", f"Partita IVA do {papel} IT{piva} com dígito verificador inválido.", "DPR 633/1972, art. 35")
            continue
        v = _vies(pais, piva) if vies and pais and pais != "IT" else None  # VIES vale para operações intra-UE
        if v and v.get("isValid") and nome(ced if papel == "fornecedor" else ces) and (v.get("name") or "").strip() not in ("", "---"):
            n1 = re.sub(r"[^a-z0-9]", "", (nome(ced if papel == "fornecedor" else ces) or "").lower())[:8]
            if n1 and n1 not in re.sub(r"[^a-z0-9]", "", v["name"].lower()):
                add("alerta", "VIES", f"Nome na fatura difere do nome no VIES ({v['name'].strip()}): confirme o cadastro do {papel}.", "VIES, Comissão Europeia")
        if v and v.get("isValid"):
            add("ok", "VIES", f"Partita IVA do {papel} {pais}{piva} ativa no VIES: {(v.get('name') or '').strip()}.", "VIES, Comissão Europeia")
        elif v and v.get("isValid") is False:
            add("erro", "VIES", f"Partita IVA do {papel} {pais}{piva} não está ativa no VIES.", "VIES, Comissão Europeia")
        elif pais == "IT":
            add("ok", "Partita IVA", f"Partita IVA do {papel} IT{piva} com dígito verificador correto.", "DPR 633/1972, art. 35")
    soma_lin = {}
    for ln in body.findall("DatiBeniServizi/DettaglioLinee"):
        k = g("NumeroLinea", ln) or "?"
        q, pu, pt = _r(g("Quantita", ln) or 1), _r(g("PrezzoUnitario", ln)), _r(g("PrezzoTotale", ln))
        if pu and abs(q * pu - pt) > 0.01 and not ln.findall("ScontoMaggiorazione"):
            add("erro", f"Linha {k}", f"Total da linha € {pt:,.2f} difere de {q:g} × € {pu:,.2f} = € {q * pu:,.2f}.", "Especificações técnicas FatturaPA")
        chave = (_r(g("AliquotaIVA", ln)), g("Natura", ln))
        soma_lin[chave] = soma_lin.get(chave, 0) + pt
    base_tot = imp_tot = 0.0
    split = False
    for rp in body.findall("DatiBeniServizi/DatiRiepilogo"):
        aliq, nat = _r(g("AliquotaIVA", rp)), g("Natura", rp)
        base, imp, esig = _r(g("ImponibileImporto", rp)), _r(g("Imposta", rp)), g("EsigibilitaIVA", rp)
        base_tot += base
        imp_tot += imp
        if aliq:
            if aliq not in IVA_IT:
                add("erro", "Alíquota IVA", f"Alíquota de {aliq:g}% não existe na Itália (22%, 10%, 5% ou 4%).", "DPR 633/1972, art. 16 e Tabela A")
            else:
                add("ok", "Alíquota IVA", f"Alíquota de {aliq:g}% prevista na lei italiana.", "DPR 633/1972, art. 16 e Tabela A")
            if abs(base * aliq / 100 - imp) > 0.01:
                add("erro", "Valor da IVA", f"IVA de € {imp:,.2f} difere de € {base:,.2f} × {aliq:g}% = € {base * aliq / 100:,.2f}.", "DPR 633/1972, art. 16")
            elif aliq in IVA_IT:
                add("ok", "Valor da IVA", f"IVA de € {imp:,.2f} = € {base:,.2f} × {aliq:g}%.", "DPR 633/1972, art. 16")
        else:
            cod = (nat or "")[:2]
            if cod not in NATURA_IT:
                add("erro", "Natura", "Operação com IVA zero sem o código Natura (N1 a N7): a fatura é rejeitada pelo SdI.", "Especificações técnicas FatturaPA")
            else:
                add("info", "Natura", f"{nat}: {NATURA_IT[cod]}.", FONTE_IT)
                if cod in ("N2", "N3", "N4") and not g("RiferimentoNormativo", rp):
                    add("alerta", "Fundamento legal", f"Operação {nat} sem o artigo de lei que justifica a não incidência (RiferimentoNormativo).", "DPR 633/1972, art. 21, c.6")
                if cod == "N6":
                    add("info", "Reverse charge", "Inversão do contribuinte: o cliente integra a fatura e recolhe a IVA (documento TD16/TD17).", "DPR 633/1972, art. 17, c.6")
        if esig == "S":
            split = True
            add("info", "Split payment", "IVA com split payment: a administração pública paga a IVA diretamente ao Erário; o fornecedor recebe só o valor sem IVA.", "DPR 633/1972, art. 17-ter")
        lin = soma_lin.get((aliq, nat))
        if lin is not None and abs(lin - base) > 0.01:
            add("alerta", "Resumo", f"Soma das linhas a {aliq:g}% (€ {lin:,.2f}) difere da base do resumo (€ {base:,.2f}).", "Especificações técnicas FatturaPA")
    tot = nota["valor_servicos"]
    if tot and abs(tot - (base_tot + imp_tot)) > 0.02:
        add("alerta", "Totais", f"Total do documento € {tot:,.2f} difere de base + IVA = € {base_tot + imp_tot:,.2f} (verifique descontos, bollo ou arredondamentos).", "Especificações técnicas FatturaPA")
    elif tot:
        add("ok", "Totais", f"Total do documento € {tot:,.2f} confere com base + IVA.", "Especificações técnicas FatturaPA")
    pag = sum(_r(g("ImportoPagamento", dp)) for dp in body.findall("DatiPagamento/DettaglioPagamento"))
    if pag and tot:
        esperado = tot - (imp_tot if split else 0) - _r(g(dg + "/DatiRitenuta/ImportoRitenuta", body))
        if abs(pag - esperado) > 0.02:
            add("alerta", "Pagamento", f"Valor a pagar € {pag:,.2f} difere do esperado € {esperado:,.2f} (total{' − IVA split payment' if split else ''} − ritenuta).", "Especificações técnicas FatturaPA")
        else:
            add("ok", "Pagamento", f"Valor a pagar € {pag:,.2f} confere com total, split payment e ritenuta.", "Especificações técnicas FatturaPA")
    rit = body.find(dg + "/DatiRitenuta")
    if rit is not None:
        add("info", "Ritenuta d'acconto", f"Retenção na fonte {g('TipoRitenuta', rit)} de {g('AliquotaRitenuta', rit)}% (€ {g('ImportoRitenuta', rit)}): o cliente recolhe pelo F24 até o dia 16 do mês seguinte.", "DPR 600/1973, art. 25")
    isento = sum(_r(g("ImponibileImporto", rp)) for rp in body.findall("DatiBeniServizi/DatiRiepilogo") if (g("Natura", rp) or "")[:2] in ("N1", "N2", "N3", "N4") and not _r(g("AliquotaIVA", rp)))
    if isento > 77.47 and body.find(dg + "/DatiBollo") is None:
        add("alerta", "Imposta di bollo", f"Operações sem IVA de € {isento:,.2f} (acima de € 77,47) sem a marca de bollo virtual de € 2,00.", "DPR 642/1972, Tabela A, art. 13")
    if any(f["nivel"] == "erro" for f in F):
        add("alerta", "Sanções", "Fatura com erro de IVA pode gerar sanção de 70% a 140% do imposto (mínimo € 250), reduzida com correção espontânea (ravvedimento operoso).", "D.Lgs. 471/1997, art. 6, e D.Lgs. 472/1997, art. 13")
    nota["split_payment"] = split
    return _fecha(F, nota, "Fattura elettronica (Itália)", "Itália")
