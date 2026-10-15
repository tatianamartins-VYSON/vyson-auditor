"""Interface web do agente auditor: envie XML, PDF ou DOCX e clique em Auditar.

Uso: python3 servidor.py  → http://localhost:8793
"""
import base64
import io
import zipfile
import http.server
import json
import pathlib

import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "agentes"))
import agentes  # noqa: E402
import ariba  # noqa: E402
import edital  # noqa: E402

UI = pathlib.Path(__file__).with_name("ui.html").read_bytes()


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _envia(self, corpo, tipo, cod=200):
        self.send_response(cod)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def do_GET(self):
        if self.path.startswith("/exemplos/"):
            f = pathlib.Path(__file__).with_name("exemplos") / pathlib.Path(self.path).name
            if f.is_file():
                return self._envia(f.read_bytes(), "application/octet-stream")
            return self._envia(b"", "text/plain", 404)
        self._envia(UI, "text/html; charset=utf-8")

    def do_POST(self):
        if self.path == "/api/ariba":
            n = int(self.headers.get("Content-Length", 0))
            if n > 30_000_000:
                return self._envia(b'{"erro":"arquivo grande demais"}', "application/json", 413)
            a = json.loads(self.rfile.read(n) or b"{}")
            custos = {int(k): float(v) for k, v in (a.get("custos") or {}).items() if v not in (None, "")}
            res = ariba.preparar(a["nome"], base64.b64decode(a["b64"]), a.get("regime", "presumido"), float(a.get("margem") or 15), custos)
            res["planilha_b64"] = base64.b64encode(ariba.planilha_resposta(res)).decode()
            return self._envia(json.dumps(res, ensure_ascii=False).encode(), "application/json; charset=utf-8")
        if self.path in ("/api/edital", "/api/validar"):
            n = int(self.headers.get("Content-Length", 0))
            if n > 60_000_000:
                return self._envia(b'{"erro":"arquivo grande demais"}', "application/json", 413)
            a = json.loads(self.rfile.read(n) or b"{}")
            an = edital.analisar(a["nome"], base64.b64decode(a["b64"]))
            emp = a.get("empresa") or {}
            if self.path == "/api/edital":
                an["docx_b64"] = base64.b64encode(edital.gerar_docx(an, emp)).decode()
            else:
                an["validacao"] = edital.validar(an, [(d["nome"], base64.b64decode(d["b64"])) for d in a.get("docs", [])[:40]], emp)
            return self._envia(json.dumps(an, ensure_ascii=False).encode(), "application/json; charset=utf-8")
        if self.path != "/api/auditar":
            return self._envia(b"{}", "application/json", 404)
        n = int(self.headers.get("Content-Length", 0))
        if n > 60_000_000:
            return self._envia(b'{"erro":"arquivo grande demais"}', "application/json", 413)
        arqs = json.loads(self.rfile.read(n) or b"[]")  # [{"nome": ..., "b64": ...}]
        out = []
        for a in arqs[:20]:
            dados = base64.b64decode(a["b64"])
            if a["nome"].lower().endswith(".zip"):  # pasta de notas zipada: audita os XMLs (ou os PDFs, se não houver XML)
                with zipfile.ZipFile(io.BytesIO(dados)) as z:
                    nomes = [n for n in z.namelist() if n.lower().endswith(".xml")] or [n for n in z.namelist() if n.lower().endswith((".pdf", ".docx"))]
                    out += [agentes.coordenar(n.split("/")[-1], z.read(n)) for n in nomes[:300]]
            else:
                out.append(agentes.coordenar(a["nome"], dados))
        self._envia(json.dumps(out, ensure_ascii=False).encode(), "application/json; charset=utf-8")


if __name__ == "__main__":
    print("Agente auditor de notas em http://localhost:8793")
    http.server.ThreadingHTTPServer(("0.0.0.0", 8793), H).serve_forever()
