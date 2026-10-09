#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_config.py - inietta rfml_config.json nel dashboard mantenendolo offline.

PERCHE' ESISTE
--------------
Il motore RFML ha due implementazioni (polisim_propensione.py e il <script> in
propensione_dashboard.html) che condividono le stesse tabelle e costanti. Per
non tenerne due copie a mano (ed evitare che ri-divergano, vedi bug D6 sul segno
del peso gini), la fonte di verita' UNICA e' rfml_config.json:

  - Python  : lo legge a runtime con json.load (ha sempre il filesystem).
  - Dashboard: la dashboard deve aprirsi OFFLINE via file:// senza server, dove
    fetch() e' bloccato (CORS null). Quindi il JSON va EMBEDDATO nell'HTML. Questo
    script lo inietta in un blocco <script type="application/json" id="rfml-config">
    delimitato da sentinelle; il JS lo legge con JSON.parse(... .textContent).

USO
---
    python build_config.py          # rigenera il blocco nell'HTML dal JSON
    python build_config.py --check  # NON scrive: esce 1 se HTML e JSON divergono

Il --check e' invocato da tests/propensione/equivalence_test.py: se qualcuno
edita il blocco a mano (o cambia il JSON senza rigenerare), il test di
equivalenza fallisce. La protezione non dipende dal ricordarsi di lanciare
questo comando a parte.

Le sentinelle RFML_CONFIG:START / RFML_CONFIG:END vanno inserite UNA volta a mano
nell'HTML nel punto desiderato (prima del <script> del motore); da li' in poi il
contenuto fra le due e' interamente generato.
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "rfml_config.json")
HTML = os.path.join(HERE, "propensione_dashboard.html")

START = "<!-- RFML_CONFIG:START (generato da build_config.py - NON editare a mano) -->"
END = "<!-- RFML_CONFIG:END -->"


def blocco_atteso_lf():
    """Blocco canonico, newline \\n. Deterministico: dipende solo dal JSON."""
    with open(CONFIG, encoding="utf-8") as f:
        cfg = json.load(f)
    js = json.dumps(cfg, ensure_ascii=False, indent=2)
    return "\n".join([
        START,
        '<script type="application/json" id="rfml-config">',
        js,
        "</script>",
        END,
    ])


def leggi_html_raw():
    """Legge l'HTML senza tradurre i newline (preserva CRLF)."""
    with open(HTML, encoding="utf-8", newline="") as f:
        return f.read()


def rileva_eol(testo):
    return "\r\n" if "\r\n" in testo else "\n"


def estrai_blocco_raw(html):
    i = html.find(START)
    if i == -1:
        return None
    j = html.find(END, i)
    if j == -1:
        return None
    return html[i:j + len(END)]


def main():
    check = "--check" in sys.argv[1:]
    atteso_lf = blocco_atteso_lf()
    html = leggi_html_raw()
    attuale_raw = estrai_blocco_raw(html)

    if check:
        if attuale_raw is None:
            sys.stderr.write(
                "[build_config --check] FAIL: blocco/sentinelle RFML_CONFIG "
                "assenti nell'HTML.\n")
            sys.exit(1)
        # Confronto EOL-insensibile: ci interessa il contenuto, non i fine-riga.
        if attuale_raw.replace("\r\n", "\n").strip() != atteso_lf.strip():
            sys.stderr.write(
                "[build_config --check] FAIL: il blocco nell'HTML NON e' "
                "sincronizzato con rfml_config.json.\n"
                "  Qualcuno ha editato l'HTML a mano o modificato il JSON senza "
                "rigenerare.\n"
                "  Rigenera con: python build_config.py\n")
            sys.exit(1)
        print("[build_config --check] OK: HTML sincronizzato con rfml_config.json.")
        sys.exit(0)

    # --- modalita scrittura ---
    if attuale_raw is None:
        sys.stderr.write(
            "[build_config] ERRORE: sentinelle RFML_CONFIG:START / "
            "RFML_CONFIG:END non trovate nell'HTML.\n"
            "  Inseriscile una volta a mano nel punto desiderato, poi rilancia.\n")
        sys.exit(2)

    eol = rileva_eol(html)
    atteso_raw = atteso_lf.replace("\n", eol)
    if attuale_raw == atteso_raw:
        print("[build_config] nessuna modifica (gia' sincronizzato).")
        sys.exit(0)
    nuovo = html.replace(attuale_raw, atteso_raw, 1)
    with open(HTML, "w", encoding="utf-8", newline="") as f:
        f.write(nuovo)
    print("[build_config] blocco RFML_CONFIG aggiornato nell'HTML "
          f"(EOL preservati: {'CRLF' if eol == chr(13)+chr(10) else 'LF'}).")
    sys.exit(0)


if __name__ == "__main__":
    main()
