#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_config.py - genera da rfml_config.json le regioni sentinella nelle pagine.

PERCHE' ESISTE
--------------
Il motore RFML ha due implementazioni (polisim_propensione.py e il <script> in
propensione_dashboard.html) che condividono le stesse tabelle e costanti. Per
non tenerne due copie a mano (ed evitare che ri-divergano, come accadde
storicamente per il segno di un peso), la fonte di verita' UNICA e' rfml_config.json:

  - Python  : lo legge a runtime con json.load (ha sempre il filesystem).
  - Dashboard: la dashboard deve aprirsi OFFLINE via file:// senza server, dove
    fetch() e' bloccato (CORS null). Quindi il JSON va EMBEDDATO nell'HTML. Questo
    script lo inietta in un blocco <script type="application/json" id="rfml-config">
    delimitato da sentinelle; il JS lo legge con JSON.parse(... .textContent).
  - metodologia.html: pubblica i parametri dello strato territoriale (pesi,
    ampiezza, riferimenti nazionali, effetto risultante, guardia, copertura).
    Stesso principio: la tabella visibile e' GENERATA dal JSON, mai battuta a mano,
    cosi' la pagina non puo' divergere dalla configurazione senza che il --check
    se ne accorga.

USO
---
    python build_config.py          # rigenera tutte le regioni dal JSON
    python build_config.py --check  # NON scrive: esce 1 se una pagina diverge

Il --check e' invocato da tests/propensione/equivalence_test.py: se qualcuno
edita una regione a mano (o cambia il JSON senza rigenerare), il test di
equivalenza fallisce. La protezione non dipende dal ricordarsi di lanciare
questo comando a parte.

Le sentinelle START / END di ogni regione vanno inserite UNA volta a mano
nell'HTML nel punto desiderato; da li' in poi il contenuto fra le due e'
interamente generato.
"""

import html
import json
import os
import re
import sys
from decimal import Decimal, ROUND_HALF_UP

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "rfml_config.json")


def carica_config():
    with open(CONFIG, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Regione 1 - dashboard: il JSON integrale in un <script type="application/json">
# ---------------------------------------------------------------------------

def blocco_dashboard(cfg, start, end):
    js = json.dumps(cfg, ensure_ascii=False, indent=2)
    return "\n".join([
        start,
        '<script type="application/json" id="rfml-config">',
        js,
        "</script>",
        end,
    ])


# ---------------------------------------------------------------------------
# Regione 2 - metodologia.html: parametri dello strato territoriale
# ---------------------------------------------------------------------------
# Qui vivono solo ETICHETTE e REGOLE DI PRESENTAZIONE (arrotondamento, ordine).
# Ogni numero viene da rfml_config.json o e' calcolato da esso.

ETICHETTE_OBIETTIVO = {
    "lascito": "Lascito",
    "upgrade": "Upgrade",
    "sostegno_continuativo": "Sostegno continuativo",
    "riattivazione": "Riattivazione",
    "one_off_emergenza": "One-off emergenza",
}

# (chiave, etichetta, decimali per centro/dispersione, etichetta unita').
# Decimali negativi = arrotondamento alle decine (-1). L'unita' e' verificata
# contro RIFERIMENTI_NAZIONALI.fonte_dati.unita, non presa per buona.
INDICATORI = [
    ("quota_over65", "Quota over 65", 4, "frazione", "frazione 0-1"),
    ("reddito_medio", "Reddito medio", -1, "euro", "euro"),
    ("istruzione_terziaria_25plus", "Istruzione terziaria", 2,
     "punti percentuali", "punti percentuali"),
]

DECIMALI_PESI = 2
DECIMALI_MOLT = 3


def num_it(x, decimali):
    """Numero in formato italiano: virgola decimale, punto per le migliaia.
    Arrotondamento half-up sul valore decimale del float (non half-even)."""
    q = Decimal(1).scaleb(-decimali)
    d = Decimal(repr(float(x))).quantize(q, rounding=ROUND_HALF_UP)
    if decimali <= 0:
        intero, frac = f"{int(d):d}", ""
    else:
        intero, frac = f"{d:.{decimali}f}".split(".")
    segno = "-" if intero.startswith("-") else ""
    intero = intero.lstrip("-")
    gruppi = []
    while len(intero) > 3:
        gruppi.insert(0, intero[-3:])
        intero = intero[:-3]
    gruppi.insert(0, intero)
    s = segno + ".".join(gruppi)
    return s + ("," + frac if frac else "")


def moltiplicatore_grezzo(zona, pesi, rif, ampiezza, guardia):
    """Stessa formula di polisim_propensione.territorio(), SENZA l'arrotondamento
    a 4 decimali del motore (arrotondare due volte, 4 poi 3, sposterebbe
    0,83649 a 0,837). La coerenza col motore e' verificata in
    verifica_contro_motore()."""
    s = sum(float(pesi[i]) * (zona[i] - rif[i]["centro"]) / rif[i]["dispersione"]
            for i, *_ in INDICATORI)
    m = 1.0 + ampiezza * s
    attiva = m < guardia["min"] or m > guardia["max"]
    return max(guardia["min"], min(guardia["max"], m)), attiva


def verifica_contro_motore(cfg, zone_usabili):
    """Fallisce se la formula qui sopra diverge da quella del motore Python:
    l'effetto pubblicato deve essere quello che il motore calcola davvero."""
    sys.path.insert(0, HERE)
    import polisim_propensione as motore
    t, c = cfg["tabelle"], cfg["costanti"]
    for ob, pesi in t["PESI_TERRITORIALI"]["valori"].items():
        for pref, zona in zone_usabili.items():
            atteso = motore.territorio(pref + "000", ob)["molt"]
            m, _ = moltiplicatore_grezzo(
                zona, pesi, t["RIFERIMENTI_NAZIONALI"]["valori"],
                c["AMPIEZZA_TERRITORIALE"]["valore"],
                c["AMPIEZZA_TERRITORIALE"]["clamp_guardia"])
            if motore.round_js(m, 4) != atteso:
                raise SystemExit(
                    f"[build_config] ERRORE: moltiplicatore {ob}/zona {pref} "
                    f"= {m} qui, {atteso} nel motore. Formula divergente.")


def copertura_dichiarata(cfg):
    """Comuni e residenti coperti, dal campo numerico
    RIFERIMENTI_NAZIONALI.fonte_dati.copertura. Il testo di 'origine' li ripete
    in prosa: se li cita, devono coincidere, altrimenti si fallisce invece di
    lasciare due versioni dello stesso numero."""
    fd = cfg["tabelle"]["RIFERIMENTI_NAZIONALI"]["fonte_dati"]
    cop = fd.get("copertura")
    if not cop or not all(isinstance(cop.get(k), int) for k in ("comuni", "residenti")):
        raise SystemExit(
            "[build_config] ERRORE: RIFERIMENTI_NAZIONALI.fonte_dati.copertura "
            "deve avere 'comuni' e 'residenti' interi.")
    m = re.search(r"Copertura:\s*(\d+)\s+comuni,\s*(\d+)\s+residenti", fd.get("origine", ""))
    if m and (int(m.group(1)), int(m.group(2))) != (cop["comuni"], cop["residenti"]):
        raise SystemExit(
            "[build_config] ERRORE: copertura in fonte_dati.origine "
            f"({m.group(1)} comuni, {m.group(2)} residenti) diversa dal campo "
            f"copertura ({cop['comuni']}, {cop['residenti']}).")
    return cop["comuni"], cop["residenti"]


def blocco_metodologia(cfg, start, end):
    t, c = cfg["tabelle"], cfg["costanti"]
    pesi_t = t["PESI_TERRITORIALI"]["valori"]
    rif = t["RIFERIMENTI_NAZIONALI"]["valori"]
    unita_cfg = t["RIFERIMENTI_NAZIONALI"]["fonte_dati"]["unita"]
    zone = t["ZONE_POSTALI"]["valori"]
    amp = c["AMPIEZZA_TERRITORIALE"]["valore"]
    guardia = c["AMPIEZZA_TERRITORIALE"]["clamp_guardia"]

    if set(pesi_t) != set(ETICHETTE_OBIETTIVO):
        raise SystemExit("[build_config] ERRORE: obiettivi di PESI_TERRITORIALI "
                         "diversi dalle etichette note in build_config.py.")
    for chiave, _, _, _, unita_attesa in INDICATORI:
        if unita_cfg.get(chiave) != unita_attesa:
            raise SystemExit(f"[build_config] ERRORE: unita' di {chiave} nel JSON "
                             f"({unita_cfg.get(chiave)!r}) diversa da quella attesa.")

    usabili = {k: v for k, v in zone.items() if not v.get("escluso")}
    escluse = {k: v for k, v in zone.items() if v.get("escluso")}
    verifica_contro_motore(cfg, usabili)
    n_comuni, n_residenti = copertura_dichiarata(cfg)
    n_us = len(usabili)

    L = [start]
    L.append("<p>Per ciascun indicatore si calcola lo scarto dal valore nazionale, "
             "diviso per la dispersione nazionale. Gli scarti si combinano con i pesi "
             "dell'obiettivo, e il risultato, moltiplicato per l'ampiezza territoriale, "
             "corregge il punteggio:</p>")
    L.append("<p><em>moltiplicatore</em> = 1 + ampiezza × Σ peso<sub>i</sub> × "
             "(valore di zona<sub>i</sub> − valore nazionale<sub>i</sub>) / "
             "dispersione<sub>i</sub></p>")

    # Pesi
    L.append("<table>")
    L.append("  <thead>")
    L.append("    <tr><th>Obiettivo</th>"
             + "".join(f"<th>{et}</th>" for _, et, *_ in INDICATORI) + "</tr>")
    L.append("  </thead>")
    L.append("  <tbody>")
    for ob, et in ETICHETTE_OBIETTIVO.items():
        celle = "".join(f"<td>{num_it(pesi_t[ob][k], DECIMALI_PESI)}</td>"
                        for k, *_ in INDICATORI)
        L.append(f"    <tr><td>{et}</td>{celle}</tr>")
    L.append("  </tbody>")
    L.append("</table>")
    L.append('<p class="table-note">Pesi dichiarati, calibrati sull\'esperienza di '
             "dominio, non stimati su esiti reali.</p>")

    L.append(f"<p><strong>Ampiezza territoriale: {num_it(amp, 2)}.</strong> "
             "Indica quanto lo strato territoriale può spostare un punteggio. "
             "Anch'essa dichiarata, non stimata.</p>")

    # Riferimenti nazionali
    L.append("<p>I riferimenti nazionali, senza i quali i pesi da soli non permettono "
             f"di riprodurre un moltiplicatore, sono calcolati sulle {n_us} zone "
             "usabili:</p>")
    L.append("<table>")
    L.append("  <thead>")
    L.append("    <tr><th>Indicatore</th><th>Valore nazionale</th><th>Dispersione</th>"
             "<th>Unità</th></tr>")
    L.append("  </thead>")
    L.append("  <tbody>")
    for k, et, dec, unita, _ in INDICATORI:
        L.append(f"    <tr><td>{et}</td><td>{num_it(rif[k]['centro'], dec)}</td>"
                 f"<td>{num_it(rif[k]['dispersione'], dec)}</td><td>{unita}</td></tr>")
    L.append("  </tbody>")
    L.append("</table>")
    L.append('<p class="table-note">Valori arrotondati; i valori esatti sono in '
             '<code>rfml_config.json</code> nel '
             '<a href="https://github.com/AlCap27/polisim">repository pubblico</a>.</p>')

    # Effetto risultante: min/max per obiettivo; obiettivi con pesi tutti zero
    # (moltiplicatore 1 esatto per costruzione) raggruppati in una riga.
    righe, neutri, guardia_attiva = [], [], False
    for ob, et in ETICHETTE_OBIETTIVO.items():
        if all(float(pesi_t[ob][k]) == 0.0 for k, *_ in INDICATORI):
            neutri.append(et)
            continue
        valori = []
        for zona in usabili.values():
            m, attiva = moltiplicatore_grezzo(zona, pesi_t[ob], rif, amp, guardia)
            valori.append(m)
            guardia_attiva = guardia_attiva or attiva
        righe.append(f"    <tr><td>{et}</td><td>{num_it(min(valori), DECIMALI_MOLT)} – "
                     f"{num_it(max(valori), DECIMALI_MOLT)}</td></tr>")
    if neutri:
        etichetta = ", ".join([neutri[0]] + [n[0].lower() + n[1:] for n in neutri[1:]])
        righe.append(f"    <tr><td>{etichetta}</td>"
                     f"<td>{num_it(1, DECIMALI_MOLT)} esatto</td></tr>")
    L.append("<p>L'effetto risultante:</p>")
    L.append("<table>")
    L.append("  <thead>")
    L.append(f"    <tr><th>Obiettivo</th><th>Moltiplicatore, minimo e massimo sulle "
             f"{n_us} zone</th></tr>")
    L.append("  </thead>")
    L.append("  <tbody>")
    L.extend(righe)
    L.append("  </tbody>")
    L.append("</table>")

    attivazione = ("non si attiva su nessuna delle " if not guardia_attiva
                   else "si attiva su almeno una delle ")
    L.append(f"<p><strong>Guardia.</strong> Il moltiplicatore è limitato "
             f"all'intervallo {num_it(guardia['min'], 1)}–{num_it(guardia['max'], 1)}. "
             "È una protezione contro aggiornamenti futuri dei dati, non un parametro "
             f"del modello, e {attivazione}{n_us} zone.</p>")

    motivi = "<br>".join(f"Zona {k}: {html.escape(v['motivo'], quote=False)}"
                         for k, v in sorted(escluse.items()))
    n_esc = len(escluse)
    L.append(f"<p><strong>Copertura.</strong> {n_us} zone usabili su {len(zone)}, "
             f"{num_it(n_comuni, 0)} comuni, {num_it(n_residenti, 0)} residenti. "
             f"{'Una zona esclusa' if n_esc == 1 else f'{n_esc} zone escluse'} con "
             "motivo dichiarato.</p>")
    L.append(f'<p class="table-note">{motivi}</p>')
    L.append(end)
    return "\n".join(L)


# ---------------------------------------------------------------------------
# Regioni gestite
# ---------------------------------------------------------------------------

TARGETS = [
    {
        "html": os.path.join(HERE, "propensione_dashboard.html"),
        "start": "<!-- RFML_CONFIG:START (generato da build_config.py - NON editare a mano) -->",
        "end": "<!-- RFML_CONFIG:END -->",
        "genera": blocco_dashboard,
    },
    {
        "html": os.path.join(HERE, "metodologia.html"),
        "start": "<!-- RFML_TERRITORIALE:START (generato da build_config.py da "
                 "rfml_config.json - NON editare a mano) -->",
        "end": "<!-- RFML_TERRITORIALE:END -->",
        "genera": blocco_metodologia,
    },
]


def leggi_html_raw(path):
    """Legge l'HTML senza tradurre i newline (preserva CRLF)."""
    with open(path, encoding="utf-8", newline="") as f:
        return f.read()


def rileva_eol(testo):
    return "\r\n" if "\r\n" in testo else "\n"


def estrai_blocco_raw(html, start, end):
    i = html.find(start)
    if i == -1:
        return None
    j = html.find(end, i)
    if j == -1:
        return None
    return html[i:j + len(end)]


def main():
    check = "--check" in sys.argv[1:]
    cfg = carica_config()
    esito = 0

    for tg in TARGETS:
        nome = os.path.basename(tg["html"])
        atteso_lf = tg["genera"](cfg, tg["start"], tg["end"])
        testo = leggi_html_raw(tg["html"])
        attuale_raw = estrai_blocco_raw(testo, tg["start"], tg["end"])

        if check:
            if attuale_raw is None:
                sys.stderr.write(
                    f"[build_config --check] FAIL: sentinelle assenti in {nome}.\n")
                esito = 1
                continue
            # Confronto EOL-insensibile: ci interessa il contenuto, non i fine-riga.
            if attuale_raw.replace("\r\n", "\n").strip() != atteso_lf.strip():
                sys.stderr.write(
                    f"[build_config --check] FAIL: la regione generata in {nome} NON "
                    "e' sincronizzata con rfml_config.json.\n"
                    "  Qualcuno ha editato l'HTML a mano o modificato il JSON senza "
                    "rigenerare.\n"
                    "  Rigenera con: python build_config.py\n")
                esito = 1
                continue
            print(f"[build_config --check] OK: {nome} sincronizzato con rfml_config.json.")
            continue

        # --- modalita scrittura ---
        if attuale_raw is None:
            sys.stderr.write(
                f"[build_config] ERRORE: sentinelle non trovate in {nome}.\n"
                "  Inseriscile una volta a mano nel punto desiderato, poi rilancia.\n")
            esito = 2
            continue
        eol = rileva_eol(testo)
        atteso_raw = atteso_lf.replace("\n", eol)
        if attuale_raw == atteso_raw:
            print(f"[build_config] {nome}: nessuna modifica (gia' sincronizzato).")
            continue
        nuovo = testo.replace(attuale_raw, atteso_raw, 1)
        with open(tg["html"], "w", encoding="utf-8", newline="") as f:
            f.write(nuovo)
        print(f"[build_config] {nome}: regione aggiornata "
              f"(EOL preservati: {'CRLF' if eol == chr(13)+chr(10) else 'LF'}).")

    sys.exit(esito)


if __name__ == "__main__":
    main()
