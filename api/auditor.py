"""API - Vyson Auditor: equipe de agentes fiscais (notas, SAP Ariba e editais).

POST /api/auditor?acao=auditar   body: [{"nome": ..., "b64": ...}]           -> auditoria multiagente de NFS-e/NF-e (aceita .zip)
POST /api/auditor?acao=ariba     body: {"nome","b64","regime","margem","custos"} -> preço e retenções do evento + planilha
POST /api/auditor?acao=edital    body: {"nome","b64","empresa"}               -> análise do edital + declarações em Word
POST /api/auditor?acao=validar   body: {"nome","b64","empresa","docs":[...]}  -> valida os documentos preenchidos

Modelo do coordenador: Gemini (GEMINI_API_KEY, GEMINI_MODEL, GEMINI_BASE_URL), lidos das variáveis de ambiente da Vercel.
"""
import base64
import io
import json
import os
import pathlib
import sys
import zipfile
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "agentes"))
for a, b in (("GEMINI_API_KEY", "LLM_API_KEY"), ("GEMINI_MODEL", "LLM_MODEL"), ("GEMINI_BASE_URL", "LLM_BASE_URL")):
    if os.getenv(a) and not os.getenv(b):
        os.environ[b] = os.environ[a]

import agentes  # noqa: E402
import ariba  # noqa: E402
import edital  # noqa: E402

LIMITE = 4_000_000  # a Vercel aceita até ~4,5 MB por requisição


class handler(BaseHTTPRequestHandler):
    def _send(self, code, payload):
        corpo = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(corpo)

    def do_GET(self):
        self._send(200, {"modelo": os.getenv("LLM_MODEL") or None, "ia": bool(os.getenv("LLM_API_KEY") and os.getenv("LLM_BASE_URL"))})

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        if n > LIMITE:
            return self._send(413, {"erro": "Arquivo grande demais para o site (máx. 4 MB). Envie menos notas por vez."})
        acao = (parse_qs(urlparse(self.path).query).get("acao") or ["auditar"])[0]
        try:
            a = json.loads(self.rfile.read(n) or b"null")
            if acao == "auditar":
                out = []
                for x in (a or [])[:20]:
                    dados = base64.b64decode(x["b64"])
                    if x["nome"].lower().endswith(".zip"):
                        with zipfile.ZipFile(io.BytesIO(dados)) as z:
                            nomes = [k for k in z.namelist() if k.lower().endswith(".xml")] or [k for k in z.namelist() if k.lower().endswith((".pdf", ".docx"))]
                            out += [agentes.coordenar(k.split("/")[-1], z.read(k)) for k in nomes[:60]]
                    else:
                        out.append(agentes.coordenar(x["nome"], dados))
                return self._send(200, out)
            if acao == "ariba":
                custos = {int(k): float(v) for k, v in (a.get("custos") or {}).items() if v not in (None, "")}
                res = ariba.preparar(a["nome"], base64.b64decode(a["b64"]), a.get("regime", "presumido"), float(a.get("margem") or 15), custos)
                res["planilha_b64"] = base64.b64encode(ariba.planilha_resposta(res)).decode()
                return self._send(200, res)
            if acao in ("edital", "validar"):
                an = edital.analisar(a["nome"], base64.b64decode(a["b64"]))
                emp = a.get("empresa") or {}
                if acao == "edital":
                    an["docx_b64"] = base64.b64encode(edital.gerar_docx(an, emp)).decode()
                else:
                    an["validacao"] = edital.validar(an, [(d["nome"], base64.b64decode(d["b64"])) for d in a.get("docs", [])[:40]], emp)
                return self._send(200, an)
            return self._send(400, {"erro": "ação desconhecida"})
        except Exception as e:  # noqa: BLE001
            return self._send(500, {"erro": f"{type(e).__name__}: {e}"[:300]})
