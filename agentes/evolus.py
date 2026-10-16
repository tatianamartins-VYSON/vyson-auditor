"""Integração com o Evolus (trilha "Evolus Business Agents on AMD").

Blocos do Evolus usados pela API REST (https://api.evolus.ai/api/v1, cabeçalhos X-ApiKey e X-Application-Id):
  1. Extrator de documentos  PDF digitalizado/foto de nota -> JSON com os campos da nota (quando o leitor local não acha nada)
  2. Fluxo de trabalho        caso que precisa de uma pessoa: POST /workflows/{id}/execute com o resumo já MASCARADO
O modelo que raciocina é o nosso: aberto, servido em vLLM na AMD (LLM_BASE_URL), conectado ao espaço do evento.

Variáveis de ambiente (nunca no código):
  EVOLUS_API_KEY, EVOLUS_APP_ID           chave e aplicação do espaço de trabalho do evento
  EVOLUS_EXTRACTOR_ID                     id do extrator "Nota fiscal" criado no Evolus (opcional)
  EVOLUS_WORKFLOW_ID                      id do fluxo "Revisão humana" (opcional)
  EVOLUS_BASE                             padrão https://api.evolus.ai/api/v1
Sem essas variáveis nada é enviado e o Auditor segue só com as regras locais.
"""
import base64
import hashlib
import json
import os
import re
import urllib.request

BASE = os.getenv("EVOLUS_BASE", "https://api.evolus.ai/api/v1").rstrip("/")


def ligado() -> bool:
    return bool(os.getenv("EVOLUS_API_KEY") and os.getenv("EVOLUS_APP_ID"))


def _post(caminho: str, corpo: dict, timeout: int = 60) -> dict:
    req = urllib.request.Request(BASE + caminho, data=json.dumps(corpo).encode(), method="POST",
                                 headers={"X-ApiKey": os.environ["EVOLUS_API_KEY"], "X-Application-Id": os.environ["EVOLUS_APP_ID"],
                                          "Content-Type": "application/json", "User-Agent": "VysonAuditor/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


# ---------------- mascaramento: nada identificável sai da máquina ----------------
_CNPJ = re.compile(r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b")
_CPF = re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


def mascarar(texto: str, nomes: list[str] | None = None) -> tuple[str, dict]:
    """Troca CNPJ, CPF, e-mail e nomes por marcadores ([CNPJ_1], [NOME_1]...). Devolve o texto e o recibo:
    para cada marcador, o SHA-256 do valor original (prova o que foi trocado sem revelar o dado)."""
    recibo, cont = {}, {}

    def troca(tipo, valor):
        for k, v in recibo.items():
            if v["original"] == valor:
                return k
        cont[tipo] = cont.get(tipo, 0) + 1
        k = f"[{tipo}_{cont[tipo]}]"
        recibo[k] = {"original": valor, "sha256": hashlib.sha256(valor.encode()).hexdigest()}
        return k

    out = texto
    for n in sorted({n.strip() for n in (nomes or []) if n and len(n.strip()) > 3}, key=len, reverse=True):
        out = re.sub(re.escape(n), lambda m: troca("NOME", n), out, flags=re.I)
    for tipo, rx in (("CNPJ", _CNPJ), ("CPF", _CPF), ("EMAIL", _EMAIL)):
        out = rx.sub(lambda m, t=tipo: troca(t, m.group(0)), out)
    return out, {k: v["sha256"] for k, v in recibo.items()}


# ---------------- blocos do Evolus ----------------
def extrair(nome: str, dados: bytes) -> dict | None:
    """Extrator de documentos do Evolus: devolve os campos da nota em JSON (ou None se não configurado)."""
    eid = os.getenv("EVOLUS_EXTRACTOR_ID")
    if not (ligado() and eid):
        return None
    r = _post(f"/extractors/{eid}/run", {"fileName": nome, "fileBase64": base64.b64encode(dados).decode()}, timeout=90)
    return r.get("data") or r.get("result") or r


def encaminhar(resultado: dict) -> dict | None:
    """Fluxo de revisão humana no Evolus. Envia só o resumo MASCARADO e o recibo dos hashes."""
    wid = os.getenv("EVOLUS_WORKFLOW_ID")
    if not (ligado() and wid):
        return None
    n = resultado.get("nota") or {}
    probs = [x for x in resultado.get("achados", []) if x.get("nivel") in ("erro", "alerta")]
    texto = (f"Nota {n.get('numero') or '—'} de {n.get('prestador_nome') or '—'} (CNPJ {n.get('prestador_cnpj') or '—'}), "
             f"valor {n.get('valor_servicos') or '—'}. Decisão: {resultado.get('decisao')}.\n"
             + "\n".join(f"- {x['campo']}: {x['msg']}" + (f" [{x['fonte']}]" if x.get("fonte") else "") for x in probs))
    texto, recibo = mascarar(texto, [n.get("prestador_nome"), n.get("tomador_nome")])
    r = _post(f"/workflows/{wid}/execute", {"text": texto, "input": {"decisao": resultado.get("decisao"), "erros": resultado.get("erros"),
                                                                    "alertas": resultado.get("alertas"), "recibo_mascaramento": recibo}})
    return {"execucao": r.get("executionId") or r.get("id"), "enviado": texto, "recibo": recibo}
