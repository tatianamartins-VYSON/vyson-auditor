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
import urllib.error
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
def perguntar(texto: str, timeout: int = 60, var: str = "EVOLUS_EXTRACTOR_ID") -> dict | None:
    """Agente do Evolus (POST /agents/{id}/ask): manda o texto e devolve o JSON da resposta."""
    eid = os.getenv(var)
    if not (ligado() and eid):
        return None
    r = _post(f"/agents/{eid.strip()}/ask", {"content": texto, "skipChatHistory": True}, timeout=timeout)
    resposta = (r.get("answer") or "").strip()
    m = re.search(r"\{.*\}", resposta, re.S)
    try:
        return json.loads(m.group(0)) if m else {"texto": resposta}
    except ValueError:
        return {"texto": resposta}


def extrair(nome: str, dados: bytes) -> dict | None:
    """Extrator de documentos do Evolus: devolve os campos da nota em JSON (ou None se não configurado)."""
    return perguntar(f"Arquivo: {nome}\n\n" + dados[:60000].decode("utf-8", "ignore"), timeout=90)


_NOTA_TESTE = ("NFS-e nº 1234, emitida em 15/09/2026, Município de Maceió/AL. Prestador: Empresa Exemplo Ltda. "
               "Tomador: Cliente Exemplo S.A. Serviço: item 1.02 da LC 116/2003, programação. "
               "Valor dos serviços R$ 10.000,00. Alíquota ISS 5%. Valor do ISS R$ 500,00. ISS retido: não. Valor líquido R$ 10.000,00.")


_PROC_TESTE = ("Processo nº 0801234-56.2024.8.02.0001. Vistos. JULGO PROCEDENTE o pedido e condeno a ré ao pagamento de R$ 50.000,00, "
               "corrigido pelo IPCA-E a partir de 10/01/2024, com juros de 1% ao mês a partir da citação, ocorrida em 05/03/2024, "
               "e honorários de 10% sobre o valor da condenação. Sentença transitada em julgado em 20/06/2024.")


def processo(texto: str, timeout: int = 45) -> dict | None:
    """Extrator de processos judiciais no Evolus; nomes, CPF, CNPJ e e-mails saem mascarados."""
    limpo, _ = mascarar(texto[:30000])
    return perguntar(limpo, timeout=timeout, var="EVOLUS_PROCESSO_ID")


def teste(tipo: str = "nota") -> dict:
    """Chama o agente com um documento fictício (sem dado real) e devolve o JSON extraído."""
    try:
        return {"ok": True, "extraido": processo(_PROC_TESTE) if tipo == "processo" else perguntar(_NOTA_TESTE, timeout=8)}
    except urllib.error.HTTPError as e:
        return {"ok": False, "http": e.code}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "erro": type(e).__name__}


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
                                                                    "alertas": resultado.get("alertas"), "recibo_mascaramento": recibo}}, timeout=8)
    return {"execucao": r.get("executionId") or r.get("id"), "enviado": texto, "recibo": recibo}


def status() -> dict:
    """Diagnóstico sem expor segredo: testa a chave em formatos diferentes e mostra só os códigos HTTP."""
    k, a = os.getenv("EVOLUS_API_KEY", ""), os.getenv("EVOLUS_APP_ID", "")
    out = {"configurado": ligado(), "chave": {"tamanho": len(k), "espacos_nas_pontas": k != k.strip(), "inicio": k[:3] + "…" if k else ""},
           "app_id": {"tamanho": len(a), "espacos_nas_pontas": a != a.strip()}, "testes": {}}
    if not ligado():
        return out
    k, a = k.strip(), a.strip()
    formas = {"X-ApiKey+AppId": {"X-ApiKey": k, "X-Application-Id": a}, "X-ApiKey": {"X-ApiKey": k},
              "Bearer+AppId": {"Authorization": f"Bearer {k}", "X-Application-Id": a}, "Bearer": {"Authorization": f"Bearer {k}"}}
    for caminho in ("/workflows", "/agents", "/ai-tools"):
        for nome, h in formas.items():
            req = urllib.request.Request(BASE + caminho, headers={**h, "User-Agent": "VysonAuditor/1.0"})
            try:
                with urllib.request.urlopen(req, timeout=5) as r:
                    out["testes"][f"{caminho} {nome}"] = r.status
            except urllib.error.HTTPError as e:
                out["testes"][f"{caminho} {nome}"] = e.code
            except Exception as e:  # noqa: BLE001
                out["testes"][f"{caminho} {nome}"] = type(e).__name__
    req = urllib.request.Request("https://api.evolus.ai/v1/models", headers={"Authorization": f"Bearer {k}", "User-Agent": "VysonAuditor/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            out["testes"]["/v1/models Bearer (chat OpenAI)"] = r.status
    except urllib.error.HTTPError as e:
        out["testes"]["/v1/models Bearer (chat OpenAI)"] = e.code
    except Exception as e:  # noqa: BLE001
        out["testes"]["/v1/models Bearer (chat OpenAI)"] = type(e).__name__
    return out
