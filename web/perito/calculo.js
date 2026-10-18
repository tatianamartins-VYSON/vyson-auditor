// Motor de atualização monetária do site (mesma regra de calculos/motor.py), em aritmética inteira exata.
// Fator com 9 casas (meio para cima a cada mês), valores em centavos. Sem IA: regra fixa e auditável.
(function (raiz) {
  const ESC = 1000000000n; // 9 casas do fator
  const meio = (n, d) => (n >= 0n ? (2n * n + d) / (2n * d) : -((-2n * n + d) / (2n * d))); // divisão com meio para cima
  const microPct = p => BigInt(Math.round(Number(p) * 1e6)); // 0,42% -> 420000 (milionésimos de ponto percentual)

  function mesesEntre(base, fim) { // do mês seguinte à data-base até o mês da data final
    const out = []; let y = +base.slice(0, 4), m = +base.slice(5, 7);
    const yf = +fim.slice(0, 4), mf = +fim.slice(5, 7);
    for (;;) { m++; if (m > 12) { y++; m = 1; } if (y > yf || (y === yf && m > mf)) return out; out.push(`${y}-${String(m).padStart(2, "0")}`); }
  }

  function atualizar({ valor, base, fim, serie, nome, jurosPct = 0, jurosDesde = null, travaNominal = false, hoje = new Date() }) {
    const alertas = [], memoria = [];
    if (fim < base) alertas.push("Data final anterior à data-base.");
    let F = ESC;
    const mesAtual = `${hoje.getFullYear()}-${String(hoje.getMonth() + 1).padStart(2, "0")}`;
    for (const mes of mesesEntre(base, fim)) {
      let p = serie[mes];
      if (p === undefined) { alertas.push(`${nome} sem valor para ${mes}: mês tratado como 0% (REVISAR).`); p = 0; }
      else if (mes === mesAtual) alertas.push(`${nome} de ${mes} é parcial (mês em andamento).`);
      const fm = 100000000n + microPct(p);          // fator do mês com 8 casas
      F = meio(F * fm, 100000000n);                 // acumulado com 9 casas
      memoria.push({ mes, pct: Number(p), fator: (Number(F) / 1e9).toFixed(9), negativo: Number(p) < 0 });
    }
    const centavos = BigInt(Math.round(valor * 100));
    let corr = meio(centavos * F, ESC);
    let trava = false;
    if (travaNominal && corr < centavos) { corr = centavos; trava = true; alertas.push("Deflação acumulada: aplicada a trava do valor nominal."); }
    let juros = 0n, nJuros = 0;
    if (jurosPct) {
      nJuros = mesesEntre(jurosDesde || base, fim).length;
      juros = BigInt(Math.round(Number(corr) * jurosPct / 100 * nJuros));
    }
    return { fator: (Number(F) / 1e9).toFixed(9), corrigido: Number(corr) / 100, juros: Number(juros) / 100,
             total: Number(corr + juros) / 100, nJuros, memoria, alertas, trava };
  }
  raiz.CalcVyson = { atualizar, mesesEntre };
  if (typeof module !== "undefined") module.exports = raiz.CalcVyson;
})(typeof window !== "undefined" ? window : globalThis);
