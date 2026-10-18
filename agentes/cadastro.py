"""Agente de cadastro em lote: audita o cadastro de produtos e serviços exportado de qualquer sistema (ERP),
aponta os campos da reforma tributária (LC 214/2025) que faltam ou estão errados, sugere o valor oficial,
trava o preço de venda pela margem mínima e devolve o mesmo arquivo, nas mesmas colunas, com as correções.

Regras determinísticas sobre tabelas oficiais (NFS-e Nacional: Anexos VIII e VI; LC 214/2025; leis do PIS/COFINS).
O modelo na AMD só escreve o parecer do lote (1 chamada), conferido pela trava de citação em agentes.py.
"""
import json
import pathlib
import re
import unicodedata

from nfe import CESTA, MONO

_R = pathlib.Path(__file__).resolve().parents[1]
BASE = next((p for p in (_R / "data", _R / "public" / "fiscal") if p.is_dir()), _R / "data")
DATA = json.loads((BASE / "data.json").read_text(encoding="utf-8"))

FONTES = {
    "LC 214/2025": "https://www.planalto.gov.br/ccivil_03/leis/lcp/lcp214.htm",
    "LC 116/2003": "https://www.planalto.gov.br/ccivil_03/leis/lcp/lcp116.htm",
    "NFS-e Nacional (Anexos VI e VIII)": "https://www.gov.br/nfse/pt-br/biblioteca/documentacao-tecnica",
    "Lei 10.147/2000 (monofásico)": "https://www.planalto.gov.br/ccivil_03/leis/l10147.htm",
    "Tabela NCM (Receita Federal)": "https://www.gov.br/receitafederal/pt-br/assuntos/aduana-e-comercio-exterior/classificacao-fiscal-de-mercadorias/ncm",
}

# nomes de coluna aceitos (o arquivo pode vir de qualquer sistema; procuramos pelo sentido do nome)
CAMPOS = {
    "codigo": r"^(cod|sku|ref|id)",
    "descricao": r"descr|produto|servi|nome|item$",
    "ncm": r"\bncm\b|^ncm",
    "item_lc116": r"lc ?116|item.*servi|subitem|cod.*servi|ctribnac",
    "nbs": r"\bnbs\b",
    "cst_pis": r"cst.*(pis|cofins)",
    "cclasstrib": r"cclasstrib|class.*trib",
    "custo": r"custo",
    "preco": r"pre[cç]o|valor.*venda|venda",
    "carga": r"carga|aliq.*total|tributos?.*%",
}


def _nz(s):
    return unicodedata.normalize("NFD", str(s or "")).encode("ascii", "ignore").decode().lower().strip()


def _num(v):
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = re.sub(r"[^\d,.-]", "", str(v))
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def brl(x):
    return f"R$ {x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def mapear_colunas(cab):
    """Associa cada coluna do arquivo a um campo conhecido (a primeira que bater). Colunas sugeridas por nós contam."""
    m = {}
    for i, h in enumerate(cab):
        h2 = _nz(h).replace("_sugerido", "")
        for campo, rx in CAMPOS.items():
            if campo not in m and re.search(rx, h2):
                m[campo] = i
                break
    return m


def _servico(item):
    """Item da LC 116 (ex.: 1.01 ou 01.01) -> NBS e cClassTrib pelas tabelas da NFS-e Nacional."""
    d = re.findall(r"\d+", str(item))
    if len(d) < 2:
        return None
    sub = f"{int(d[0]):02d}.{int(d[1]):02d}"
    ct = next((c for c in DATA["ct"] if c[0].startswith(sub.replace(".", ""))), None)
    if not ct:
        return None
    cls = DATA["L"][ct[4]] if isinstance(ct[4], int) and ct[4] < len(DATA["L"]) else []
    nb = (DATA["nb"].get(sub) or [[""]])[0][0]
    return {"item": f"{int(d[0])}.{d[1]}", "ctrib": ct[0], "nome": ct[1], "nbs": nb,
            "cclasstrib": "000001" if "000001" in cls else (cls[0] if cls else "")}


def auditar_linha(v, m, margem_min=0.10, aliq_ref=0.265):
    """v: valores da linha; m: mapa de colunas. Devolve status, apontamentos e as correções sugeridas."""
    g = lambda c: v[m[c]] if c in m and m[c] < len(v) else None
    ap, sug, fontes = [], {}, set()
    ncm = re.sub(r"\D", "", str(g("ncm") or ""))
    item = g("item_lc116")
    servico = bool(item) and not ncm
    if not servico:
        if not ncm:
            ap.append(("erro", "NCM ausente: produto sem classificação fiscal."))
            fontes.add("Tabela NCM (Receita Federal)")
        elif len(ncm) != 8:
            ap.append(("erro", f"NCM {ncm} com {len(ncm)} dígitos (deve ter 8)."))
            fontes.add("Tabela NCM (Receita Federal)")
        cesta = bool(ncm) and ncm.startswith(CESTA)
        if cesta:
            ap.append(("alerta", "Cesta Básica Nacional: redução de 100% de IBS/CBS; informe o cClassTrib de redução da tabela oficial."))
            fontes.add("LC 214/2025")
        elif not g("cclasstrib"):
            sug["cclasstrib"] = "000001"
            ap.append(("alerta", "cClassTrib ausente: sugerido 000001 (tributação integral pelo IBS e CBS)."))
            fontes.add("LC 214/2025")
        if ncm and ncm.startswith(MONO) and str(g("cst_pis") or "").strip() not in ("04", "4", ""):
            sug["cst_pis"] = "04"
            ap.append(("erro", f"Produto do PIS/COFINS monofásico (NCM {ncm[:4]}) com CST {g('cst_pis')}: na revenda o CST é 04 (corrigido no arquivo)."))
            fontes.add("Lei 10.147/2000 (monofásico)")
        red = 1.0 if cesta else 0.0
    else:
        s = _servico(item)
        red = 0.0
        if not s:
            ap.append(("erro", f"Item {item} não existe na LC 116."))
            fontes.add("LC 116/2003")
        else:
            if not g("nbs"):
                sug["nbs"] = s["nbs"]
                ap.append(("alerta", f"NBS ausente: sugerido {s['nbs']} para o item {s['item']} ({s['nome'][:60]})."))
            elif re.sub(r"\D", "", str(g("nbs"))) != re.sub(r"\D", "", s["nbs"]):
                ap.append(("alerta", f"NBS {g('nbs')} difere da correlação oficial do item {s['item']}: {s['nbs']}."))
            if not g("cclasstrib") and s["cclasstrib"]:
                sug["cclasstrib"] = s["cclasstrib"]
                ap.append(("alerta", f"cClassTrib ausente: sugerido {s['cclasstrib']}."))
            fontes.add("NFS-e Nacional (Anexos VI e VIII)")
    # trava de preço: IBS/CBS são calculados "por fora"; mantendo o preço final, quanto sobra de margem?
    custo, preco = _num(g("custo")), _num(g("preco"))
    if custo and preco:
        t = aliq_ref * (1 - red)
        liq = preco / (1 + t)
        mg = (liq - custo) / liq if liq else 0
        sug["margem_pos_reforma"] = round(mg * 100, 1)
        sug["preco_minimo"] = round(custo / (1 - margem_min) * (1 + t), 2)
        if mg < 0:
            ap.append(("erro", f"Trava de preço: com IBS/CBS de {t*100:.1f}% o preço de {brl(preco)} dá prejuízo (margem {mg*100:.1f}%). Preço mínimo: {brl(sug['preco_minimo'])}."))
        elif mg < margem_min:
            ap.append(("alerta", f"Trava de preço: margem cai para {mg*100:.1f}% (mínima {margem_min*100:.0f}%). Preço mínimo: {brl(sug['preco_minimo'])}."))
        fontes.add("LC 214/2025")
    nivel = "corrigir" if any(n == "erro" for n, _ in ap) else "revisar" if ap else "ok"
    return {"status": nivel, "apontamentos": [{"nivel": n, "msg": t} for n, t in ap], "sugestoes": sug,
            "fontes": [{"nome": f, "url": FONTES[f]} for f in sorted(fontes)], "tipo": "serviço" if servico else "produto"}


def auditar_cadastro(cab, linhas, margem_min=0.10, aliq_ref=0.265):
    """Audita o cadastro inteiro e devolve o resumo, cada linha auditada e o arquivo corrigido no layout original."""
    m = mapear_colunas(cab)
    res = [auditar_linha(v, m, margem_min, aliq_ref) for v in linhas]
    novas = ["CST PIS/COFINS", "NBS", "cClassTrib", "Margem pós-reforma (%)", "Preço mínimo (R$)", "Status Vyson", "Apontamentos Vyson"]
    chaves = {"CST PIS/COFINS": "cst_pis", "NBS": "nbs", "cClassTrib": "cclasstrib", "Margem pós-reforma (%)": "margem_pos_reforma", "Preço mínimo (R$)": "preco_minimo"}
    saida_cab = list(cab) + [c for c in novas if c in ("Status Vyson", "Apontamentos Vyson") or chaves[c] not in m]
    saida = []
    for v, r in zip(linhas, res):
        lin = list(v) + [""] * (len(cab) - len(v))
        for c, k in chaves.items():  # preenche no lugar se a coluna já existe e está vazia
            if k in m and k in r["sugestoes"] and (not lin[m[k]] or k == "cst_pis"):
                lin[m[k]] = r["sugestoes"][k]
        for c in saida_cab[len(cab):]:
            lin.append(r["status"] if c == "Status Vyson" else " | ".join(a["msg"] for a in r["apontamentos"]) if c == "Apontamentos Vyson"
                       else r["sugestoes"].get(chaves[c], ""))
        saida.append(lin)
    cont = {k: sum(r["status"] == k for r in res) for k in ("ok", "revisar", "corrigir")}
    return {"colunas_reconhecidas": {k: cab[i] for k, i in m.items()}, "resumo": cont, "total": len(res),
            "linhas": res, "arquivo": {"cabecalho": saida_cab, "linhas": saida}}
