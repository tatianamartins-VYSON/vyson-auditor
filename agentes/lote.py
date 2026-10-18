"""Auditoria em lote de NF-e/NFC-e de SAÍDA (ex.: zip do mês exportado do sistema de vendas).

Confere numeração, autorização, CFOP x CSOSN/CST, NCM e CEST por produto e monta o resumo de receitas
para o PGDAS-D do Simples Nacional (receita normal, com ICMS-ST e monofásica de PIS/COFINS).
"""
import xml.etree.ElementTree as ET
from collections import defaultdict

from nfe import MONO, NS, _f, _n, brl

ST_CSOSN = {"201", "202", "203", "500"}
ST_CST = {"10", "30", "60", "70"}
CFOP_ST = {"5401", "5403", "5405", "6401", "6403", "6404"}
REFEICAO = ("REFEIC", "QUENTINHA", "MARMITA", "PRATO", "SELF", "PANQUECA", "LANCHE")
# bebidas e outros com ICMS-ST e monofásico de PIS/COFINS comuns no varejo de alimentos (Lei 13.097/2015)
ST_NCM = ("2201", "2202", "2203", "2204", "2205", "2206", "2207", "2208")


def eh_lote(arquivos):
    """Mais de 20 XMLs de NF-e/NFC-e no mesmo envio: audita como lote do período."""
    return sum(1 for _, b in arquivos if b"portalfiscal.inf.br/nfe" in b[:3000]) > 20


def auditar_lote(arquivos):
    F, achou = [], set()

    def add(nivel, campo, msg, fonte=""):
        if (campo, msg) not in achou:
            achou.add((campo, msg))
            F.append({"nivel": nivel, "campo": campo, "msg": msg, "fonte": fonte})

    notas, canceladas, sem_prot, emit, mods, crts = [], 0, 0, set(), set(), set()
    por_cfop = defaultdict(float)
    rec = {"normal": 0.0, "st": 0.0, "mono": 0.0}
    prob = defaultdict(lambda: [0, 0.0, set()])  # chave -> [qtd, valor, exemplos]
    total = 0.0
    for nome, b in arquivos:
        try:
            r = ET.fromstring(b)
        except ET.ParseError:
            add("erro", "Arquivo", f"{nome}: XML inválido ou corrompido.")
            continue
        inf = r.find(".//n:infNFe", NS)
        if inf is None:
            continue
        stat = _f(r, ".//n:protNFe/n:infProt/n:cStat")
        if stat in ("101", "135", "151", "155"):
            canceladas += 1
            continue
        if stat != "100":
            sem_prot += 1
        mods.add(_f(inf, "n:ide/n:mod"))
        crts.add(_f(inf, "n:emit/n:CRT"))
        emit.add((_f(inf, "n:emit/n:CNPJ"), _f(inf, "n:emit/n:xNome")))
        num = int(_f(inf, "n:ide/n:nNF") or 0)
        serie = _f(inf, "n:ide/n:serie")
        vnf = _n(inf, "n:total/n:ICMSTot/n:vNF")
        total += vnf
        notas.append((serie, num))
        soma = 0.0
        for d in inf.findall("n:det", NS):
            p = d.find("n:prod", NS)
            nm, ncm, cfop, cest = (_f(p, "n:xProd") or "").strip(), _f(p, "n:NCM") or "", _f(p, "n:CFOP") or "", _f(p, "n:CEST")
            v = _n(p, "n:vProd") - _n(p, "n:vDesc") + _n(p, "n:vOutro")
            soma += _n(p, "n:vProd")
            ic = d.find(".//n:ICMS/*", NS)
            csosn = _f(ic, "n:CSOSN") if ic is not None else None
            cst = csosn or (_f(ic, "n:CST") if ic is not None else "") or ""
            pis = _f(d, ".//n:PIS/*/n:CST") or ""
            por_cfop[f"CFOP {cfop} · {'CSOSN' if csosn else 'CST'} {cst}"] += v
            st = cst in ST_CSOSN or cst in ST_CST
            mono = ncm.startswith(MONO) or pis in ("04", "05")
            rec["st" if st else "normal"] += v
            if mono:
                rec["mono"] += v
            up = nm.upper()
            ref = any(k in up for k in REFEICAO)

            def p_(k, txt):
                x = prob[(k, txt)]
                x[0] += 1
                x[1] += v
                if len(x[2]) < 3:
                    x[2].add(nm[:30])

            if cfop in CFOP_ST and not st:
                p_("erro", f"CFOP {cfop} (substituição tributária) com {'CSOSN' if csosn else 'CST'} {cst}; o esperado é CSOSN 500 / CST 60")
            if st and cfop not in CFOP_ST:
                p_("erro", f"{'CSOSN' if csosn else 'CST'} {cst} (ICMS já retido) com CFOP {cfop}; o esperado é 5405")
            if st and (not cest or set(cest) == {"0"}):
                p_("erro", "Produto com substituição tributária sem CEST válido (Convênio ICMS 142/2018)")
            if not st and cest and set(cest) == {"0"}:
                p_("alerta", "CEST 0000000 informado (código inválido); em produto sem substituição tributária o CEST pode ficar em branco")
            if ncm.startswith(ST_NCM) and not st:
                p_("alerta", f"NCM {ncm} (bebida) vendido como tributado normal (CSOSN/CST {cst}); bebidas costumam ter ICMS-ST: revenda com CSOSN 500 e CFOP 5405")
            if ncm.startswith("2201") is False and ncm.startswith("2202") and ("AGUA" in up or "ÁGUA" in up):
                p_("alerta", f"Água cadastrada com NCM {ncm} (refrigerantes e águas com açúcar); água mineral natural ou com gás é NCM 2201.10.00, com CEST próprio")
            if ref and not ncm.startswith(("2106", "1902", "1905", "1601", "1602", "1604")):
                p_("alerta", f"Refeição/prato com NCM {ncm}, que não é de preparação alimentícia; o usual para refeição é 2106.90.90")
            if ref and cfop == "5102":
                p_("alerta", "Refeição preparada no próprio estabelecimento com CFOP 5102 (revenda); o usual é 5101 (produção do estabelecimento), como nos demais itens")
            if mono and pis not in ("04", "05", "06") and "1" in crts:
                p_("alerta", f"Bebida monofásica de PIS/COFINS (NCM {ncm}) com CST {pis}; no Simples, segregue essa receita como monofásica no PGDAS-D para não pagar PIS e COFINS de novo")
        if abs(soma - _n(inf, "n:total/n:ICMSTot/n:vProd")) > 0.05:
            add("erro", f"Nota {num}", f"Soma dos itens {brl(soma)} difere do total de produtos.", "Manual de Orientação do Contribuinte NF-e")

    for (nivel, txt), (q, v, ex) in sorted(prob.items(), key=lambda x: (x[0][0] != "erro", -x[1][1])):
        add(nivel, "Itens", f"{txt}. {q} item(ns), {brl(v)}. Ex.: {', '.join(sorted(ex))}.", "Ajuste SINIEF 07/2005, tabela CFOP e Convênio ICMS 142/2018")

    # numeração
    for serie in sorted({s for s, _ in notas}):
        ns_ = sorted(n for s, n in notas if s == serie)
        falt = [n for n in range(ns_[0], ns_[-1]) if n not in set(ns_)]
        if falt:
            add("alerta", "Numeração", f"Série {serie}: {len(falt)} número(s) faltando entre {ns_[0]} e {ns_[-1]} ({', '.join(map(str, falt[:10]))}{'…' if len(falt) > 10 else ''}). Confira se foram cancelados ou inutilizados; nota emitida e não escriturada é omissão de receita.", "Ajuste SINIEF 07/2005, cláusula 14ª")
        else:
            add("ok", "Numeração", f"Série {serie}: sequência {ns_[0]} a {ns_[-1]} sem lacunas.")
    if sem_prot:
        add("erro", "Autorização", f"{sem_prot} nota(s) sem protocolo de autorização 100.", "Portal da NF-e")
    else:
        add("ok", "Autorização", f"{len(notas)} nota(s) autorizadas" + (f"; {canceladas} cancelada(s) fora da receita" if canceladas else "") + ".")

    simples = crts <= {"1", "2", "4"}
    if simples:
        add("info", "PGDAS-D (Simples Nacional)",
            f"Receita do período {brl(total)}. Para o PGDAS-D: {brl(rec['normal'])} sem substituição tributária; {brl(rec['st'])} com ICMS já retido por ST "
            f"(informe como 'com substituição tributária' para tirar o ICMS do DAS); e, dentro do total, {brl(rec['mono'])} de produtos monofásicos de PIS/COFINS "
            f"(informe como monofásica para tirar PIS e COFINS do DAS). Comércio e restaurante: Anexo I.", "LC 123/2006, art. 18, §4º-A, e Resolução CGSN 140/2018, art. 25")
        add("info", "IBS/CBS 2026", "Emitente do Simples: em 2026 o destaque de IBS/CBS na nota é dispensado; a regra muda a partir de 2027.", "LC 214/2025 e Ato Conjunto RFB/CGIBS 1/2025")
    add("info", "Resumo por CFOP", "; ".join(f"{k}: {brl(v)}" for k, v in sorted(por_cfop.items(), key=lambda x: -x[1])) + ".")

    nerr, nal = sum(f["nivel"] == "erro" for f in F), sum(f["nivel"] == "alerta" for f in F)
    ordem = {"erro": 0, "alerta": 1, "info": 2, "ok": 3}
    e = sorted(emit)[0] if emit else ("", "")
    tipo = "NFC-e" if mods == {"65"} else "NF-e" if mods == {"55"} else "NF-e/NFC-e"
    nota = {"origem": f"Lote de {tipo}", "numero": f"{len(notas)} notas", "prestador_cnpj": e[0], "prestador_nome": e[1], "valor_servicos": round(total, 2),
            "municipio": "", "emissao": ""}
    return {"arquivo": f"Lote de {len(notas)} {tipo} · {e[1]}", "tipo": f"Lote {tipo} de saída", "nota": nota, "municipio": "",
            "achados": sorted(F, key=lambda f: ordem[f["nivel"]]), "erros": nerr, "alertas": nal,
            "decisao": "aprovar" if not nerr and not nal else "revisar",
            "parecer": "Auditoria do lote: " + ("sem pendências." if not nerr and not nal else f"{nerr} erro(s) e {nal} alerta(s) de cadastro fiscal para corrigir no sistema de vendas."),
            "passos": [{"agente": "Auditor de lote", "acao": "Conferência de numeração, CFOP x CSOSN/CST, NCM, CEST e resumo do PGDAS-D", "detalhe": f"{nerr} erro(s), {nal} alerta(s)"}], "modo": "regras", "email": None}
