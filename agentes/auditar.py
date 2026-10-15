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

    if item in ("7.02", "7.05"):
        add("info", "Base de cálculo", "Construção civil: o ISS pode excluir os materiais fornecidos pelo prestador. Conferir as deduções.", "LC 116/2003, art. 7º, §2º, I")

    for campo in ("numero", "prestador_cnpj", "valor_servicos"):
        if not d.get(campo):
            add("alerta", campo, "Campo não encontrado no documento.")

    nerr = sum(f["nivel"] == "erro" for f in F)
    nal = sum(f["nivel"] == "alerta" for f in F)
    decisao = "aprovar" if not nerr and not nal else "revisar" if not nerr else "devolver ao fornecedor"
    ordem = {"erro": 0, "alerta": 1, "info": 2, "ok": 3}
    return {"municipio": mun, "achados": sorted(F, key=lambda f: ordem[f["nivel"]]), "erros": nerr, "alertas": nal, "decisao": decisao}
