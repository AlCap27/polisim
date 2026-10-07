#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_py.py — esegue il motore RFML Python (polisim_propensione.py) su un dataset
CSV e serializza l'output in JSON, per il confronto di equivalenza con il JS.

Replica la sequenza di main() (lettura -> segnali -> propensione -> contesto ->
valida_lift), senza scrivere il CSV ne stampare il riepilogo.

NOTA SUL DELIMITATORE (divergenza D0, documentata)
--------------------------------------------------
Il dataset di test usa ';' come separatore, perche i casi-limite europei
("1.000,50", "10,50") contengono virgole che un file ','-delimitato
spezzerebbe. Il parser JS (parseCSV) auto-rileva ';' vs ','; il modulo Python
(leggi_csv) hardcoda ',' e sul file ';' collasserebbe tutto in un'unica colonna.
Questa E' una divergenza (D0). Per isolare le divergenze NUMERICHE da questo
problema di I/O, qui leggiamo il lato Python con delimiter=';' e chiamiamo le
FUNZIONI PURE del modulo (non leggi_csv). D0 resta documentata a parte.
"""

import csv
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, REPO)

import polisim_propensione as pp  # noqa: E402

ANA = os.path.join(HERE, "donors_anagrafica_test.csv")
TX = os.path.join(HERE, "donors_transazioni_test.csv")
OUT = os.path.join(HERE, "py_output.json")


def leggi_csv_semicolon(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f, delimiter=";"))


def main():
    anagrafica = leggi_csv_semicolon(ANA)
    transazioni = leggi_csv_semicolon(TX)

    segnali = pp.calcola_segnali(anagrafica, transazioni)

    fascia_eta_per_donor = {
        r["donor_id"]: (r.get("fascia_eta") or "").strip() for r in anagrafica
    }
    cap_per_donor = {
        r["donor_id"]: (r.get("cap") or r.get("cap_donatore") or "").strip()
        for r in anagrafica
    }

    propensione = pp.calcola_propensione(segnali, fascia_eta_per_donor, cap_per_donor)
    contesto = pp.calcola_contesto(anagrafica)

    obiettivi = list(pp.PESI_OBIETTIVO.keys())

    donatori = {}
    for did in segnali:
        prop = propensione[did]
        top_ob = max(prop, key=prop.get)
        cap = cap_per_donor.get(did, "")
        regione = pp.cap_to_regione(cap) or "n/d"
        row = {"donor_id": did}
        for o in obiettivi:
            row[f"prop_{o}"] = prop[o]
        row["obiettivo_top"] = top_ob
        row["score_top"] = prop[top_ob]
        for k in ("R", "F", "M_livello", "M_trend", "L"):
            row[k] = segnali[did][k]
        row["_recency_days"] = segnali[did]["_recency_days"]
        row["_n_tx"] = segnali[did]["_n_tx"]
        row["regione_istat"] = regione
        row["molt_lascito"] = pp.calcola_moltiplicatore_territoriale(cap, "lascito")
        row["molt_upgrade"] = pp.calcola_moltiplicatore_territoriale(cap, "upgrade")
        row["molt_sostegno_continuativo"] = pp.calcola_moltiplicatore_territoriale(cap, "sostegno_continuativo")
        row["molt_riattivazione"] = pp.calcola_moltiplicatore_territoriale(cap, "riattivazione")
        row["molt_one_off"] = pp.calcola_moltiplicatore_territoriale(cap, "one_off_emergenza")
        row.update(contesto.get(did, {}))
        donatori[did] = row

    # valida_lift: come in main(), solo obiettivo 'lascito'
    lift_lascito = pp.valida_lift(anagrafica, propensione, "lascito")

    out = {"donatori": donatori, "lift_lascito": lift_lascito}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"PY: {len(donatori)} donatori -> {OUT}")


if __name__ == "__main__":
    main()
