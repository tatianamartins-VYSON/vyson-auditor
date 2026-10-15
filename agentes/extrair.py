"""Extrai os campos da nota fiscal de serviço a partir de XML (NFS-e Nacional ou ABRASF), PDF ou DOCX."""
import io
import re
import xml.etree.ElementTree as ET

CAMPOS = ("numero", "emissao", "prestador_cnpj", "prestador_nome", "tomador_cnpj", "tomador_nome", "municipio",
          "item_lc116", "ctribnac", "cod_municipal", "descricao", "valor_servicos", "deducoes", "aliquota_iss",
          "valor_iss", "iss_retido", "inss_retido", "valor_liquido")


def _num(s):
    if s is None or s == "":
        return None
    s = str(s).strip().replace("R$", "").replace("%", "").strip()
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _xml(texto: str) -> dict:
    raiz = ET.fromstring(texto)
    todos = {}
    for el in raiz.iter():
        nome = el.tag.split("}")[-1]
        if el.text and el.text.strip() and nome not in todos:
            todos[nome] = el.text.strip()
    g = lambda *ns: next((todos[n] for n in ns if n in todos), None)
    ctrib = g("cTribNac")
    item = g("ItemListaServico")
    if not item and ctrib:
        item = f"{int(ctrib[:2])}.{ctrib[2:4]}"
    aliq = _num(g("pAliq", "Aliquota"))
    if aliq is not None and aliq < 1:  # ABRASF usa 0.05 para 5%
        aliq *= 100
    if "tpRetISSQN" in todos:  # NFS-e Nacional: 1 = não retido, 2 = tomador, 3 = intermediário
        retido = todos["tpRetISSQN"] in ("2", "3")
    elif "IssRetido" in todos:  # ABRASF: 1 = sim, 2 = não
        retido = todos["IssRetido"] == "1"
    else:
        retido = None
    return {
        "numero": g("nNFSe", "Numero", "nDPS"), "emissao": g("dhEmi", "DataEmissao", "dCompet"),
        "prestador_cnpj": g("CNPJ", "Cnpj"), "prestador_nome": g("xNome", "RazaoSocial"),
        "tomador_cnpj": None, "tomador_nome": None,
        "municipio": g("cLocPrestacao", "CodigoMunicipio", "cLocIncid"),
        "item_lc116": item, "ctribnac": ctrib, "cod_municipal": g("cTribMun", "CodigoTributacaoMunicipio"),
        "descricao": g("xDescServ", "Discriminacao"),
        "valor_servicos": _num(g("vServ", "ValorServicos")), "deducoes": _num(g("vDR", "ValorDeducoes")),
        "aliquota_iss": aliq, "valor_iss": _num(g("vISSQN", "ValorIss")),
        "iss_retido": retido,
        "inss_retido": _num(g("vRetCP", "ValorInss")), "valor_liquido": _num(g("vLiq", "ValorLiquidoNfse")),
    }


ROTULOS = {
    "numero": r"(?:N[úu]mero da (?:Nota|NFS-?e)|NFS-?e n[ºo°]?)\s*[:\-]?\s*(\d+)",
    "emissao": r"(?:Data (?:e Hora )?(?:de )?Emiss[ãa]o)\s*[:\-]?\s*([\d/]{10})",
    "item_lc116": r"Item[^\n:]*:\s*(\d{1,2}\.\d{2})\b",
    "cod_municipal": r"C[óo]digo (?:de )?(?:Tributa[çc][ãa]o|Servi[çc]o) Municipal\s*[:\-]?\s*([\d.\-/]+)",
    "valor_servicos": r"Valor (?:Total )?(?:dos )?Servi[çc]os\s*[:\-]?\s*R?\$?\s*([\d.,]+)",
    "deducoes": r"Dedu[çc][õo]es\s*[:\-]?\s*R?\$?\s*([\d.,]+)",
    "aliquota_iss": r"Al[íi]quota(?: do ISS)?\s*(?:\(%\))?\s*[:\-]?\s*([\d.,]+)\s*%?",
    "valor_iss": r"Valor (?:do )?ISS(?:QN)?\s*[:\-]?\s*R?\$?\s*([\d.,]+)",
    "inss_retido": r"INSS(?: Retido)?\s*[:\-]?\s*R?\$?\s*([\d.,]+)",
    "valor_liquido": r"Valor L[íi]quido(?: da Nota)?\s*[:\-]?\s*R?\$?\s*([\d.,]+)",
    "municipio": r"Munic[íi]pio(?: de Incid[êe]ncia| da Presta[çc][ãa]o)?\s*[:\-]?\s*([A-ZÀ-Úa-zà-ú ]+?(?:/[A-Z]{2})?)\s*(?:\n|$)",
    "descricao": r"(?:Discrimina[çc][ãa]o|Descri[çc][ãa]o) (?:dos |do )?Servi[çc]os?\s*[:\-]?\s*(.+)",
}


def _texto(texto: str) -> dict:
    d = {k: None for k in CAMPOS}
    for k, rx in ROTULOS.items():
        m = re.search(rx, texto, re.I)
        if m:
            v = m.group(1).strip()
            d[k] = _num(v) if k in ("valor_servicos", "deducoes", "aliquota_iss", "valor_iss", "inss_retido", "valor_liquido") else v
    # tabela em duas linhas: rótulos numa linha e valores na seguinte (layout comum de prefeituras)
    linhas = texto.splitlines()
    TAB = [("valor_liquido", r"Valor L[íi]quido"), ("deducoes", r"Dedu[çc][õo]es"), ("base", r"Base de C[áa]lculo"),
           ("aliquota_iss", r"Al[íi]quota"), ("valor_iss", r"Valor do ISS"), ("valor_servicos", r"Valor (?:Total )?(?:dos )?Servi[çc]os")]
    for i, ln in enumerate(linhas[:-1]):
        achados = sorted((m.start(), k) for k, rx in TAB for m in [re.search(rx, ln, re.I)] if m)
        nums = re.findall(r"\d[\d.]*,\d{2}", linhas[i + 1])
        if len(achados) >= 3 and len(nums) == len(achados):
            for (_, k), v in zip(achados, nums):
                d[k] = _num(v)
    sec = lambda nome: re.search(nome + r"[\s\S]{0,300}?(\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2})", texto, re.I)
    p, t = sec(r"PRESTADOR"), sec(r"TOMADOR")
    if p:
        d["prestador_cnpj"] = p.group(1)
    if t:
        d["tomador_cnpj"] = t.group(1)
    if not d.get("numero"):
        m = re.search(r"N[úu]mero da Nota:?[\s\S]{0,120}?\b(\d{6,15})\b", texto, re.I)
        d["numero"] = m.group(1) if m else None
    if d.get("valor_servicos") is None:
        m = re.search(r"VALOR TOTAL DA NOTA\s*=?\s*R?\$?\s*([\d.,]+)", texto, re.I)
        d["valor_servicos"] = _num(m.group(1)) if m else d.get("base")
    d["simples"] = bool(re.search(r"Simples Nacional", texto, re.I))
    d["isento"] = bool(re.search(r"\b(ISEN[ÇC][ÃA]O|Isentos?/Imunes|sem a incid[êe]ncia de ISS)", texto, re.I))
    d["locacao_bens"] = bool(re.search(r"(LOCA[ÇC][ÃA]O|ALUGUEL) DE (M[ÁA]QUINAS|EQUIPAMENTOS|BENS)", texto, re.I))
    cnpjs = [] if p else re.findall(r"\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}", texto)
    if cnpjs:
        d["prestador_cnpj"] = cnpjs[0]
        d["tomador_cnpj"] = cnpjs[1] if len(cnpjs) > 1 else None
    d["iss_retido"] = bool(re.search(r"ISS\s*Retido\s*[:\-]?\s*(Sim|S\b)", texto, re.I)) if re.search(r"ISS\s*Retido", texto, re.I) else None
    return d


def extrair(nome: str, dados: bytes) -> dict:
    ext = nome.lower().rsplit(".", 1)[-1]
    if ext == "xml" or dados.lstrip()[:1] == b"<":
        d, origem = _xml(dados.decode("utf-8", "ignore")), "XML"
    elif ext == "pdf":
        import pdfplumber
        with pdfplumber.open(io.BytesIO(dados)) as pdf:
            texto = "\n".join(p.extract_text() or "" for p in pdf.pages)
        d, origem = _texto(texto), "PDF"
    elif ext == "docx":
        import docx
        doc = docx.Document(io.BytesIO(dados))
        linhas = [p.text for p in doc.paragraphs] + [" ".join(c.text for c in r.cells) for t in doc.tables for r in t.rows]
        d, origem = _texto("\n".join(linhas)), "DOCX"
    else:
        d, origem = _texto(dados.decode("utf-8", "ignore")), "texto"
    d["origem"] = origem
    return d
