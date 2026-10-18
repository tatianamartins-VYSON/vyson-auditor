"""Sistema multiagente de auditoria fiscal: um coordenador planeja e chama agentes especialistas como ferramentas.

Agentes (ferramentas):
  extrator      lê XML, PDF ou DOCX e devolve os campos da nota
  auditor_iss   confere NFS-e: LC 116, alíquota municipal, valor do ISS, INSS
  auditor_icms  confere NF-e: chave, CFOP, alíquota interestadual, ICMS, totais, DIFAL
  cnpj          consulta a situação cadastral do CNPJ (BrasilAPI / Receita Federal)
  juridico      busca na base de legislação o fundamento de cada achado
  comunicador   escreve o e-mail ao fornecedor e o parecer final

Modelo (variáveis de ambiente, nunca no código; endpoint compatível com OpenAI e chamada de ferramentas):
  Gemini:   LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai  LLM_MODEL=gemini-3-flash-preview  LLM_API_KEY=<chave do AI Studio>
  AMD:      LLM_BASE_URL=https://developer.amd.com.cn/radeon/api/v1  LLM_MODEL=Gemma-4-31B-IT  LLM_API_KEY=<rc-...>
Sem modelo, um planejador de regras executa o mesmo roteiro, para a demonstração nunca parar.
"""
import json
import os
import pathlib
import re
import time
import urllib.parse
import urllib.error
import urllib.request

import evolus
from auditar import auditar
from extrair import extrair
from nfe import auditar_nfe, eh_nfe
from internacional import auditar_india, auditar_italia, auditar_uae, eh_india, eh_italia, eh_uae

_R = pathlib.Path(__file__).resolve().parents[1]
BASE = next((p for p in (_R / "data", _R / "public" / "fiscal") if p.is_dir()), _R / "data")
LEIS = json.loads((BASE / "leis.json").read_text(encoding="utf-8"))["normas"]
JURIS = json.loads((BASE / "jurisprudencia.json").read_text(encoding="utf-8"))["decisoes"]
_tok = lambda s: set(re.findall(r"[a-z0-9]{3,}", s.lower().translate(str.maketrans("áàâãéêíóôõúç", "aaaaeeiooouc"))))


class Sessao:
    """Estado compartilhado entre os agentes durante uma auditoria."""

    def __init__(self, nome, dados):
        self.nome, self.dados, self.passos, self.nota, self.auditoria = nome, dados, [], None, None
        self.cnpj, self.fundamentos, self.email, self.juris = {}, [], None, []

    def log(self, agente, acao, detalhe=""):
        self.passos.append({"agente": agente, "acao": acao, "detalhe": detalhe, "t": round(time.time(), 2)})


# ---------------- agentes especialistas ----------------
def ag_extrator(s: Sessao, **_):
    texto = s.dados[:400000].decode("utf-8", "ignore") if s.dados.lstrip()[:1] == b"<" else ""
    txt = s.dados[:400000].decode("utf-8", "ignore")
    if eh_uae(txt):
        s.nota = {"tipo": "Fatura UAE (VAT)", "_txt": txt}
        s.log("Extrator", "Identificou fatura eletrônica dos Emirados (UBL/PINT AE, VAT)")
        return {"tipo": "Fatura UAE (VAT)", "proximo": "auditor_vat_uae"}
    if eh_italia(txt):
        s.nota = {"tipo": "Fattura elettronica (Itália)", "_txt": txt}
        s.log("Extrator", "Identificou fatura eletrônica da Itália (FatturaPA)")
        return {"tipo": "Fattura elettronica (Itália)", "proximo": "auditor_iva_italia"}
    if eh_india(txt):
        s.nota = {"tipo": "e-Invoice Índia (GST)", "_txt": txt}
        s.log("Extrator", "Identificou e-invoice da Índia (JSON do IRP, GST)")
        return {"tipo": "e-Invoice Índia (GST)", "proximo": "auditor_gst_india"}
    if texto and eh_nfe(texto):
        s.auditoria = None
        s.nota = {"tipo": "NF-e mercadoria", "_xml": texto}
        s.log("Extrator", "Identificou NF-e de mercadoria (XML modelo 55)")
        return {"tipo": "NF-e mercadoria", "proximo": "auditor_icms"}
    s.nota = extrair(s.nome, s.dados)
    s.nota["tipo"] = "NFS-e serviço"
    campos = {k: v for k, v in s.nota.items() if v not in (None, "", False) and not k.startswith("_")}
    s.log("Extrator", f"Leu {s.nota['origem']} de NFS-e: {len(campos)} campos", ", ".join(f"{k}={v}" for k, v in list(campos.items())[:8]))
    return {"tipo": "NFS-e serviço", "campos": campos, "proximo": "auditor_iss"}


def ag_auditor_iss(s: Sessao, **_):
    s.auditoria = auditar(s.nota)
    s.auditoria["tipo"] = "NFS-e serviço"
    s.log("Auditor ISS", f"{s.auditoria['erros']} erro(s), {s.auditoria['alertas']} alerta(s) → {s.auditoria['decisao']}")
    return _resumo(s)


def ag_auditor_icms(s: Sessao, **_):
    s.auditoria = auditar_nfe(s.nota["_xml"], os.getenv("CNPJ_EMPRESA"))
    s.nota = {**s.auditoria["nota"], "tipo": "NF-e mercadoria"}
    s.log("Auditor ICMS", f"{s.auditoria['erros']} erro(s), {s.auditoria['alertas']} alerta(s) → {s.auditoria['decisao']}")
    return _resumo(s)


def ag_auditor_vat_uae(s: Sessao, **_):
    s.auditoria = auditar_uae(s.nota["_txt"])
    s.nota = {**s.auditoria["nota"], "tipo": s.auditoria["tipo"]}
    s.log("Auditor VAT (Emirados)", f"{s.auditoria['erros']} erro(s), {s.auditoria['alertas']} alerta(s) → {s.auditoria['decisao']}")
    return _resumo(s)


def ag_auditor_iva_italia(s: Sessao, **_):
    s.auditoria = auditar_italia(s.nota["_txt"])
    s.nota = {**s.auditoria["nota"], "tipo": s.auditoria["tipo"]}
    s.log("Auditor IVA (Itália)", f"{s.auditoria['erros']} erro(s), {s.auditoria['alertas']} alerta(s) → {s.auditoria['decisao']}")
    return _resumo(s)


def ag_auditor_gst_india(s: Sessao, **_):
    s.auditoria = auditar_india(s.nota["_txt"])
    s.nota = {**s.auditoria["nota"], "tipo": s.auditoria["tipo"]}
    s.log("Auditor GST (Índia)", f"{s.auditoria['erros']} erro(s), {s.auditoria['alertas']} alerta(s) → {s.auditoria['decisao']}")
    return _resumo(s)


def _resumo(s):
    return {"decisao": s.auditoria["decisao"], "achados": [f"{a['nivel']}: {a['campo']}: {a['msg']}" for a in s.auditoria["achados"] if a["nivel"] != "ok"]}


def ag_cnpj(s: Sessao, cnpj=None, **_):
    c = re.sub(r"\D", "", cnpj or (s.nota or {}).get("prestador_cnpj") or "")
    if len(c) != 14:
        s.log("CNPJ", "Sem CNPJ válido para consultar")
        return {"erro": "CNPJ ausente"}
    try:
        req = urllib.request.Request(f"https://brasilapi.com.br/api/cnpj/v1/{c}", headers={"User-Agent": "VysonAuditor/1.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            j = json.loads(r.read())
        info = {"situacao": j.get("descricao_situacao_cadastral"), "simples": j.get("opcao_pelo_simples"), "cnae": j.get("cnae_fiscal_descricao"), "uf": j.get("uf")}
        s.cnpj = info
        s.log("CNPJ", f"Situação cadastral: {info['situacao']}", f"CNAE: {info['cnae']}")
        if info["situacao"] and info["situacao"].upper() != "ATIVA":
            s.auditoria["achados"].insert(0, {"nivel": "erro", "campo": "CNPJ", "msg": f"Fornecedor com CNPJ {info['situacao']}.", "fonte": "Receita Federal (BrasilAPI)"})
            s.auditoria["erros"] += 1
            s.auditoria["decisao"] = "devolver ao fornecedor"
        return info
    except Exception as e:  # noqa: BLE001
        s.log("CNPJ", "Consulta indisponível agora; seguiu sem ela", type(e).__name__)
        return {"erro": "consulta indisponível"}


def ag_juridico(s: Sessao, tema=None, **_):
    achados = [{"msg": tema, "fonte": "", "campo": ""}] if tema else [a for a in s.auditoria["achados"] if a["nivel"] in ("erro", "alerta")][:4]
    out = []
    for a in achados:
        texto = f"{a.get('campo', '')} {a['msg']} {a.get('fonte', '')}"
        nums = re.findall(r"(\d[\d.]*/\d{4})", a.get("fonte") or "")
        cand = [n for n in LEIS if any(x.replace(".", "") in n.get("numero", "").replace(".", "") for x in nums)]
        if not cand:  # sem norma citada: busca filtrando pelo tributo do achado
            trib = next((t for t in ("ISS", "ICMS", "INSS", "IPI", "PIS", "COFINS", "IRRF") if t.lower() in texto.lower()), None)
            cand = [n for n in LEIS if not trib or trib in (n.get("tributos") or [])] or LEIS
            if "macei" in texto.lower():
                cand = [n for n in cand if "macei" in (n.get("numero", "") + n.get("ementa", "")).lower()] or cand
        q = _tok(texto)
        best = max(cand, key=lambda n: len(q & _tok(" ".join([n.get("numero", ""), n.get("ementa", ""), " ".join(n.get("temas") or [])]))))
        out.append({"tema": a["msg"][:90], "norma": best["numero"], "link": best.get("link"), "ementa": best.get("ementa", "")[:160]})
    s.fundamentos = out
    s.log("Jurídico", f"Buscou fundamento legal para {len(out)} achado(s)", "; ".join(o["norma"] for o in out))
    return out


def ag_jurisprudencia(s: Sessao, tema=None, **_):
    """Confronta a nota e os achados com decisões vinculantes (STF/STJ) e aponta conflito entre a lei e a jurisprudência."""
    n = s.nota or {}
    ctx = " ".join(str(x) for x in [tema or "", n.get("descricao"), n.get("item_lc116"), "locacao" if n.get("locacao_bens") else "",
                                     " ".join(f"{a['campo']} {a['msg']}" for a in (s.auditoria or {}).get("achados", []))]).lower()
    ctx = ctx.translate(str.maketrans("áàâãéêíóôõúç", "aaaaeeiooouc"))
    if (n.get("item_lc116") or "").startswith("1.05"):
        ctx += " software"
    achadas = []
    for d in JURIS:
        hits = [p for p in d["palavras"] if p in ctx]
        if hits and (len(hits) >= 2 or any(len(p) > 8 or p[0].isdigit() for p in hits)):
            achadas.append({k: d[k] for k in ("tribunal", "tipo", "numero", "tese", "lei", "conflito", "efeito", "link")})
    vivo = []
    if tema:  # busca ao vivo no LexML (API pública oficial de jurisprudência e legislação)
        try:
            q = urllib.parse.quote(f'tipoDocumento="Jurisprudência" and dc.description any "{tema}"')
            req = urllib.request.Request(f"https://www.lexml.gov.br/busca/SRU?operation=searchRetrieve&version=1.1&maximumRecords=5&query={q}", headers={"User-Agent": "VysonAuditor/1.0"})
            with urllib.request.urlopen(req, timeout=8) as r:
                xml = r.read().decode("utf-8", "ignore")
            vivo = [{"titulo": t.strip()[:160]} for t in re.findall(r"<dc:title>(.*?)</dc:title>", xml, re.S)[:5]]
        except Exception as e:  # noqa: BLE001
            s.log("Jurisprudência", "LexML indisponível agora; usou a base local", type(e).__name__)
    s.juris = achadas
    s.log("Jurisprudência", f"{len(achadas)} decisão(ões) vinculante(s) aplicável(is)" if achadas else "Nenhuma decisão vinculante muda a regra desta nota",
          "; ".join(d["numero"] for d in achadas))
    return {"decisoes": achadas, "lexml": vivo}


def ag_comunicador(s: Sessao, **_):
    a = s.auditoria
    probs = [x for x in a["achados"] if x["nivel"] in ("erro", "alerta")]
    if a["decisao"] == "aprovar":
        s.email = None
        s.log("Comunicador", "Nota aprovada: nenhum e-mail necessário")
        return {"email": None}
    n = s.nota or {}
    linhas = "\n".join(f"- {x['campo']}: {x['msg']}" + (f" ({x['fonte']})" if x.get("fonte") else "") for x in probs)
    s.email = (f"Assunto: Nota fiscal nº {n.get('numero') or '—'}: {'correção necessária' if a['decisao'].startswith('devolver') else 'pontos para confirmar'}\n\n"
               f"Prezados,\n\nNa conferência da nota fiscal nº {n.get('numero') or '—'} encontramos os pontos abaixo:\n{linhas}\n\n"
               + ("Pedimos o cancelamento e a emissão de uma nova nota com as correções, ou carta de correção quando a legislação permitir.\n" if a["decisao"].startswith("devolver")
                  else "Pedimos que confirmem esses pontos antes da aprovação do pagamento.\n")
               + "\nAtenciosamente,\nSetor fiscal")
    s.log("Comunicador", "Redigiu e-mail ao fornecedor", f"{len(probs)} ponto(s)")
    return {"email": s.email}


AGENTES = {"extrator": ag_extrator, "auditor_iss": ag_auditor_iss, "auditor_icms": ag_auditor_icms,
           "auditor_vat_uae": ag_auditor_vat_uae, "auditor_gst_india": ag_auditor_gst_india, "auditor_iva_italia": ag_auditor_iva_italia, "cnpj": ag_cnpj, "juridico": ag_juridico,
           "jurisprudencia": ag_jurisprudencia, "comunicador": ag_comunicador}
DESCR = {
    "extrator": "Lê o arquivo enviado e identifica se é NFS-e (serviço) ou NF-e (mercadoria). Sempre o primeiro passo.",
    "auditor_iss": "Audita NFS-e: item da LC 116, alíquota oficial do município, valor do ISS, retenção de INSS.",
    "auditor_icms": "Audita NF-e: chave, autorização, CNPJ, NCM, CFOP, alíquota interestadual, ICMS por item, totais e DIFAL.",
    "auditor_vat_uae": "Audita fatura dos Emirados Árabes (Dubai): TRN, categoria e alíquota de VAT (5%, zero, isento), valor do VAT em AED e campos obrigatórios.",
    "auditor_gst_india": "Audita e-invoice da Índia: GSTIN com dígito verificador, CGST+SGST ou IGST conforme o estado, alíquotas do GST 2.0, SAC/HSN e IRN.",
    "auditor_iva_italia": "Audita fatura eletrônica da Itália (FatturaPA): Partita IVA com dígito verificador e VIES, alíquotas de IVA (22, 10, 5, 4%), Natura N1 a N7, reverse charge, split payment, ritenuta e imposta di bollo.",
    "cnpj": "Consulta a situação cadastral do CNPJ do emitente na Receita Federal. Parâmetro opcional: cnpj.",
    "juridico": "Busca na base de legislação o fundamento legal dos achados. Parâmetro opcional: tema.",
    "jurisprudencia": "Confronta a nota com decisões vinculantes do STF e do STJ (súmulas vinculantes, repercussão geral, repetitivos) e aponta quando a jurisprudência muda o que a lei diz. Parâmetro opcional: tema, para busca ao vivo no LexML.",
    "comunicador": "Escreve o e-mail ao fornecedor quando a nota precisa de correção ou confirmação. Último passo.",
}
SISTEMA = ("Você é o auditor fiscal sênior que revisa o trabalho de uma equipe de agentes. Recebe os resultados já apurados "
           "(achados, fundamentos legais, jurisprudência, CNPJ). Escreva em português um parecer de até 6 linhas com a decisão final "
           "(aprovar, revisar ou devolver ao fornecedor). Toda afirmação deve citar entre colchetes uma fonte que aparece nos resultados. "
           "Use apenas os resultados recebidos; não invente lei, valor ou fato.")


import threading as _th
_VAGAS = _th.Semaphore(int(os.getenv("LLM_PARALELO", "3")))  # o serviço do modelo limita chamadas simultâneas


def _llm(msgs, espera=60):
    import time
    base, model = os.getenv("LLM_BASE_URL"), os.getenv("LLM_MODEL")
    corpo = json.dumps({"model": model, "messages": msgs, "temperature": 0.1, "max_tokens": 400}).encode()
    fim = time.time() + espera
    for tentativa in range(2):  # uma nova tentativa se o serviço recusar por excesso de chamadas
        req = urllib.request.Request(base.rstrip("/") + "/chat/completions", data=corpo, method="POST",
                                     headers={"Authorization": f"Bearer {os.getenv('LLM_API_KEY', 'x')}", "Content-Type": "application/json"})
        with _VAGAS:
            try:
                with urllib.request.urlopen(req, timeout=max(2, min(25, fim - time.time()))) as r:
                    return json.loads(r.read())["choices"][0]["message"]
            except urllib.error.HTTPError as e:
                if e.code not in (429, 502, 503) or tentativa or fim - time.time() < 6:
                    raise
        time.sleep(1.5)


def _citacoes_ok(texto: str, resultados: str) -> bool:
    """Trava de citação: o parecer precisa citar fontes, e cada fonte citada precisa existir nos resultados dos agentes."""
    import re
    cits = [c.strip() for c in re.findall(r"\[([^\]]+)\]", texto or "")]
    if not cits:
        return False
    base = resultados.lower()
    def ok(c):
        toks = re.findall(r"[\w./§º-]{3,}", c.lower())
        nums = [t for t in toks if any(ch.isdigit() for ch in t)]
        if any(t not in base for t in nums):  # número de lei, artigo ou item inventado
            return False
        return bool(toks) and sum(t in base for t in toks) >= 0.6 * len(toks)
    return all(ok(c) for c in cits)


def coordenar(nome: str, dados: bytes, prazo: float | None = None) -> dict:
    """Os agentes especialistas rodam em sequência fixa (regras, sem IA no caminho). Depois, UMA chamada ao modelo
    na AMD escreve o parecer citado; a trava de citação o confere. Se o modelo falhar ou não citar, as regras escrevem."""
    import time
    s = Sessao(nome, dados)
    modo, parecer, aviso, chamadas = "regras", None, None, []
    s.log("Coordenador", "Plano: extrair → auditar → CNPJ → jurisprudência → jurídico → comunicar")
    r = ag_extrator(s)
    AGENTES[r["proximo"]](s)
    if r["proximo"] in ("auditor_iss", "auditor_icms"):
        ag_cnpj(s)
        ag_jurisprudencia(s)
    if s.auditoria["decisao"] != "aprovar":
        ag_juridico(s)
    ag_comunicador(s)
    if os.getenv("LLM_BASE_URL") and os.getenv("LLM_MODEL") and (prazo is None or time.time() < prazo - 3):
        a = s.auditoria
        resultados = json.dumps({"decisao": a["decisao"], "achados": [x for x in a["achados"] if x["nivel"] in ("erro", "alerta")] or a["achados"][:6],
                                 "fundamentos": s.fundamentos, "jurisprudencia": s.juris, "cnpj": s.cnpj}, ensure_ascii=False, default=str)[:6000]
        # nada identificável sai para o modelo: CNPJ, CPF, e-mails e nomes viram marcadores; o parecer volta com os dados reais só na tela
        n = s.nota or {}
        ids = [str(v) for k, v in n.items() if v and isinstance(v, (str, int)) and any(w in k.lower() for w in ("nome", "cnpj", "cpf", "piva", "partita", "trn", "gstin", "vat", "email"))]
        resultados, recibo, mapa = evolus.mascarar(resultados, ids + [(s.cnpj or {}).get("razao_social")], com_mapa=True)
        try:
            t0 = time.time()
            m = _llm([{"role": "system", "content": SISTEMA}, {"role": "user", "content": f"Arquivo '{nome}'. Resultados dos agentes:\n{resultados}"}],
                     (prazo - time.time() - 1) if prazo else 60)
            chamadas.append(round(time.time() - t0, 2))
            texto = (m.get("content") or "").strip()
            if _citacoes_ok(texto, resultados):
                for k, v in mapa.items():
                    texto = texto.replace(k, v)
                parecer, modo = texto, "ia"
                s.log("Coordenador (IA)", f"Parecer escrito por {os.getenv('LLM_MODEL')} em 1 chamada", f"{chamadas[0]} s · citações conferidas pela trava · {len(mapa)} dado(s) mascarado(s) antes do envio")
            else:
                aviso = "parecer da IA recusado pela trava de citação; parecer pelas regras"
                s.log("Trava de citação", "Parecer da IA recusado (fonte ausente ou não encontrada nos resultados)", "as regras escrevem o parecer")
        except Exception as e:  # noqa: BLE001
            aviso = "modelo indisponível; parecer pelas regras"
            s.log("Coordenador", "Modelo indisponível; as regras escrevem o parecer", type(e).__name__)
    a = s.auditoria
    humano = None
    if a["decisao"] != "aprovar" and evolus.ligado():
        try:
            humano = evolus.encaminhar({"nota": s.nota, "achados": a["achados"], "decisao": a["decisao"], "erros": a["erros"], "alertas": a["alertas"]})
            if humano:
                s.log("Revisão humana (Evolus)", "Caso enviado ao fluxo de revisão, com CNPJ e nomes mascarados", f"{len(humano['recibo'])} dado(s) mascarado(s)")
        except Exception as e:  # noqa: BLE001
            s.log("Revisão humana (Evolus)", "Fluxo indisponível; o caso fica na tela para revisão", type(e).__name__)
    if not parecer:
        probs = [x for x in a["achados"] if x["nivel"] in ("erro", "alerta")]
        parecer = {"aprovar": "Nota conforme: nenhum erro ou alerta.", "revisar": "Nota com alertas: revisão humana recomendada.",
                   "devolver ao fornecedor": "Nota com erros: devolver ao fornecedor."}[a["decisao"]] + "".join(
            f"\n- {x['campo']}: {x['msg']}" + (f" [{x['fonte']}]" if x.get("fonte") else "") for x in probs)
    nota = {k: v for k, v in (s.nota or {}).items() if not k.startswith("_")}
    return {"arquivo": nome, "tipo": a.get("tipo"), "nota": nota, "municipio": a.get("municipio"), "achados": a["achados"], "erros": a["erros"], "alertas": a["alertas"],
            "decisao": a["decisao"], "parecer": parecer, "email": s.email, "cnpj": s.cnpj, "fundamentos": s.fundamentos, "jurisprudencia": s.juris, "passos": s.passos,
            "modo": modo, "modelo": os.getenv("LLM_MODEL") if modo == "ia" else None, "ficha": a.get("ficha"), "itens": a.get("itens"), "revisao_humana": humano, "aviso_llm": aviso, "tempos_llm": chamadas}


def parecer_cadastro(r: dict) -> tuple[str, str]:
    """Parecer do lote de cadastro: 1 chamada ao modelo na AMD, conferida pela trava de citação; sem modelo, as regras."""
    tops = [a["msg"] for x in r["linhas"] for a in x["apontamentos"]][:25]
    fontes = sorted({f["nome"] for x in r["linhas"] for f in x["fontes"]})
    regras = (f"Cadastro com {r['total']} itens: {r['resumo']['ok']} conformes, {r['resumo']['revisar']} a revisar e "
              f"{r['resumo']['corrigir']} a corrigir. Importe o arquivo corrigido no sistema e audite de novo após a implantação."
              + (f" Fontes: {'; '.join(f'[{f}]' for f in fontes)}." if fontes else ""))
    if not (os.getenv("LLM_BASE_URL") and os.getenv("LLM_MODEL")):
        return regras, "regras"
    dados = json.dumps({"resumo": r["resumo"], "total": r["total"], "apontamentos": tops, "fontes": fontes}, ensure_ascii=False)
    try:
        m = _llm([{"role": "system", "content": "Você é o auditor fiscal sênior. Escreva em português um parecer de até 6 linhas sobre o cadastro "
                   "de produtos e serviços auditado: riscos para a margem e para a nota fiscal, o que corrigir primeiro e que, após importar o "
                   "arquivo corrigido no sistema, o cadastro deve ser auditado de novo. Cite entre colchetes só as fontes recebidas."},
                  {"role": "user", "content": dados}], 25)
        texto = (m.get("content") or "").strip()
        if _citacoes_ok(texto, dados):
            return texto, "ia"
    except Exception:  # noqa: BLE001
        pass
    return regras, "regras"
