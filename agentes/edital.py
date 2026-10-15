"""Agente de edital: lê o edital (PDF, Word ou texto), analisa as exigências e gera a documentação modelo da proposta.

Passos:
  1. Leitor: extrai número, órgão, objeto, modalidade, data da sessão, valor estimado e critério de julgamento.
  2. Analista de habilitação: encontra as exigências (atestados, índices contábeis, capital mínimo, garantia, visita técnica,
     registro em conselho, certidões) e indica a página/trecho onde aparecem.
  3. Analista de riscos: aponta prazos curtos, exclusividade ME/EPP, garantia, amostra, consórcio vedado e subcontratação.
  4. Redator: gera em Word as declarações da Lei 14.133/2021 e a proposta comercial modelo, já com os dados da empresa.
"""
import io
import re
from datetime import date

EXIG = [
    ("Atestado de capacidade técnica", r"atestado[s]? de capacidade t[ée]cnica|capacidade t[ée]cnico-operacional|capacidade t[ée]cnico-profissional"),
    ("Registro no conselho (CREA, CAU, CRC…)", r"\b(CREA|CAU|CRC|CRA|CRM|OAB)\b"),
    ("Índices contábeis (LG, SG, LC)", r"liquidez geral|solv[êe]ncia geral|liquidez corrente|\bLG\b.{0,20}\bSG\b"),
    ("Capital social ou patrimônio líquido mínimo", r"(capital social|patrim[ôo]nio l[íi]quido) m[íi]nimo|n[ãa]o inferior a \d{1,2} ?%"),
    ("Balanço patrimonial", r"balan[çc]o patrimonial"),
    ("Garantia da proposta ou do contrato", r"garantia (da proposta|contratual|de execu[çc][ãa]o)|cau[çc][ãa]o|seguro-garantia|fian[çc]a banc[áa]ria"),
    ("Visita técnica", r"visita t[ée]cnica|vistoria"),
    ("Amostra ou prova de conceito", r"\bamostra|prova de conceito"),
    ("Certidões fiscais e trabalhistas", r"regularidade fiscal|CNDT|certid[ãa]o negativa|FGTS"),
    ("Planilha de custos / BDI", r"planilha (de custos|or[çc]ament[áa]ria)|\bBDI\b|composi[çc][ãa]o de custos"),
    ("Certificação (ISO, etc.)", r"\bISO ?\d{4,5}|certifica[çc][ãa]o"),
]
RISCOS = [
    ("Exclusiva para ME/EPP", r"exclusiv[ao] (para|a) (microempresas?|ME)|participa[çc][ãa]o exclusiva", "Só microempresas e empresas de pequeno porte podem participar (LC 123/2006, art. 48)."),
    ("Consórcio vedado", r"vedad[ao] a participa[çc][ãa]o de (empresas reunidas em )?cons[óo]rcio|n[ãa]o ser[áa] admitida a participa[çc][ãa]o de cons[óo]rcio", "Não é possível participar em consórcio."),
    ("Subcontratação limitada", r"subcontrata[çc][ãa]o", "O edital trata de subcontratação: confira o limite antes de contar com terceiros."),
    ("Margem de preferência", r"margem de prefer[êe]ncia", "Há margem de preferência para produtos nacionais (Lei 14.133/2021, art. 26)."),
    ("Reajuste e repactuação", r"reajuste|repactua[çc][ãa]o", "Confira o índice e a data-base do reajuste."),
]


def ler_texto(nome, dados):
    ext = nome.lower().rsplit(".", 1)[-1]
    if ext == "pdf":
        import pdfplumber
        with pdfplumber.open(io.BytesIO(dados)) as pdf:
            return [(i + 1, p.extract_text() or "") for i, p in enumerate(pdf.pages[:300])]
    if ext == "docx":
        import docx
        d = docx.Document(io.BytesIO(dados))
        return [(1, "\n".join(p.text for p in d.paragraphs))]
    return [(1, dados.decode("utf-8", "ignore"))]


def analisar(nome, dados):
    paginas = ler_texto(nome, dados)
    tudo = "\n".join(t for _, t in paginas)
    g = lambda rx: (lambda m: m.group(1).strip() if m else None)(re.search(rx, tudo, re.I))
    cab = {
        "numero": g(r"(?:PREG[ÃA]O|CONCORR[ÊE]NCIA|EDITAL|DISPENSA)[^\n]{0,30}?N[º°o.]*\s*([\d./-]+)"),
        "modalidade": g(r"\b(Preg[ãa]o Eletr[ôo]nico|Concorr[êe]ncia(?: Eletr[ôo]nica)?|Dispensa Eletr[ôo]nica|Leil[ãa]o|Di[áa]logo Competitivo)\b"),
        "orgao": g(r"\n?([A-ZÀ-Ú][A-ZÀ-Ú ]{8,}(?:PREFEITURA|SECRETARIA|TRIBUNAL|MINIST[ÉE]RIO|UNIVERSIDADE|C[ÂA]MARA|GOVERNO)[A-ZÀ-Ú ]*|(?:PREFEITURA|SECRETARIA|TRIBUNAL|MINIST[ÉE]RIO|UNIVERSIDADE|C[ÂA]MARA|GOVERNO)[A-ZÀ-Ú ,]{4,80})"),
        "objeto": g(r"OBJETO\s*[:\-–]?\s*(?:\d[\d.]*\s*)?(.{20,400}?)(?:\.\s|\n\d)"),
        "sessao": g(r"(?:sess[ãa]o p[úu]blica|abertura das propostas|data da sess[ãa]o)[^\n]{0,60}?(\d{2}/\d{2}/\d{4}(?:[^\n]{0,15}?\d{1,2}[h:]\d{2})?)"),
        "valor": g(r"valor (?:total )?(?:estimado|global|m[áa]ximo)[^\n]{0,40}?R\$\s*(\d[\d.]*,\d{2})"),
        "criterio": g(r"(menor pre[çc]o|maior desconto|t[ée]cnica e pre[çc]o|melhor t[ée]cnica)"),
    }
    exig = []
    for nomex, rx in EXIG:
        pags = [p for p, t in paginas if re.search(rx, t, re.I)]
        if pags:
            m = re.search(r"[^.\n]{0,120}(" + rx + r")[^.\n]{0,160}", tudo, re.I)
            exig.append({"exigencia": nomex, "paginas": pags[:6], "trecho": re.sub(r"\s+", " ", m.group(0)).strip()[:260] if m else ""})
    riscos = [{"risco": n, "nota": nota} for n, rx, nota in RISCOS if re.search(rx, tudo, re.I)]
    UN = r"(UN|UND|UNID|UNIDADE|SC|SACO|KG|M2|M²|M3|M³|M|ML|L|LT|CX|CAIXA|PCT|PACOTE|PC|PÇ|PEÇA|DIA|DIÁRIA|MÊS|MES|SERV|SERVIÇO|SV|HORA|H|KIT|RESMA|GL|GALÃO)"
    itens = []
    for ln in tudo.splitlines():
        m = re.match(r"^\s*(\d{1,3})[\s.|)\-–]+(.{4,160}?)\s*[|;]?\s*" + UN + r"\.?\s*[|;]?\s*(\d+(?:[.,]\d+)?)\s*$", ln, re.I) or \
            re.match(r"^\s*(\d{1,3})[\s.|)\-–]+(.{4,160}?)\s*[|;]?\s*(\d+(?:[.,]\d+)?)\s*[|;]?\s*" + UN + r"\.?\s*$", ln, re.I)
        if m:
            a, b = (m.group(3), m.group(4)) if not m.group(3)[0].isdigit() else (m.group(4), m.group(3))
            try:
                itens.append({"item": int(m.group(1)), "descricao": m.group(2).strip(" |;-–"), "unidade": a.lower(), "quantidade": float(b.replace(".", "").replace(",", "."))})
            except ValueError:
                pass
    lg = re.search(r"liquidez geral[^\n]{0,40}?(\d[,.]\d{1,2})", tudo, re.I)
    if lg:
        riscos.append({"risco": "Índice mínimo", "nota": f"Liquidez geral mínima de {lg.group(1)}: confira no seu balanço antes de participar."})
    passos = [f"Leitor: {len(paginas)} página(s); edital {cab['numero'] or 'sem número'}, {cab['modalidade'] or 'modalidade não identificada'}",
              f"Analista de habilitação: {len(exig)} exigência(s) encontradas", f"Analista de riscos: {len(riscos)} ponto(s) de atenção",
              "Redator: declarações da Lei 14.133/2021 e proposta comercial prontas para baixar"]
    passos.insert(1, f"Leitor de itens: {len(itens)} item(ns) do termo de referência")
    return {"cabecalho": cab, "exigencias": exig, "riscos": riscos, "itens": itens[:200], "passos": passos, "paginas": len(paginas)}


DECL = [
    ("Declaração de cumprimento dos requisitos de habilitação", "declara, sob as penas da lei, que cumpre plenamente os requisitos de habilitação exigidos no edital, conforme o art. 63, I, da Lei nº 14.133/2021."),
    ("Declaração de inexistência de fato impeditivo", "declara, sob as penas da lei, que não há fato impeditivo à sua habilitação e que não está impedida ou declarada inidônea para licitar ou contratar com a Administração Pública (Lei nº 14.133/2021, arts. 14 e 156)."),
    ("Declaração de não emprego de menor", "declara, para fins do art. 7º, XXXIII, da Constituição Federal e do art. 68, VI, da Lei nº 14.133/2021, que não emprega menor de 18 anos em trabalho noturno, perigoso ou insalubre, nem menor de 16 anos, salvo na condição de aprendiz, a partir de 14 anos."),
    ("Declaração de reserva de cargos para pessoa com deficiência", "declara que cumpre as exigências de reserva de cargos para pessoa com deficiência e para reabilitado da Previdência Social previstas em lei (Lei nº 14.133/2021, art. 63, IV; Lei nº 8.213/1991, art. 93)."),
    ("Declaração de elaboração independente da proposta", "declara que a proposta apresentada foi elaborada de maneira independente, sem consulta, acordo ou troca de informações com qualquer outro participante, e que seu conteúdo não foi informado a terceiros antes da abertura oficial."),
    ("Declaração de que a proposta compreende todos os custos", "declara que a proposta econômica compreende a integralidade dos custos para atendimento dos direitos trabalhistas assegurados na Constituição Federal, nas leis trabalhistas, nas normas infralegais, nas convenções coletivas de trabalho e nos termos de ajustamento de conduta vigentes na data de entrega da proposta (Lei nº 14.133/2021, art. 63, §1º)."),
]
DECL_ME = ("Declaração de enquadramento como ME/EPP", "declara, sob as penas da lei, que se enquadra como microempresa ou empresa de pequeno porte, nos termos do art. 3º da Lei Complementar nº 123/2006, e que não se encontra em nenhuma das vedações do §4º do mesmo artigo, estando apta a usufruir do tratamento favorecido previsto nos arts. 42 a 49.")


def gerar_docx(analise, empresa):
    """Gera um Word com todas as declarações e a proposta comercial modelo, preenchidas com os dados informados."""
    import docx
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt
    d = docx.Document()
    st = d.styles["Normal"]
    st.font.name, st.font.size = "Arial", Pt(11)
    cab = analise["cabecalho"]
    e = {k: (empresa.get(k) or f"[{k.upper()}]") for k in ("razao", "cnpj", "endereco", "representante", "cargo", "cidade")}
    ident = f"{e['razao']}, inscrita no CNPJ sob o nº {e['cnpj']}, com sede em {e['endereco']}, por seu representante legal {e['representante']}, {e['cargo']},"
    ref = f"Ref.: {cab.get('modalidade') or 'Licitação'} nº {cab.get('numero') or '____'}" + (f" — {cab['orgao']}" if cab.get("orgao") else "")
    hoje = date.today().strftime("%d/%m/%Y")
    docs = DECL + ([DECL_ME] if empresa.get("me") else [])
    for i, (titulo, corpo) in enumerate(docs):
        if i:
            d.add_page_break()
        h = d.add_paragraph()
        h.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r = h.add_run(titulo.upper())
        r.bold = True
        d.add_paragraph(ref)
        p = d.add_paragraph(f"{ident} {corpo}")
        p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        d.add_paragraph(f"\n{e['cidade']}, {hoje}.\n\n\n______________________________\n{e['representante']}\n{e['cargo']}").alignment = WD_ALIGN_PARAGRAPH.CENTER
    d.add_page_break()
    h = d.add_paragraph()
    h.alignment = WD_ALIGN_PARAGRAPH.CENTER
    h.add_run("PROPOSTA COMERCIAL").bold = True
    d.add_paragraph(ref)
    d.add_paragraph(f"Objeto: {cab.get('objeto') or '[descrever o objeto conforme o edital]'}").alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    t = d.add_table(rows=1, cols=5)
    t.style = "Table Grid"
    for c, txt in zip(t.rows[0].cells, ["Item", "Descrição", "Quantidade", "Valor unitário (R$)", "Valor total (R$)"]):
        c.text = txt
    for i in range(1, 4):
        for c, txt in zip(t.add_row().cells, [str(i), "", "", "", ""]):
            c.text = txt
    for linha in ["Valor global da proposta: R$ ____________ (por extenso).",
                  "Validade da proposta: 60 (sessenta) dias a contar da data da sessão pública.",
                  "Nos preços estão incluídos todos os custos diretos e indiretos, tributos, encargos sociais e trabalhistas, seguros, fretes e o BDI.",
                  f"Dados bancários: Banco ____ Agência ____ Conta ____, em nome de {e['razao']}."]:
        d.add_paragraph(linha).alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    d.add_paragraph(f"\n{e['cidade']}, {hoje}.\n\n\n______________________________\n{e['representante']}\n{e['cargo']}").alignment = WD_ALIGN_PARAGRAPH.CENTER
    out = io.BytesIO()
    d.save(out)
    return out.getvalue()


# ---------------- validação dos documentos preenchidos contra o edital ----------------
TIPOS_DOC = [
    ("Declaração de cumprimento dos requisitos de habilitação", r"cumpre plenamente os requisitos de habilita"),
    ("Declaração de inexistência de fato impeditivo", r"fato impeditivo"),
    ("Declaração de não emprego de menor", r"menor de (18|dezoito)"),
    ("Declaração de reserva de cargos para pessoa com deficiência", r"pessoa com defici[êe]ncia"),
    ("Declaração de elaboração independente da proposta", r"elaborad[ao] de maneira independente"),
    ("Declaração de que a proposta compreende todos os custos", r"integralidade dos custos"),
    ("Declaração de enquadramento como ME/EPP", r"microempresa ou empresa de pequeno porte"),
    ("Proposta comercial", r"proposta comercial|valor global da proposta"),
    ("CND federal (Receita/PGFN)", r"tributos federais e [àa] d[íi]vida ativa da uni[ãa]o"),
    ("Certificado do FGTS (CRF)", r"certificado de regularidade do fgts|\bCRF\b"),
    ("CNDT (débitos trabalhistas)", r"certid[ãa]o negativa de d[ée]bitos trabalhistas"),
    ("Balanço patrimonial", r"balan[çc]o patrimonial"),
    ("Atestado de capacidade técnica", r"atesta(mos)? (para os devidos fins )?que|atestado de capacidade"),
    ("Contrato social", r"contrato social|ato constitutivo"),
]


def validar(analise, docs, empresa):
    """docs: lista de (nome, bytes). Confere presença, CNPJ, referência ao edital, validade e campos em branco."""
    num = re.sub(r"\D", "", (analise["cabecalho"].get("numero") or ""))
    cnpj = re.sub(r"\D", "", empresa.get("cnpj") or "")
    hoje = date.today()
    achados, presentes = [], {}
    for nome, dados in docs:
        texto = "\n".join(t for _, t in ler_texto(nome, dados))
        tipos = [t for t, rx in TIPOS_DOC if re.search(rx, texto, re.I)]
        tipo = tipos[0] if tipos else None
        probs = []
        if not tipo:
            probs.append("Tipo de documento não reconhecido")
        cn = [re.sub(r"\D", "", c) for c in re.findall(r"\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}", texto)]
        if cnpj and cn and cnpj not in cn:
            probs.append("CNPJ do documento diferente do CNPJ da empresa")
        if tipo and tipo.startswith(("Declaração", "Proposta")):
            if num and num not in re.sub(r"\D", "", texto):
                probs.append(f"Não cita o número do edital ({analise['cabecalho'].get('numero')})")
            if re.search(r"\[[A-ZÇÃ_ ]{3,}\]|_{6,}", texto):
                probs.append("Há campos em branco para preencher")
        val = re.search(r"(?:v[áa]lid[ao] at[ée]|validade[: ]+(?:at[ée] )?)\s*(\d{2})/(\d{2})/(\d{4})", texto, re.I)
        if val:
            try:
                dv = date(int(val.group(3)), int(val.group(2)), int(val.group(1)))
                if dv < hoje:
                    probs.append(f"Vencido em {dv.strftime('%d/%m/%Y')}")
                elif (dv - hoje).days <= 10:
                    probs.append(f"Vence em {dv.strftime('%d/%m/%Y')}: renove antes da sessão")
            except ValueError:
                pass
        for t in tipos:
            presentes[t] = presentes.get(t, 0) + 1
        achados.append({"arquivo": nome, "tipo": ", ".join(tipos) if len(tipos) <= 2 else f"{len(tipos)} documentos no mesmo arquivo", "status": "ok" if not probs else "corrigir", "problemas": probs})
    exigidos = [t for t, _ in DECL] + ["Proposta comercial", "CND federal (Receita/PGFN)", "Certificado do FGTS (CRF)", "CNDT (débitos trabalhistas)"]
    if empresa.get("me"):
        exigidos.append(DECL_ME[0])
    for x in analise.get("exigencias", []):
        if x["exigencia"] in ("Balanço patrimonial", "Atestado de capacidade técnica"):
            exigidos.append(x["exigencia"])
    faltando = [t for t in dict.fromkeys(exigidos) if t not in presentes]
    ok = sum(a["status"] == "ok" for a in achados)
    return {"documentos": achados, "faltando": faltando, "resumo": f"{ok} de {len(achados)} documento(s) sem pendência; {len(faltando)} exigido(s) ainda não enviado(s)"}
