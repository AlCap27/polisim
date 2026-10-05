"""polisim_mrp.py - Modello MRP (Multilevel Regression and Poststratification)
per PoliSim, struttura multi-coalizione simmetrica.

Modello (4 coalizioni: CDX, CSX, M5S, ALTRI):

    votes[j] ~ DirichletMultinomial(N[j], alpha[j])

    log(alpha[j,k]) = mu_k
                     + beta_region_k[regione[j]]
                     + beta_area_k[area[j]]       # macro-area Nord-Ovest/Est, Centro, Sud, Isole
                     + gamma_k . X[j]              # feature ISTAT z-score

    beta_region_k ~ Normal(0, sigma_region_k)     (non-centered)
    beta_area_k   ~ Normal(0, sigma_area_k)
    sigma_*       ~ HalfNormal(1)
    mu, gamma     ~ Normal weakly informative

Input:
    data/collegi_uninominali_2022.csv              - quote politiche 2022
    data/celle_demografiche_collegi.csv            - 144 collegi x 18 celle ISTAT 2021 (v1)
    data/celle_demografiche_collegi_v2.csv         - 144 collegi x 54 celle ISTAT 2021 (v2, con occupazione)
    data/collegi_regionali_lombardia_2023.csv      - validation (opzionale)
    data/collegi_regionali_lazio_2023.csv          - validation (opzionale)
    data/collegi_regionali_emilia_romagna_2024.csv - validation (opzionale)
    data/collegi_regionali_liguria_2024.csv        - validation (opzionale)
    data/collegi_regionali_umbria_2024.csv         - validation (opzionale)
    data/collegi_regionali_basilicata_2024.csv     - validation (opzionale)

Output (per ogni collegio e coalizione):
    - mediana posteriore quota %
    - intervallo di credibilita' 90% (5%-95%)
    - P(CDX > CSX) congiunta posteriore
    - winner probabilities per le 4 coalizioni

Stack: PyMC 5.x, ArviZ, pandas, numpy. Venv: /opt/polisim_mrp/

CLI:
    python polisim_mrp.py --train
    python polisim_mrp.py --predict --collegio "LAZIO 1 - U01"
    python polisim_mrp.py --predict-all --out data/mrp_predictions.csv
    python polisim_mrp.py --validate
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd

# ---------------------------------------------------------------- costanti --
COALITIONS: List[str] = ["CDX", "CSX", "M5S", "ALTRI"]
K_COAL: int = len(COALITIONS)

# Mapping percentuali del CSV politiche -> coalizioni del modello.
# CENTRO (TZP/Azione/IV) confluisce in ALTRI insieme al residuo (liste minori
# che non sommano a 100). Cosi' la struttura e' simmetrica e chiusa.
PCT_COLS_INPUT: Dict[str, str] = {
    "CDX":   "pct_CDX",
    "CSX":   "pct_CSX",
    "M5S":   "pct_M5S",
    "ALTRI": "pct_CENTRO",  # base ALTRI, poi sommiamo il residuo a 100
}

# N proxy per Dirichlet-Multinomial (impatta solo la calibrazione di varianza).
# Ordine di grandezza realistico per votanti uninominali Camera in un collegio.
DEFAULT_N_VOTES: int = 50_000

# ---------------------------------------------------------------- logging --
logging.basicConfig(
    format="[%(asctime)s] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
    level=logging.INFO,
    stream=sys.stdout,
)
log = logging.getLogger("polisim_mrp")


# ---------------------------------------------------------------- helpers --
def _strip_accents(s: str) -> str:
    """Rimuove diacritici (es. Ü -> U)."""
    return "".join(c for c in unicodedata.normalize("NFKD", s)
                   if not unicodedata.combining(c))


def normalize_collegio_id(s: Any) -> Any:
    """Normalizza nome_collegio per join politiche <-> celle.

    politiche: ``ABRUZZO - U01``
    celle:     ``ABRUZZO - U01 (CHIETI)``  -> taglio prima di ' ('
    SUDTIROL:  politiche usa ``SUDTIROL`` (U semplice), celle ``SÜDTIROL`` (Ü)
               -> rimozione diacritici + uppercase.
    """
    if not isinstance(s, str):
        return s
    base = s.split(" (", 1)[0]
    return _strip_accents(base).strip().upper()


# ---------------------------------------------------------------- loaders --
def load_politiche(path: Path) -> pd.DataFrame:
    """Carica risultati politiche 2022 (collegio Camera uninominale).

    Aggiunge:
      - ``collegio_id_norm`` per join
      - ``pct_ALTRI_full = pct_CENTRO + residuo a 100`` (chiusura quote)
      - ``pct_*_norm`` quote rinormalizzate a 1 sulle 4 coalizioni.
    """
    df = pd.read_csv(path)
    df["collegio_id_norm"] = df["nome_collegio"].apply(normalize_collegio_id)
    # Numerizza
    for c in ("pct_CDX", "pct_CSX", "pct_M5S", "pct_CENTRO"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    # ALTRI = CENTRO + (100 - somma) (chiude la distribuzione su 4 categorie).
    sum4 = df[["pct_CDX", "pct_CSX", "pct_M5S", "pct_CENTRO"]].sum(axis=1)
    residuo = (100.0 - sum4).clip(lower=0.0)
    df["pct_ALTRI_full"] = df["pct_CENTRO"].fillna(0) + residuo
    return df


def load_celle(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["collegio_id_norm"] = df["collegio_id"].apply(normalize_collegio_id)
    return df


def load_validation(path: Path) -> Optional[pd.DataFrame]:
    """Carica validation set regionale (Lombardia 2023 / Lazio 2023). Schema
    atteso: colonne ``nome_collegio, pct_CDX, pct_CSX, pct_M5S, pct_CENTRO``
    (stesso layout del CSV politiche).
    """
    if not path.exists():
        log.warning("Validation file mancante: %s (skip)", path)
        return None
    df = load_politiche(path)
    return df


# Path al CSV Eligendo Camera 2022 sezione-livello-comune (per derivare N reale).
DEFAULT_CAMERA_CSV = Path("data/Camera_Italia_LivComune.csv")


def compute_n_votes_politiche(
    camera_csv: Path = DEFAULT_CAMERA_CSV,
) -> Dict[str, int]:
    """Aggrega i voti totali per collegio Camera uninominale 2022 dal CSV
    Eligendo ``Camera_Italia_LivComune.csv``.

    Schema: ``COLLUNINOM, COMUNE, DESCRLISTA, VOTILISTA, ...`` (livello
    candidato; le righe hanno VOTILISTA ripetuto per ogni candidato della
    lista). Aggregazione: max per (COLLUNINOM, COMUNE, DESCRLISTA), poi somma
    su comune+lista per ottenere il totale voti validi del collegio.

    Returns
    -------
    dict ``{collegio_id_norm: N_voti_totali}``. Vuoto se il file non esiste.
    """
    p = Path(camera_csv)
    if not p.exists():
        log.warning("[N] Camera CSV non trovato (proxy fallback): %s", p)
        return {}
    log.info("[N] aggrego voti politiche 2022 da %s", p.name)
    df = pd.read_csv(p, sep=";", dtype=str, encoding="latin-1", engine="python")
    df.columns = [c.strip() for c in df.columns]
    if "COLLUNINOM" not in df.columns or "VOTILISTA" not in df.columns:
        log.warning("[N] schema Camera CSV imprevisto: %s", df.columns.tolist())
        return {}
    df["COLLUNINOM"] = df["COLLUNINOM"].astype(str).str.strip()
    df["COMUNE"] = df["COMUNE"].astype(str).str.strip().str.upper()
    df["DESCRLISTA"] = df["DESCRLISTA"].astype(str).str.strip()
    df["VOTILISTA"] = pd.to_numeric(df["VOTILISTA"], errors="coerce").fillna(0)

    # Max per (collegio, comune, lista) per de-duplicare le righe candidato,
    # poi somma su comune+lista per il totale collegio.
    grp = (df.groupby(["COLLUNINOM", "COMUNE", "DESCRLISTA"], as_index=False)
              .agg(V=("VOTILISTA", "max")))
    tot = grp.groupby("COLLUNINOM")["V"].sum()
    out = {normalize_collegio_id(c): int(v) for c, v in tot.items()}
    log.info("[N] %d collegi con N reale (mean=%d, median=%d)",
             len(out), int(np.mean(list(out.values())) if out else 0),
             int(np.median(list(out.values())) if out else 0))
    return out


# ---------------------------------------------------------------- features -
def build_collegio_features(celle: pd.DataFrame) -> pd.DataFrame:
    """Aggrega le celle in feature di sintesi per collegio (z-score).

    Feature prodotte:
      - ``pct_giovani``   = quota popolazione 18-34
      - ``pct_anziani``   = quota popolazione 65+
      - ``pct_alta_istr`` = quota popolazione con titolo terziario
      - ``pct_femmine``   = quota popolazione femminile
      - ``pct_occupati``  = quota occupati sul totale 15-64 (solo celle v2 con
                            colonna ``occupazione``; omessa se assente -> 4 feature)

    Le quote derivano dai pesi normalizzati (peso totale per collegio = 1).
    Standardizziamo a media 0, sd 1 sull'insieme dei collegi per stabilita'.
    Compatibile sia con celle v1 (18 celle, senza colonna ``occupazione``) sia
    con celle v2 (54 celle, con colonna ``occupazione``).
    """
    has_occ = "occupazione" in celle.columns
    g = celle.groupby("collegio_id_norm", sort=True)
    feat = pd.DataFrame({
        "pct_giovani":   g.apply(lambda d: d.loc[d["fascia_eta"] == "18-34", "peso"].sum()),
        "pct_anziani":   g.apply(lambda d: d.loc[d["fascia_eta"] == "65+",   "peso"].sum()),
        "pct_alta_istr": g.apply(lambda d: d.loc[d["istruzione"] == "alta",  "peso"].sum()),
        "pct_femmine":   g.apply(lambda d: d.loc[d["genere"]     == "F",     "peso"].sum()),
    })
    if has_occ:
        mask_15_64 = celle["fascia_eta"].isin(["18-34", "35-64"])
        pop_15_64 = (celle[mask_15_64]
                     .groupby("collegio_id_norm")["peso"].sum()
                     .rename("pop_15_64"))
        pop_occ = (celle[mask_15_64 & (celle["occupazione"] == "occupato")]
                   .groupby("collegio_id_norm")["peso"].sum()
                   .rename("pop_occ"))
        feat["pct_occupati"] = (pop_occ / pop_15_64.replace(0, np.nan)).fillna(0.0)
    # z-score
    z = (feat - feat.mean()) / feat.std(ddof=0).replace(0, 1.0)
    z.columns = [f"{c}_z" for c in z.columns]
    return pd.concat([feat, z], axis=1)


def assemble_dataset(
    politiche: pd.DataFrame,
    celle: pd.DataFrame,
    n_votes_proxy: int = DEFAULT_N_VOTES,
    regionali: Optional[List[Tuple[str, pd.DataFrame]]] = None,
    n_votes_politiche: Optional[Dict[str, int]] = None,
) -> Tuple[pd.DataFrame, np.ndarray, np.ndarray, np.ndarray]:
    """Join (politiche [+ regionali]) con celle, costruisce X (features
    z-score + dummy ``is_politiche``), ``votes_obs`` e ``n_votes`` (per riga).

    Parameters
    ----------
    politiche : DataFrame caricato da ``load_politiche()``.
    celle     : DataFrame caricato da ``load_celle()``.
    regionali : opzionale, lista di ``(label, DataFrame)`` con quote regionali.
                Se fornita, vengono concatenate al training set con
                ``is_politiche=0`` (politiche=1). I collegi possono ripetersi
                tra politiche e diverse regionali: sono osservazioni indipendenti
                grazie a ``is_politiche`` che funge da intercept additivo.
                ``label`` viene usata per costruire l'``obs_id`` ed e' tipicamente
                lo stem del file (es. 'collegi_regionali_lazio_2023').
    n_votes_politiche : opzionale, dict ``{collegio_id_norm: N_voti}`` derivato
                da ``compute_n_votes_politiche()``. Se fornito, ogni osservazione
                politiche usa il suo N reale; le regionali usano la colonna
                ``voti_totali`` se presente nel CSV, altrimenti la media N
                regionale dai politiche (fallback robusto). Se None, ricade su
                ``n_votes_proxy`` per tutto.

    Returns
    -------
    df, X, votes_obs, n_votes : tuple
        ``n_votes`` ha shape ``(J,)`` dove J = numero osservazioni; va passato
        a ``pm.DirichletMultinomial(n=n_votes, ...)``.
    """
    feat = build_collegio_features(celle)

    # Politiche -> is_politiche=1
    pol = politiche.copy()
    pol["is_politiche"] = 1
    pol["fonte_idx"] = "politiche_2022"

    blocks = [pol]
    # Regionali -> is_politiche=0
    if regionali:
        for label, r in regionali:
            r2 = r.copy()
            r2["is_politiche"] = 0
            r2["fonte_idx"] = label
            blocks.append(r2)
    full = pd.concat(blocks, ignore_index=True)

    df = full.merge(feat, left_on="collegio_id_norm", right_index=True,
                    how="inner")

    # -- Arricchimento ISTAT 2021: pct_stranieri, pct_extra_ue ----------------
    # Features validate su 14 collegi Lazio (r=-0.800, r=-0.792 vs errore MRP)
    _istat_path = Path(__file__).parent / "data" / "istat_features_lazio.json"
    if _istat_path.exists():
        import json as _json
        _istat = _json.load(open(_istat_path))
        _istat_df = pd.DataFrame(_istat).T.rename_axis("collegio_id_norm").reset_index()
        _istat_df = _istat_df.astype({c: float for c in _istat_df.columns if c != "collegio_id_norm"})
        df = df.merge(_istat_df, on="collegio_id_norm", how="left")
        for _col in ["pct_stranieri", "pct_extra_ue"]:
            if _col in df.columns:
                _mu = df[_col].mean()
                _sd = df[_col].std(ddof=0)
                df[f"{_col}_z"] = ((df[_col] - _mu) / (_sd if _sd > 0 else 1.0)).fillna(0.0)
        log.info("ISTAT features: pct_stranieri_z, pct_extra_ue_z aggiunte")
    else:
        log.warning("istat_features_lazio.json non trovato — modello senza nuove features")
    # ---------------------------------------------------------------------------

    # Drop righe con quote mancanti o area_macro nulla
    df = df.dropna(subset=["pct_CDX", "pct_CSX", "pct_M5S", "pct_ALTRI_full",
                           "regione", "macro_area"]).reset_index(drop=True)

    # ID univoco per ogni osservazione (un collegio puo' apparire in piu' fonti).
    df["obs_id"] = df["collegio_id_norm"].astype(str) + "__" + df["fonte_idx"].astype(str)

    # Quote a 4 coalizioni rinormalizzate (somma = 1).
    pcts = df[["pct_CDX", "pct_CSX", "pct_M5S", "pct_ALTRI_full"]].to_numpy(dtype=float)
    pcts = pcts / pcts.sum(axis=1, keepdims=True).clip(min=1e-9)

    # ---- N voti per riga (Dirichlet-Multinomial size) -----------------
    n_votes_arr = np.full(len(df), n_votes_proxy, dtype=np.int64)
    if n_votes_politiche:
        # 1) Politiche: N reale dal CSV Camera 2022 (lookup su collegio_id_norm)
        pol_mask = (df["is_politiche"] == 1).to_numpy()
        for i in np.where(pol_mask)[0]:
            n = n_votes_politiche.get(df["collegio_id_norm"].iat[i])
            if n is not None and n > 0:
                n_votes_arr[i] = int(n)

        # 2) Media N politiche per regione (per stima fallback regionali)
        avg_pol_per_regione: Dict[str, int] = {}
        for reg in df.loc[pol_mask, "regione"].unique():
            mask_r = pol_mask & (df["regione"] == reg).to_numpy()
            if mask_r.any():
                avg_pol_per_regione[reg] = int(np.mean(n_votes_arr[mask_r]))
        avg_pol_global = (int(np.mean([v for v in avg_pol_per_regione.values()]))
                          if avg_pol_per_regione else n_votes_proxy)

        # 3) Regionali: usa voti_totali del CSV se presente, altrimenti media
        #    regionale (preserva la magnitudine corretta del collegio Camera
        #    nel contesto regionale).
        reg_mask = (df["is_politiche"] == 0).to_numpy()
        has_voti_tot = "voti_totali" in df.columns
        for i in np.where(reg_mask)[0]:
            n_real = None
            if has_voti_tot:
                v = df["voti_totali"].iat[i]
                if pd.notna(v):
                    try:
                        n_real = int(v)
                    except (ValueError, TypeError):
                        n_real = None
            if n_real and n_real > 0:
                n_votes_arr[i] = n_real
            else:
                n_votes_arr[i] = avg_pol_per_regione.get(
                    df["regione"].iat[i], avg_pol_global)

    # Conteggi interi per Dirichlet-Multinomial (vettorializzati su N per riga).
    votes_obs = np.round(pcts * n_votes_arr[:, None]).astype(np.int64)
    delta = n_votes_arr - votes_obs.sum(axis=1)
    votes_obs[:, -1] += delta

    # is_politiche: covariata fissa binaria (1 politiche, 0 regionali). Il
    # coefficiente gamma_k cattura il gap sistematico tra le due tipologie.
    df["is_politiche"] = df["is_politiche"].astype(float)

    feat_cols = [c for c in df.columns if c.endswith("_z")] + ["is_politiche"]
    X = df[feat_cols].to_numpy(dtype=float)
    return df, X, votes_obs, n_votes_arr


# ---------------------------------------------------------------- modello --
def build_mrp_model(
    votes_obs: np.ndarray,
    X: np.ndarray,
    region_idx: np.ndarray,
    area_idx: np.ndarray,
    coords: Dict[str, List[Any]],
    n_votes: "int | np.ndarray" = DEFAULT_N_VOTES,
):
    """Costruisce il modello PyMC. Parametrizzazione non-centered per stabilita'
    MCMC (sigma * raw normal).

    ``n_votes`` puo' essere uno scalare (legacy) o un array shape ``(J,)`` con
    il totale voti reale per ciascuna osservazione. Per i collegi Camera 2022
    si aggira intorno a 185k (mean), ben oltre il proxy 50k.
    """
    import pymc as pm
    import pytensor.tensor as pt

    with pm.Model(coords=coords) as model:
        # Hyperparametri (per coalizione)
        sigma_region = pm.HalfNormal("sigma_region", 1.0, dims="coalition")
        sigma_area   = pm.HalfNormal("sigma_area",   1.0, dims="coalition")

        # Intercept per coalizione (debolmente informativo)
        mu = pm.Normal("mu", mu=0.0, sigma=2.0, dims="coalition")

        # Random effects (non-centered)
        beta_region_raw = pm.Normal("beta_region_raw", 0.0, 1.0,
                                    dims=("region", "coalition"))
        beta_region = pm.Deterministic(
            "beta_region", beta_region_raw * sigma_region,
            dims=("region", "coalition"))

        beta_area_raw = pm.Normal("beta_area_raw", 0.0, 1.0,
                                  dims=("area", "coalition"))
        beta_area = pm.Deterministic(
            "beta_area", beta_area_raw * sigma_area,
            dims=("area", "coalition"))

        # Coefficienti feature ISTAT
        gamma = pm.Normal("gamma", 0.0, 1.0, dims=("feature", "coalition"))

        # Linear predictor (J x K)
        log_alpha = (mu[None, :]
                     + beta_region[region_idx, :]
                     + beta_area[area_idx, :]
                     + pt.dot(X, gamma))
        alpha = pm.Deterministic(
            "alpha", pt.exp(log_alpha), dims=("collegio", "coalition"))

        # Likelihood Dirichlet-Multinomial
        pm.DirichletMultinomial(
            "votes", n=n_votes, a=alpha,
            observed=votes_obs,
            dims=("collegio", "coalition"),
        )

    return model


# ---------------------------------------------------------------- train ---
def discover_regionali_csvs(politiche_csv: Path) -> List[Path]:
    """Trova automaticamente tutti i ``collegi_regionali_*.csv`` nella stessa
    directory di ``politiche_csv`` (default ``data/``).
    """
    base = Path(politiche_csv).parent
    return sorted(base.glob("collegi_regionali_*.csv"))


def train(
    politiche_csv: Path,
    celle_csv: Path,
    trace_dir: Path,
    n_samples: int = 2000,
    n_tune: int = 1000,
    n_chains: int = 4,
    target_accept: float = 0.9,
    seed: int = 42,
) -> Path:
    """Fitta il modello MRP. Salva trace ArviZ NetCDF + metadata JSON.

    Auto-discovery: tutti i file ``data/collegi_regionali_*.csv`` (rilevati nella
    stessa directory di ``politiche_csv``) vengono caricati come osservazioni
    aggiuntive. Una covariata fissa binaria ``is_politiche`` (1=politiche 2022,
    0=regionali) cattura lo shift sistematico tra le due tipologie di elezione,
    mentre i random effects per regione/area sono condivisi (pooling
    cross-elezione).
    """
    import pymc as pm
    import arviz as az

    log.info("[train] carico %s", politiche_csv)
    politiche = load_politiche(politiche_csv)
    log.info("[train] carico %s", celle_csv)
    celle = load_celle(celle_csv)

    regionali_csvs = discover_regionali_csvs(politiche_csv)
    regionali_pairs: List[Tuple[str, pd.DataFrame]] = []
    for rp in regionali_csvs:
        log.info("[train] carico regionale %s", rp.name)
        regionali_pairs.append((rp.stem, load_politiche(rp)))
    log.info("[train] auto-discovered %d CSV regionali in %s",
             len(regionali_pairs), politiche_csv.parent)

    # N voti reali per collegio politiche (Camera_Italia_LivComune.csv).
    n_votes_politiche = compute_n_votes_politiche()

    df, X, votes_obs, n_votes_arr = assemble_dataset(
        politiche, celle, regionali=regionali_pairs or None,
        n_votes_politiche=n_votes_politiche or None,
    )
    log.info("[train] dataset: %d osservazioni (%d politiche + %d regionali), "
             "%d feature, %d coalizioni",
             len(df), int((df['is_politiche']==1).sum()),
             int((df['is_politiche']==0).sum()), X.shape[1], K_COAL)
    log.info("[train] N voti per riga: min=%d, mean=%d, max=%d (vs proxy=%d)",
             int(n_votes_arr.min()), int(n_votes_arr.mean()),
             int(n_votes_arr.max()), DEFAULT_N_VOTES)

    regions = sorted(df["regione"].unique())
    areas   = sorted(df["macro_area"].unique())
    region_idx = df["regione"].map({r: i for i, r in enumerate(regions)}).to_numpy()
    area_idx   = df["macro_area"].map({a: i for i, a in enumerate(areas)}).to_numpy()
    # feat_cols include sia le z-score ISTAT sia is_politiche
    feat_cols  = [c for c in df.columns
                   if c.endswith("_z") or c == "is_politiche"]

    coords = {
        "collegio":  df["obs_id"].tolist(),
        "coalition": COALITIONS,
        "region":    regions,
        "area":      areas,
        "feature":   feat_cols,
    }

    model = build_mrp_model(votes_obs, X, region_idx, area_idx, coords,
                            n_votes=n_votes_arr)

    log.info("[train] sampling: %d draws x %d chains, tune=%d",
             n_samples, n_chains, n_tune)
    with model:
        idata = pm.sample(
            draws=n_samples, tune=n_tune, chains=n_chains,
            target_accept=target_accept, random_seed=seed,
            return_inferencedata=True,
        )

    trace_dir.mkdir(parents=True, exist_ok=True)
    nc_path = trace_dir / "trace.nc"
    az.to_netcdf(idata, nc_path)
    meta = {
        "regions": regions, "areas": areas, "feature_cols": feat_cols,
        "coalitions": COALITIONS, "n_collegi": len(df),
        "n_samples": n_samples, "n_chains": n_chains, "n_tune": n_tune,
        "n_votes_proxy": DEFAULT_N_VOTES,
        "n_votes_real_used": True,
        "n_votes_min": int(n_votes_arr.min()),
        "n_votes_mean": int(n_votes_arr.mean()),
        "n_votes_max": int(n_votes_arr.max()),
    }
    (trace_dir / "meta.json").write_text(json.dumps(meta, indent=2,
                                                     ensure_ascii=False))
    log.info("[train] trace salvato: %s", nc_path)

    # Diagnostica veloce
    summ = az.summary(idata, var_names=["mu", "sigma_region", "sigma_area"],
                       round_to=3)
    log.info("[train] diagnostica top-level:\n%s", summ.to_string())
    return nc_path


# ---------------------------------------------------------------- predict -
def _load_trace(trace_dir: Path):
    import arviz as az
    nc = trace_dir / "trace.nc"
    if not nc.exists():
        raise FileNotFoundError(f"Trace non trovato: {nc}. Esegui --train prima.")
    return az.from_netcdf(nc)


def _shares_from_alpha(alpha_xr) -> "xr.DataArray":
    """alpha (chain, draw, collegio, coalition) -> shares che sommano a 1 su coalition."""
    return alpha_xr / alpha_xr.sum(dim="coalition")


def _resolve_obs_id(coords_coll: List[str], norm: str,
                    fonte_pref: str = "politiche_2022") -> str:
    """Da un nome normalizzato ``norm`` (es. 'LAZIO 1 - U01') al coord 'collegio'
    nel trace (formato ``<norm>__<fonte>``). Default: usa l'osservazione politiche
    come baseline per la previsione futura. Fallback: prima entry trovata.
    """
    target = f"{norm}__{fonte_pref}"
    if target in coords_coll:
        return target
    matches = [c for c in coords_coll if c.startswith(norm + "__")]
    if not matches:
        raise ValueError(
            f"Collegio '{norm}' non presente nel trace. "
            f"Esempi disponibili: {coords_coll[:5]}")
    return matches[0]


def predict_collegio(
    trace_dir: Path,
    collegio_id: str,
    politiche_csv: Optional[Path] = None,
    celle_csv: Optional[Path] = None,
    fonte: str = "politiche_2022",
) -> Dict[str, Any]:
    """Inferenza posteriore per un singolo collegio.

    Se il trace contiene piu' osservazioni dello stesso collegio (es. politiche
    2022 + regionali 2024), seleziona la fonte indicata da ``fonte`` (default
    politiche, scenario piu' rilevante per previsioni elettorali nazionali).
    """
    idata = _load_trace(trace_dir)
    norm = normalize_collegio_id(collegio_id)
    coords_coll = idata.posterior.coords["collegio"].values.tolist()
    obs_id = _resolve_obs_id(coords_coll, norm, fonte_pref=fonte)

    alpha = idata.posterior["alpha"].sel(collegio=obs_id)
    shares = _shares_from_alpha(alpha)  # (chain, draw, coalition)

    out: Dict[str, Any] = {"collegio_id": collegio_id, "collegio_norm": norm,
                           "obs_id": obs_id, "shares": {}}
    for k in COALITIONS:
        s = shares.sel(coalition=k).values.flatten() * 100.0
        out["shares"][k] = {
            "median":  float(np.median(s)),
            "ci90_lo": float(np.percentile(s, 5)),
            "ci90_hi": float(np.percentile(s, 95)),
            "mean":    float(s.mean()),
        }

    s_cdx = shares.sel(coalition="CDX").values.flatten()
    s_csx = shares.sel(coalition="CSX").values.flatten()
    out["P(CDX>CSX)"] = float((s_cdx > s_csx).mean())

    arr = shares.values.reshape(-1, K_COAL)
    winners = arr.argmax(axis=1)
    out["winner_probs"] = {
        COALITIONS[i]: float((winners == i).mean()) for i in range(K_COAL)
    }
    return out


def predict_all(
    trace_dir: Path,
    out_csv: Optional[Path] = None,
    fonte_filter: Optional[str] = "politiche_2022",
) -> pd.DataFrame:
    """Inferenza posteriore per tutti i collegi del trace.

    Se ``fonte_filter`` e' fornita, restituisce solo le osservazioni con quella
    fonte (default 'politiche_2022'). Se None, restituisce tutte le osservazioni
    (utile per ispezionare politiche+regionali insieme).
    """
    idata = _load_trace(trace_dir)
    alpha = idata.posterior["alpha"]
    shares = _shares_from_alpha(alpha)

    rows: List[Dict[str, Any]] = []
    coords_coll = idata.posterior.coords["collegio"].values.tolist()
    for j, name in enumerate(coords_coll):
        if "__" in str(name):
            collegio_part, fonte_part = str(name).rsplit("__", 1)
        else:
            collegio_part, fonte_part = str(name), ""
        if fonte_filter is not None and fonte_part != fonte_filter:
            continue

        s_j = shares.isel(collegio=j)            # (chain, draw, coalition)
        arr = s_j.values.reshape(-1, K_COAL)
        winners = arr.argmax(axis=1)
        s_cdx = arr[:, COALITIONS.index("CDX")]
        s_csx = arr[:, COALITIONS.index("CSX")]

        row: Dict[str, Any] = {
            "collegio": collegio_part, "fonte": fonte_part,
            "P_CDX_gt_CSX": float((s_cdx > s_csx).mean()),
        }
        for i, k in enumerate(COALITIONS):
            sk = arr[:, i] * 100.0
            row[f"{k}_median"]  = float(np.median(sk))
            row[f"{k}_ci90_lo"] = float(np.percentile(sk, 5))
            row[f"{k}_ci90_hi"] = float(np.percentile(sk, 95))
            row[f"P_winner_{k}"] = float((winners == i).mean())
        rows.append(row)

    out_df = pd.DataFrame(rows)
    if out_csv is not None:
        out_csv.parent.mkdir(parents=True, exist_ok=True)
        out_df.to_csv(out_csv, index=False)
        log.info("[predict-all] salvato %s (%d collegi, fonte=%s)",
                 out_csv, len(out_df), fonte_filter or "all")
    return out_df


# ---------------------------------------------------------------- baseline -
def baseline_ols(
    df_train: pd.DataFrame, X_cols: List[str],
) -> Tuple[Any, Any]:
    """OLS multi-output: per ogni coalizione regredisce % su X (z-score) +
    dummy regione + dummy macro-area. Coefficienti separati per coalizione.
    """
    from sklearn.linear_model import LinearRegression
    from sklearn.preprocessing import OneHotEncoder

    enc = OneHotEncoder(handle_unknown="ignore", sparse_output=False)
    cat = enc.fit_transform(df_train[["regione", "macro_area"]].astype(str))
    Xfull = np.hstack([df_train[X_cols].to_numpy(dtype=float), cat])
    y = df_train[["pct_CDX", "pct_CSX", "pct_M5S", "pct_ALTRI_full"]].to_numpy(dtype=float)
    model = LinearRegression().fit(Xfull, y)
    return model, enc


def _ols_predict(model, enc, df: pd.DataFrame, X_cols: List[str]) -> np.ndarray:
    cat = enc.transform(df[["regione", "macro_area"]].astype(str))
    Xfull = np.hstack([df[X_cols].to_numpy(dtype=float), cat])
    return model.predict(Xfull)


def rmse_per_coalition(y_true: np.ndarray, y_pred: np.ndarray) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for i, k in enumerate(COALITIONS):
        e = y_true[:, i] - y_pred[:, i]
        out[k] = float(np.sqrt(np.mean(e ** 2)))
    out["overall"] = float(np.sqrt(np.mean((y_true - y_pred) ** 2)))
    return out


# ---------------------------------------------------------------- validate -
def validate(
    trace_dir: Path,
    politiche_csv: Path,
    celle_csv: Path,
    validation_csvs: List[Path],
    out_csv: Optional[Path] = None,
) -> pd.DataFrame:
    """Confronto MRP vs OLS baseline su validation set regionali.

    1. Re-fit OLS su train (politiche 2022).
    2. Per ogni validation CSV: predict MRP (mediana posteriore) e OLS, calcola
       RMSE per coalizione e overall.
    """
    politiche = load_politiche(politiche_csv)
    celle = load_celle(celle_csv)
    feat = build_collegio_features(celle)

    df_train, _X_train, _votes_train, _n_votes = assemble_dataset(politiche, celle)
    feat_cols = [c for c in df_train.columns if c.endswith("_z")]
    ols_model, enc = baseline_ols(df_train, feat_cols)

    mrp_preds = predict_all(trace_dir)  # mediana per ogni collegio del trace

    rows: List[Dict[str, Any]] = []
    for vp in validation_csvs:
        v = load_validation(vp)
        if v is None:
            continue
        v = v.merge(feat, left_on="collegio_id_norm", right_index=True,
                    how="inner")
        # Arricchimento ISTAT validation set (stesso trattamento di build_dataset)
        _istat_path_v = Path(__file__).parent / "data" / "istat_features_lazio.json"
        if _istat_path_v.exists():
            import json as _jv
            _iv = _jv.load(open(_istat_path_v))
            _iv_df = pd.DataFrame(_iv).T.rename_axis("collegio_id_norm").reset_index()
            _iv_df = _iv_df.astype({c: float for c in _iv_df.columns if c != "collegio_id_norm"})
            v = v.merge(_iv_df, on="collegio_id_norm", how="left")
            for _col in ["pct_stranieri", "pct_extra_ue"]:
                if _col in v.columns:
                    _mu = v[_col].mean()
                    _sd = v[_col].std(ddof=0)
                    v[f"{_col}_z"] = ((v[_col] - _mu) / (_sd if _sd > 0 else 1.0)).fillna(0.0)

        v = v.dropna(subset=["pct_CDX", "pct_CSX", "pct_M5S",
                              "pct_ALTRI_full", "regione", "macro_area"])
        if v.empty:
            log.warning("[validate] %s: nessun match con celle", vp.name)
            continue

        y_true = v[["pct_CDX", "pct_CSX", "pct_M5S",
                    "pct_ALTRI_full"]].to_numpy(dtype=float)
        y_ols = _ols_predict(ols_model, enc, v, feat_cols)

        # MRP: lookup mediana posteriore per ogni collegio del validation
        mrp_lookup = mrp_preds.set_index("collegio")
        cols_med = [f"{k}_median" for k in COALITIONS]
        y_mrp = []
        ok_mask = []
        for nm in v["collegio_id_norm"]:
            if nm in mrp_lookup.index:
                y_mrp.append(mrp_lookup.loc[nm, cols_med].to_numpy(dtype=float))
                ok_mask.append(True)
            else:
                y_mrp.append(np.full(K_COAL, np.nan))
                ok_mask.append(False)
        y_mrp = np.asarray(y_mrp)
        mask = np.asarray(ok_mask)
        if not mask.any():
            log.warning("[validate] %s: 0 collegi nel trace MRP", vp.name)
            continue

        rmse_ols = rmse_per_coalition(y_true[mask], y_ols[mask])
        rmse_mrp = rmse_per_coalition(y_true[mask], y_mrp[mask])

        for k in COALITIONS + ["overall"]:
            rows.append({
                "validation": vp.stem, "n_collegi": int(mask.sum()),
                "coalition": k,
                "rmse_OLS": round(rmse_ols[k], 3),
                "rmse_MRP": round(rmse_mrp[k], 3),
                "delta_pp": round(rmse_mrp[k] - rmse_ols[k], 3),
            })

    out_df = pd.DataFrame(rows)
    if out_csv is not None:
        out_csv.parent.mkdir(parents=True, exist_ok=True)
        out_df.to_csv(out_csv, index=False)
    print()
    print("=== VALIDATION RMSE (pp): MRP vs OLS baseline ===")
    print(out_df.to_string(index=False))
    return out_df


# ---------------------------------------------------------------- CLI -----
def main(argv: Optional[Iterable[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("--train", action="store_true", help="Fit MRP su politiche 2022.")
    p.add_argument("--predict", action="store_true",
                   help="Inferenza posteriore su un singolo collegio.")
    p.add_argument("--predict-all", action="store_true",
                   help="Esporta CSV con predizioni per tutti i collegi.")
    p.add_argument("--validate", action="store_true",
                   help="RMSE su validation set vs OLS baseline.")
    p.add_argument("--collegio", type=str, default=None,
                   help="Nome collegio (es. 'LAZIO 1 - U01').")
    p.add_argument("--politiche", type=Path,
                   default=Path("data/collegi_uninominali_2022.csv"))
    p.add_argument("--celle", type=Path,
                   default=Path("data/celle_demografiche_collegi.csv"))
    p.add_argument("--validation-csvs", nargs="*", type=Path, default=[
        Path("data/collegi_regionali_lombardia_2023.csv"),
        Path("data/collegi_regionali_lazio_2023.csv"),
        Path("data/collegi_regionali_emilia_romagna_2024.csv"),
        Path("data/collegi_regionali_liguria_2024.csv"),
        Path("data/collegi_regionali_umbria_2024.csv"),
        Path("data/collegi_regionali_basilicata_2024.csv"),
    ])
    p.add_argument("--trace-dir", type=Path, default=Path("data/mrp_trace"))
    p.add_argument("--out", type=Path, default=None,
                   help="CSV di output per --predict-all e --validate.")
    p.add_argument("--draws", type=int, default=2000,
                   help="Numero di sample posteriori per chain (default 2000).")
    p.add_argument("--tune", type=int, default=1000,
                   help="Sample di warmup/tune per chain (default 1000).")
    p.add_argument("--chains", type=int, default=4)
    p.add_argument("--target-accept", type=float, default=0.9,
                   help="Target acceptance rate NUTS (default 0.9; aumenta a "
                        "0.95-0.99 in caso di divergenze).")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--fonte", type=str, default="politiche_2022",
                   help="Fonte da selezionare per --predict (default "
                        "'politiche_2022', baseline elezioni nazionali).")
    args = p.parse_args(list(argv) if argv is not None else None)

    if args.train:
        # Auto-discovery: i CSV regionali in data/ vengono caricati
        # automaticamente come osservazioni aggiuntive. Vedi train().
        train(args.politiche, args.celle, args.trace_dir,
              n_samples=args.draws, n_tune=args.tune,
              n_chains=args.chains, target_accept=args.target_accept,
              seed=args.seed)
        return 0

    if args.predict:
        if not args.collegio:
            p.error("--predict richiede --collegio NOME")
        result = predict_collegio(args.trace_dir, args.collegio,
                                  args.politiche, args.celle,
                                  fonte=args.fonte)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0

    if args.predict_all:
        out = args.out or Path("data/mrp_predictions.csv")
        predict_all(args.trace_dir, out)
        return 0

    if args.validate:
        out = args.out or Path("data/mrp_validation_summary.csv")
        validate(args.trace_dir, args.politiche, args.celle,
                 args.validation_csvs, out)
        return 0

    p.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
