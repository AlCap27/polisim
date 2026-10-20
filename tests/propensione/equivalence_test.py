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

TOLLERANZA: ZERO
----------------
TUTTI i campi devono coincidere ESATTAMENTE fra Python e JS, compresi i campi
arrotondati (punteggi, segnali, moltiplicatori, tassi e lift). Nessuna tolleranza.

Perche zero e non "1 ULP": la differenza fra round() di Python (bancario,
half-to-even) e Math.round di JS (mezzo verso +infinito) NON e imprecisione in
virgola mobile, e una differenza di REGOLA che sposta un punteggio di 0,1 esatti
sui valori a meta (es. 21,25 -> 21,2 vs 21,3). Una tolleranza di 1 ULP maschererebbe
per sempre qualunque regressione futura che sposta un output di quella entita'.
Il Python arrotonda quindi con round_js (replica di Math.round, negativi compresi,
vedi polisim_propensione.py) OVUNQUE arrotondi un output, e qui il confronto e a
tolleranza zero. Il caso a meta e coperto in modo permanente dal donatore
dedicato in donors_*_arrotondamento.csv (punteggio costruito su .x5).
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

# --- tolleranze: ZERO su tutto. Python arrotonda con round_js (= Math.round),
# quindi gli output devono essere identici al bit. Qualsiasi scarto != 0 e' una
# divergenza (di regola o di logica) e DEVE far fallire il test. ---
TOL_SCORE = 0.0      # prop_*, score_top
TOL_SIGNAL = 0.0     # R,F,M_livello,M_trend,L
TOL_MOLT = 0.0       # molt_*
TOL_TASSO = 0.0      # tassi lift %
TOL_LIFT = 0.0       # lift ratio

SCORE_FIELDS = [f"prop_{o}" for o in OBIETTIVI] + ["score_top"]
SIGNAL_FIELDS = ["R", "F", "M_livello", "M_trend", "L"]
MOLT_FIELDS = ["molt_lascito", "molt_upgrade", "molt_sostegno_continuativo",
               "molt_riattivazione", "molt_one_off"]
INT_FIELDS = ["_recency_days", "_n_tx"]
CAT_FIELDS = ["obiettivo_top", "engagement", "coinvolgimento",
              "stato_territoriale", "zona_postale",
              "canale_acquisizione", "tipo_sostegno"]

# I 7 stati territoriali attesi (Compito 4). calcolato/dati_non_caricati coperti
# rispettivamente dalla tabella reale e dalla tabella vuota.
STATI_TERRITORIALI = {
    "calcolato", "cap_assente", "cap_non_italiano", "zona_esclusa",
    "zona_non_riconosciuta", "non_applicabile", "dati_non_caricati",
}

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
    rigenerare, le due implementazioni possono ri-divergere (storicamente: una
    divergenza sul segno di un peso). Questo check rende la protezione parte del
    test: non dipende dal ricordarsi di lanciare build_config.py a parte."""
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


def _norm_js_row(r):
    """Una riga-donatore JS -> schema comune (nomi campo appianati JS->PY)."""
    row = {"donor_id": r["donor_id"]}
    for o in OBIETTIVI:
        row[f"prop_{o}"] = r[o]                # il JS usa il nome obiettivo nudo
    row["obiettivo_top"] = r["obiettivo_top"]
    row["score_top"] = r["score_top"]
    for k in SIGNAL_FIELDS:
        row[k] = r[k]
    row["_recency_days"] = r["_rd"]            # JS: _rd  vs PY: _recency_days
    row["_n_tx"] = r["_n"]                     # JS: _n   vs PY: _n_tx
    for k in MOLT_FIELDS:
        row[k] = r[k]
    for k in ("stato_territoriale", "zona_postale", "canale_acquisizione",
              "tipo_sostegno", "engagement", "coinvolgimento"):
        row[k] = r.get(k)
    return row


def normalizza_js(js):
    """JS -> schema comune {donor_id: {campi...}}, {lift}."""
    return {r["donor_id"]: _norm_js_row(r) for r in js["risultati"]}


def normalizza_py(py):
    donatori = {}
    for did, r in py["donatori"].items():
        row = dict(r)
        donatori[did] = row
    return donatori


def _conteggi(l):
    """Conteggi dei due gruppi, appianando i nomi-campo JS/PY."""
    return {
        "positivi": l.get("positivi_totali", l.get("positivi")),
        "positivi_top": l.get("positivi_top", l.get("positiviTop")),
        "positivi_resto": l.get("positivi_resto", l.get("positiviResto")),
        "n_top": l.get("n_top", l.get("nTop")),
        "n_resto": l.get("n_resto", l.get("nResto")),
    }


def stato_lift(l):
    """Riduce l'esito del lift (JS o PY) a uno stato comune + i campi comparabili.
    Stati: 'calcolato' / 'sotto_soglia' / 'colonna_assente' / 'denominatore_zero'
    / 'nessun_dato'. Appiana i nomi-campo divergenti fra le due implementazioni."""
    if l is None:
        return "nessun_dato", {}
    if l.get("colonna_assente") or l.get("colonnaAssente"):
        return "colonna_assente", {"colonna": l.get("colonna_attesa") or l.get("colonnaAttesa")}
    if l.get("sotto_soglia") or l.get("sottoSoglia"):
        return "sotto_soglia", {"positivi": l.get("positivi")}
    if l.get("denominatore_zero") or l.get("denominatoreZero"):
        return "denominatore_zero", _conteggi(l)
    campi = _conteggi(l)
    campi.update({
        "tasso_top": l.get("tasso_top20pct", l.get("tassoTop")),
        "tasso_resto": l.get("tasso_resto", l.get("tassoResto")),
        "lift": l.get("lift"),
    })
    return "calcolato", campi


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
    elif js_stato == "denominatore_zero":
        # conteggi dei due gruppi devono coincidere esattamente; nessun numero di lift
        for campo in ("positivi", "positivi_top", "positivi_resto", "n_top", "n_resto"):
            if js_f[campo] != py_f[campo]:
                fail.append(f"{label}.{campo}: JS={js_f[campo]} PY={py_f[campo]}")
        if py_f["positivi_resto"] != 0:
            fail.append(f"{label}: positivi_resto atteso 0 (denominatore zero), trovato {py_f['positivi_resto']}")
    elif js_stato == "calcolato":
        for campo in ("positivi", "positivi_top", "positivi_resto", "n_top", "n_resto"):
            if js_f[campo] != py_f[campo]:
                fail.append(f"{label}.{campo}: JS={js_f[campo]} PY={py_f[campo]}")
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
    # Tolleranza zero: qualunque scarto non nullo e' una divergenza. (Con tol=0 il
    # ramo 'warn' non si attiva mai: Python e JS arrotondano con la stessa regola.)
    if d <= tol:
        return "warn", f"{campo}: JS={vjs} PY={vpy} (d={d:.6g} entro tol {tol:g})"
    return "fail", f"{campo}: JS={vjs} PY={vpy} (d={d:.6g} OLTRE tol {tol:g})"


def confronta_dataset(label, js_d, py_d, out):
    """Robustezza CSV (Compito 1): confronta su un dataset le righe lette, le
    righe escluse (conteggio campi) e i punteggi per donatore. Ritorna lista FAIL.
    Entrambe le implementazioni devono leggere/escludere ESATTAMENTE lo stesso."""
    fail = []
    if js_d is None or py_d is None:
        return [f"{label}: sezione assente (JS={js_d is not None} PY={py_d is not None})"]
    # 1) numero di righe lette (ana + tx)
    for campo in ("n_ana", "n_tx"):
        if js_d[campo] != py_d[campo]:
            fail.append(f"{label}.{campo}: JS={js_d[campo]} PY={py_d[campo]}")
    out(f"  {label:16s} righe ana JS={js_d['n_ana']} PY={py_d['n_ana']}  "
        f"tx JS={js_d['n_tx']} PY={py_d['n_tx']}")
    # 2) righe escluse (conteggio campi): stesso numero E stesse righe (riga, n_campi, anteprima)
    for campo in ("escluse_ana", "escluse_tx"):
        je, pe = js_d[campo], py_d[campo]
        out(f"  {label:16s} {campo}: JS={len(je)} PY={len(pe)}")
        if len(je) != len(pe):
            fail.append(f"{label}.{campo}: numero righe escluse JS={len(je)} PY={len(pe)}")
            continue
        for a, b in zip(je, pe):
            if a != b:
                fail.append(f"{label}.{campo}: riga esclusa divergente JS={a} PY={b}")
    # 3) punteggi per donatore (stesso row-set + ogni campo entro tolleranza)
    js_don = {did: _norm_js_row(r) for did, r in js_d["donatori"].items()}
    py_don = py_d["donatori"]
    js_ids, py_ids = set(js_don), set(py_don)
    if js_ids != py_ids:
        fail.append(f"{label}: row-set donatori divergente "
                    f"solo-JS={sorted(js_ids - py_ids)} solo-PY={sorted(py_ids - js_ids)}")
    tutti_campi = (SCORE_FIELDS + SIGNAL_FIELDS + MOLT_FIELDS + INT_FIELDS + CAT_FIELDS)
    for did in sorted(js_ids & py_ids):
        jr, pr = js_don[did], py_don[did]
        for c in tutti_campi:
            if c not in jr or c not in pr:
                continue
            stato, det = confronta_valore(c, jr[c], pr[c])
            if stato == "fail":
                fail.append(f"{label}.{did}.{det}")
    return fail


# Risoluzione territoriale ATTESA per donatore del dataset territori (contratto del
# dataset, non derivabile dal config): un CAP per classe di stato.
DONOR_STATO_ATTESO = {
    "T1_nocap": "cap_assente",
    "T2_vaticano": "cap_non_italiano",
    "T3_zona08": "zona_esclusa",
    "T4_prefisso99": "zona_non_riconosciuta",
    "T5_zona20": "calcolato",
    # 47890 (San Marino) condivide il prefisso con la zona italiana 47: deve
    # risultare cap_non_italiano, non zona 47.
    "T6_sanmarino": "cap_non_italiano",
}


def _obiettivi_non_applicabili():
    """Obiettivi con tutti i pesi territoriali a zero, letti dal config (fonte
    unica): non duplichiamo la regola nel test."""
    repo = os.path.abspath(os.path.join(HERE, "..", ".."))
    with open(os.path.join(repo, "rfml_config.json"), encoding="utf-8") as f:
        cfg = json.load(f)
    pesi = cfg["tabelle"]["PESI_TERRITORIALI"]["valori"]
    na = set()
    for ob in OBIETTIVI:
        p = pesi.get(ob, {})
        if all(float(p.get(k, 0.0)) == 0.0 for k in p):
            na.add(ob)
    return na


def _stato_atteso(donor, ob, na_set, tabella_vuota):
    if ob in na_set:
        return "non_applicabile"
    if tabella_vuota:
        return "dati_non_caricati"
    return DONOR_STATO_ATTESO[donor]


def confronta_stati_territoriali(js_st, py_st, out):
    """Compito 4: i 7 stati territoriali devono essere (a) identici fra JS e PY
    cella per cella e (b) CORRETTI rispetto all'atteso, con tabella reale e vuota.
    Ritorna (lista_fail, insieme_stati_osservati)."""
    fail = []
    osservati = set()
    if js_st is None or py_st is None:
        return ([f"stati_territoriali: sezione assente (JS={js_st is not None} "
                 f"PY={py_st is not None})"], osservati)
    na_set = _obiettivi_non_applicabili()
    for scenario, vuota in (("reale", False), ("vuota", True)):
        js_m = js_st.get(scenario, {})
        py_m = py_st.get(scenario, {})
        if set(js_m) != set(py_m):
            fail.append(f"stati[{scenario}]: donatori divergenti JS={sorted(js_m)} PY={sorted(py_m)}")
            continue
        for did in sorted(js_m):
            for ob in OBIETTIVI:
                vjs, vpy = js_m[did].get(ob), py_m[did].get(ob)
                osservati.add(vjs)
                osservati.add(vpy)
                atteso = _stato_atteso(did, ob, na_set, vuota)
                if vjs != vpy:
                    fail.append(f"stati[{scenario}].{did}.{ob}: JS={vjs} PY={vpy} (divergono)")
                elif vjs != atteso:
                    fail.append(f"stati[{scenario}].{did}.{ob}: stato={vjs} atteso={atteso}")
    return (fail, osservati)


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

    # --- confronto lift denominatore-zero (positivi tutti nel top 20%, 0 nel resto) ---
    out("")
    out("-" * 78)
    out("CONFRONTO LIFT DENOMINATORE-ZERO - positivi tutti nel top 20%, 0 nel resto")
    out("-" * 78)
    dz_fail = []
    js_dz = js.get("liftDenomZero")
    py_dz = py.get("lift_denomzero")
    js_dz_stato = stato_lift(js_dz)[0]
    py_dz_stato = stato_lift(py_dz)[0]
    if js_dz_stato != "denominatore_zero":
        dz_fail.append(f"JS stato atteso 'denominatore_zero', trovato '{js_dz_stato}' "
                       "(il dataset non ha prodotto il caso: regolare i positivi/score)")
    if py_dz_stato != "denominatore_zero":
        dz_fail.append(f"PY stato atteso 'denominatore_zero', trovato '{py_dz_stato}' "
                       "(il dataset non ha prodotto il caso: regolare i positivi/score)")
    dz_fail += confronta_lift("denominatore_zero", js_dz, py_dz, out)
    for d in dz_fail:
        out(f"    FAIL  {d}")

    # --- robustezza CSV (Compito 1): a-capo citato + riga malformata ---
    out("")
    out("-" * 78)
    out("CONFRONTO ROBUSTEZZA CSV - a-capo dentro campo citato + riga malformata")
    out("-" * 78)
    out("  Atteso: a-capo citato -> 0 esclusioni (riga letta intera, come Python);")
    out("  riga malformata (n° campi != intestazione) -> esclusa e CONTATA uguale.")
    csv_fail = []
    csv_fail += confronta_dataset("quotednewline", js.get("quotednewline"),
                                  py.get("quotednewline"), out)
    csv_fail += confronta_dataset("malformed", js.get("malformed"),
                                  py.get("malformed"), out)
    csv_fail += confronta_dataset("novaliddate", js.get("novaliddate"),
                                  py.get("novaliddate"), out)
    for d in csv_fail:
        out(f"    FAIL  {d}")

    # --- Compito 2: semantica dell'helper maxArr/minArr (sostituisce lo spread
    # Math.max/min, che rompeva a ~123k elementi). Deve coincidere con Math.max/
    # Math.min: vuoto -> ∓Infinity; un NaN -> NaN. Check SOLO-JS (il Python usa i
    # builtin max()/min(), che su [] sollevano: qui si verifica l'helper JS). ---
    out("")
    out("-" * 78)
    out("CONFRONTO HELPER maxArr/minArr - semantica Math.max/min preservata")
    out("-" * 78)
    hs_fail = []
    hs = js.get("helperSemantics")
    attesi = {"maxEmpty": "-Infinity", "minEmpty": "Infinity",
              "maxNaN": "NaN", "minNaN": "NaN", "maxVal": "3", "minVal": "1"}
    if not hs:
        hs_fail.append("helperSemantics assente nell'output JS")
    else:
        for k, atteso in attesi.items():
            got = hs.get(k)
            stato_ok = "OK" if got == atteso else "FAIL"
            out(f"  {k:10s} atteso={atteso:10s} ottenuto={str(got):10s} [{stato_ok}]")
            if got != atteso:
                hs_fail.append(f"helper {k}: atteso {atteso} ottenuto {got}")
    csv_fail += hs_fail
    for d in hs_fail:
        out(f"    FAIL  {d}")

    # --- Arrotondamento (regola Math.round, non bancaria): caso .x5 permanente ---
    out("")
    out("-" * 78)
    out("CONFRONTO ARROTONDAMENTO - regola Math.round (mezzo verso +infinito) su .x5")
    out("-" * 78)
    out("  ARR002_mezzo ha prop_riattivazione con grezzo ESATTO 21.25 -> deve dare")
    out("  21.3 (half-up) in ENTRAMBE; round() bancario di Python darebbe 21.2.")
    arr_fail = confronta_dataset("arrotondamento", js.get("arrotondamento"),
                                 py.get("arrotondamento"), out)
    # Asserzione di REGOLA (non solo equivalenza): se qualcuno reintroduce round()
    # bancario in Python il valore scende a 21.2 e qui si vede subito, anche se per
    # ipotesi JS e PY tornassero d'accordo sul valore sbagliato.
    ATTESO_X5 = 21.3
    js_arr = (js.get("arrotondamento") or {}).get("donatori", {})
    py_arr = (py.get("arrotondamento") or {}).get("donatori", {})
    jv = js_arr.get("ARR002_mezzo", {}).get("riattivazione")   # JS: nome obiettivo nudo
    pv = py_arr.get("ARR002_mezzo", {}).get("prop_riattivazione")
    out(f"  ARR002_mezzo.prop_riattivazione: JS={jv} PY={pv} (atteso {ATTESO_X5}; bancario=21.2)")
    if jv != ATTESO_X5:
        arr_fail.append(f"ARR002_mezzo: JS prop_riattivazione={jv}, atteso {ATTESO_X5} (regola Math.round)")
    if pv != ATTESO_X5:
        arr_fail.append(f"ARR002_mezzo: PY prop_riattivazione={pv}, atteso {ATTESO_X5} "
                        f"(usare round_js, non round() bancario)")
    for d in arr_fail:
        out(f"    FAIL  {d}")

    # --- Compito 4: i 7 stati territoriali, con tabella ZONE_POSTALI reale e vuota ---
    out("")
    out("-" * 78)
    out("CONFRONTO STATI TERRITORIALI - 7 stati, identici JS/PY e corretti (reale+vuota)")
    out("-" * 78)
    out("  6 donatori (senza CAP, 00120, zona 08, prefisso inesistente, zona valida, 47890 San Marino)")
    out("  x 5 obiettivi, con tabella reale e con tabella vuota (dati_non_caricati).")
    terr_fail, stati_osservati = confronta_stati_territoriali(
        js.get("statiTerritoriali"), py.get("stati_territoriali"), out)
    mancanti = STATI_TERRITORIALI - stati_osservati
    if mancanti:
        terr_fail.append(f"stati non prodotti dal banco: {sorted(mancanti)} "
                         f"(osservati: {sorted(stati_osservati)})")
    out(f"  stati osservati: {sorted(stati_osservati)}")
    out(f"  i 7 stati attesi tutti prodotti: {'SI' if not mancanti else 'NO'}")
    for d in terr_fail:
        out(f"    FAIL  {d}")

    # --- verdetto ---
    out("")
    out("=" * 78)
    divergenze = (len(solo_js) + len(solo_py) + len(donatori_div)
                  + len(lift_fail) + len(ss_fail) + len(dz_fail) + len(csv_fail)
                  + len(arr_fail) + len(terr_fail))
    if divergenze == 0:
        out("ESITO: PASS - le due implementazioni sono IDENTICHE (tolleranza zero).")
        if n_warn:
            out(f"       ({n_warn} donatori con scarti di solo arrotondamento, entro tolleranza)")
        verdict = 0
    else:
        out("ESITO: FAIL - le due implementazioni DIVERGONO.")
        out(f"  - donatori solo-JS / solo-PY : {len(solo_js)} / {len(solo_py)}")
        out(f"  - donatori con divergenze    : {len(donatori_div)}")
        out(f"  - divergenze sul lift        : {len(lift_fail)}")
        out(f"  - divergenze lift sotto-soglia: {len(ss_fail)}")
        out(f"  - divergenze lift denom-zero : {len(dz_fail)}")
        out(f"  - divergenze robustezza CSV  : {len(csv_fail)}")
        out(f"  - divergenze arrotondamento  : {len(arr_fail)}")
        out(f"  - divergenze stati territoriali: {len(terr_fail)}")
        verdict = 1
    out("=" * 78)

    with open(REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(righe) + "\n")
    print(f"\nReport scritto in: {REPORT}")
    sys.exit(verdict)


if __name__ == "__main__":
    main()
