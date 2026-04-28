"""polisim_istat.py - validazione swing model multi-regionale + celle demografiche.

Estensione di ``polisim_build_collegi.py``. Scarica i dati Eligendo OpenData
delle regionali 2023-2024, aggrega per collegio uninominale Camera 2022 e
calcola RMSE/R^2 confrontandoli con la PoC Lazio (RMSE 3.91pp, R^2 0.618, N=11).

Aggiunge inoltre ``build_celle_demografiche()`` che costruisce la matrice
18 celle/collegio (3 fasce eta x 3 livelli istruzione x 2 generi) usando il
Censimento Permanente ISTAT 2021.

Repo: https://github.com/AlCap27/polisim
Cwd : C:\\Users\\work\\Dropbox\\Public\\Q-Italia\\

Esecuzione:
    python polisim_istat.py --out ./out --cache ./_cache
    python polisim_istat.py --regions LOMBARDIA UMBRIA
    python polisim_istat.py --build-celle
"""
from __future__ import annotations

import argparse
import io
import logging
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd
import requests

# ---------------------------------------------------------------- costanti --
ELIGENDO_BASE = "https://elezionistorico.interno.gov.it/daithome/documenti/opendata"
REGIONALI_URL = f"{ELIGENDO_BASE}/regionali/regionali-{{date}}.zip"
COLLEGI_COMUNI_URL = f"{ELIGENDO_BASE}/catalogoagid/elenco-collegi-comuni-camera.csv"
CAMERA_2022_URL = f"{ELIGENDO_BASE}/catalogoagid/camera-2022-Italia-livcomune.csv"

POC_LAZIO_BASELINE = {"region": "LAZIO", "n_collegi": 11, "rmse_pp": 3.91, "r2": 0.618}

REGIONI_TARGET: Dict[str, Dict[str, str]] = {
    "LOMBARDIA":      {"data": "20230212", "anno": "2023"},
    "SARDEGNA":       {"data": "20240225", "anno": "2024"},  # ZIP non in Eligendo (404)
    "BASILICATA":     {"data": "20240421", "anno": "2024"},
    "UMBRIA":         {"data": "20241117", "anno": "2024"},
    "LIGURIA":        {"data": "20241027", "anno": "2024"},
    "EMILIA-ROMAGNA": {"data": "20241117", "anno": "2024"},
}

# Mapping coalizione: keyword case-insensitive, prima regola che matcha.
COALITION_RULES: List[Tuple[str, str]] = [
    ("FRATELLI D'ITALIA", "CDX"), ("LEGA", "CDX"), ("FORZA ITALIA", "CDX"),
    ("NOI MODERATI", "CDX"), ("UDC", "CDX"), ("RINASCIMENTO SGARBI", "CDX"),
    ("LOMBARDIA IDEALE", "CDX"), ("LISTA TOTI", "CDX"), ("VANNACCI", "CDX"),
    ("BUCCI PRESIDENTE", "CDX"), ("ORGOGLIO LIGURIA", "CDX"),
    ("MARSILIO PRESIDENTE", "CDX"), ("OCCHIUTO PRESIDENTE", "CDX"),
    ("BARDI PRESIDENTE", "CDX"), ("UGOLINI PRESIDENTE", "CDX"),
    ("PARTITO DEMOCRATICO", "CSX"), ("PART.DEMOCR", "CSX"),
    ("ALLEANZA VERDI E SINISTRA", "CSX"), ("AVS", "CSX"),
    ("+EUROPA", "CSX"), ("PIU EUROPA", "CSX"),
    ("PATTO CIVICO", "CSX"), ("CIVICI PER", "CSX"),
    ("ORLANDO PRESIDENTE", "CSX"), ("DE PASCALE PRESIDENTE", "CSX"),
    ("PROIETTI PRESIDENTE", "CSX"), ("STEFANIA PROIETTI", "CSX"),
    ("UMBRIA DOMANI", "CSX"), ("MAJORINO PRESIDENTE", "CSX"),
    ("MOVIMENTO 5 STELLE", "M5S"), ("MOVIMENTO CINQUE STELLE", "M5S"),
    ("M5S", "M5S"), ("CINQUE STELLE", "M5S"),
    ("AZIONE", "TZP"), ("ITALIA VIVA", "TZP"),
    ("CALENDA", "TZP"), ("MORATTI", "TZP"),
    # Liste civiche regionali frequentemente associate al CSX
    ("BASILICATA CASA COMUNE", "CSX"), ("BASILICATA UNITA", "CSX"),
    # AVS varianti (Basilicata 2024 omette la "E")
    ("ALLEANZA VERDI SINISTRA", "CSX"), ("VERDI E SINISTRA", "CSX"),
    ("VOLT", "CSX"),
]

CORE_COALITIONS = ["CDX", "CSX", "M5S", "TZP"]


def map_coalition(lista: str) -> str:
    if not isinstance(lista, str):
        return "ALTRI"
    norm = lista.upper().strip().strip('"')
    for kw, coal in COALITION_RULES:
        if kw in norm:
            return coal
    return "ALTRI"


# ---------------------------------------------------------------- logging --
logging.basicConfig(
    format="[%(asctime)s] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
    level=logging.INFO,
    stream=sys.stdout,
)
log = logging.getLogger("polisim_istat")


# ---------------------------------------------------------------- cache ----
@dataclass
class CacheConfig:
    cache_dir: Path
    enabled: bool = True

    def path_for(self, name: str) -> Path:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        return self.cache_dir / name


def fetch(url: str, cache: CacheConfig, fname: Optional[str] = None) -> bytes:
    fname = fname or url.rsplit("/", 1)[-1]
    p = cache.path_for(fname)
    if cache.enabled and p.exists() and p.stat().st_size > 0:
        log.info("Cache hit: %s (%d KB)", fname, p.stat().st_size // 1024)
        return p.read_bytes()
    log.info("Download: %s", url)
    r = requests.get(url, timeout=120, allow_redirects=True)
    r.raise_for_status()
    if cache.enabled:
        p.write_bytes(r.content)
    return r.content


# ---------------------------------------------------------------- parsers --
def _read_csv_smart(blob: bytes) -> pd.DataFrame:
    last_err: Optional[Exception] = None
    for enc in ("utf-8", "latin-1", "cp1252"):
        try:
            return pd.read_csv(io.BytesIO(blob), sep=";", quotechar='"',
                               dtype=str, encoding=enc, engine="python")
        except (UnicodeDecodeError, pd.errors.ParserError) as e:
            last_err = e
    assert last_err is not None
    raise last_err


def _norm_str_cols(df: pd.DataFrame, cols: Iterable[str]) -> pd.DataFrame:
    df = df.copy()
    df.columns = [c.strip().strip('"') for c in df.columns]
    for c in cols:
        if c in df.columns:
            df[c] = df[c].astype(str).str.strip().str.strip('"').str.upper()
    return df


def parse_regionali_zip(blob: bytes, region_filter: str) -> pd.DataFrame:
    """Long-form (REGIONE, PROVINCIA, COMUNE, DESCRLISTA, VOTILISTA, COALIZIONE).

    Schemi gestiti:
      A. 2023 ``regionali-YYYYMMDD.csv`` (Lombardia, Lazio).
      B. 2024 ``Liste&Candidati_DDMMYYYY.txt`` (Basilicata).
      C. 2024 ``Regionali_<Reg>_2024_Scrutini.csv`` sezione-livello
         (Liguria, Umbria, Emilia-Romagna).
    """
    region_u = region_filter.upper()
    region_key = region_u.replace("-", "").replace(" ", "").replace("_", "")
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        names = zf.namelist()

        # Schema A
        a = [n for n in names if n.lower().startswith("regionali-")
             and n.lower().endswith(".csv")]
        if a:
            with zf.open(a[0]) as fh:
                df = _read_csv_smart(fh.read())
            df = _norm_str_cols(df, ["REGIONE", "PROVINCIA", "COMUNE", "DESCRLISTA"])
            df = df[df["REGIONE"] == region_u]
            if df.empty:
                raise ValueError(f"Regione '{region_u}' assente nel CSV.")
            df["VOTILISTA"] = pd.to_numeric(df["VOTILISTA"], errors="coerce")
            df = df.dropna(subset=["VOTILISTA", "DESCRLISTA"])
            out = (df.groupby(["REGIONE", "PROVINCIA", "COMUNE", "DESCRLISTA"],
                              as_index=False)
                     .agg(VOTILISTA=("VOTILISTA", "max")))
            out["COALIZIONE"] = out["DESCRLISTA"].map(map_coalition)
            return out

        # Schema B
        b = [n for n in names if "liste" in n.lower() and "candidat" in n.lower()]
        if b:
            with zf.open(b[0]) as fh:
                df = _read_csv_smart(fh.read())
            df = _norm_str_cols(df, ["REGIONE", "PROVINCIA", "COMUNE", "DESCRLISTA"])
            df = df[df["REGIONE"] == region_u]
            if df.empty:
                raise ValueError(f"Regione '{region_u}' assente nel TXT.")
            df["VOTILISTA"] = pd.to_numeric(df["VOTILISTA"], errors="coerce")
            df = df.dropna(subset=["VOTILISTA", "DESCRLISTA"])
            out = (df.groupby(["REGIONE", "PROVINCIA", "COMUNE", "DESCRLISTA"],
                              as_index=False)
                     .agg(VOTILISTA=("VOTILISTA", "max")))
            out["COALIZIONE"] = out["DESCRLISTA"].map(map_coalition)
            return out

        # Schema C
        scrut = [n for n in names if "scrutin" in n.lower() and n.lower().endswith(".csv")]
        if scrut:
            chosen = next(
                (n for n in scrut
                 if region_key in n.upper().replace("-", "").replace(" ", "").replace("_", "")),
                scrut[0],
            )
            with zf.open(chosen) as fh:
                df = _read_csv_smart(fh.read())
            df = df.rename(columns={"LISTA": "DESCRLISTA", "VOTI_LISTA": "VOTILISTA"})
            df = _norm_str_cols(df, ["REGIONE", "COMUNE", "DESCRLISTA"])
            df = df[df["REGIONE"] == region_u]
            if df.empty:
                raise ValueError(f"Regione '{region_u}' assente nello scrutini CSV.")
            df["VOTILISTA"] = pd.to_numeric(df["VOTILISTA"], errors="coerce")
            df = df.dropna(subset=["VOTILISTA", "DESCRLISTA"])
            sez = (df.groupby(["REGIONE", "COMUNE", "SEZIONE", "DESCRLISTA"],
                              as_index=False).agg(VOTILISTA=("VOTILISTA", "max")))
            out = (sez.groupby(["REGIONE", "COMUNE", "DESCRLISTA"], as_index=False)
                      .agg(VOTILISTA=("VOTILISTA", "sum")))
            out["PROVINCIA"] = ""
            out["COALIZIONE"] = out["DESCRLISTA"].map(map_coalition)
            return out[["REGIONE", "PROVINCIA", "COMUNE", "DESCRLISTA",
                        "VOTILISTA", "COALIZIONE"]]

        raise FileNotFoundError(f"ZIP senza file riconoscibili: {names}")


def parse_camera_2022(blob: bytes) -> pd.DataFrame:
    df = _read_csv_smart(blob)
    df = _norm_str_cols(df, ["CIRC-REG", "COLLUNINOM", "COMUNE", "DESCRLISTA"])
    df["VOTILISTA"] = pd.to_numeric(df["VOTILISTA"], errors="coerce")
    df = df.dropna(subset=["VOTILISTA", "DESCRLISTA"])
    base = (df.groupby(["CIRC-REG", "COLLUNINOM", "COMUNE", "DESCRLISTA"],
                       as_index=False).agg(VOTILISTA=("VOTILISTA", "max")))
    base["COALIZIONE"] = base["DESCRLISTA"].map(map_coalition)
    return base


def parse_collegi_map(blob: bytes) -> pd.DataFrame:
    df = pd.read_csv(io.BytesIO(blob), sep=";", dtype=str, encoding="utf-8")
    df.columns = [c.strip() for c in df.columns]
    df["COMUNE"] = df["COMUNE"].str.strip().str.upper()
    df["COLLEGIO UNINOMINALE"] = df["COLLEGIO UNINOMINALE"].str.strip()
    rename = {
        "COLLEGIO UNINOMINALE": "COLLUNINOM",
        "SIGLA PROVINCIA": "PROV_SIGLA",
        "CIRCOSCRIZIONE": "CIRC",
    }
    df = df.rename(columns=rename)
    keep = ["COMUNE", "PROV_SIGLA", "COLLUNINOM", "CIRC"]
    if "CODICE ISTAT" in df.columns:
        keep.append("CODICE ISTAT")
    return df[keep]


# ---------------------------------------------------------------- modello --
def shares_by_collegio(long_df: pd.DataFrame, mapper: pd.DataFrame,
                       region: Optional[str] = None) -> pd.DataFrame:
    """Aggrega voti regionali per collegio uninominale Camera.

    Per i comuni che cadono in piu' collegi (es. Roma, Milano) il voto
    comunale viene allocato in modo uniforme su ciascun collegio.

    Se ``region`` e' fornita, il mapper viene filtrato preliminarmente sulla
    circoscrizione regionale per scartare collegi omonimi cross-regione.
    """
    if region:
        # Trentino-Alto Adige nel CSV elenco-collegi e' "TRENTINO-ALTO ADIGE/SUDTIROL"
        # mentre la regione e' "TRENTINO-ALTO ADIGE": uso match parziale.
        m_region = mapper[mapper["CIRC"].str.upper().str.contains(
            region.upper().split("-")[0], na=False)]
        if not m_region.empty:
            mapper = m_region
    df = long_df.merge(mapper[["COMUNE", "COLLUNINOM"]], on="COMUNE", how="inner")
    split = df.groupby("COMUNE")["COLLUNINOM"].nunique().rename("n_split").reset_index()
    df = df.merge(split, on="COMUNE", how="left")
    df["VOTI_ALLOC"] = df["VOTILISTA"] / df["n_split"]
    agg = (df.groupby(["COLLUNINOM", "COALIZIONE"], as_index=False)
             .agg(VOTI=("VOTI_ALLOC", "sum")))
    tot = agg.groupby("COLLUNINOM")["VOTI"].sum().rename("TOT").reset_index()
    agg = agg.merge(tot, on="COLLUNINOM")
    agg["SHARE"] = 100.0 * agg["VOTI"] / agg["TOT"]
    return agg


def shares_camera_2022(base: pd.DataFrame, region: str) -> pd.DataFrame:
    df = base[base["CIRC-REG"].str.contains(region.upper(), na=False)].copy()
    agg = (df.groupby(["COLLUNINOM", "COALIZIONE"], as_index=False)
             .agg(VOTI=("VOTILISTA", "sum")))
    tot = agg.groupby("COLLUNINOM")["VOTI"].sum().rename("TOT").reset_index()
    agg = agg.merge(tot, on="COLLUNINOM")
    agg["SHARE"] = 100.0 * agg["VOTI"] / agg["TOT"]
    return agg


def apply_uniform_swing(predicted: pd.DataFrame, baseline: pd.DataFrame) -> pd.DataFrame:
    """Uniform National Swing.

    Per ogni coalizione c:
        swing_c    = mean(predicted_share_c) - mean(baseline_share_c)
        forecast_c = baseline_share_c + swing_c
    """
    pred_avg = predicted.groupby("COALIZIONE")["SHARE"].mean().rename("AVG_REG").reset_index()
    base_avg = baseline.groupby("COALIZIONE")["SHARE"].mean().rename("AVG_CAM22").reset_index()
    swing = pred_avg.merge(base_avg, on="COALIZIONE", how="outer").fillna(0.0)
    swing["SWING_PP"] = swing["AVG_REG"] - swing["AVG_CAM22"]
    forecast = baseline.merge(swing[["COALIZIONE", "SWING_PP"]], on="COALIZIONE", how="left")
    forecast["SWING_PP"] = forecast["SWING_PP"].fillna(0.0)
    forecast["SHARE_PRED"] = forecast["SHARE"] + forecast["SWING_PP"]
    return forecast


def rmse_r2(y_true: np.ndarray, y_pred: np.ndarray) -> Tuple[float, float]:
    mask = ~(np.isnan(y_true) | np.isnan(y_pred))
    yt, yp = y_true[mask], y_pred[mask]
    if len(yt) < 2:
        return float("nan"), float("nan")
    rmse = float(np.sqrt(np.mean((yt - yp) ** 2)))
    ss_res = float(np.sum((yt - yp) ** 2))
    ss_tot = float(np.sum((yt - yt.mean()) ** 2))
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else float("nan")
    return rmse, r2


# =============================================================================
# CELLE DEMOGRAFICHE - Censimento Permanente ISTAT 2021
# =============================================================================
# Strategia in due stadi: ISTAT non pubblica la cross-tab eta x istruzione x
# sesso a livello comunale (la tabulazione 3-way e' disponibile solo a livello
# provinciale). Ricostruiamo le 18 celle assumendo indipendenza condizionale
# dell'istruzione dal comune dato (provincia, eta, sesso):
#
#   N(eta, istr, sesso | collegio) =
#       sum_{comune in collegio} N(eta, sesso | comune)
#                                * P(istr | eta, sesso, prov(comune))
#
# PERCORSO PRIMARIO (raccomandato) - Sezioni di Censimento 2021:
#   bulk zip pubblicato da ISTAT che contiene per ogni sezione di censimento
#   le frequenze sesso x classe eta x titolo di studio (oltre a cittadinanza,
#   occupazione, ecc.). Aggregazione: sezione -> comune -> collegio. E' il
#   dataset 3-way disponibile a granularita' sub-comunale, quindi non richiede
#   l'assunzione di indipendenza condizionale dello stadio 1+2.
#   Pagina: https://www.istat.it/it/archivio/285267
#
# PERCORSO FALLBACK - due stadi via Data Warehouse Censimenti Permanenti:
#   Stadio 1: popolazione comunale per eta singola x sesso  -> demo.istat.it / SDMX DCIS_POPSTRRES1
#   Stadio 2: distribuzione provinciale istruzione|eta,sesso -> SDMX DCSC_GRADOISTRUZIONE
#   Stadio 3: aggregazione su comuni del collegio + normalizzazione a peso.
# Sorgente SDMX: http://dati-censimentipermanenti.istat.it/SDMXWS/rest/data
# -----------------------------------------------------------------------------
# Data Warehouse Censimenti Permanenti (endpoint SDMX 2.1).
# Endpoint legacy esploradati.censimentopopolazione.istat.it -> 404 dall'autunno 2024.
ISTAT_SDMX_BASE = "http://dati-censimentipermanenti.istat.it/SDMXWS/rest/data"
# Dataflow CP - popolazione residente per eta singola x sesso (livello LAU/comune)
ISTAT_DF_POP_COMUNE = "IT1,DCIS_POPSTRRES1,1.0"
# Dataflow CP - popolazione 9+ per grado istruzione x eta x sesso (livello provinciale)
ISTAT_DF_GRADO_ISTR = "IT1,DCSC_GRADOISTRUZIONE,1.0"

# GEODEMO/POSAS - bulk CSV per anno: lo schema URL e' cambiato (post-2023 il
# file e' su /app/?i=POS, non piu' su /data/posas). Proviamo i candidati in
# ordine, il primo che risponde 200 vince.
ISTAT_GEODEMO_URL_CANDIDATES: List[str] = [
    "https://demo.istat.it/data/posas/POSAS_{year}.zip",
    "https://demo.istat.it/app/posas/POSAS_{year}.zip",
    "https://demo.istat.it/data/POSAS/POSAS_{year}.zip",
    "https://demo.istat.it/data/posas{year}/POSAS_{year}.zip",
]

# Sezioni di Censimento 2021 - landing page e candidati URL diretto (zip).
# La pagina archivio elenca i file effettivi; manteniamo dei pattern noti come
# fallback ma il discovery dinamico (parse della pagina) e' piu' robusto.
ISTAT_SEZIONI_CENS_PAGE = "https://www.istat.it/it/archivio/285267"
ISTAT_SEZIONI_CENS_URL_CANDIDATES: List[str] = [
    "https://www.istat.it/storage/cartografia/Censimento_2021_dati_sezioni.zip",
    "https://www.istat.it/storage/cartografia/sezioni_censimento_2021_dati.zip",
    "https://www.istat.it/storage/cartografia/dati-cens-var_2021.zip",
]

# Anno di riferimento del Censimento Permanente
ISTAT_ANNO_RIFERIMENTO = 2021

# Bin eta target -> intervallo (estremi inclusi) sull'eta singola
ETA_BINS_TARGET: List[Tuple[str, int, int]] = [
    ("18-34", 18, 34),
    ("35-64", 35, 64),
    ("65+",   65, 120),
]

# ISTAT codifica EDU2011 (CL_EDU2011) -> 3 livelli aggregati.
# Bassa: nessun titolo, licenza elementare, licenza media (ISCED 0-2).
# Media: diploma scuola secondaria di II grado, qualifica professionale (ISCED 3-4).
# Alta : titolo terziario - diploma accademico, laurea, master, dottorato (ISCED 5-8).
ISTRUZIONE_GROUPS: Dict[str, List[str]] = {
    "bassa": ["EDU0", "EDU1", "EDU2", "ISCED0", "ISCED1", "ISCED2",
              "_T_NESSUNO", "ELEM", "MEDIA"],
    "media": ["EDU3", "EDU4", "ISCED3", "ISCED4", "DIPLOMA", "QUAL"],
    "alta":  ["EDU5", "EDU6", "EDU7", "EDU8",
              "ISCED5", "ISCED6", "ISCED7", "ISCED8",
              "LAUREA", "LM", "MASTER", "DOTT"],
}

# Mapping CIRCOSCRIZIONE Eligendo -> nome regione canonico.
CIRC_TO_REGIONE: Dict[str, str] = {
    "ABRUZZO": "Abruzzo", "BASILICATA": "Basilicata", "CALABRIA": "Calabria",
    "CAMPANIA 1": "Campania", "CAMPANIA 2": "Campania",
    "EMILIA-ROMAGNA": "Emilia-Romagna",
    "FRIULI-VENEZIA GIULIA": "Friuli-Venezia Giulia",
    "LAZIO 1": "Lazio", "LAZIO 2": "Lazio",
    "LIGURIA": "Liguria",
    "LOMBARDIA 1": "Lombardia", "LOMBARDIA 2": "Lombardia",
    "LOMBARDIA 3": "Lombardia", "LOMBARDIA 4": "Lombardia",
    "MARCHE": "Marche", "MOLISE": "Molise",
    "PIEMONTE 1": "Piemonte", "PIEMONTE 2": "Piemonte",
    "PUGLIA": "Puglia", "SARDEGNA": "Sardegna",
    "SICILIA 1": "Sicilia", "SICILIA 2": "Sicilia",
    "TOSCANA": "Toscana",
    "TRENTINO-ALTO ADIGE": "Trentino-Alto Adige",
    "TRENTINO-ALTO ADIGE/SUDTIROL": "Trentino-Alto Adige",
    "UMBRIA": "Umbria",
    "VALLE D'AOSTA": "Valle d'Aosta",
    "VALLE D'AOSTA/VALLEE D'AOSTE": "Valle d'Aosta",
    "VENETO 1": "Veneto", "VENETO 2": "Veneto",
}


def _circ_to_regione(circ: str) -> str:
    if not isinstance(circ, str):
        return ""
    key = circ.strip().upper()
    if key in CIRC_TO_REGIONE:
        return CIRC_TO_REGIONE[key]
    base = key.rsplit(" ", 1)[0] if key.rsplit(" ", 1)[-1].isdigit() else key
    return CIRC_TO_REGIONE.get(base, base.title())


def _bin_eta(age: int) -> Optional[str]:
    for label, lo, hi in ETA_BINS_TARGET:
        if lo <= age <= hi:
            return label
    return None


def _classify_istruzione(code: str) -> Optional[str]:
    """Mappa codice ISTAT EDU/ISCED -> 'bassa' | 'media' | 'alta'."""
    if not isinstance(code, str):
        return None
    c = code.strip().upper()
    for level, codes in ISTRUZIONE_GROUPS.items():
        if c in codes:
            return level
    # Match permissivo (varianti tipo "L_EDU3", "_T_EDU5")
    for level, codes in ISTRUZIONE_GROUPS.items():
        for k in codes:
            if k == "_T_NESSUNO":
                continue
            if c.endswith(k) or (len(k) >= 4 and k in c):
                return level
    return None


def _remap_fascia_istat(code: str) -> Optional[str]:
    """Remap codice fascia eta SDMX (es. ``Y15-24``, ``Y_GE65``) -> 18-34/35-64/65+.

    Ricado al bin che copre il punto medio della fascia ISTAT, accettando un
    piccolo errore di assegnazione su 18-19 (in 15-24) e 35-44 mid (in 35-64).
    """
    if not isinstance(code, str):
        return None
    c = code.strip().upper().lstrip("Y")
    if c.startswith("_LT"):
        try:
            return _bin_eta(max(0, int(c[3:]) - 1))
        except ValueError:
            return None
    if c.startswith("_GE"):
        try:
            return _bin_eta(int(c[3:]))
        except ValueError:
            return None
    if "-" in c:
        try:
            a, b = c.split("-", 1)
            return _bin_eta((int(a) + int(b)) // 2)
        except ValueError:
            return None
    if c.isdigit():
        return _bin_eta(int(c))
    return None


# ---------------------------------------------------------------- ISTAT IO --
def _fetch_istat_sdmx_csv(dataflow: str, key: str, cache: CacheConfig,
                          year_start: int = ISTAT_ANNO_RIFERIMENTO,
                          year_end: int = ISTAT_ANNO_RIFERIMENTO,
                          fname: Optional[str] = None) -> pd.DataFrame:
    """Scarica un cubo SDMX ISTAT in formato CSV.

    Path SDMX 2.1: ``{base}/{flow}/{key}/?startPeriod=YYYY&endPeriod=YYYY&format=csv``.
    ``key`` segue la sintassi SDMX (dimensioni separate da ``.``, valori multipli
    con ``+``, wildcard con stringa vuota).
    """
    safe = (dataflow + "_" + key).replace("/", "_").replace(",", "_") \
                                  .replace(".", "-").replace("+", "p") + ".csv"
    fname = fname or safe
    url = (f"{ISTAT_SDMX_BASE}/{dataflow}/{key}/"
           f"?startPeriod={year_start}&endPeriod={year_end}&format=csv")
    blob = fetch(url, cache, fname)
    df = pd.read_csv(io.BytesIO(blob), dtype=str, low_memory=False)
    df.columns = [c.strip().upper() for c in df.columns]
    return df


def _try_fetch_candidates(urls: List[str], cache: CacheConfig,
                          fname: str, **fmt: object) -> Optional[bytes]:
    """Prova in ordine una lista di URL candidate. Restituisce il primo che
    risponde 200, o None se tutti falliscono.
    """
    for u in urls:
        try:
            url = u.format(**fmt) if fmt else u
            return fetch(url, cache, fname)
        except requests.HTTPError as e:
            log.info("Candidate KO (%s): %s", e.response.status_code if e.response else "?", url)
        except requests.RequestException as e:
            log.info("Candidate err: %s -> %s", url, e)
    return None


def load_pop_comune_eta_sesso(cache: CacheConfig,
                              year: int = ISTAT_ANNO_RIFERIMENTO) -> pd.DataFrame:
    """Popolazione residente per eta singola x sesso a livello comunale.

    Fonte preferita: ISTAT GEODEMO POSAS (bulk CSV nazionale, una riga per
    (comune, sesso, eta)). Lo schema URL di demo.istat.it e' cambiato negli
    ultimi anni: proviamo i candidati in ``ISTAT_GEODEMO_URL_CANDIDATES`` in
    ordine. Fallback: SDMX DCIS_POPSTRRES1 a livello LAU sul Data Warehouse
    Censimenti Permanenti.

    Returns
    -------
    DataFrame con colonne: COD_ISTAT, COMUNE, SESSO ('M'/'F'), ETA (int 0-100),
    POP (int).
    """
    fname = f"POSAS_{year}.zip"
    blob = _try_fetch_candidates(ISTAT_GEODEMO_URL_CANDIDATES, cache, fname,
                                 year=year)
    if blob is None:
        log.warning("GEODEMO POSAS_%d: tutti i candidati hanno restituito errore"
                    " -> fallback SDMX", year)
        return _load_pop_comune_via_sdmx(cache, year)

    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        csv_name = next((n for n in zf.namelist() if n.lower().endswith(".csv")), None)
        if csv_name is None:
            raise FileNotFoundError(f"POSAS_{year}.zip privo di CSV")
        with zf.open(csv_name) as fh:
            raw = pd.read_csv(fh, dtype=str, low_memory=False)

    raw.columns = [c.strip().upper() for c in raw.columns]
    # Schema POSAS: ITTER107, Territorio, Eta, Sesso, Value (variants by year).
    col_cod = next((c for c in ("ITTER107", "CODICE", "COD_ISTAT") if c in raw.columns), None)
    col_terr = next((c for c in ("TERRITORIO", "DENOMINAZIONE", "COMUNE") if c in raw.columns), None)
    col_eta = next((c for c in ("ETA", "ETA1", "AGE", "ETA_ANNI") if c in raw.columns), None)
    if col_cod is None or col_terr is None or col_eta is None:
        raise ValueError(f"POSAS schema imprevisto: {raw.columns.tolist()}")

    if "MASCHI" in raw.columns and "FEMMINE" in raw.columns:
        m = raw[[col_cod, col_terr, col_eta, "MASCHI"]].copy()
        m["SESSO"] = "M"; m = m.rename(columns={"MASCHI": "POP"})
        f = raw[[col_cod, col_terr, col_eta, "FEMMINE"]].copy()
        f["SESSO"] = "F"; f = f.rename(columns={"FEMMINE": "POP"})
        df = pd.concat([m, f], ignore_index=True)
    elif "SESSO" in raw.columns and "VALUE" in raw.columns:
        df = raw[[col_cod, col_terr, col_eta, "SESSO", "VALUE"]].rename(
            columns={"VALUE": "POP"})
        df["SESSO"] = df["SESSO"].astype(str).str.upper().str[0]
        df = df[df["SESSO"].isin(["M", "F"])]
    else:
        raise ValueError(f"POSAS schema sesso non riconosciuto: {raw.columns.tolist()}")

    df = df.rename(columns={col_cod: "COD_ISTAT", col_terr: "COMUNE", col_eta: "ETA"})
    df["ETA"] = pd.to_numeric(df["ETA"].astype(str).str.extract(r"(\d+)")[0],
                              errors="coerce")
    df["POP"] = pd.to_numeric(df["POP"], errors="coerce").fillna(0).astype(int)
    df["COMUNE"] = df["COMUNE"].astype(str).str.strip().str.upper()
    df["COD_ISTAT"] = df["COD_ISTAT"].astype(str).str.strip().str.zfill(6)
    df = df.dropna(subset=["ETA"])
    df["ETA"] = df["ETA"].astype(int)
    df = df[(df["ETA"] >= 0) & (df["ETA"] <= 120)]
    return df[["COD_ISTAT", "COMUNE", "SESSO", "ETA", "POP"]]


def _load_pop_comune_via_sdmx(cache: CacheConfig, year: int) -> pd.DataFrame:
    """Fallback: popolazione comunale via SDMX DCIS_POPSTRRES1."""
    # KEY SDMX (ordine standard): FREQ.ITTER107.TIPO_DATO.SEXISTAT1.ETA1
    key = "A....."
    df = _fetch_istat_sdmx_csv(ISTAT_DF_POP_COMUNE, key, cache,
                               year_start=year, year_end=year,
                               fname=f"sdmx_pop_comune_{year}.csv")
    rename = {"ITTER107": "COD_ISTAT", "REF_AREA": "COD_ISTAT",
              "SEXISTAT1": "SESSO", "SEX": "SESSO",
              "ETA1": "ETA", "AGE": "ETA",
              "OBS_VALUE": "POP", "VALUE": "POP",
              "TERRITORIO": "COMUNE"}
    df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})
    df["SESSO"] = df["SESSO"].astype(str).str.upper().str[0]
    df = df[df["SESSO"].isin(["M", "F"])]
    df["ETA"] = pd.to_numeric(df["ETA"].astype(str).str.extract(r"(\d+)")[0],
                              errors="coerce")
    df["POP"] = pd.to_numeric(df["POP"], errors="coerce").fillna(0).astype(int)
    df = df.dropna(subset=["ETA"])
    df["ETA"] = df["ETA"].astype(int)
    if "COMUNE" not in df.columns:
        df["COMUNE"] = ""
    df["COMUNE"] = df["COMUNE"].astype(str).str.strip().str.upper()
    df["COD_ISTAT"] = df["COD_ISTAT"].astype(str).str.strip().str.zfill(6)
    return df[["COD_ISTAT", "COMUNE", "SESSO", "ETA", "POP"]]


def load_istruzione_provincia(cache: CacheConfig,
                              year: int = ISTAT_ANNO_RIFERIMENTO) -> pd.DataFrame:
    """Distribuzione condizionale P(istruzione | fascia_eta, sesso, provincia).

    SDMX DCSC_GRADOISTRUZIONE: popolazione 9+ per grado istruzione, classe eta,
    sesso; livello territoriale provinciale (NUTS3, ITTER107 codice 3 cifre).

    Returns
    -------
    DataFrame con colonne: PROV_COD (3 cifre), FASCIA_ETA, SESSO,
    ISTRUZIONE ('bassa'/'media'/'alta'), P_COND (somma=1 per (PROV,ETA,SESSO)).
    """
    # KEY SDMX: FREQ.ITTER107.TIPO_DATO.SEXISTAT1.ETA1.TITOLO_STUDIO
    key = "A......"
    df = _fetch_istat_sdmx_csv(ISTAT_DF_GRADO_ISTR, key, cache,
                               year_start=year, year_end=year,
                               fname=f"sdmx_grado_istruzione_{year}.csv")
    rename = {"ITTER107": "TERR", "REF_AREA": "TERR",
              "SEXISTAT1": "SESSO", "SEX": "SESSO",
              "ETA1": "ETA_CL", "AGE": "ETA_CL",
              "TITOLO_STUDIO": "EDU", "EDU2011": "EDU", "EDU_LEV": "EDU",
              "OBS_VALUE": "VAL", "VALUE": "VAL"}
    df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})
    df["VAL"] = pd.to_numeric(df["VAL"], errors="coerce").fillna(0.0)
    df["SESSO"] = df["SESSO"].astype(str).str.upper().str[0]
    df = df[df["SESSO"].isin(["M", "F"])]
    df["TERR"] = df["TERR"].astype(str).str.strip()
    # Provincia ITTER107: codice numerico 3 cifre (es. "015" Milano).
    df["PROV_COD"] = df["TERR"].str.extract(r"^(\d{3})$")[0]
    df = df.dropna(subset=["PROV_COD"])

    df["FASCIA_ETA"] = df["ETA_CL"].apply(_remap_fascia_istat)
    df = df.dropna(subset=["FASCIA_ETA"])

    df["ISTRUZIONE"] = df["EDU"].apply(_classify_istruzione)
    df = df.dropna(subset=["ISTRUZIONE"])

    agg = (df.groupby(["PROV_COD", "FASCIA_ETA", "SESSO", "ISTRUZIONE"],
                      as_index=False).agg(VAL=("VAL", "sum")))
    tot = (agg.groupby(["PROV_COD", "FASCIA_ETA", "SESSO"])["VAL"].sum()
              .rename("TOT").reset_index())
    agg = agg.merge(tot, on=["PROV_COD", "FASCIA_ETA", "SESSO"])
    agg["P_COND"] = np.where(agg["TOT"] > 0, agg["VAL"] / agg["TOT"], 0.0)
    return agg[["PROV_COD", "FASCIA_ETA", "SESSO", "ISTRUZIONE", "P_COND"]]


# ---------------------------------------------------------------- sezioni -
# Tracciato Censimento Permanente 2021 - file regionali "indicatori sezioni".
# Schema confermato dal documento "TRACCIATO FILE REGIONALI.xlsx" (130 colonne).
# Variabili rilevanti:
#   PROCOM           = codice comune nazionale (6 cifre, zero-padded)
#   P30..P45         = popolazione MASCHI per classi quinquennali eta:
#                      <5, 5-9, 10-14, 15-19, 20-24, 25-29, 30-34, 35-39,
#                      40-44, 45-49, 50-54, 55-59, 60-64, 65-69, 70-74, >74
#   P67..P82         = popolazione FEMMINE, stesse classi
#   P91..P95         = MASCHI 9+ per titolo di studio: senza titolo (P91),
#                      elementare (P92), media (P93), diploma+qualifica (P94),
#                      terziari (P95)
#   P96..P100        = FEMMINE 9+, idem ordinato (senza titolo..terziari)
#
# Cartella locale con i 20 file regionali R01..R20 (uno per regione,
# foglio "Rxx", una riga per sezione di censimento).
ISTAT_LOCAL_DIR = r"C:\Users\work\Dropbox\Public\Q-Italia\q-italia\data_istat"

SEZIONI_2021_PROCOM_COL = "PROCOM"

# Età × sesso: una tupla per ogni classe quinquennale.
# (var_M, var_F, lo_inclusivo, hi_inclusivo). Per la classe terminale ">74"
# uso un cap convenzionale 99 ai fini dell'overlap con le fasce target.
SEZIONI_2021_ETA_BANDS: List[Tuple[str, str, int, int]] = [
    ("P30", "P67",  0,  4),
    ("P31", "P68",  5,  9),
    ("P32", "P69", 10, 14),
    ("P33", "P70", 15, 19),
    ("P34", "P71", 20, 24),
    ("P35", "P72", 25, 29),
    ("P36", "P73", 30, 34),
    ("P37", "P74", 35, 39),
    ("P38", "P75", 40, 44),
    ("P39", "P76", 45, 49),
    ("P40", "P77", 50, 54),
    ("P41", "P78", 55, 59),
    ("P42", "P79", 60, 64),
    ("P43", "P80", 65, 69),
    ("P44", "P81", 70, 74),
    ("P45", "P82", 75, 99),
]

# Istruzione 9+ per sesso. Bassa = senza titolo + elementare + media; Media =
# diploma sup + qualifica (gia' aggregati in P94/P99); Alta = terziari.
SEZIONI_2021_ISTR_M: Dict[str, List[str]] = {
    "bassa": ["P91", "P92", "P93"],
    "media": ["P94"],
    "alta":  ["P95"],
}
SEZIONI_2021_ISTR_F: Dict[str, List[str]] = {
    "bassa": ["P96", "P97", "P98"],
    "media": ["P99"],
    "alta":  ["P100"],
}


def _overlap_share(lo_b: int, hi_b: int, lo_t: int, hi_t: int) -> float:
    """Frazione (in anni interi) della banda quinquennale [lo_b,hi_b] che
    cade nella fascia target [lo_t,hi_t]. Estremi inclusi.
    """
    overlap = max(0, min(hi_b, hi_t) - max(lo_b, lo_t) + 1)
    band = hi_b - lo_b + 1
    return overlap / band if band > 0 else 0.0


def load_da_sezioni_censimento(
    cache: Optional[CacheConfig] = None,
    local_dir: Optional[Path] = None,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """PERCORSO PRIMARIO. Carica i 20 file regionali Censimento Permanente 2021
    da disco locale (no download).

    Legge ``Rxx_indicatori_2021_sezioni.xlsx`` per xx in 01..20 da
    ``ISTAT_LOCAL_DIR``, concatena, aggrega da sezione a comune (PROCOM) e
    produce:

      - ``pop_es``: popolazione per (COD_ISTAT, SESSO, FASCIA_ETA), con
                    allocazione frazionaria della classe 15-19 (anni 18-19
                    = 2/5 finiscono in 18-34).
      - ``istr_c``: distribuzione condizionale P(istruzione | comune, sesso),
                    long con (COD_ISTAT, SESSO, ISTRUZIONE, P_ISTR).

    Performance: la lettura di 20 file xlsx (~250MB totali) tramite openpyxl
    impiega 5-10 minuti. Se ``cache`` e' fornita, l'output aggregato a livello
    comunale viene salvato in parquet sotto ``cache.cache_dir`` e riutilizzato
    nelle chiamate successive (sub-second).

    Parameters
    ----------
    cache : CacheConfig | None
        Se fornita, usa ``cache.cache_dir/sezioni_com_2021.parquet`` come cache
        del DataFrame ``com`` (aggregato sezione->comune).
    local_dir : Path | None
        Override della directory dei file. Se None usa ``ISTAT_LOCAL_DIR``.
    """
    src_dir = Path(local_dir) if local_dir is not None else Path(ISTAT_LOCAL_DIR)
    if not src_dir.exists():
        raise FileNotFoundError(f"ISTAT_LOCAL_DIR non trovato: {src_dir}")

    cache_path: Optional[Path] = None
    if cache is not None and cache.enabled:
        cache_path = cache.path_for("sezioni_com_2021.parquet")

    # Subset minimo di colonne (limita memoria - i file pesano fino a ~40MB l'uno).
    cols_etam = [b[0] for b in SEZIONI_2021_ETA_BANDS]
    cols_etaf = [b[1] for b in SEZIONI_2021_ETA_BANDS]
    cols_istr_m = (SEZIONI_2021_ISTR_M["bassa"] + SEZIONI_2021_ISTR_M["media"]
                   + SEZIONI_2021_ISTR_M["alta"])
    cols_istr_f = (SEZIONI_2021_ISTR_F["bassa"] + SEZIONI_2021_ISTR_F["media"]
                   + SEZIONI_2021_ISTR_F["alta"])
    num_cols = cols_etam + cols_etaf + cols_istr_m + cols_istr_f
    usecols = [SEZIONI_2021_PROCOM_COL] + num_cols

    com: Optional[pd.DataFrame] = None
    if cache_path is not None and cache_path.exists():
        try:
            com = pd.read_parquet(cache_path)
            log.info("[sezioni] cache HIT %s (%d comuni)",
                     cache_path.name, len(com))
        except Exception as e:
            log.warning("[sezioni] cache parquet illeggibile: %s -> rebuild", e)
            com = None

    if com is None:
        files = sorted(p for p in src_dir.glob(
            "R[0-9][0-9]_indicatori_2021_sezioni.xlsx"))
        if not files:
            raise FileNotFoundError(
                f"Nessun file R<NN>_indicatori_2021_sezioni.xlsx in {src_dir}")
        log.info("[sezioni] %d file regionali trovati in %s", len(files), src_dir)

        frames: List[pd.DataFrame] = []
        for fp in files:
            log.info("[sezioni] lettura %s ...", fp.name)
            # Foglio: i file ISTAT 2021 nominano il foglio "Rxx" (es. "R01").
            sheet = fp.stem.split("_")[0]
            try:
                chunk = pd.read_excel(fp, sheet_name=sheet, dtype=str,
                                      usecols=usecols)
            except ValueError:
                chunk = pd.read_excel(fp, sheet_name=0, dtype=str,
                                      usecols=usecols)
            frames.append(chunk)
        raw = pd.concat(frames, ignore_index=True)
        log.info("[sezioni] %d sezioni totali (concat 20 regioni)", len(raw))

        # Cast numerico
        for c in num_cols:
            raw[c] = pd.to_numeric(raw[c], errors="coerce").fillna(0)

        # PROCOM puo' venire come "1001" (Piemonte/Torino/Aglie') -> zfill(6) "001001".
        raw["COD_ISTAT"] = (raw[SEZIONI_2021_PROCOM_COL].astype(str)
                            .str.strip().str.zfill(6))

        # Aggrega sezione -> comune.
        com = raw.groupby("COD_ISTAT", as_index=False)[num_cols].sum()
        log.info("[sezioni] %d comuni dopo aggregazione sezioni->PROCOM",
                 com["COD_ISTAT"].nunique())

        if cache_path is not None:
            try:
                com.to_parquet(cache_path, index=False)
                log.info("[sezioni] cache scritta: %s", cache_path)
            except Exception as e:
                log.warning("[sezioni] impossibile scrivere cache parquet: %s", e)

    # ----- pop_es: alloca classi quinquennali sulle 3 fasce target -----
    pop_rows: List[pd.DataFrame] = []
    for label, lo_t, hi_t in ETA_BINS_TARGET:
        for var_m, var_f, lo_b, hi_b in SEZIONI_2021_ETA_BANDS:
            share = _overlap_share(lo_b, hi_b, lo_t, hi_t)
            if share == 0.0:
                continue
            pop_rows.append(pd.DataFrame({
                "COD_ISTAT": com["COD_ISTAT"],
                "SESSO": "M",
                "FASCIA_ETA": label,
                "POP": com[var_m].astype(float) * share,
            }))
            pop_rows.append(pd.DataFrame({
                "COD_ISTAT": com["COD_ISTAT"],
                "SESSO": "F",
                "FASCIA_ETA": label,
                "POP": com[var_f].astype(float) * share,
            }))
    pop_es = (pd.concat(pop_rows, ignore_index=True)
                .groupby(["COD_ISTAT", "SESSO", "FASCIA_ETA"], as_index=False)
                .agg(POP=("POP", "sum")))

    # ----- istr_c: P(istr | comune, sesso) (3-way, dal 9+ sex-conditioned) -----
    istr_rows: List[pd.DataFrame] = []
    for sex, varmap in (("M", SEZIONI_2021_ISTR_M), ("F", SEZIONI_2021_ISTR_F)):
        for level in ("bassa", "media", "alta"):
            cols = varmap[level]
            istr_rows.append(pd.DataFrame({
                "COD_ISTAT": com["COD_ISTAT"],
                "SESSO": sex,
                "ISTRUZIONE": level,
                "POP": com[cols].sum(axis=1).astype(float),
            }))
    istr_long = pd.concat(istr_rows, ignore_index=True)
    tot = (istr_long.groupby(["COD_ISTAT", "SESSO"])["POP"].sum()
                    .rename("TOT").reset_index())
    istr_c = istr_long.merge(tot, on=["COD_ISTAT", "SESSO"])
    istr_c["P_ISTR"] = np.where(istr_c["TOT"] > 0,
                                 istr_c["POP"] / istr_c["TOT"], 0.0)
    istr_c = istr_c[["COD_ISTAT", "SESSO", "ISTRUZIONE", "P_ISTR"]]

    log.info("[sezioni] OK: pop_es %d righe, istr_c %d righe",
             len(pop_es), len(istr_c))
    return pop_es, istr_c


# ---------------------------------------------------------------- builder -
def build_celle_demografiche(
    mapper: pd.DataFrame,
    cache: CacheConfig,
    out_csv: Path,
    year: int = ISTAT_ANNO_RIFERIMENTO,
    collegi_meta_csv: Optional[Path] = None,
    source: str = "auto",
) -> pd.DataFrame:
    """Costruisce la matrice 18 celle/collegio Camera uninominale.

    18 celle = 3 fasce eta (18-34, 35-64, 65+) x 3 livelli istruzione
    (bassa/media/alta) x 2 generi (M/F).

    Parameters
    ----------
    mapper : DataFrame
        Output di ``parse_collegi_map``. Colonne richieste: COMUNE, PROV_SIGLA,
        COLLUNINOM, CIRC; opzionale COD_ISTAT (raccomandato).
    cache : CacheConfig
    out_csv : Path
        Destinazione del CSV finale.
    year : int
        Anno di riferimento Censimento Permanente (default 2021).
    collegi_meta_csv : Path | None
        CSV opzionale con metadati collegi (almeno colonne ``nome_collegio`` e
        ``regione``). Tipicamente ``data/collegi_uninominali_2022.csv``.
    source : {'auto', 'sezioni', 'twostage'}
        Sorgente dati. ``'sezioni'`` usa il bulk Sezioni di Censimento 2021
        (granularita' sub-comunale, raccomandato). ``'twostage'`` usa POSAS +
        SDMX provinciale (richiede 2 endpoint funzionanti). ``'auto'`` (default)
        prova prima ``'sezioni'`` e ricade su ``'twostage'`` se fallisce.

    Returns
    -------
    DataFrame con colonne: collegio_id, regione, fascia_eta, istruzione, genere,
    popolazione, peso (peso = popolazione_cella / popolazione_totale_collegio).
    """
    cells: Optional[pd.DataFrame] = None
    if source in ("auto", "sezioni"):
        try:
            cells = _build_cells_via_sezioni(cache)
        except Exception as e:
            if source == "sezioni":
                raise
            log.warning("[celle] percorso 'sezioni' fallito (%s) -> fallback "
                        "two-stage POSAS+SDMX", e)
    if cells is None:
        cells = _build_cells_via_twostage(cache, year)

    return _finalize_celle(cells, mapper, out_csv, collegi_meta_csv, year)


def _build_cells_via_sezioni(cache: Optional[CacheConfig] = None) -> pd.DataFrame:
    """Percorso primario: Sezioni di Censimento 2021 (file locali).

    Restituisce un DataFrame long con colonne:
    ``COD_ISTAT, FASCIA_ETA, SESSO, ISTRUZIONE, POP_CELLA``
    dove POP_CELLA = pop(eta,sesso|comune) * P(istr | comune, sesso).
    L'istruzione e' sex-conditioned (P91-P95 / P96-P100 dal 9+ ISCED), quindi
    e' un'indipendenza eta | (sesso, comune) - meno restrittiva della versione
    precedente che usava solo la marginale comunale.
    """
    pop_es, istr_c = load_da_sezioni_censimento(cache)
    cells = pop_es.merge(istr_c, on=["COD_ISTAT", "SESSO"], how="left")
    cells["P_ISTR"] = cells["P_ISTR"].fillna(1.0 / 3)
    cells["POP_CELLA"] = cells["POP"] * cells["P_ISTR"]
    return cells[["COD_ISTAT", "FASCIA_ETA", "SESSO", "ISTRUZIONE", "POP_CELLA"]]


def _build_cells_via_twostage(cache: CacheConfig, year: int) -> pd.DataFrame:
    """Percorso fallback: POSAS comune + DCSC_GRADOISTRUZIONE provincia.

    Restituisce un DataFrame long compatibile con ``_build_cells_via_sezioni``.
    """
    log.info("[celle] Carico popolazione comunale eta x sesso (CP %d)...", year)
    pop = load_pop_comune_eta_sesso(cache, year=year)
    log.info("  %d righe (comune,eta,sesso) pop totale=%s",
             len(pop), f"{int(pop['POP'].sum()):,}".replace(",", "."))

    log.info("[celle] Carico distribuzione provinciale istruzione|eta,sesso...")
    istr = load_istruzione_provincia(cache, year=year)
    log.info("  %d righe (prov,fascia,sesso,istr)", len(istr))

    # Filtra 18+ ed assegna fascia eta target.
    pop = pop[pop["ETA"] >= 18].copy()
    pop["FASCIA_ETA"] = pop["ETA"].apply(_bin_eta)
    pop = pop.dropna(subset=["FASCIA_ETA"])
    pop_cf = (pop.groupby(["COD_ISTAT", "COMUNE", "FASCIA_ETA", "SESSO"],
                          as_index=False).agg(POP=("POP", "sum")))

    # Provincia da codice ISTAT comune (primi 3 char = ITTER107 provincia).
    if "COD_ISTAT" not in pop_cf.columns or pop_cf["COD_ISTAT"].eq("").all():
        raise ValueError("popolazione comunale priva di COD_ISTAT: impossibile "
                         "agganciare la provincia per la cross-tab istruzione.")
    pop_cf["PROV_COD"] = pop_cf["COD_ISTAT"].astype(str).str[:3].str.zfill(3)

    # Join con distribuzione provinciale istruzione | (eta, sesso): 1->3 fanout.
    cells = pop_cf.merge(istr, on=["PROV_COD", "FASCIA_ETA", "SESSO"], how="left")

    # Diagnostica match: % righe (comune, fascia, sesso) senza match provinciale.
    miss_mask = cells["P_COND"].isna()
    miss = float(miss_mask.mean())
    if miss > 0:
        log.warning("[celle] %.1f%% righe senza match istruzione provinciale "
                    "(uso fallback uniforme 1/3).", 100 * miss)
        # Espandi le righe senza match su tutti e 3 i livelli con peso 1/3.
        no = cells[miss_mask].copy()
        ok = cells[~miss_mask]
        expanded = []
        for lev in ("bassa", "media", "alta"):
            tmp = no.copy()
            tmp["ISTRUZIONE"] = lev
            tmp["P_COND"] = 1.0 / 3
            expanded.append(tmp)
        cells = pd.concat([ok, *expanded], ignore_index=True)

    cells["POP_CELLA"] = cells["POP"] * cells["P_COND"]
    return cells[["COD_ISTAT", "FASCIA_ETA", "SESSO", "ISTRUZIONE", "POP_CELLA"]]


def _finalize_celle(
    cells: pd.DataFrame,
    mapper: pd.DataFrame,
    out_csv: Path,
    collegi_meta_csv: Optional[Path],
    year: int,
) -> pd.DataFrame:
    """Aggrega le celle a livello collegio, calcola pesi, scrive CSV finale.

    ``cells`` long: CODICE ISTAT, FASCIA_ETA, SESSO, ISTRUZIONE, POP_CELLA.
    """
    # Normalizzazione difensiva: i loader upstream (_build_cells_via_*) emettono
    # la colonna come ``COD_ISTAT``; allineiamola al nome con spazio prima del
    # join col mapper.
    cells = cells.copy()
    if "CODICE ISTAT" not in cells.columns and "COD_ISTAT" in cells.columns:
        cells = cells.rename(columns={"COD_ISTAT": "CODICE ISTAT"})

    # Aggancia il collegio. Preferisci join robusto su CODICE ISTAT se disponibile.
    join_keys: List[str]
    if "CODICE ISTAT" in mapper.columns and mapper["CODICE ISTAT"].notna().any():
        m = mapper[["CODICE ISTAT", "COLLUNINOM", "CIRC"]].drop_duplicates()
        m["CODICE ISTAT"] = m["CODICE ISTAT"].astype(str).str.strip().str.zfill(6)
        cells["CODICE ISTAT"] = cells["CODICE ISTAT"].astype(str).str.strip().str.zfill(6)
        join_keys = ["CODICE ISTAT"]
    else:
        # Senza CODICE ISTAT nel mapper non possiamo joinare se cells e' indicizzato
        # solo su CODICE ISTAT. Forziamo un fallback risolvendo COMUNE via mapper.
        raise ValueError("mapper privo di 'CODICE ISTAT': aggiungere la colonna "
                         "'CODICE ISTAT' nel CSV elenco-collegi-comuni.")

    cells_j = cells.merge(m, on=join_keys, how="inner")

    # Comuni multi-collegio (Roma, Milano): allocazione uniforme.
    n_split = (cells_j.groupby(join_keys)["COLLUNINOM"].nunique()
                       .rename("N_SPLIT").reset_index())
    cells_j = cells_j.merge(n_split, on=join_keys, how="left")
    cells_j["POP_ALLOC"] = cells_j["POP_CELLA"] / cells_j["N_SPLIT"].clip(lower=1)

    agg = (cells_j.groupby(
        ["COLLUNINOM", "CIRC", "FASCIA_ETA", "ISTRUZIONE", "SESSO"],
        as_index=False).agg(POP=("POP_ALLOC", "sum")))

    # Peso = pop cella / pop totale collegio (su 18+).
    tot_coll = (agg.groupby("COLLUNINOM")["POP"].sum()
                   .rename("POP_TOT_COLL").reset_index())
    agg = agg.merge(tot_coll, on="COLLUNINOM")
    agg["PESO"] = np.where(agg["POP_TOT_COLL"] > 0,
                           agg["POP"] / agg["POP_TOT_COLL"], 0.0)

    # Forza presenza di tutte le 18 celle per ogni collegio (anche pop=0).
    full_index = pd.MultiIndex.from_product(
        [agg["COLLUNINOM"].unique(),
         [b[0] for b in ETA_BINS_TARGET],
         ["bassa", "media", "alta"],
         ["M", "F"]],
        names=["COLLUNINOM", "FASCIA_ETA", "ISTRUZIONE", "SESSO"],
    )
    agg = (agg.set_index(["COLLUNINOM", "FASCIA_ETA", "ISTRUZIONE", "SESSO"])
              .reindex(full_index, fill_value=0.0)
              .reset_index())

    # Riallaccia CIRC dopo il reindex (la moda per collegio).
    circ_lookup = (cells_j.groupby("COLLUNINOM")["CIRC"]
                          .agg(lambda s: s.value_counts().index[0])
                          .reset_index())
    agg = agg.drop(columns=["CIRC"], errors="ignore").merge(
        circ_lookup, on="COLLUNINOM", how="left")

    # Regione: prima da CSV metadati (se passato), poi fallback CIRC->REGIONE.
    regione_map: Dict[str, str] = {}
    if collegi_meta_csv is not None and Path(collegi_meta_csv).exists():
        meta = pd.read_csv(collegi_meta_csv, dtype=str)
        meta.columns = [c.strip().lower() for c in meta.columns]
        if "nome_collegio" in meta.columns and "regione" in meta.columns:
            regione_map = dict(zip(
                meta["nome_collegio"].str.strip().str.upper(),
                meta["regione"].str.strip(),
            ))

    def _reg_for(coll: str, circ: str) -> str:
        r = regione_map.get(str(coll).strip().upper())
        if r:
            return r
        return _circ_to_regione(circ or "")

    agg["REGIONE"] = [_reg_for(c, ci) for c, ci in zip(agg["COLLUNINOM"], agg["CIRC"])]

    out = agg.rename(columns={
        "COLLUNINOM": "collegio_id", "REGIONE": "regione",
        "FASCIA_ETA": "fascia_eta", "ISTRUZIONE": "istruzione",
        "SESSO": "genere", "POP": "popolazione", "PESO": "peso",
    })[[
        "collegio_id", "regione", "fascia_eta", "istruzione", "genere",
        "popolazione", "peso",
    ]]
    out["popolazione"] = out["popolazione"].round().astype(int)
    out["peso"] = out["peso"].round(6)

    # Ordinamento canonico per stabilita' diff.
    fascia_order = {"18-34": 0, "35-64": 1, "65+": 2}
    istr_order = {"bassa": 0, "media": 1, "alta": 2}
    sesso_order = {"M": 0, "F": 1}
    out = (out.assign(
            _f=out["fascia_eta"].map(fascia_order),
            _i=out["istruzione"].map(istr_order),
            _s=out["genere"].map(sesso_order))
            .sort_values(["collegio_id", "_f", "_i", "_s"])
            .drop(columns=["_f", "_i", "_s"])
            .reset_index(drop=True))

    out_csv.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(out_csv, index=False)
    log.info("[celle] Salvato %s (%d righe, %d collegi, anno %d)",
             out_csv, len(out), out["collegio_id"].nunique(), year)

    # QA: somma pesi ~= 1 per collegio.
    sums = out.groupby("collegio_id")["peso"].sum()
    bad = sums[(sums - 1.0).abs() > 1e-3]
    if not bad.empty:
        log.warning("[celle] %d collegi con somma pesi != 1 (max diff %.4f)",
                    len(bad), float((sums - 1.0).abs().max()))
    return out


# ---------------------------------------------------------------- pipeline --
@dataclass
class RegionResult:
    region: str
    n_collegi: int
    rmse_pp: float
    r2: float
    rmse_per_coal: Dict[str, float] = field(default_factory=dict)
    note: str = ""


def run_region(region: str, meta: Dict[str, str], mapper: pd.DataFrame,
               base22: pd.DataFrame, cache: CacheConfig,
               out_dir: Path) -> RegionResult:
    url = REGIONALI_URL.format(date=meta["data"])
    fname = f"regionali-{meta['data']}.zip"
    try:
        blob = fetch(url, cache, fname)
    except requests.HTTPError as e:
        log.warning("[%s] ZIP non disponibile: %s", region, e)
        return RegionResult(region, 0, float("nan"), float("nan"),
                            note=f"Eligendo ZIP 404 ({url})")

    try:
        long_df = parse_regionali_zip(blob, region_filter=region)
    except Exception as e:
        log.warning("[%s] parse fallito: %s", region, e)
        return RegionResult(region, 0, float("nan"), float("nan"),
                            note=f"Parse error: {e}")

    pred = shares_by_collegio(long_df, mapper, region=region)
    base = shares_camera_2022(base22, region)

    if pred.empty or base.empty:
        return RegionResult(region, 0, float("nan"), float("nan"),
                            note="Aggregazione vuota (mapping comune->collegio)")

    forecast = apply_uniform_swing(pred, base)
    cmp = forecast.merge(
        pred.rename(columns={"SHARE": "SHARE_REG_TARGET"})[
            ["COLLUNINOM", "COALIZIONE", "SHARE_REG_TARGET"]
        ],
        on=["COLLUNINOM", "COALIZIONE"], how="inner",
    )
    cmp = cmp[cmp["COALIZIONE"].isin(CORE_COALITIONS)]

    rmse_g, r2_g = rmse_r2(cmp["SHARE_REG_TARGET"].values, cmp["SHARE_PRED"].values)
    per_coal: Dict[str, float] = {}
    for c in CORE_COALITIONS:
        sub = cmp[cmp["COALIZIONE"] == c]
        if len(sub) >= 2:
            per_coal[c] = rmse_r2(sub["SHARE_REG_TARGET"].values,
                                  sub["SHARE_PRED"].values)[0]

    n_coll = cmp["COLLUNINOM"].nunique()
    out_dir.mkdir(parents=True, exist_ok=True)
    cmp.sort_values(["COLLUNINOM", "COALIZIONE"]).to_csv(
        out_dir / f"detail_{region.lower()}.csv", index=False, sep=";")

    return RegionResult(region, n_coll, rmse_g, r2_g, per_coal)


# ---------------------------------------------------------------- CLI ------
def main(argv: Optional[Iterable[str]] = None) -> int:
    p = argparse.ArgumentParser(description="polisim - validazione swing model multi-regionale")
    p.add_argument("--regions", nargs="*", default=list(REGIONI_TARGET.keys()))
    p.add_argument("--out", type=Path, default=Path("./out"))
    p.add_argument("--cache", type=Path, default=Path("./_cache"))
    p.add_argument("--no-cache", action="store_true")
    p.add_argument("--build-celle", action="store_true",
                   help="Costruisce data/celle_demografiche_collegi.csv (ISTAT CP) "
                        "e termina senza calcolare RMSE.")
    p.add_argument("--celle-out", type=Path,
                   default=Path("./data/celle_demografiche_collegi.csv"))
    p.add_argument("--collegi-meta", type=Path,
                   default=Path("./data/collegi_uninominali_2022.csv"),
                   help="CSV opzionale con metadati collegi (nome_collegio,regione).")
    p.add_argument("--anno-cp", type=int, default=ISTAT_ANNO_RIFERIMENTO,
                   help="Anno di riferimento Censimento Permanente (default 2021).")
    p.add_argument("--celle-source", choices=("auto", "sezioni", "twostage"),
                   default="auto",
                   help="Sorgente dati per build-celle: 'sezioni' usa il bulk "
                        "Sezioni di Censimento 2021 (raccomandato); 'twostage' "
                        "usa POSAS + SDMX provinciale; 'auto' (default) prova "
                        "sezioni e ricade su twostage se fallisce.")
    args = p.parse_args(list(argv) if argv is not None else None)

    cache = CacheConfig(cache_dir=args.cache, enabled=not args.no_cache)
    args.out.mkdir(parents=True, exist_ok=True)

    log.info("Carico catalogo collegi Camera...")
    mapper = parse_collegi_map(fetch(COLLEGI_COMUNI_URL, cache,
                                     "elenco-collegi-comuni-camera.csv"))
    log.info("  %d righe (comune,collegio).", len(mapper))

    if args.build_celle:
        meta_csv = args.collegi_meta if args.collegi_meta.exists() else None
        build_celle_demografiche(
            mapper=mapper, cache=cache, out_csv=args.celle_out,
            year=args.anno_cp, collegi_meta_csv=meta_csv,
            source=args.celle_source,
        )
        return 0

    log.info("Carico baseline Camera 2022...")
    base22 = parse_camera_2022(fetch(CAMERA_2022_URL, cache,
                                     "camera-2022-Italia-livcomune.csv"))
    log.info("  %d righe baseline.", len(base22))

    rows = []
    for region in args.regions:
        meta = REGIONI_TARGET.get(region.upper())
        if meta is None:
            log.warning("Regione sconosciuta: %s (skip)", region)
            continue
        log.info("=== %s (%s) ===", region, meta["anno"])
        res = run_region(region.upper(), meta, mapper, base22, cache, args.out)
        row = {
            "region": res.region, "anno": meta["anno"], "n_collegi": res.n_collegi,
            "rmse_pp": round(res.rmse_pp, 3) if not np.isnan(res.rmse_pp) else None,
            "r2": round(res.r2, 3) if not np.isnan(res.r2) else None,
            "note": res.note,
        }
        for c in CORE_COALITIONS:
            row[f"rmse_{c}"] = (round(res.rmse_per_coal[c], 3)
                                if c in res.rmse_per_coal else None)
        rows.append(row)

    rows.append({
        "region": "LAZIO (PoC)", "anno": "2023",
        "n_collegi": POC_LAZIO_BASELINE["n_collegi"],
        "rmse_pp": POC_LAZIO_BASELINE["rmse_pp"], "r2": POC_LAZIO_BASELINE["r2"],
        "note": "baseline validata",
        **{f"rmse_{c}": None for c in CORE_COALITIONS},
    })

    summary = pd.DataFrame(rows)
    sp = args.out / "rmse_summary.csv"
    summary.to_csv(sp, index=False, sep=";")
    log.info("Salvato %s", sp)
    print()
    print("=== RMSE SUMMARY (vs PoC Lazio: RMSE=3.91pp, R^2=0.618, N=11) ===")
    print(summary.to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
