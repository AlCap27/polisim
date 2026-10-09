#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
equivalence_test.py - Test di equivalenza Python vs JS per il motore RFML del
Modulo Propensione (polisim_propensione.py  vs  propensione_dashboard.html).

COSA FA
-------
1. Esegue entrambe le implementazioni sullo STESSO dataset di test
   (donors_anagrafica_test.csv + donors_transazioni_test.csv):
     - JS : node run_js.mjs   (motore estratto dal dashboard via jsdom)
     - PY : python run_py.py  (funzioni del modulo)
2. Confronta gli output donatore per donatore, campo per campo.
3. FALLISCE (exit code 1) se emergono divergenze oltre la tolleranza. Passa
   (exit 0) solo se le due implementazioni coincidono entro tolleranza.

QUESTO NON E' LA BASELINE STORICA DEL MODULO
--------------------------------------------
Il dataset e un BANCO DI PROVA sintetico, progettato APPOSTA per stressare i
punti di divergenza noti (date in formati misti, importi con virgola decimale e
separatore migliaia, esiti campagna in forme diverse). I numeri qui prodotti NON
vanno confrontati con output pubblicati in passato: servono solo a verificare che
le due implementazioni facciano LA STESSA COSA sugli stessi input.

TOLLERANZA (documentata)
------------------------
Gli output delle due implementazioni sono arrotondati:
  - punteggi (prop_*, score_top)      -> 1 decimale  -> 1 ULP = 0.1
  - segnali grezzi (R,F,M_livello,...) -> 4 decimali  -> 1 ULP = 1e-4
  - moltiplicatori territoriali molt_* -> 4 decimali  -> 1 ULP = 1e-4
  - tassi lift (%)                     -> 1 decimale  -> 0.1
  - lift (rapporto)                    -> 2 decimali  -> 0.01
La tolleranza e fissata a 1 ULP dell'output arrotornato: e il massimo scarto che
puo produrre una differenza di arrotondamento cross-linguaggio (Python round()
usa half-to-even, JS Math.round() usa half-up). Qualsiasi scarto OLTRE 1 ULP e'
una divergenza LOGICA, non di arrotondamento, e fa fallire il test.
Campi interi (_recency_days, _n_tx) e categorici (obiettivo_top, engagement,
coinvolgimento, regione_istat, ...) devono coincidere ESATTAMENTE.
Gli scarti non-nulli ma ENTRO tolleranza vengono riportati come avvisi
(livello-arrotondamento) senza far fallire il test.
"""

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

JS_OUT = os.path.join(HERE, "js_output.json")
PY_OUT = os.path.join(HERE, "py_output.json")
REPORT = os.path.join(HERE, "divergenze_report.txt")

OBIETTIVI = ["lascito", "upgrade", "sostegno_continuativo", "riattivazione", "one_off_emergenza"]

# --- tolleranze (1 ULP dell'output arrotondato) ---
TOL_SCORE = 0.1      # prop_*, score_top (1 decimale)
TOL_SIGNAL = 1e-4    # R,F,M_livello,M_trend,L (4 decimali)
TOL_MOLT = 1e-4      # molt_* (4 decimali)
TOL_TASSO = 0.1      # tassi lift % (1 decimale)
TOL_LIFT = 0.01      # lift ratio (2 decimali)

SCORE_FIELDS = [f"prop_{o}" for o in OBIETTIVI] + ["score_top"]
SIGNAL_FIELDS = ["R", "F", "M_livello", "M_trend", "L"]
MOLT_FIELDS = ["molt_lascito", "molt_upgrade", "molt_sostegno_continuativo",
               "molt_riattivazione", "molt_one_off"]
INT_FIELDS = ["_recency_days", "_n_tx"]
CAT_FIELDS = ["obiettivo_top", "engagement", "coinvolgimento", "regione_istat",
              "canale_acquisizione", "tipo_sostegno"]

TOL_BY_FIELD = {}
for f in SCORE_FIELDS:
    TOL_BY_FIELD[f] = TOL_SCORE
for f in SIGNAL_FIELDS:
    TOL_BY_FIELD[f] = TOL_SIGNAL
for f in MOLT_FIELDS:
    TOL_BY_FIELD[f] = TOL_MOLT


def check_config_sync():
    """Esegue 'build_config.py --check': il motore JS (dashboard) e quello Python
    condividono rfml_config.json. Il JSON e' la fonte; nell'HTML e' iniettato a
    build-time. Se qualcuno edita il blocco a mano, o cambia il JSON senza
    rigenerare, le due implementazioni possono ri-divergere (es. bug D6, segno
    del peso gini). Questo check rende la protezione parte del test: non dipende
    dal ricordarsi di lanciare build_config.py a parte."""
    repo = os.path.abspath(os.path.join(HERE, "..", ".."))
    build = os.path.join(repo, "build_config.py")
    print("Verifico la sincronia della config (build_config.py --check)...")
    r = subprocess.run([sys.executable, build, "--check"], cwd=repo,
                       capture_output=True, text=True)
    sys.stdout.write(r.stdout)
    if r.returncode != 0:
        sys.stderr.write(r.stderr)
        return False
    return True


def run_harnesses():
    """Esegue i due harness. Ritorna True se entrambi hanno prodotto output."""
    print("Eseguo il motore JS (node run_js.mjs)...")
    js = subprocess.run(["node", "run_js.mjs"], cwd=HERE, capture_output=True, text=True)
    sys.stdout.write(js.stdout)
    if js.returncode != 0:
        sys.stderr.write(js.stderr)
        print("[FALLITO] harness JS non riuscito.")
        return False
    print("Eseguo il motore Python (python run_py.py)...")
    py = subprocess.run([sys.executable, "run_py.py"], cwd=HERE, capture_output=True, text=True)
    sys.stdout.write(py.stdout)
    if py.returncode != 0:
        sys.stderr.write(py.stderr)
        print("[FALLITO] harness Python non riuscito.")
        return False
    return True


def normalizza_js(js):
    """JS -> schema comune {donor_id: {campi...}}, {lift}."""
    donatori = {}
    for r in js["risultati"]:
        did = r["donor_id"]
        row = {"donor_id": did}
        for o in OBIETTIVI:
            row[f"prop_{o}"] = r[o]            # il JS usa il nome obiettivo nudo
        row["obiettivo_top"] = r["obiettivo_top"]
        row["score_top"] = r["score_top"]
        for k in SIGNAL_FIELDS:
            row[k] = r[k]
        row["_recency_days"] = r["_rd"]        # JS: _rd  vs PY: _recency_days
        row["_n_tx"] = r["_n"]                 # JS: _n   vs PY: _n_tx
        for k in MOLT_FIELDS:
            row[k] = r[k]
        for k in ("regione_istat", "canale_acquisizione", "tipo_sostegno",
                  "engagement", "coinvolgimento"):
            row[k] = r.get(k)
        donatori[did] = row
    return donatori


def normalizza_py(py):
    donatori = {}
    for did, r in py["donatori"].items():
        row = dict(r)
        donatori[did] = row
    return donatori


def stato_lift(l):
    """Riduce l'esito del lift (JS o PY) a uno stato comune + i campi comparabili.
    Stati: 'calcolato' / 'sotto_soglia' / 'colonna_assente' / 'nessun_dato'.
    Appiana i nomi-campo divergenti fra le due implementazioni."""
    if l is None:
        return "nessun_dato", {}
    if l.get("colonna_assente") or l.get("colonnaAssente"):
        return "colonna_assente", {"colonna": l.get("colonna_attesa") or l.get("colonnaAttesa")}
    if l.get("sotto_soglia") or l.get("sottoSoglia"):
        return "sotto_soglia", {"positivi": l.get("positivi")}
    pos = l.get("positivi_totali", l.get("positivi"))
    return "calcolato", {
        "positivi": pos,
        "tasso_top": l.get("tasso_top20pct", l.get("tassoTop")),
        "tasso_resto": l.get("tasso_resto", l.get("tassoResto")),
        "lift": l.get("lift"),
    }


def confronta_lift(label, js_l, py_l, out):
    """Confronta un singolo esito-lift fra JS e PY. Ritorna lista di FAIL."""
    fail = []
    js_stato, js_f = stato_lift(js_l)
    py_stato, py_f = stato_lift(py_l)
    out(f"  {label:22s} JS={js_stato:16s} PY={py_stato}")
    if js_stato != py_stato:
        fail.append(f"{label}: stato diverso JS={js_stato} PY={py_stato}")
        return fail
    if js_stato == "colonna_assente":
        if js_f["colonna"] != py_f["colonna"]:
            fail.append(f"{label}: colonna attesa diversa JS={js_f['colonna']} PY={py_f['colonna']}")
    elif js_stato == "sotto_soglia":
        if js_f["positivi"] != py_f["positivi"]:
            fail.append(f"{label}: positivi diversi JS={js_f['positivi']} PY={py_f['positivi']}")
    elif js_stato == "calcolato":
        if js_f["positivi"] != py_f["positivi"]:
            fail.append(f"{label}: positivi diversi JS={js_f['positivi']} PY={py_f['positivi']}")
        for campo, tol in (("tasso_top", TOL_TASSO), ("tasso_resto", TOL_TASSO), ("lift", TOL_LIFT)):
            vjs, vpy = js_f[campo], py_f[campo]
            try:
                if abs(float(vjs) - float(vpy)) > tol:
                    fail.append(f"{label}.{campo}: JS={vjs} PY={vpy} (oltre tol {tol})")
            except (TypeError, ValueError):
                if vjs != vpy:
                    fail.append(f"{label}.{campo}: JS={vjs!r} PY={vpy!r}")
    return fail


def confronta_valore(campo, vjs, vpy):
    """Ritorna (stato, dettaglio). stato in {'ok','warn','fail'}."""
    # categorici / interi -> match esatto
    if campo in CAT_FIELDS or campo in INT_FIELDS:
        if vjs == vpy:
            return "ok", ""
        return "fail", f"{campo}: JS={vjs!r} PY={vpy!r}"
    # numerici con tolleranza
    tol = TOL_BY_FIELD.get(campo)
    if tol is None:
        # campo numerico non classificato: match esatto prudenziale
        if vjs == vpy:
            return "ok", ""
        return "fail", f"{campo}: JS={vjs!r} PY={vpy!r}"
    try:
        d = abs(float(vjs) - float(vpy))
    except (TypeError, ValueError):
        if vjs == vpy:
            return "ok", ""
        return "fail", f"{campo}: JS={vjs!r} PY={vpy!r}"
    if d == 0:
        return "ok", ""
    if d <= tol:
        return "warn", f"{campo}: JS={vjs} PY={vpy} (d={d:.6g} entro tol {tol:g})"
    return "fail", f"{campo}: JS={vjs} PY={vpy} (d={d:.6g} OLTRE tol {tol:g})"


def main():
    # Console Windows: evita UnicodeEncodeError su caratteri non-cp1252.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    if not check_config_sync():
        print("\n[FALLITO] rfml_config.json e propensione_dashboard.html sono "
              "fuori sincrono.\n  Rigenera con 'python build_config.py' e rilancia. "
              "Test interrotto: le due\n  implementazioni potrebbero divergere sui "
              "valori di config.")
        sys.exit(1)
    if not run_harnesses():
        sys.exit(2)

    with open(JS_OUT, encoding="utf-8") as f:
        js = json.load(f)
    with open(PY_OUT, encoding="utf-8") as f:
        py = json.load(f)

    js_don = normalizza_js(js)
    py_don = normalizza_py(py)

    js_ids = set(js_don)
    py_ids = set(py_don)
    comuni = sorted(js_ids & py_ids)
    solo_js = sorted(js_ids - py_ids)
    solo_py = sorted(py_ids - js_ids)

    righe = []
    def out(s=""):
        righe.append(s)
        print(s)

    out("=" * 78)
    out("TEST DI EQUIVALENZA PYTHON vs JS - Modulo Propensione (RFML)")
    out("=" * 78)
    out("ATTENZIONE: banco di prova sintetico, NON la baseline storica del modulo.")
    out("I numeri servono solo a confrontare le due implementazioni fra loro.")
    out("")
    out(f"Donatori  JS: {len(js_ids):3d}   PY: {len(py_ids):3d}   in comune: {len(comuni)}")
    if solo_js:
        out(f"  Presenti SOLO nel JS: {solo_js}")
    if solo_py:
        out(f"  Presenti SOLO nel PY: {solo_py}   <-- row-set divergente")
    out("")

    n_fail = 0
    n_warn = 0
    donatori_ok = []
    donatori_div = []

    out("-" * 78)
    out("CONFRONTO PER DONATORE (solo donatori in comune)")
    out("-" * 78)
    for did in comuni:
        jr, pr = js_don[did], py_don[did]
        campi = [c for c in pr.keys() if c in jr or c in INT_FIELDS or c in SIGNAL_FIELDS]
        # confronta l'unione dei campi comparabili
        tutti_campi = (SCORE_FIELDS + SIGNAL_FIELDS + MOLT_FIELDS + INT_FIELDS + CAT_FIELDS)
        fail_det, warn_det = [], []
        for c in tutti_campi:
            if c not in jr or c not in pr:
                continue
            stato, det = confronta_valore(c, jr[c], pr[c])
            if stato == "fail":
                fail_det.append(det)
            elif stato == "warn":
                warn_det.append(det)
        if fail_det:
            n_fail += 1
            donatori_div.append(did)
            out(f"\n[DIVERGE] {did}")
            for d in fail_det:
                out(f"    FAIL  {d}")
            for d in warn_det:
                out(f"    warn  {d}")
        elif warn_det:
            n_warn += 1
            donatori_ok.append(did)
            out(f"\n[OK~]     {did}  (scarti entro tolleranza)")
            for d in warn_det:
                out(f"    warn  {d}")
        else:
            donatori_ok.append(did)

    out("")
    out(f"Donatori identici (o entro tolleranza): {len(donatori_ok)}/{len(comuni)}")
    out(f"Donatori con divergenze logiche        : {len(donatori_div)}/{len(comuni)}  {donatori_div}")

    # --- confronto lift per OGNI obiettivo (ognuno sulla sua colonna esito) ---
    out("")
    out("-" * 78)
    out("CONFRONTO LIFT - per obiettivo (colonna esito da ESITO_COLONNE nel JSON)")
    out("-" * 78)
    lift_fail = []
    js_lift_ob = js.get("lift", {})
    py_lift_ob = py.get("lift_obiettivi", {})
    for ob in OBIETTIVI:
        lift_fail += confronta_lift(ob, js_lift_ob.get(ob), py_lift_ob.get(ob), out)
    for d in lift_fail:
        out(f"    FAIL  {d}")

    # --- confronto lift sotto-soglia (<5 positivi: lift NON calcolato) ---
    out("")
    out("-" * 78)
    out("CONFRONTO LIFT SOTTO-SOGLIA - dataset con <5 positivi (lascito)")
    out("-" * 78)
    ss_fail = []
    js_ss = js.get("liftSottoSoglia")
    py_ss = py.get("lift_sottosoglia")
    if js_ss is None or py_ss is None:
        ss_fail.append(f"segmento sotto-soglia assente: JS={js_ss is not None} PY={py_ss is not None}")
        out(f"  liftSottoSoglia JS presente={js_ss is not None}  PY presente={py_ss is not None}")
    else:
        # Entrambe devono DICHIARARE il sotto-soglia e NON produrre un lift.
        js_sotto = bool(js_ss.get("sottoSoglia"))
        py_sotto = bool(py_ss.get("sotto_soglia"))
        js_pos = js_ss.get("positivi")
        py_pos = py_ss.get("positivi")
        js_lift_val = js_ss.get("lift")          # atteso: None (null)
        py_lift_val = py_ss.get("lift")          # atteso: assente -> None
        out(f"  {'campo':16s} {'JS':>12s} {'PY':>12s}")
        out(f"  {'sotto_soglia':16s} {str(js_sotto):>12s} {str(py_sotto):>12s}")
        out(f"  {'positivi':16s} {str(js_pos):>12s} {str(py_pos):>12s}")
        out(f"  {'lift':16s} {str(js_lift_val):>12s} {str(py_lift_val):>12s}")
        if not js_sotto:
            ss_fail.append("JS non ha dichiarato sottoSoglia sul dataset <5 positivi")
        if not py_sotto:
            ss_fail.append("PY non ha dichiarato sotto_soglia sul dataset <5 positivi")
        if js_pos != py_pos:
            ss_fail.append(f"positivi divergono: JS={js_pos} PY={py_pos}")
        if js_lift_val is not None or py_lift_val is not None:
            ss_fail.append(f"lift calcolato nonostante sotto-soglia: JS={js_lift_val} PY={py_lift_val}")
    for d in ss_fail:
        out(f"    FAIL  {d}")

    # --- verdetto ---
    out("")
    out("=" * 78)
    divergenze = (len(solo_js) + len(solo_py) + len(donatori_div)
                  + len(lift_fail) + len(ss_fail))
    if divergenze == 0:
        out("ESITO: PASS - le due implementazioni sono equivalenti entro tolleranza.")
        if n_warn:
            out(f"       ({n_warn} donatori con scarti di solo arrotondamento, entro tolleranza)")
        verdict = 0
    else:
        out("ESITO: FAIL - le due implementazioni DIVERGONO.")
        out(f"  - donatori solo-JS / solo-PY : {len(solo_js)} / {len(solo_py)}")
        out(f"  - donatori con divergenze    : {len(donatori_div)}")
        out(f"  - divergenze sul lift        : {len(lift_fail)}")
        out(f"  - divergenze lift sotto-soglia: {len(ss_fail)}")
        verdict = 1
    out("=" * 78)

    with open(REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(righe) + "\n")
    print(f"\nReport scritto in: {REPORT}")
    sys.exit(verdict)


if __name__ == "__main__":
    main()
