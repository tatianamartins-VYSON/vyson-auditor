"""Regras de auditoria da NFS-e com as bases oficiais do Vyson Fiscal (LC 116, GISS Maceió, IN SF/SUREM São Paulo)."""
import json
import pathlib


def brl(v):
    return "R$ " + f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

_R = pathlib.Path(__file__).resolve().parents[1]
BASE = next((p for p in (_R / "data", _R / "public" / "fiscal") if p.is_dir()), _R / "data")
DATA = json.loads((BASE / "data.json").read_text(encoding="utf-8"))
MCZ = json.loads((BASE / "maceio.json").read_text(encoding="utf-8"))["itens"]
SP = json.loads((BASE / "saopaulo.json").read_text(encoding="utf-8"))

SUBITENS = {f"{int(c[0][:2])}.{c[0][2:4]}": c[1] for c in DATA["ct"]}
MUNICIPIOS = {"2704302": "Maceió/AL", "3550308": "São Paulo/SP"}
# subitens com retenção de 11% de INSS na cessão de mão de obra/empreitada (IN RFB 2.110/2022, arts. 111 e 112)
INSS_11 = {"7.02", "7.04", "7.05", "7.10", "7.11", "7.12", "7.17", "7.19", "11.02", "11.04", "17.05"}


import re as _re
import unicodedata as _ud
try:
    RECIFE = json.loads((BASE / "recife_cnae.json").read_text(encoding="utf-8"))["r"]
except FileNotFoundError:
    RECIFE = []
_PARE = {"servicos", "servico", "outros", "outras", "demais", "congeneres", "qualquer", "natureza", "inclusive", "exceto", "classificados", "subclasses", "anteriormente", "especificados", "atividades", "geral"}


def _tok(t):
    t = _ud.normalize("NFD", str(t or "")).encode("ascii", "ignore").decode().lower()
    return {w for w in _re.findall(r"[a-z]{4,}", t) if w not in _PARE}


def _sugerir_nbs(atividade):
    """NBS cuja descrição mais combina com a atividade da nota, em toda a tabela (item, código, descrição, nota de 0 a 1)."""
    a = _tok(atividade)
    if not a:
        return []
    out = []
    for sub, lst in DATA["nb"].items():
        for x in lst:
            w = _tok(x[1].replace("§", ""))
            if w:
                out.append((len(a & w) / len(a), sub[:5], x[0], x[1].replace("§", "")))
    return sorted(out, reverse=True)[:3]


def _nbs_fmt(c):
    c = "".join(ch for ch in str(c) if ch.isdigit())
    return f"{c[0]}.{c[1:5]}.{c[5:7]}.{c[7:9]}" if len(c) == 9 else c


def _mun(d):
    m = str(d.get("municipio") or "")
    if m in MUNICIPIOS:
        return MUNICIPIOS[m]
    low = m.lower()
    return "Maceió/AL" if "macei" in low else "São Paulo/SP" if "paulo" in low else m or None


def _aliq_oficial(mun, item, cod):
    if mun == "Maceió/AL":
        cands = [x for x in MCZ if x["i"] == item and not x.get("fim")]
        if cod:
            exato = [x for x in cands if x["c"] == str(cod).replace(".", "").replace("-", "")]
            cands = exato or cands
        return [(x["c"], x["d"], x["a"], f"GISS Online Maceió, código {x['c']}") for x in cands]
    if mun == "São Paulo/SP":
        cands = [x for x in SP["itens"] if x.get("i") == item]
        return [(x.get("c"), x.get("d"), x.get("a"), f"IN SF/SUREM 08/2011, código {x.get('c')}") for x in cands]
    return []


def auditar(d: dict) -> dict:
    F = []
    add = lambda nivel, campo, msg, fonte="": F.append({"nivel": nivel, "campo": campo, "msg": msg, "fonte": fonte})
    item, mun = d.get("item_lc116"), _mun(d)
    vs, al, viss = d.get("valor_servicos"), d.get("aliquota_iss"), d.get("valor_iss")

    if d.get("locacao_bens"):
        add("info", "Incidência", "Locação de máquinas/equipamentos sem operador não é serviço: não incide ISS.", "STF, Súmula Vinculante 31")
    if d.get("isento"):
        add("info", "Incidência", "Nota emitida como isenta/sem incidência de ISS; conferir a base legal citada na nota.")
    if d.get("simples") is False:
        add("info", "Regime", "Prestador não optante pelo Simples Nacional: ISS recolhido por guia própria do município e PIS/COFINS/IRPJ/CSLL na apuração do regime (Presumido ou Real).", "LC 116/2003; Lei 9.718/1998")
    if d.get("simples"):
        add("info", "Regime", "Prestador optante pelo Simples Nacional: o ISS é recolhido no DAS, com alíquota da faixa do Anexo III ou IV.", "LC 123/2006, art. 18")
    if not item and (d.get("locacao_bens") or d.get("isento")):
        pass
    elif not item:
        add("erro", "Item LC 116", "Item da lista de serviços não encontrado na nota.", "LC 116/2003, art. 1º")
    elif item not in SUBITENS:
        add("erro", "Item LC 116", f"Item {item} não existe na lista da LC 116/2003.", "LC 116/2003, lista anexa")
    else:
        add("ok", "Item LC 116", f"Item {item}: {SUBITENS[item][:90]}", "LC 116/2003, lista anexa")

    if al is not None and (d.get("isento") or d.get("locacao_bens")) and al == 0:
        add("ok", "Alíquota ISS", "Alíquota 0% coerente com a não incidência/isenção informada.")
    elif al is not None and d.get("simples"):
        pass
    elif al is not None:
        if item not in ("7.02", "7.05", "16.01") and (al < 2 or al > 5):
            add("erro", "Alíquota ISS", f"Alíquota {str(al).replace(".", ",").rstrip("0").rstrip(",")}% fora da faixa legal de 2% a 5%.", "LC 116/2003, arts. 8º e 8º-A")
        of = _aliq_oficial(mun, item, d.get("cod_municipal")) if item else []
        if of:
            certas = sorted({x[2] for x in of})
            if any(abs(al - a) < 0.001 for a in certas):
                c = next(x for x in of if abs(al - x[2]) < 0.001)
                add("ok", "Alíquota ISS", f"{str(al).replace(".", ",").rstrip("0").rstrip(",")}% confere com a tabela oficial de {mun}.", c[3])
            else:
                add("erro", "Alíquota ISS", f"{str(al).replace(".", ",").rstrip("0").rstrip(",")}% diverge da tabela de {mun} para o item {item}: " + ", ".join(f"{a:g}%" for a in certas) + ".", of[0][3])
        elif mun:
            add("info", "Alíquota ISS", f"Sem tabela municipal carregada para {mun}; conferir na legislação do município.")
    elif not d.get("simples"):
        add("alerta", "Alíquota ISS", "Alíquota do ISS não informada.")

    if vs and al is not None:
        base = vs - (d.get("deducoes") or 0)
        esperado = round(base * al / 100, 2)
        if viss is None:
            add("alerta", "Valor ISS", f"Valor do ISS não informado; o esperado é {brl(esperado)}.")
        elif abs(esperado - viss) > 0.05:
            add("erro", "Valor ISS", f"ISS de {brl(viss)} não bate com base × alíquota = {brl(esperado)}.", "LC 116/2003, art. 7º")
        else:
            add("ok", "Valor ISS", f"{brl(viss)} = base {brl(base)} × {str(al).replace(".", ",").rstrip("0").rstrip(",")}%.", "LC 116/2003, art. 7º")

    if item in INSS_11 and vs:
        inss = d.get("inss_retido")
        if not inss:
            add("alerta", "INSS", f"Item {item} costuma ter retenção de 11% de INSS ({brl(vs * 0.11)} sobre o bruto, antes das deduções legais).", "Lei 8.212/1991, art. 31; IN RFB 2.110/2022")
        else:
            add("ok", "INSS", f"INSS retido informado: {brl(inss)}.", "Lei 8.212/1991, art. 31")

    elif item and vs and d.get("inss_retido") in (None, 0, 0.0):
        add("ok", "INSS", f"Sem retenção de INSS: correto para o item {item}, que não é cessão de mão de obra nem empreitada da lista de 11%.", "IN RFB 2.110/2022, arts. 111 e 112")

    # retenção do ISS pelo tomador (LC 116, art. 6º, §2º, II e art. 3º)
    if item and d.get("iss_retido") is not None:
        resp = item in ("3.05", "7.02", "7.04", "7.05", "7.09", "7.10", "7.12", "7.16", "7.17", "7.19", "11.02", "17.05", "17.10")
        if d["iss_retido"] and not resp:
            add("alerta", "ISS retido", f"ISS marcado como retido pelo tomador, mas o item {item} não está na lista de responsabilidade da LC 116 (art. 6º, §2º, II); confira se a lei municipal prevê a retenção.", "LC 116/2003, art. 6º")
        elif not d["iss_retido"] and resp and len("".join(ch for ch in str(d.get("tomador_cnpj") or "") if ch.isdigit())) == 14:
            add("alerta", "ISS retido", f"Item {item} com tomador pessoa jurídica: a LC 116 atribui ao tomador a retenção do ISS, e a nota está sem retenção.", "LC 116/2003, art. 6º, §2º, II")
        else:
            add("ok", "ISS retido", f"ISS {'retido pelo tomador' if d['iss_retido'] else 'sem retenção, recolhido pelo prestador'}: coerente com o item {item} e o local de incidência.", "LC 116/2003, arts. 3º e 6º")

    # PIS/COFINS na nota (prestador fora do Simples)
    if d.get("simples") is False and vs and (d.get("pis_valor") or 0) == 0 and (d.get("cofins_valor") or 0) == 0:
        add("info", "PIS/COFINS", f"A nota não destaca PIS/COFINS ('apuração própria'). Não há erro na nota, mas o prestador apura: no Lucro Presumido 0,65% + 3% ({brl(vs * 0.0365)}); no Real 1,65% + 7,6% com créditos. Na NFS-e, o CST de PIS/COFINS de serviço tributado é 01 (cumulativo/não cumulativo à alíquota básica).", "Leis 9.718/1998, 10.637/2002 e 10.833/2003")

    if item in ("7.02", "7.05"):
        add("info", "Base de cálculo", "Construção civil: o ISS pode excluir os materiais fornecidos pelo prestador. Conferir as deduções.", "LC 116/2003, art. 7º, §2º, I")

    # item da LC 116 x descrição do serviço (vale para qualquer layout; a reforma tem a checagem própria abaixo)
    desc_serv = d.get("descricao") or d.get("atividade") or ""
    if item and item in SUBITENS and desc_serv and not any(d.get(k) for k in ("ref_nbs", "ref_cindop", "ref_cst", "ref_cclass")):
        sug = _sugerir_nbs(desc_serv)
        sub = f"{int(item.split('.')[0]):02d}.{item.split('.')[1]}"
        txt_item = " ".join(x.get("d", "") for x in MCZ if x.get("i") == item) or str(SUBITENS.get(item, ""))
        casa = len(_tok(desc_serv) & _tok(txt_item)) / max(1, len(_tok(desc_serv)))
        if sug and sug[0][0] >= 0.6 and sug[0][1].split(".")[0] != sub.split(".")[0] and casa < 0.3:
            add("alerta", "Item x atividade", f"A descrição do serviço ('{desc_serv[:80]}') não combina com o item {item} da LC 116; pela descrição, o item mais adequado parece ser o {sug[0][1]}. "
                "Confira o código de tributação: item errado muda a alíquota, o local de incidência e as retenções.", "LC 116/2003, lista anexa")

    # códigos da reforma tributária (IBS/CBS) x atividade: NBS, cIndOp, CST e cClassTrib (LC 214/2025; NT NFS-e Nacional)
    if item and item in SUBITENS and any(d.get(k) for k in ("ref_nbs", "ref_cindop", "ref_cst", "ref_cclass")):
        sub = f"{int(item.split('.')[0]):02d}.{item.split('.')[1]}"
        rows = [r for r in DATA["ct"] if r[0][:4] == sub.replace(".", "")]
        ccl_ok = sorted({c for r in rows for c in DATA["L"][r[6]]})
        ind_ok = sorted({c for r in rows for c in DATA["I"][r[4]]})
        nbs_l = [x for k, v in DATA["nb"].items() if k == sub or k.startswith(sub + ".") for x in v]
        nbs = "".join(ch for ch in str(d.get("ref_nbs") or "") if ch.isdigit())
        ativ = d.get("atividade") if d.get("atividade") and "?" not in d.get("atividade") else None
        ativ = ativ or next((x.get("d") for x in MCZ if x["c"] == str(d.get("cod_municipal") or "")), None)
        sug = _sugerir_nbs(ativ) if ativ else []
        if sug and sug[0][0] >= 0.6:
            melhor = sug[0]
            usado = next(((sc, it, c, ds) for sc, it, c, ds in _sugerir_nbs(ativ + " ") if c == nbs), None)
            desc_usada = next((x[1].replace("§", "") for v in DATA["nb"].values() for x in v if x[0] == nbs), "")
            casa = len(_tok(ativ) & _tok(desc_usada)) / max(1, len(_tok(ativ)))
            if nbs and melhor[2] != nbs and casa < 0.5:
                add("alerta", "Reforma · NBS x atividade", f"A NBS {d.get('ref_nbs')} ({desc_usada or 'descrição não encontrada'}) não descreve a atividade da nota ('{ativ}'). "
                    f"Sugestão: NBS {_nbs_fmt(melhor[2])} ({melhor[3]}), ligada ao item {melhor[1]}.", "Tabela NBS e correlação LC 116 x NBS (NFS-e Nacional)")
            if melhor[1] != f"{int(item.split('.')[0]):02d}.{item.split('.')[1]}":
                add("alerta", "Reforma · item x atividade", f"Pela descrição da atividade, o item da LC 116 mais adequado parece ser o {melhor[1]}, e a nota usa o {item}. Revise o código de tributação municipal escolhido na emissão.", "LC 116/2003, lista anexa")
        cnae = "".join(ch for ch in str(d.get("cod_municipal") or "") if ch.isdigit())
        rec = sorted({r[2] for r in RECIFE if "".join(ch for ch in r[0] if ch.isdigit()) == cnae}) if len(cnae) == 7 else []
        if rec and f"{int(item.split('.')[0]):02d}.{item.split('.')[1]}" not in rec:
            add("info", "CNAE x item", f"O código {cnae} corresponde ao CNAE {cnae[:4]}-{cnae[4]}/{cnae[5:]}; na tabela oficial da Prefeitura do Recife esse CNAE se liga aos itens {', '.join(rec)}, e a nota usa o {item}. A tabela de cada município prevalece; vale revisar.", "Prefeitura do Recife, tabela CNAE x LC 116")
        if not d.get("ref_nbs"):
            add("alerta", "Reforma · NBS", f"Código NBS não informado. Para o item {item} há {len(nbs_l)} códigos NBS possíveis (veja na Consulta de códigos).", "LC 214/2025; NT NFS-e Nacional (cNBS obrigatório)")
        elif nbs_l and nbs not in {x[0] for x in nbs_l}:
            add("alerta", "Reforma · NBS", f"NBS {d['ref_nbs']} não está entre os códigos ligados ao item {item} da LC 116. Confira se a NBS descreve a atividade da nota.", "Correlação LC 116 x NBS (NFS-e Nacional)")
        elif nbs_l and not any(f["campo"] == "Reforma · NBS x atividade" for f in F):
            desc = next(x[1] for x in nbs_l if x[0] == nbs).replace("§", "")
            add("ok", "Reforma · NBS", f"NBS {d['ref_nbs']} compatível com o item {item} ({desc}).", "Correlação LC 116 x NBS (NFS-e Nacional)")
        ci = d.get("ref_cindop")
        if not ci:
            add("alerta", "Reforma · cIndOp", f"Indicador da operação (cIndOp) não informado; para o item {item} o esperado é {' ou '.join(ind_ok)}.", "LC 214/2025, art. 11 (local da operação)")
        elif ind_ok and ci not in ind_ok:
            add("erro", "Reforma · cIndOp", f"cIndOp {ci} não corresponde ao item {item}; o esperado é {' ou '.join(ind_ok)}.", "LC 214/2025, art. 11; tabela cIndOp")
        else:
            add("ok", "Reforma · cIndOp", f"cIndOp {ci} confere com o item {item}.", "Tabela cIndOp")
        cc, cst = d.get("ref_cclass"), d.get("ref_cst")
        if not cc:
            add("alerta", "Reforma · cClassTrib", f"cClassTrib não informado; para o item {item}: {', '.join(ccl_ok)}.", "LC 214/2025; tabela cClassTrib")
        else:
            if ccl_ok and cc not in ccl_ok:
                add("erro", "Reforma · cClassTrib", f"cClassTrib {cc} não está entre as classificações do item {item} ({', '.join(ccl_ok)}).", "LC 214/2025; tabela cClassTrib")
            else:
                nm = (DATA["cls"].get(cc) or [""])[0]
                add("ok", "Reforma · cClassTrib", f"cClassTrib {cc} ({nm[:60]}) compatível com o item {item}.", "Tabela cClassTrib")
            if cst and cst != cc[:3]:
                add("erro", "Reforma · CST IBS/CBS", f"CST {cst} não combina com o cClassTrib {cc} (os 3 primeiros dígitos devem ser {cc[:3]}).", "Tabela CST x cClassTrib")
            elif cst:
                add("ok", "Reforma · CST IBS/CBS", f"CST {cst} coerente com o cClassTrib {cc}.", "Tabela CST x cClassTrib")
        vi, vc = d.get("ref_vibs"), d.get("ref_vcbs")
        if vs and (vi is not None or vc is not None or d.get("ref_vbc")):
            base = d.get("ref_vbc") or round(vs - (viss or 0) - (d.get("pis_valor") or 0) - (d.get("cofins_valor") or 0), 2)
            esp_i, esp_c = round(base * 0.001, 2), round(base * 0.009, 2)
            if vi is not None and vc is not None and (abs(vi - esp_i) > 0.02 or abs(vc - esp_c) > 0.02) and (cc or "").startswith("000"):
                add("alerta", "Reforma · valores 2026", f"IBS {brl(vi)} e CBS {brl(vc)}; em 2026 o esperado é 0,1% e 0,9% sobre {brl(base)}: {brl(esp_i)} e {brl(esp_c)}.", "LC 214/2025, arts. 343 a 346 (ano-teste 2026)")
            elif vi is not None or vc is not None:
                add("ok", "Reforma · valores 2026", f"IBS {brl(vi or 0)} e CBS {brl(vc or 0)} conferem com 0,1% e 0,9% sobre a base de {brl(base)} (valor sem o ISS e o PIS/COFINS).", "LC 214/2025, arts. 343 a 346")
            else:
                add("info", "Reforma · valores 2026", f"Base do IBS/CBS informada: {brl(base)}; em 2026: IBS 0,1% ({brl(esp_i)}) e CBS 0,9% ({brl(esp_c)}), compensáveis.", "LC 214/2025, arts. 343 a 346")
    elif item and item in SUBITENS:
        add("alerta", "Reforma tributária", "A nota não traz os códigos da reforma (NBS, cIndOp, CST e cClassTrib do IBS/CBS), exigidos na NFS-e a partir de 2026.", "LC 214/2025; NT NFS-e Nacional")

    # retenções federais quando o tomador é pessoa jurídica e o prestador não é do Simples
    tom = "".join(ch for ch in str(d.get("tomador_cnpj") or "") if ch.isdigit())
    if vs and len(tom) == 14 and d.get("simples") is False and item:
        grupo = item.split(".")[0]
        prof = grupo in ("1", "4", "5", "8", "10", "17", "23", "28", "30", "31", "32", "35", "36", "38") or item in ("7.01", "7.03", "7.19")
        limp = item in ("7.10", "7.11", "11.02", "17.05") or grupo == "11"
        ir, csll = d.get("ir_retido") or 0, d.get("csll_retido") or 0
        if prof or limp:
            pir = 1.0 if limp else 1.5
            vir, vcs = round(vs * pir / 100, 2), round(vs * 4.65 / 100, 2)
            if not ir and not csll:
                add("alerta", "Retenções federais",
                    f"Tomador pessoa jurídica (CNPJ {d.get('tomador_cnpj')}) e prestador fora do Simples, item {item}: se a atividade estiver entre os serviços "
                    f"{'de limpeza, conservação, segurança ou locação de mão de obra' if limp else 'profissionais'}, o tomador deve reter IRRF {str(pir).replace(".", ",").replace(",0", "")}% ({brl(vir)}) "
                    f"e PIS/COFINS/CSLL 4,65% ({brl(vcs)}), e a nota não destaca nenhuma retenção. Confira o enquadramento da atividade.",
                    "RIR/2018 (Decreto 9.580), arts. 714 e 716; Lei 10.833/2003, arts. 30 e 31; IN SRF 459/2004")
            else:
                add("ok", "Retenções federais", f"Retenções informadas: IRRF {brl(ir)}, CSLL {brl(csll)}.", "RIR/2018, art. 714; Lei 10.833/2003, art. 30")
    elif d.get("simples"):
        add("info", "Retenções federais", "Prestador do Simples Nacional: não há retenção de IRRF nem de PIS/COFINS/CSLL na fonte.", "IN SRF 459/2004, art. 3º, XI; IN RFB 765/2007")

    for campo in ("numero", "prestador_cnpj", "valor_servicos"):
        if not d.get(campo):
            add("alerta", campo, "Campo não encontrado no documento.")

    ficha = []
    if vs:
        LEI = {"ISS": "LC 116/2003, arts. 3º, 7º e 8º; tabela do município", "IBS (2026)": "LC 214/2025, arts. 12 e 343 a 346", "CBS (2026)": "LC 214/2025, arts. 12 e 343 a 346",
               "PIS": "Lei 9.718/1998, art. 4º (Presumido); Lei 10.637/2002, art. 2º (Real)", "COFINS": "Lei 9.718/1998, art. 4º (Presumido); Lei 10.833/2003, art. 2º (Real)",
               "IRRF retido": "RIR/2018 (Decreto 9.580/2018), arts. 714 e 716", "CSRF retida (PIS/COFINS/CSLL)": "Lei 10.833/2003, arts. 30, 31 e 32; IN SRF 459/2004",
               "IRPJ (Presumido)": "Lei 9.249/1995, art. 15, §1º, III; Lei 9.430/1996, art. 25", "CSLL (Presumido)": "Lei 9.249/1995, art. 20; Lei 7.689/1988",
               "Valor líquido": "LC 116/2003, art. 6º; Lei 10.833/2003, art. 30; Lei 8.212/1991, art. 31", "INSS retido": "Lei 8.212/1991, art. 31; IN RFB 2.110/2022, arts. 110 a 112",
               "PIS/COFINS/IRPJ/CSLL": "LC 123/2006, arts. 13 e 18 (Anexos III a V)"}

        def lin(t, regra, al_, esp, nota, sit):
            ficha.append({"tributo": t, "regra": regra, "aliquota": al_, "esperado": esp, "nota": nota, "situacao": sit, "lei": LEI.get(t, "")})
        simp = d.get("simples")
        lin("ISS", f"Município de incidência: {mun or '—'}" + (" · retido pelo tomador" if d.get("iss_retido") else " · recolhido pelo prestador"),
            f"{al:g}%" if al is not None else "—", round(vs * (al or 0) / 100, 2) if al else None, viss,
            "confere" if viss is not None and al and abs(round(vs * al / 100, 2) - viss) < 0.05 else ("no DAS" if simp else "conferir"))
        base_ref = d.get("ref_vbc") or round(vs - (viss or 0), 2)
        lin("IBS (2026)", "Ano-teste: 0,1% sobre o valor sem ISS/PIS/COFINS, compensável", "0,1%", round(base_ref * 0.001, 2), d.get("ref_vibs"),
            "confere" if d.get("ref_vibs") is not None and abs(round(base_ref * 0.001, 2) - d["ref_vibs"]) < 0.03 else "não destacado" if d.get("ref_vibs") is None else "conferir")
        lin("CBS (2026)", "Ano-teste: 0,9% sobre a mesma base, compensável", "0,9%", round(base_ref * 0.009, 2), d.get("ref_vcbs"),
            "confere" if d.get("ref_vcbs") is not None and abs(round(base_ref * 0.009, 2) - d["ref_vcbs"]) < 0.03 else "não destacado" if d.get("ref_vcbs") is None else "conferir")
        if simp:
            lin("PIS/COFINS/IRPJ/CSLL", "Prestador do Simples: tudo no DAS (Anexo III ou IV)", "faixa do Simples", None, None, "no DAS")
        else:
            lin("PIS", "Prestador fora do Simples: Presumido 0,65% (Real 1,65% com créditos)", "0,65%", round(vs * 0.0065, 2), d.get("pis_valor"), "apuração própria")
            lin("COFINS", "Presumido 3% (Real 7,6% com créditos)", "3%", round(vs * 0.03, 2), d.get("cofins_valor"), "apuração própria")
            tomPJ = len("".join(ch for ch in str(d.get("tomador_cnpj") or "") if ch.isdigit())) == 14
            ret = any(f["campo"] == "Retenções federais" and f["nivel"] == "alerta" for f in F)
            lin("IRRF retido", "Tomador PJ em serviço profissional: 1,5% (limpeza/segurança/mão de obra: 1%)", "1,5%" if ret else "—",
                round(vs * 0.015, 2) if ret else None, d.get("ir_retido"), "verificar retenção" if ret else ("não se aplica" if tomPJ else "tomador não é PJ"))
            lin("CSRF retida (PIS/COFINS/CSLL)", "Lei 10.833/2003, art. 30: 4,65% nos serviços da lista", "4,65%" if ret else "—",
                round(vs * 0.0465, 2) if ret else None, d.get("csll_retido"), "verificar retenção" if ret else "não se aplica")
        if not simp:
            lin("IRPJ (Presumido)", "Presunção de 32% para serviços × 15% (adicional de 10% acima de R$ 20 mil/mês de lucro presumido)", "4,8%", round(vs * 0.048, 2), None, "apuração própria")
            lin("CSLL (Presumido)", "Presunção de 32% × 9%", "2,88%", round(vs * 0.0288, 2), None, "apuração própria")
        ret_tot = sum(d.get(k) or 0 for k in ("ir_retido", "csll_retido", "inss_retido")) + ((viss or 0) if d.get("iss_retido") else 0)
        if d.get("valor_liquido") is not None:
            liq = round(vs - (d.get("deducoes") or 0) * 0 - ret_tot, 2)
            lin("Valor líquido", "Valor do serviço − retenções (ISS retido, INSS, IRRF, CSRF)", "—", liq, d.get("valor_liquido"),
                "confere" if abs(liq - d["valor_liquido"]) < 0.05 else "conferir")
        lin("INSS retido", "11% só em cessão de mão de obra/empreitada (IN RFB 2.110/2022)", "11%" if item in INSS_11 else "—",
            round(vs * 0.11, 2) if item in INSS_11 else None, d.get("inss_retido"),
            ("confere" if d.get("inss_retido") else "verificar retenção") if item in INSS_11 else "não se aplica")

    nerr = sum(f["nivel"] == "erro" for f in F)
    nal = sum(f["nivel"] == "alerta" for f in F)
    decisao = "aprovar" if not nerr and not nal else "revisar" if not nerr else "devolver ao fornecedor"
    ordem = {"erro": 0, "alerta": 1, "info": 2, "ok": 3}
    return {"municipio": mun, "achados": sorted(F, key=lambda f: ordem[f["nivel"]]), "erros": nerr, "alertas": nal, "decisao": decisao, "ficha": ficha}
