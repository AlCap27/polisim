#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
polisim_propensione.py — Modulo Propensione PoliSim (RFML multi-obiettivo)
===========================================================================
Verticale: NGO (fundraising). Predisposto per Sindacato / Municipalita.

COSA FA
-------
NON e un RFM generico. Risponde a: "ho un OBIETTIVO di raccolta, ho dei dati,
chi devo raggiungere e come?"

Calcola 4 segnali grezzi (R, F, M, L) UNA volta per donatore, poi applica
una matrice OBIETTIVO -> PESI. Lo stesso donatore ha propensioni diverse per
obiettivi diversi (lascito, upgrade, sostegno continuativo, riattivazione,
one-off). Niente cluster unico: un punteggio per ogni obiettivo.

ONESTA METODOLOGICA
-------------------
I pesi in PESI_OBIETTIVO sono EXPERT-CALIBRATED (Sargeant 2007 adattato al
contesto italiano), NON validati su A/B test propri. Sono il punto di aggancio
del feedback loop futuro: dopo ~20 campagne con esito misurato, QUESTA matrice
si ricalibra sui donatori reali. Finche cio non avviene, vanno trattati come
ipotesi ragionate, non come verita.

LIMITE NOTO SUI DATI SINTETICI
------------------------------
Su donors_*_sintetici, anzianita_anni va da 3 a 15. La saturazione di L a 20
anni (corretta per dati reali) e quindi inerte qui: L risulta di fatto lineare.
Il comportamento della saturazione non e verificabile finche non arrivano dati
reali con anzianita piu alte.

INPUT
-----
- anagrafica CSV : donor_id, anzianita_anni, fascia_eta, canale_acquisizione,
                   tipo_sostegno_prevalente, metodo_pagamento_prevalente,
                   volontariato, eventi_partecipati, lascito_dichiarato,
                   cap (opzionale: abilita layer territoriale ISTAT 2021) ...
- transazioni CSV: donor_id, data_donazione, importo_eur, metodo_pagamento, ...

OUTPUT
------
- propensione_output.csv : un punteggio 0-100 per OGNI obiettivo + tag contesto
- riepilogo a console    : top target per obiettivo, validazione lift su lasciti

USO
---
    python polisim_propensione.py \\
        --anagrafica donors_anagrafica_sintetica.csv \\
        --transazioni donors_transazioni_sintetiche.csv \\
        --out propensione_output.csv
"""

import argparse
import csv
import json
import math
import os
import re
import sys
from collections import defaultdict
from datetime import datetime
from statistics import mean, pstdev

# ===========================================================================
# 1. CONFIGURAZIONE CONDIVISA   <-- UNICA FONTE: rfml_config.json
# ===========================================================================
# Tabelle e costanti del motore RFML (pesi obiettivo, layer territoriale ISTAT,
# bonus demografico, soglie) NON vivono piu' come literal qui: stanno in
# rfml_config.json, letto a runtime. La dashboard (propensione_dashboard.html)
# usa LO STESSO file, iniettato a build-time da build_config.py. Unico punto di
# modifica dei pesi = il JSON; equivalence_test.py esegue 'build_config.py
# --check' e fallisce se le due copie ri-divergono. Provenienza e stato di
# validazione di ogni tabella: campo "fonte_dati" nel JSON (molte marcate
# "verificato": false — non ancora ricontrollate su fonte primaria).
#
# Significato dei segnali (pesi in PESI_OBIETTIVO, somma per riga = 1.0):
#   R = recency (invertita: donato di recente -> alto)
#   F = frequency/costanza (regolarita intervalli + bonus metodo ricorrente)
#   M_livello = importo medio per donazione (capacita)
#   M_trend   = pendenza dell'importo nel tempo (in crescita / in calo)
#   L = longevity (anni di relazione, con saturazione)

_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rfml_config.json")
with open(_CONFIG_PATH, encoding="utf-8") as _f:
    _CFG = json.load(_f)
_TAB = _CFG["tabelle"]
_COST = _CFG["costanti"]

# Matrice OBIETTIVO -> PESI {R, F, M_livello, M_trend, L}.
PESI_OBIETTIVO = _TAB["PESI_OBIETTIVO"]["valori"]

# Obiettivi per cui la Recency va INTERPRETATA come target (alta distanza = segnale).
# Per la riattivazione cerchiamo proprio chi NON dona da tempo ma e stato fedele.
OBIETTIVI_RECENCY_INVERSA = set(_COST["OBIETTIVI_RECENCY_INVERSA"])

# ---------------------------------------------------------------------------
# BONUS DEMOGRAFICO  <-- fattore dichiarato e ISOLATO, non un quinto segnale
# ---------------------------------------------------------------------------
# L'eta NON e un segnale RFML, ma e il predittore reale del lascito (Sargeant;
# confermato sui dati: 65+ ha tasso lasciti ~3x rispetto a 45-64). Ignorarla
# per purezza architetturale sarebbe una scorciatoia al contrario.
#
# Si applica SOLO agli obiettivi elencati qui (di default solo 'lascito') ed
# entra come moltiplicatore additivo dichiarato, separato dai pesi RFML.
# Punto di aggancio del feedback loop, ricalibrabile come PESI_OBIETTIVO.
#
# Valori calibrati sul tasso lascito_dichiarato per fascia eta nei dati
# sintetici (base debole; "verificato": false nel JSON). NB: la fascia 35-44
# mostra un tasso anomalo (rumore) e viene trattata come neutra, non premiata.
BONUS_DEMOGRAFICO = _TAB["BONUS_DEMOGRAFICO"]["valori"]

# Saturazione Longevity: oltre questa soglia (anni) la propensione non cresce
# piu linearmente. Corretta per dati reali; inerte sui sintetici (max 15 anni).
L_SATURAZIONE_ANNI = _COST["L_SATURAZIONE_ANNI"]

# Metodi di pagamento considerati "ricorrenti" (commitment automatizzato).
METODI_RICORRENTI = set(_COST["METODI_RICORRENTI"])


# ===========================================================================
# LAYER TERRITORIALE ISTAT 2021  <-- moltiplicatori calibrati su dati reali
# ===========================================================================
# Fonte: ISTAT Censimento Permanente 2021 + MEF redditi per regione.
# Granularità: regione (ricavata dal CAP in input).
# Entra come MOLTIPLICATORE DICHIARATO su prop_lascito e prop_one_off_emergenza.
# Non modifica gli altri 3 obiettivi — non ci sono basi empiriche sufficienti.
#
# Logica dei tre indicatori:
#   pct_over65       → proxy diretto legacy prospect (Sargeant: 65+ ha tasso
#                       lasciti ~3x rispetto a 45-64)
#   reddito_medio    → proxy capacità donativa
#   gini_index       → disuguaglianza interna: alta gini = più varianza nelle
#                       donazioni, penalizza leggermente la stima media
#
# Il moltiplicatore finale è compreso tra 0.85 e 1.15 (±15% sullo score).
# Dichiarato e isolato — non contamina i segnali RFML.

ISTAT_REGIONI = _TAB["ISTAT_REGIONI"]["valori"]

# Lookup CAP (prime 2 cifre = prefisso provincia) → regione.
# Copertura: prefissi CAP italiani 00-98 (49, 77-79 non assegnati, assenti).
CAP_PREFISSO_REGIONE = _TAB["CAP_PREFISSO_REGIONE"]["valori"]

# Valori nazionali medi — usati come fallback se CAP mancante o non riconosciuto
ISTAT_NAZIONALE = _TAB["ISTAT_NAZIONALE"]["valori"]


def cap_to_regione(cap: str) -> str | None:
    """Ricava la regione dalle prime 2 cifre del CAP. None se non riconosciuto."""
    if not cap:
        return None
    cap_clean = str(cap).strip().zfill(5)[:2]
    return CAP_PREFISSO_REGIONE.get(cap_clean)


# Pesi dei tre indicatori ISTAT per ogni obiettivo.
# Struttura (dict per obiettivo): over65, reddito, gini_penalita, cap
# cap: ampiezza massima del moltiplicatore (es. 0.15 = ±15%)
# gini_penalita e' POSITIVO: combinato con l'inversione gia' presente nella
# formula (gini_norm = -(gini-0.328)/0.04), un'alta gini ABBASSA lo score.
# Ipotesi di dominio NON validata (vedi "ipotesi_non_validate" nel JSON).
#
# Razionale per obiettivo:
#   lascito            — over65 dominante (legacy prospect); reddito secondario;
#                        gini penalizza. Cap ±15%: impatto forte e documentato.
#   upgrade            — reddito dominante (capacità di aumentare); over65 neutro;
#                        gini penalizza (alta disuguaglianza = donatore medio meno
#                        rappresentativo). Cap ±10%.
#   sostegno_continu.  — reddito favorisce stabilità RID; over65 lievemente negativo
#                        (anziani preferiscono one-off a RID automatico); gini
#                        penalizza. Cap ±10%.
#   riattivazione      — territorio conta meno (dipende da storia individuale);
#                        reddito ha un piccolo effetto; over65 neutro; gini lieve
#                        penalità. Cap smorzato ±5%.
#   one_off_emergenza  — reddito dominante; over65 secondario (donano anche 65+
#                        in emergenza); gini penalizza fortemente (alta
#                        disuguaglianza = risposta all'appello meno prevedibile).
#                        Cap ±12%.
PESI_TERRITORIALI = _TAB["PESI_TERRITORIALI"]["valori"]


def calcola_moltiplicatore_territoriale(cap: str, obiettivo: str) -> float:
    """
    Restituisce un moltiplicatore territoriale per tutti e 5 gli obiettivi.
    Range: 1 ± cap_variazione (es. 0.85–1.15 per lascito, 0.95–1.05 per
    riattivazione). Dichiarato e isolato — non contamina i segnali RFML.

    Se CAP mancante o non riconosciuto: moltiplicatore neutro 1.0.
    """
    pesi = PESI_TERRITORIALI.get(obiettivo)
    if not pesi:
        return 1.0

    regione = cap_to_regione(cap)
    dati = ISTAT_REGIONI.get(regione, ISTAT_NAZIONALE) if regione else ISTAT_NAZIONALE

    w_over65 = pesi["over65"]
    w_reddito = pesi["reddito"]
    w_gini = pesi["gini_penalita"]
    cap_var = pesi["cap"]

    # Normalizza i tre indicatori rispetto ai range nazionali italiani
    # over65: range 21.2–28.7 → centro 24.1
    over65_norm = (dati["over65"] - 24.1) / 4.5

    # reddito: range 16500–34400 → centro 24700
    reddito_norm = (dati["reddito"] - 24700) / 9000

    # gini: range 0.289–0.371 → centro 0.328 — alto gini penalizza (invertito)
    gini_norm = -(dati["gini"] - 0.328) / 0.04

    score = w_over65 * over65_norm + w_reddito * reddito_norm + w_gini * gini_norm

    # Cappa al range dichiarato per questo obiettivo
    moltiplicatore = 1.0 + max(-cap_var, min(cap_var, score * cap_var))
    return round(moltiplicatore, 4)

# Soglia minima di positivi storici (lascito_dichiarato) sotto la quale
# l'addestramento ML supervisionato e sconsigliato (overfitting).
SOGLIA_ML_POSITIVI = _COST["SOGLIA_ML_POSITIVI"]

# Soglia minima di positivi storici sotto la quale il lift NON viene calcolato
# (ne mostrato): con pochi positivi il bucket top-20% ne contiene 0-1 e il
# rapporto oscilla in modo instabile. Distinta da SOGLIA_ML_POSITIVI. Unica
# costante con struttura {fonte_dati, valore} nel JSON -> si legge ["valore"].
SOGLIA_LIFT_POSITIVI = _COST["SOGLIA_LIFT_POSITIVI"]["valore"]

# Mapping obiettivo -> colonna esito-campagna e ripiego, dal JSON condiviso
# (fonte unica, non duplicare qui). valida_lift li usa per sapere su quale
# colonna misurare il lift di ciascun obiettivo (prima solo 'lascito').
ESITO_COLONNE = _TAB["ESITO_COLONNE"]["valori"]
ESITO_FALLBACK = _TAB["ESITO_FALLBACK"]["valori"]


# ===========================================================================
# 2. LETTURA DATI
# ===========================================================================
def leggi_csv(path):
    # D0: auto-rileva il delimitatore come il dashboard (parseCSV): ';' se presente
    # nell'intestazione, altrimenti ','. Un export con ';' letto come ',' non da'
    # eccezione — collassa tutto in un'unica colonna e si manifesta come "tutti i
    # campi vuoti", un errore silenzioso. L'auto-detect lo previene.
    with open(path, newline="", encoding="utf-8-sig") as f:
        prima = f.readline()
        delim = ";" if ";" in prima else ","
        f.seek(0)
        return list(csv.DictReader(f, delimiter=delim))


def parse_data(s):
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s.strip(), fmt)
        except (ValueError, AttributeError):
            continue
    return None


def parse_float(s, default=0.0):
    if s is None:
        return default
    try:
        return float(str(s).replace(",", ".").strip())
    except (ValueError, AttributeError):
        return default


_NUM_PREFIX = re.compile(r"^[0-9.,]+")


def _valid_thousands(s):
    """True se s e' un intero con separatore migliaia ben formato (1.000.000) o un
    intero semplice (1000). Primo gruppo 1-3 cifre, successivi esattamente 3."""
    if "." not in s:
        return s.isdigit()
    gruppi = s.split(".")
    if any(not g.isdigit() for g in gruppi):
        return False
    if not (1 <= len(gruppi[0]) <= 3):
        return False
    return all(len(g) == 3 for g in gruppi[1:])


def _parse_num_token(tok):
    """Parsa un token [0-9.,]+. Ritorna (valore, esito):
    esito True = ok, None = AMBIGUO (non indovinare), False = malformato."""
    if "," in tok:
        # virgola presente -> formato EUROPEO: punto=migliaia, virgola=decimali
        if tok.count(",") != 1:
            return 0.0, False
        intp, dec = tok.split(",")
        if dec == "" or not dec.isdigit():
            return 0.0, False
        if intp == "":
            intp_val = "0"
        elif _valid_thousands(intp):
            intp_val = intp.replace(".", "")
        else:
            return 0.0, False
        return float(intp_val + "." + dec), True
    dots = tok.count(".")
    if dots == 0:
        return (float(tok), True) if tok.isdigit() else (0.0, False)
    if dots == 1:
        left, right = tok.split(".")
        if right == "" or not right.isdigit():
            return 0.0, False
        if left == "":
            left = "0"
        elif not left.isdigit():
            return 0.0, False
        if len(right) == 3:
            return 0.0, None            # punto + ESATTAMENTE 3 cifre -> AMBIGUO
        return float(left + "." + right), True   # 1,2,4+ cifre -> anglo decimale
    # dots >= 2, nessuna virgola -> solo separatore migliaia e' valido
    if _valid_thousands(tok):
        return float(tok.replace(".", "")), True
    return 0.0, False


def parse_importo(raw):
    """Parser importi EUR condiviso con il dashboard JS (D3/D4). Ritorna
    (valore, stato), stato in {'ok','coda','ambiguo','malformato','vuoto'}.
    Regole (vedi README): virgola -> europeo; punto+3 cifre senza virgola ->
    AMBIGUO (scarta, non indovina); punto+1/2/4+ cifre -> decimale anglosassone;
    piu' punti a gruppi di 3 -> migliaia; coda non numerica (50abc) -> accetta il
    prefisso SOLO se completo, ma conta sempre l'occorrenza. Chi scarta
    (ambiguo/malformato/vuoto) usa 0.0; gli stati != ok/vuoto vanno mostrati a
    fine run col valore originale (mai 0 in silenzio)."""
    if raw is None:
        return 0.0, "vuoto"
    s = str(raw).strip()
    if s == "":
        return 0.0, "vuoto"
    sign = 1.0
    if s[0] in "+-":
        if s[0] == "-":
            sign = -1.0
        s = s[1:]
    m = _NUM_PREFIX.match(s)
    if not m:
        return 0.0, "malformato"
    tok = m.group(0)
    coda = len(tok) != len(s)
    val, esito = _parse_num_token(tok)
    if esito is None:
        return 0.0, "ambiguo"
    if esito is False:
        return 0.0, "malformato"
    return sign * val, ("coda" if coda else "ok")


def conta_importi_non_validi(transazioni):
    """D3/D4: importi scartati (ambiguo/malformato) o recuperati con coda non
    numerica. Ritorna {valore_originale: (n_occorrenze, stato)}; esclude ok/vuoto.
    main() lo riporta a fine run: nessun importo imputato a 0 in silenzio."""
    rep = {}
    for t in transazioni:
        s = (t.get("importo_eur") or "").strip()
        if s == "":
            continue
        _, stato = parse_importo(s)
        if stato in ("coda", "ambiguo", "malformato"):
            n, _st = rep.get(s, (0, stato))
            rep[s] = (n + 1, stato)
    return rep


def conta_date_non_valide(transazioni):
    """D1/D2: date con valore non vuoto ma non riconducibile a un formato
    documentato (YYYY-MM-DD, YYYY/MM/DD, DD/MM/YYYY). Ritorna
    {valore_originale: n_occorrenze}. Non si scartano in silenzio: main() le
    riporta a fine run con il valore originale (speculare al dashboard JS)."""
    anomale = {}
    for t in transazioni:
        s = (t.get("data_donazione") or "").strip()
        if s and parse_data(s) is None:
            anomale[s] = anomale.get(s, 0) + 1
    return anomale


# Vocabolario esiti campagna / flag (D5), condiviso col dashboard (flagPositivo).
# Case-insensitive, trim. "x" e' positivo ma TRACCIATO a parte: in molti CSV IT
# e' una spunta, ma altrove indica l'opposto (escluso) -> va verificato.
_FLAG_POS = {"1", "si", "sì", "s", "true", "vero", "v", "yes", "y", "ok"}
_FLAG_NEG = {"0", "no", "n", "false", "falso", ""}


def flag_positivo(raw):
    """Classifica un valore-esito/flag (es. lascito_dichiarato) in:
      'pos'    positivo riconosciuto
      'pos_x'  positivo via 'x' (spunta) — contato come positivo ma tracciato
      'neg'    negativo esplicito (0/no/n/false/falso/vuoto) o numerico == 0
      'ignoto' non riconosciuto -> NON positivo, va contato e mostrato col
               valore originale. Stesso principio di date/importi: un valore che
               non riconosci non e' un negativo, e' un valore che non hai capito."""
    if isinstance(raw, bool):
        return "pos" if raw else "neg"
    if raw is None:
        return "neg"
    s = str(raw).strip().lower()
    if s == "x":
        return "pos_x"
    if s in _FLAG_POS:
        return "pos"
    if s in _FLAG_NEG:
        return "neg"
    try:
        return "pos" if float(s.replace(",", ".")) != 0 else "neg"
    except ValueError:
        return "ignoto"


# ===========================================================================
# 3. CALCOLO DEI 4 SEGNALI RFML (grezzi, una volta per donatore)
# ===========================================================================
def calcola_segnali(anagrafica, transazioni):
    """
    Ritorna dict donor_id -> {R, F, M_livello, M_trend, L} su scala 0-1.
    I segnali sono OBIETTIVO-AGNOSTICI: i pesi vengono applicati dopo.
    """
    # raggruppa transazioni per donatore
    tx_per_donor = defaultdict(list)
    for t in transazioni:
        tx_per_donor[t["donor_id"]].append(t)

    # data di riferimento = transazione piu recente nel dataset (oggi sintetico)
    tutte_date = [parse_data(t["data_donazione"]) for t in transazioni]
    tutte_date = [d for d in tutte_date if d]
    data_rif = max(tutte_date) if tutte_date else datetime.now()

    grezzi = {}  # donor_id -> dict di valori grezzi pre-normalizzazione
    for r in anagrafica:
        did = r["donor_id"]
        tx = sorted(
            (t for t in tx_per_donor.get(did, []) if parse_data(t["data_donazione"])),
            key=lambda t: parse_data(t["data_donazione"]),
        )
        if not tx:
            continue

        date = [parse_data(t["data_donazione"]) for t in tx]
        importi = [parse_importo(t["importo_eur"])[0] for t in tx]

        # --- R grezza: giorni dall'ultima donazione (piu basso = piu recente)
        recency_days = (data_rif - date[-1]).days

        # --- F grezza: regolarita degli intervalli tra donazioni
        # CV basso = donatore regolare. Inverso del CV -> "costanza".
        if len(date) >= 3:
            gap = [(date[i + 1] - date[i]).days for i in range(len(date) - 1)]
            gap = [g for g in gap if g > 0]
            if gap and mean(gap) > 0:
                cv = pstdev(gap) / mean(gap)
                costanza = 1.0 / (1.0 + cv)  # 0..1, alto = regolare
            else:
                costanza = 0.5
        else:
            costanza = 0.3  # pochi dati: costanza non valutabile, valore prudente

        # bonus metodo ricorrente: RID/bonifico ricorrente = commitment forte
        metodo = (r.get("metodo_pagamento_prevalente") or "").strip()
        bonus_ricorrente = 0.25 if metodo in METODI_RICORRENTI else 0.0
        f_grezza = min(1.0, costanza + bonus_ricorrente)

        # --- M livello: importo medio per donazione (capacita, NON totale)
        m_livello_grezzo = mean(importi) if importi else 0.0

        # --- M trend: pendenza importo nel tempo (regressione lineare semplice)
        # x = indice progressivo donazione, y = importo. slope normalizzata.
        if len(importi) >= 3:
            n = len(importi)
            xs = list(range(n))
            mx, my = mean(xs), mean(importi)
            num = sum((xs[i] - mx) * (importi[i] - my) for i in range(n))
            den = sum((xs[i] - mx) ** 2 for i in range(n))
            slope = num / den if den else 0.0
            # normalizza la pendenza sull'importo medio -> trend relativo
            m_trend_grezzo = slope / my if my else 0.0
        else:
            m_trend_grezzo = 0.0

        # --- L grezza: anzianita con saturazione
        anz = parse_float(r.get("anzianita_anni"))
        l_grezza = min(anz, L_SATURAZIONE_ANNI)

        grezzi[did] = {
            "recency_days": recency_days,
            "F": f_grezza,                  # gia 0..1
            "M_livello": m_livello_grezzo,
            "M_trend": m_trend_grezzo,
            "L": l_grezza,
            "n_tx": len(tx),
        }

    # --- normalizzazione min-max su R, M_livello, M_trend, L (F gia 0..1)
    def minmax(valori):
        vmin, vmax = min(valori), max(valori)
        rng = vmax - vmin
        return (lambda v: (v - vmin) / rng) if rng > 0 else (lambda v: 0.5)

    norm_rec = minmax([g["recency_days"] for g in grezzi.values()])
    norm_mliv = minmax([g["M_livello"] for g in grezzi.values()])
    norm_mtr = minmax([g["M_trend"] for g in grezzi.values()])
    norm_l = minmax([g["L"] for g in grezzi.values()])

    segnali = {}
    for did, g in grezzi.items():
        # R: invertita -> donato di recente = punteggio alto
        r_norm = 1.0 - norm_rec(g["recency_days"])
        segnali[did] = {
            "R": round(r_norm, 4),
            "F": round(g["F"], 4),
            "M_livello": round(norm_mliv(g["M_livello"]), 4),
            "M_trend": round(norm_mtr(g["M_trend"]), 4),
            "L": round(norm_l(g["L"]), 4),
            "_recency_days": g["recency_days"],
            "_n_tx": g["n_tx"],
        }
    return segnali


# ===========================================================================
# 4. PUNTEGGI DI PROPENSIONE (un punteggio per ogni obiettivo)
# ===========================================================================
def calcola_propensione(segnali, fascia_eta_per_donor, cap_per_donor=None):
    """
    Per ogni donatore e ogni obiettivo applica i pesi e produce 0-100.
    Aggiunge il BONUS_DEMOGRAFICO (dichiarato, isolato) agli obiettivi previsti.
    Applica il MOLTIPLICATORE TERRITORIALE ISTAT (dichiarato, isolato) se CAP
    disponibile — solo su lascito e one_off_emergenza.
    Lo score resta cappato a 100.
    """
    if cap_per_donor is None:
        cap_per_donor = {}

    risultati = {}
    for did, s in segnali.items():
        scores = {}
        cap = cap_per_donor.get(did, "")
        for obiettivo, pesi in PESI_OBIETTIVO.items():
            # per riattivazione: R entra come target (alta distanza = segnale)
            r_val = (1.0 - s["R"]) if obiettivo in OBIETTIVI_RECENCY_INVERSA else s["R"]
            val = (
                pesi["R"] * r_val
                + pesi["F"] * s["F"]
                + pesi["M_livello"] * s["M_livello"]
                + pesi["M_trend"] * s["M_trend"]
                + pesi["L"] * s["L"]
            ) * 100.0

            # bonus demografico: additivo, dichiarato
            bonus_tab = BONUS_DEMOGRAFICO.get(obiettivo, {})
            fascia = fascia_eta_per_donor.get(did, "")
            val += bonus_tab.get(fascia, 0.0)

            # moltiplicatore territoriale ISTAT: dichiarato, solo lascito/one_off
            molt = calcola_moltiplicatore_territoriale(cap, obiettivo)
            val = val * molt

            scores[obiettivo] = round(min(val, 100.0), 1)
        risultati[did] = scores
    return risultati


# ===========================================================================
# 5. MATRICE DI CONTESTO (tag qualitativi — NON entrano nei punteggi)
# ===========================================================================
def calcola_contesto(anagrafica):
    """
    Tag qualitativi che dicono COME comunicare, non QUANTO vale il donatore.
    Aggancio diretto allo Stage 3 message optimizer.
    """
    contesto = {}
    for r in anagrafica:
        did = r["donor_id"]
        canale = (r.get("canale_acquisizione") or "").strip()
        metodo = (r.get("metodo_pagamento_prevalente") or "").strip()

        # engagement digitale vs cartaceo (proxy da canale + metodo pagamento)
        digitale = canale == "web" or metodo in {"carta_credito", "RID_SEPA"}
        cartaceo = canale == "direct_mail" or metodo in {"bollettino", "assegno"}
        if digitale and not cartaceo:
            engagement = "digitale"
        elif cartaceo and not digitale:
            engagement = "cartaceo"
        else:
            engagement = "misto"

        # coinvolgimento attivo (volontariato / eventi) = relazione oltre il denaro
        vol = parse_float(r.get("volontariato")) > 0
        eventi = parse_float(r.get("eventi_partecipati")) > 0
        if vol:
            coinvolgimento = "volontario"
        elif eventi:
            coinvolgimento = "partecipa_eventi"
        else:
            coinvolgimento = "solo_donatore"

        contesto[did] = {
            "canale_acquisizione": canale,
            "tipo_sostegno": (r.get("tipo_sostegno_prevalente") or "").strip(),
            "engagement": engagement,
            "coinvolgimento": coinvolgimento,
        }
    return contesto


# ===========================================================================
# 6. VALIDAZIONE — lift per obiettivo sulla rispettiva colonna esito-campagna
# ===========================================================================
def valida_lift(anagrafica, propensione, obiettivo="lascito"):
    """
    Misura se il punteggio dell'obiettivo separa davvero chi ha risposto alla
    campagna corrispondente. Confronta il tasso di positivi nel top 20% per score
    vs il resto. Lift > 1 = il modello funziona.

    La colonna esito dipende dall'obiettivo (ESITO_COLONNE nel JSON condiviso),
    con un unico ripiego dichiarato (ESITO_FALLBACK: lascito -> lascito_dichiarato),
    esattamente come calcolaLift nel dashboard. Se la colonna dell'obiettivo non e
    presente nel CSV restituisce un esito 'colonna_assente' esplicito (non None,
    non eccezione): non e un errore ne un silenzio.
    """
    col = ESITO_COLONNE.get(obiettivo)
    col_fb = ESITO_FALLBACK.get(obiettivo)
    # Presenza = colonna nell'header del CSV (equivalente Python del mapState JS:
    # cosa l'utente ha effettivamente mappato). DictReader da a ogni riga le stesse
    # chiavi dell'intestazione, quindi basta ispezionare la prima riga.
    presenti = set(anagrafica[0].keys()) if anagrafica else set()
    ha_col = col is not None and col in presenti
    ha_fb = col_fb is not None and col_fb in presenti
    if not ha_col and not ha_fb:
        return {
            "colonna_assente": True,
            "colonna_attesa": col,
            "avviso": (f"obiettivo {obiettivo}: lift non calcolabile, "
                       f"colonna esito '{col}' assente nel CSV"),
        }
    usa_col = col if ha_col else col_fb

    coppie = []
    valori_ignoti = {}
    positivi_x = {}
    for r in anagrafica:
        did = r["donor_id"]
        if did not in propensione:
            continue
        raw = r.get(usa_col)
        stato = flag_positivo(raw)
        if stato == "ignoto":
            v = str(raw).strip()
            valori_ignoti[v] = valori_ignoti.get(v, 0) + 1
        elif stato == "pos_x":
            v = str(raw).strip()
            positivi_x[v] = positivi_x.get(v, 0) + 1
        coppie.append((propensione[did][obiettivo], 1 if stato in ("pos", "pos_x") else 0))

    if not coppie:
        return None
    # diagnostica qualita' colonna esito: sempre presente, da mostrare accanto al
    # lift. Valori ignoti = esclusi dai positivi, NON negativi (D5).
    diag = {
        "colonna": usa_col,
        "valori_ignoti": valori_ignoti,
        "n_ignoti": sum(valori_ignoti.values()),
        "positivi_x": positivi_x,
        "n_valutati": len(coppie),
    }
    positivi = sum(d for _, d in coppie)
    diag["positivi"] = positivi
    if positivi < SOGLIA_LIFT_POSITIVI:
        diag["sotto_soglia"] = True
        diag["avviso"] = (f"solo {positivi} positivi (soglia {SOGLIA_LIFT_POSITIVI}): "
                          f"lift non calcolato perche non significativo")
        return diag

    coppie.sort(key=lambda x: x[0], reverse=True)
    cut = max(1, len(coppie) // 5)
    top = coppie[:cut]
    resto = coppie[cut:]
    pos_top = sum(d for _, d in top)
    pos_resto = sum(d for _, d in resto)
    n_top = len(top)
    n_resto = len(resto)
    tasso_top = pos_top / n_top
    tasso_resto = pos_resto / n_resto if n_resto else 0.0
    # conteggi sempre esposti (anche nei casi degeneri), per rendere visibile su
    # quali gruppi si misura il lift.
    diag.update({
        "positivi_totali": positivi,
        "positivi_top": pos_top,
        "positivi_resto": pos_resto,
        "n_top": n_top,
        "n_resto": n_resto,
        "tasso_top20pct": round(tasso_top * 100, 1),
        "tasso_resto": round(tasso_resto * 100, 1),
    })
    # Denominatore zero: tutti i positivi nel top 20%, 0 nel resto -> nessun
    # termine di paragone, il lift NON e un numero (ne 'inf' ne null direbbero
    # cosa e successo). Quarto stato esplicito, come sotto_soglia/colonna_assente.
    if tasso_resto == 0:
        diag["denominatore_zero"] = True
        diag["avviso"] = (f"tutti i {positivi} positivi nel top 20% per score "
                          f"({pos_top}/{n_top}), 0 nel resto (0/{n_resto}): lift non "
                          f"definito, manca il termine di paragone. Su un segmento "
                          f"piccolo e quasi sempre un artefatto.")
        return diag
    diag["lift"] = round(tasso_top / tasso_resto, 2)
    return diag


def _stampa_validazione_lift(obiettivo, lift):
    """Stampa l'esito del lift per un obiettivo gestendo i quattro casi in modo
    esplicito (mai errore, mai silenzio): colonna assente / sotto soglia /
    denominatore zero / calcolato. Stessa semantica del render nel dashboard."""
    etichetta = obiettivo.replace("_", " ")
    if lift is None:
        print(f"  {etichetta:22s}: nessun donatore valutabile")
        return
    if lift.get("colonna_assente"):
        print(f"  {etichetta:22s}: lift non calcolabile — colonna esito "
              f"'{lift['colonna_attesa']}' assente nel CSV")
        return
    if lift.get("sotto_soglia"):
        print(f"  {etichetta:22s}: {lift['avviso']} "
              f"(colonna {lift.get('colonna')!r})")
        return
    if lift.get("denominatore_zero"):
        print(f"  {etichetta:22s}: lift non definito — tutti i "
              f"{lift['positivi_totali']} positivi nel top 20% "
              f"({lift['positivi_top']}/{lift['n_top']}), 0 nel resto "
              f"(0/{lift['n_resto']}). Manca il termine di paragone; su un "
              f"segmento piccolo e quasi sempre un artefatto.")
        return
    print(f"  {etichetta:22s}: LIFT {lift['lift']}x  "
          f"(top20%={lift['tasso_top20pct']}% vs resto={lift['tasso_resto']}%, "
          f"{lift['positivi_totali']} positivi, colonna {lift.get('colonna')!r})")
    if lift["positivi_totali"] < SOGLIA_ML_POSITIVI:
        print(f"      NOTA: {lift['positivi_totali']} positivi < soglia ML "
              f"({SOGLIA_ML_POSITIVI}). Modello rule-based confermato; "
              f"ML supervisionato sconsigliato (rischio overfitting).")
    # Qualita' colonna esito (D5), accanto al lift: ignoti esclusi (non negativi).
    if lift.get("n_ignoti"):
        n_val = lift.get("n_valutati", 0)
        print(f"      >>> ATTENZIONE: {lift['n_ignoti']} valori NON riconosciuti "
              f"nella colonna esito (su {n_val} donatori valutati), esclusi dai "
              f"positivi (non negativi). Lift su campione ridotto:")
        for v, n in sorted(lift["valori_ignoti"].items(), key=lambda x: -x[1]):
            print(f"          {n:4d}x  {v!r}")
    if lift.get("positivi_x"):
        nx = sum(lift["positivi_x"].values())
        print(f"      >>> {nx} positivi dichiarati con 'x' (spunta). Contati come "
              f"positivi, ma in certi export la X significa l'opposto:")
        for v, n in sorted(lift["positivi_x"].items(), key=lambda x: -x[1]):
            print(f"          {n:4d}x  {v!r}  -> verifica la convenzione con l'organizzazione")


# ===========================================================================
# 7. MAIN
# ===========================================================================
def main():
    ap = argparse.ArgumentParser(description="Modulo Propensione PoliSim — NGO")
    ap.add_argument("--anagrafica", required=True)
    ap.add_argument("--transazioni", required=True)
    ap.add_argument("--out", default="propensione_output.csv")
    ap.add_argument("--vertical", default="ngo",
                    help="ngo (default). sindacato/municipalita: non ancora calibrati.")
    args = ap.parse_args()

    if args.vertical != "ngo":
        print(f"[STOP] Verticale '{args.vertical}' non ancora calibrato. "
              f"Solo 'ngo' ha pesi validati su dati. Uscita.", file=sys.stderr)
        sys.exit(1)

    print("Lettura dati...")
    anagrafica = leggi_csv(args.anagrafica)
    transazioni = leggi_csv(args.transazioni)
    print(f"  anagrafica : {len(anagrafica)} donatori")
    print(f"  transazioni: {len(transazioni)} righe")

    print("Calcolo segnali RFML...")
    segnali = calcola_segnali(anagrafica, transazioni)
    print(f"  segnali calcolati per {len(segnali)} donatori")

    print("Calcolo propensione multi-obiettivo...")
    fascia_eta_per_donor = {
        r["donor_id"]: (r.get("fascia_eta") or "").strip() for r in anagrafica
    }
    # CAP: campo opzionale — se presente abilita il layer territoriale ISTAT
    cap_per_donor = {
        r["donor_id"]: (r.get("cap") or r.get("cap_donatore") or "").strip()
        for r in anagrafica
    }
    n_con_cap = sum(1 for v in cap_per_donor.values() if v)
    if n_con_cap > 0:
        print(f"  layer territoriale ISTAT: {n_con_cap}/{len(anagrafica)} donatori con CAP")
    else:
        print("  layer territoriale ISTAT: CAP non presente — moltiplicatore neutro")
    propensione = calcola_propensione(segnali, fascia_eta_per_donor, cap_per_donor)

    print("Calcolo matrice di contesto...")
    contesto = calcola_contesto(anagrafica)

    # --- scrittura output
    obiettivi = list(PESI_OBIETTIVO.keys())
    campi = (["donor_id"]
             + [f"prop_{o}" for o in obiettivi]
             + ["obiettivo_top", "score_top"]
             + ["R", "F", "M_livello", "M_trend", "L"]
             + ["regione_istat", "molt_lascito", "molt_upgrade",
                "molt_sostegno_continuativo", "molt_riattivazione", "molt_one_off"]
             + ["canale_acquisizione", "tipo_sostegno", "engagement", "coinvolgimento"])

    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=campi)
        w.writeheader()
        for did in segnali:
            prop = propensione[did]
            top_ob = max(prop, key=prop.get)
            row = {"donor_id": did}
            for o in obiettivi:
                row[f"prop_{o}"] = prop[o]
            row["obiettivo_top"] = top_ob
            row["score_top"] = prop[top_ob]
            for k in ("R", "F", "M_livello", "M_trend", "L"):
                row[k] = segnali[did][k]
            # layer territoriale: regione e moltiplicatori per trasparenza
            cap = cap_per_donor.get(did, "")
            regione = cap_to_regione(cap) or "n/d"
            row["regione_istat"] = regione
            row["molt_lascito"] = calcola_moltiplicatore_territoriale(cap, "lascito")
            row["molt_upgrade"] = calcola_moltiplicatore_territoriale(cap, "upgrade")
            row["molt_sostegno_continuativo"] = calcola_moltiplicatore_territoriale(cap, "sostegno_continuativo")
            row["molt_riattivazione"] = calcola_moltiplicatore_territoriale(cap, "riattivazione")
            row["molt_one_off"] = calcola_moltiplicatore_territoriale(cap, "one_off_emergenza")
            row.update(contesto.get(did, {}))
            w.writerow(row)
    print(f"  output scritto: {args.out}")

    # --- riepilogo console
    print("\n" + "=" * 67)
    print("RIEPILOGO PROPENSIONE — verticale NGO")
    print("=" * 67)
    for o in obiettivi:
        vals = sorted((propensione[d][o] for d in propensione), reverse=True)
        print(f"  {o:24s}  top score {vals[0]:5.1f}  "
              f"mediana {vals[len(vals)//2]:5.1f}  media {mean(vals):5.1f}")

    # distribuzione obiettivo_top
    print("\n  Donatori per obiettivo dominante:")
    conteggio = defaultdict(int)
    for d in propensione:
        top = max(propensione[d], key=propensione[d].get)
        conteggio[top] += 1
    for o in obiettivi:
        print(f"    {o:24s}  {conteggio[o]:4d}")

    # --- validazione lift per ogni obiettivo (ognuno sulla sua colonna esito)
    print("\n" + "-" * 67)
    print("VALIDAZIONE — i punteggi separano chi ha risposto alle campagne?")
    print("-" * 67)
    for ob in obiettivi:
        lift = valida_lift(anagrafica, propensione, ob)
        _stampa_validazione_lift(ob, lift)

    # --- diagnostica date non parsabili (D1/D2): mai scartare in silenzio
    date_anomale = conta_date_non_valide(transazioni)
    if date_anomale:
        tot = sum(date_anomale.values())
        print("\n" + "-" * 67)
        print(f"AVVISO — {tot} date non riconosciute e scartate "
              f"({len(date_anomale)} valori distinti)")
        print("-" * 67)
        print("  Formati accettati: YYYY-MM-DD, YYYY/MM/DD, DD/MM/YYYY.")
        for v, n in sorted(date_anomale.items(), key=lambda x: -x[1]):
            print(f"    {n:4d}x  {v!r}")

    # --- diagnostica importi non validi (D3/D4): mai imputare 0 in silenzio
    importi_anomali = conta_importi_non_validi(transazioni)
    if importi_anomali:
        tot = sum(n for n, _ in importi_anomali.values())
        print("\n" + "-" * 67)
        print(f"AVVISO — {tot} importi non interpretabili come dato pulito "
              f"({len(importi_anomali)} valori distinti)")
        print("-" * 67)
        for v, (n, stato) in sorted(importi_anomali.items(), key=lambda x: -x[1][0]):
            val, _ = parse_importo(v)
            reso = f"-> {val:g}" if stato == "coda" else "-> scartato (0)"
            print(f"    {n:4d}x  {v!r:14s} {stato:11s} {reso}")

    print("\n" + "=" * 67)
    print("PROMEMORIA ONESTA METODOLOGICA")
    print("=" * 67)
    print("  I pesi in PESI_OBIETTIVO e i valori in BONUS_DEMOGRAFICO sono")
    print("  EXPERT-CALIBRATED (Sargeant adattato + tasso lasciti per eta sui")
    print("  dati), NON validati su A/B test propri. Il lift sopra misura solo")
    print("  il verticale 'lascito' su dati sintetici. Gli altri 4 obiettivi non")
    print("  hanno colonna-esito nei dati: non sono validabili affatto, vanno")
    print("  trattati come ipotesi ragionate finche il feedback loop non gira.")


if __name__ == "__main__":
    main()
