#!/usr/bin/env python3
"""
PoliSim — Scraper giornaliero PolitPro Italia
==============================================
Fetcha le quote partiti da politpro.eu/it/italia
e salva in /opt/polisim/data/sondaggi_correnti.json

Cron: 0 8 * * * /opt/polisim/scraper_politpro.py >> /var/log/polisim_scraper.log 2>&1

Output JSON:
{
  "fonte": "PolitPro",
  "url": "https://politpro.eu/it/italia",
  "aggiornato": "2026-05-01T08:00:00",
  "partiti": {
    "FDI": 28.5,
    "PD": 22.2,
    "M5S": 12.5,
    "FI": 8.3,
    "LEGA": 7.0,
    "AVS": 6.5,
    "FN": 3.4,
    "AZ": 3.1,
    "IV": 2.4,
    "ALTRI": 3.4
  },
  "coalizioni": {
    "CDX": 47.2,
    "CSX": 28.7,
    "M5S": 12.5,
    "CENTRO": 5.5,
    "ALTRI": 6.8
  }
}
"""

import json
import re
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

OUTPUT_DIR  = Path('/opt/polisim/data')
OUTPUT_FILE = OUTPUT_DIR / 'sondaggi_correnti.json'
BACKUP_FILE = OUTPUT_DIR / 'sondaggi_backup.json'
URL         = 'https://politpro.eu/it/italia'

# Mappa nomi PolitPro → ID interni PoliSim
NOMI_MAP = {
    'FdI':    'FDI',
    'PD':     'PD',
    'M5S':    'M5S',
    'FI':     'FI',
    'Lega':   'LEGA',
    'AVS':    'AVS',
    'FN':     'FN',
    'A':      'AZ',
    'Azione': 'AZ',
    'IV':     'IV',
    '+E':     'PIU_E',
    'NM':     'NM',
}

# Coalizioni
CDX    = ['FDI', 'FI', 'LEGA', 'FN', 'NM']
CSX    = ['PD', 'AVS', 'PIU_E']
M5S    = ['M5S']
CENTRO = ['AZ', 'IV']


def fetch_html(url: str) -> str:
    req = urllib.request.Request(
        url,
        headers={
            'User-Agent': 'Mozilla/5.0 (compatible; PoliSim/1.0; +https://polisim.dev)',
            'Accept': 'text/html,application/xhtml+xml',
            'Accept-Language': 'it-IT,it;q=0.9',
        }
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.read().decode('utf-8', errors='replace')


def estrai_quote(html: str) -> dict:
    """
    Estrae le quote dal markup PolitPro.
    Pattern: <a href="/it/italia/partiti/..."><strong>FdI</strong>\n28,5</a>
    oppure: [**FdI**\n\n28,5]
    """
    partiti = {}

    # Pattern principale: bold + numero decimale vicini
    pattern = r'\*\*([A-Za-zÀ-ÿ+]+)\*\*\s*[\n\r]+\s*([\d]+[,.][\d]+)'
    matches = re.findall(pattern, html)

    for nome, quota_str in matches:
        nome = nome.strip()
        quota = float(quota_str.replace(',', '.'))
        if nome in NOMI_MAP and quota > 0:
            id_interno = NOMI_MAP[nome]
            # Prendi il valore più alto se lo stesso partito appare più volte
            if id_interno not in partiti or quota > partiti[id_interno]:
                partiti[id_interno] = quota

    # Fallback: pattern href + numero
    if len(partiti) < 5:
        pattern2 = r'href="/it/italia/partiti/[^"]+">.*?<strong>([^<]+)</strong>.*?([\d]+[,.][\d]+)'
        matches2 = re.findall(pattern2, html, re.DOTALL)
        for nome, quota_str in matches2:
            nome = nome.strip()
            quota = float(quota_str.replace(',', '.'))
            if nome in NOMI_MAP and quota > 0:
                id_interno = NOMI_MAP[nome]
                if id_interno not in partiti:
                    partiti[id_interno] = quota

    return partiti


def calcola_coalizioni(partiti: dict) -> dict:
    cdx   = sum(partiti.get(p, 0) for p in CDX)
    csx   = sum(partiti.get(p, 0) for p in CSX)
    m5s   = sum(partiti.get(p, 0) for p in M5S)
    centro = sum(partiti.get(p, 0) for p in CENTRO)
    noti  = cdx + csx + m5s + centro
    altri = max(0, round(100 - noti, 1))
    return {
        'CDX':    round(cdx, 1),
        'CSX':    round(csx, 1),
        'M5S':    round(m5s, 1),
        'CENTRO': round(centro, 1),
        'ALTRI':  altri,
    }


def main():
    ts = datetime.now().isoformat(timespec='seconds')
    print(f"[{ts}] Scraper PolitPro avviato")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Backup del file esistente
    if OUTPUT_FILE.exists():
        import shutil
        shutil.copy(OUTPUT_FILE, BACKUP_FILE)

    try:
        html = fetch_html(URL)
        partiti = estrai_quote(html)

        if len(partiti) < 4:
            raise ValueError(f"Troppo pochi partiti estratti: {partiti}")

        # Calcola ALTRI come residuo
        tot = sum(partiti.values())
        partiti['ALTRI'] = round(max(0, 100 - tot), 1)

        coalizioni = calcola_coalizioni(partiti)

        output = {
            'fonte':      'PolitPro',
            'url':        URL,
            'aggiornato': ts,
            'partiti':    {k: round(v, 1) for k, v in sorted(partiti.items())},
            'coalizioni': coalizioni,
            'tot_verificato': round(sum(partiti.values()), 1),
        }

        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            json.dump(output, f, ensure_ascii=False, indent=2)

        print(f"[{ts}] OK — {len(partiti)} partiti estratti")
        print(f"  Partiti: {partiti}")
        print(f"  Coalizioni: {coalizioni}")
        print(f"  Salvato: {OUTPUT_FILE}")

    except Exception as e:
        print(f"[{ts}] ERRORE: {e}")
        # Se fallisce, usa il backup senza toccare il file corrente
        if BACKUP_FILE.exists() and not OUTPUT_FILE.exists():
            import shutil
            shutil.copy(BACKUP_FILE, OUTPUT_FILE)
            print(f"[{ts}] Ripristinato backup")
        sys.exit(1)


if __name__ == '__main__':
    main()
