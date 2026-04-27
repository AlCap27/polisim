"""polisim_istat.py - validazione swing model multi-regionale.

Estensione di ``polisim_build_collegi.py``. Scarica i dati Eligendo OpenData
delle regionali 2023-2024, aggrega per collegio uninominale Camera 2022 e
calcola RMSE/R^2 confrontandoli con la PoC Lazio (RMSE 3.91pp, R^2 0.618, N=11).

Repo: https://github.com/AlCap27/polisim
Cwd : C:\\Users\\work\\Dropbox\\Public\\Q-Italia\\

Esecuzione:
    python polisim_istat.py --out ./out --cache ./_cache
    python polisim_istat.py --regions LOMBARDIA UMBRIA
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
    r = requests.get(url, timeout=60, allow_redirects=True)
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
    return df.rename(columns={
        "COLLEGIO UNINOMINALE": "COLLUNINOM",
        "SIGLA PROVINCIA": "PROV_SIGLA",
        "CIRCOSCRIZIONE": "CIRC",
    })[["COMUNE", "PROV_SIGLA", "COLLUNINOM", "CIRC"]]


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
    args = p.parse_args(list(argv) if argv is not None else None)

    cache = CacheConfig(cache_dir=args.cache, enabled=not args.no_cache)
    args.out.mkdir(parents=True, exist_ok=True)

    log.info("Carico catalogo collegi Camera...")
    mapper = parse_collegi_map(fetch(COLLEGI_COMUNI_URL, cache,
                                     "elenco-collegi-comuni-camera.csv"))
    log.info("  %d righe (comune,collegio).", len(mapper))

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
