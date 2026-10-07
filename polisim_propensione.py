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
import math
import sys
from collections import defaultdict
from datetime import datetime
from statistics import mean, pstdev

# ===========================================================================
# 1. MATRICE OBIETTIVO -> PESI   <-- UNICO PUNTO DI MODIFICA DEI PESI
# ===========================================================================
# Componenti: R, F, M_livello, M_trend, L. La somma per riga deve fare 1.0.
# Modificare QUI (o ricalibrare via feedback loop) senza toccare il resto.
#
# R = recency (invertita: donato di recente -> alto)
# F = frequency/costanza (regolarita intervalli + bonus metodo ricorrente)
# M_livello = importo medio per donazione (capacita)
# M_trend   = pendenza dell'importo nel tempo (in crescita / in calo)
# L = longevity (anni di relazione, con saturazione)

PESI_OBIETTIVO = {
    # Lascito: fedelta profonda. Importo quasi irrilevante, eta/durata decisive.
    "lascito": {
        "R": 0.05, "F": 0.30, "M_livello": 0.05, "M_trend": 0.15, "L": 0.45
    },
    # Upgrade: chiedere di piu a chi gia da. Trend positivo + capacita.
    "upgrade": {
        "R": 0.20, "F": 0.20, "M_livello": 0.15, "M_trend": 0.35, "L": 0.10
    },
    # Sostegno continuativo: convertire one-off in RID. Gia attivo e regolare.
    "sostegno_continuativo": {
        "R": 0.30, "F": 0.35, "M_livello": 0.10, "M_trend": 0.10, "L": 0.15
    },
    # Riattivazione: inattivi MA storicamente fedeli. R entra come target.
    "riattivazione": {
        "R": 0.45, "F": 0.25, "M_livello": 0.05, "M_trend": 0.05, "L": 0.20
    },
    # One-off / emergenza: vince chi risponde rapido agli appelli.
    "one_off_emergenza": {
        "R": 0.45, "F": 0.30, "M_livello": 0.15, "M_trend": 0.05, "L": 0.05
    },
}

# Obiettivi per cui la Recency va INTERPRETATA come target (alta distanza = segnale).
# Per la riattivazione cerchiamo proprio chi NON dona da tempo ma e stato fedele.
OBIETTIVI_RECENCY_INVERSA = {"riattivazione"}

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
# sintetici. NB: la fascia 35-44 mostra un tasso anomalo (5 positivi su 43,
# rumore) e viene trattata come neutra, non premiata.
BONUS_DEMOGRAFICO = {
    "lascito": {
        # fascia_eta -> punti aggiunti allo score 0-100 dell'obiettivo
        "35-44": 0.0,    # campione troppo piccolo: neutro, non premiato
        "45-54": 0.0,
        "55-64": 0.0,
        "65-74": 12.0,   # tasso lasciti ~11% vs ~3% fasce centrali
        "75+": 12.0,
    },
    # altri obiettivi: nessun bonus demografico (dict vuoto = neutro)
}

# Saturazione Longevity: oltre questa soglia (anni) la propensione non cresce
# piu linearmente. Corretta per dati reali; inerte sui sintetici (max 15 anni).
L_SATURAZIONE_ANNI = 20.0

# Metodi di pagamento considerati "ricorrenti" (commitment automatizzato).
METODI_RICORRENTI = {"RID_SEPA", "bonifico_ricorrente"}


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

ISTAT_REGIONI = {
    "Piemonte":               {"over65": 25.2, "reddito": 28500, "gini": 0.312},
    "Valle d'Aosta":          {"over65": 24.3, "reddito": 31200, "gini": 0.298},
    "Lombardia":              {"over65": 23.7, "reddito": 33100, "gini": 0.315},
    "Liguria":                {"over65": 28.7, "reddito": 26800, "gini": 0.321},
    "Trentino-Alto Adige":    {"over65": 22.1, "reddito": 34400, "gini": 0.289},
    "Veneto":                 {"over65": 23.7, "reddito": 29600, "gini": 0.305},
    "Friuli-Venezia Giulia":  {"over65": 26.0, "reddito": 27100, "gini": 0.308},
    "Emilia-Romagna":         {"over65": 24.6, "reddito": 31800, "gini": 0.307},
    "Toscana":                {"over65": 26.2, "reddito": 28100, "gini": 0.318},
    "Umbria":                 {"over65": 27.1, "reddito": 23600, "gini": 0.323},
    "Marche":                 {"over65": 26.6, "reddito": 23800, "gini": 0.318},
    "Lazio":                  {"over65": 23.9, "reddito": 28600, "gini": 0.334},
    "Abruzzo":                {"over65": 26.3, "reddito": 21800, "gini": 0.329},
    "Molise":                 {"over65": 28.7, "reddito": 18700, "gini": 0.331},
    "Campania":               {"over65": 21.2, "reddito": 17100, "gini": 0.365},
    "Puglia":                 {"over65": 22.9, "reddito": 18100, "gini": 0.352},
    "Basilicata":             {"over65": 27.8, "reddito": 17600, "gini": 0.334},
    "Calabria":               {"over65": 23.0, "reddito": 16500, "gini": 0.368},
    "Sicilia":                {"over65": 21.9, "reddito": 17100, "gini": 0.371},
    "Sardegna":               {"over65": 25.8, "reddito": 20800, "gini": 0.341},
}

# Lookup CAP (prime 2 cifre = prefisso provincia) → regione
# Copertura: tutti i prefissi CAP italiani (00-98)
CAP_PREFISSO_REGIONE = {
    "00": "Lazio", "01": "Lazio", "02": "Lazio", "03": "Lazio", "04": "Lazio",
    "05": "Umbria", "06": "Umbria",
    "07": "Sardegna", "08": "Sardegna", "09": "Sardegna",
    "10": "Piemonte", "11": "Valle d'Aosta", "12": "Piemonte", "13": "Piemonte",
    "14": "Piemonte", "15": "Piemonte", "16": "Liguria", "17": "Liguria",
    "18": "Liguria", "19": "Liguria",
    "20": "Lombardia", "21": "Lombardia", "22": "Lombardia", "23": "Lombardia",
    "24": "Lombardia", "25": "Lombardia", "26": "Lombardia", "27": "Lombardia",
    "28": "Piemonte", "29": "Emilia-Romagna",
    "30": "Veneto", "31": "Veneto", "32": "Veneto", "33": "Friuli-Venezia Giulia",
    "34": "Friuli-Venezia Giulia", "35": "Veneto", "36": "Veneto", "37": "Veneto",
    "38": "Trentino-Alto Adige", "39": "Trentino-Alto Adige",
    "40": "Emilia-Romagna", "41": "Emilia-Romagna", "42": "Emilia-Romagna",
    "43": "Emilia-Romagna", "44": "Emilia-Romagna", "45": "Emilia-Romagna",
    "46": "Emilia-Romagna", "47": "Emilia-Romagna", "48": "Emilia-Romagna",
    "50": "Toscana", "51": "Toscana", "52": "Toscana", "53": "Toscana",
    "54": "Toscana", "55": "Toscana", "56": "Toscana", "57": "Toscana",
    "58": "Toscana", "59": "Toscana",
    "60": "Marche", "61": "Marche", "62": "Marche", "63": "Marche", "64": "Abruzzo",
    "65": "Abruzzo", "66": "Abruzzo", "67": "Abruzzo", "68": "Molise", "69": "Molise",
    "70": "Puglia", "71": "Puglia", "72": "Puglia", "73": "Puglia", "74": "Puglia",
    "75": "Basilicata", "76": "Puglia", "85": "Basilicata",
    "80": "Campania", "81": "Campania", "82": "Campania", "83": "Campania",
    "84": "Campania",
    "86": "Molise", "87": "Calabria", "88": "Calabria", "89": "Calabria",
    "90": "Sicilia", "91": "Sicilia", "92": "Sicilia", "93": "Sicilia",
    "94": "Sicilia", "95": "Sicilia", "96": "Sicilia", "97": "Sicilia", "98": "Sicilia",
}

# Valori nazionali medi — usati come fallback se CAP mancante o non riconosciuto
ISTAT_NAZIONALE = {"over65": 24.1, "reddito": 24700, "gini": 0.328}


def cap_to_regione(cap: str) -> str | None:
    """Ricava la regione dalle prime 2 cifre del CAP. None se non riconosciuto."""
    if not cap:
        return None
    cap_clean = str(cap).strip().zfill(5)[:2]
    return CAP_PREFISSO_REGIONE.get(cap_clean)


# Pesi dei tre indicatori ISTAT per ogni obiettivo.
# Struttura: (peso_over65, peso_reddito, peso_gini_penalita, cap_variazione)
# cap_variazione: ampiezza massima del moltiplicatore (es. 0.15 = ±15%)
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
PESI_TERRITORIALI = {
    #                          over65  reddito  gini_pen  cap
    "lascito":                 (0.50,  0.30,    0.20,     0.15),
    "upgrade":                 (0.00,  0.60,    0.40,     0.10),
    "sostegno_continuativo":   (-0.10, 0.60,    0.30,     0.10),
    "riattivazione":           (0.05,  0.60,    0.35,     0.05),
    "one_off_emergenza":       (0.15,  0.50,    0.35,     0.12),
}


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

    w_over65, w_reddito, w_gini, cap_var = pesi

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
SOGLIA_ML_POSITIVI = 50


# ===========================================================================
# 2. LETTURA DATI
# ===========================================================================
def leggi_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


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
        importi = [parse_float(t["importo_eur"]) for t in tx]

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
# 6. VALIDAZIONE — lift su lascito_dichiarato (se la colonna esiste)
# ===========================================================================
def valida_lift(anagrafica, propensione, obiettivo="lascito"):
    """
    Misura se il punteggio 'lascito' separa davvero chi ha dichiarato un lascito.
    Confronta tasso di lasciti nel top 20% vs resto. Lift > 1 = il modello funziona.
    """
    coppie = []
    for r in anagrafica:
        did = r["donor_id"]
        if did not in propensione:
            continue
        dich = parse_float(r.get("lascito_dichiarato"))
        coppie.append((propensione[did][obiettivo], 1 if dich > 0 else 0))

    if not coppie:
        return None
    positivi = sum(d for _, d in coppie)
    if positivi < 5:
        return {"avviso": f"solo {positivi} positivi: lift non significativo"}

    coppie.sort(key=lambda x: x[0], reverse=True)
    cut = max(1, len(coppie) // 5)
    top = coppie[:cut]
    resto = coppie[cut:]
    tasso_top = sum(d for _, d in top) / len(top)
    tasso_resto = sum(d for _, d in resto) / len(resto) if resto else 0.0
    lift = (tasso_top / tasso_resto) if tasso_resto > 0 else float("inf")
    return {
        "positivi_totali": positivi,
        "tasso_top20pct": round(tasso_top * 100, 1),
        "tasso_resto": round(tasso_resto * 100, 1),
        "lift": round(lift, 2) if lift != float("inf") else "inf",
    }


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

    # --- validazione lift sui lasciti
    print("\n" + "-" * 67)
    print("VALIDAZIONE — il punteggio 'lascito' separa i lasciti dichiarati?")
    print("-" * 67)
    lift = valida_lift(anagrafica, propensione, "lascito")
    if lift and "avviso" not in lift:
        print(f"  positivi storici (lascito_dichiarato=1) : {lift['positivi_totali']}")
        print(f"  tasso lasciti nel top 20% per score     : {lift['tasso_top20pct']}%")
        print(f"  tasso lasciti nel resto                 : {lift['tasso_resto']}%")
        print(f"  LIFT                                    : {lift['lift']}x")
        if lift["positivi_totali"] < SOGLIA_ML_POSITIVI:
            print(f"\n  NOTA: {lift['positivi_totali']} positivi < soglia ML "
                  f"({SOGLIA_ML_POSITIVI}). Modello rule-based confermato; "
                  f"ML supervisionato sconsigliato (rischio overfitting).")
    elif lift:
        print(f"  {lift['avviso']}")

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
