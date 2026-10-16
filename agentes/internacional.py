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
