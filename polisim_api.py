"""
PoliSim — FastAPI Backend v2.1
==============================
v2.1: Integrazione dati reali ISTAT 2021 + Eligendo 2022 per collegi Camera Lazio.
      Margini calibrati su risultati reali invece di stime macro-area.
"""
import os, json, time
from datetime import datetime
from collections import defaultdict
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import anthropic

# ── Caricamento sondaggi live da PolitPro ─────────────────────────
def _carica_sondaggi_live():
    """Legge sondaggi_correnti.json aggiornato ogni mattina dal scraper PolitPro."""
    try:
        path = "/opt/polisim/data/sondaggi_correnti.json"
        if os.path.exists(path):
            with open(path) as f:
                d = json.load(f)
            p = d.get("partiti", {})
            if p.get("AVS") and p.get("FDI"):
                return {
                    "FDI_pct": p.get("FDI", 28.3),
                    "PD_pct":  p.get("PD",  22.2),
                    "M5S_pct": p.get("M5S", 12.6),
                    "LEGA_pct":p.get("LEGA", 7.0),
                    "FI_pct":  p.get("FI",   8.2),
                    "AVS_pct": p.get("AVS",  6.5),
                    "AZ_IV_pct": round(p.get("AZ", 3.1) + p.get("IV", 2.4), 1),
                    "FN_pct":  p.get("FN",   3.4),
                    "ALTRI_pct":p.get("ALTRI", 3.6),
                }
    except Exception:
        pass
    return None

app = FastAPI(title="PoliSim API", version="2.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://polisim.dev",
        "https://www.polisim.dev",
        "http://localhost:3000",
        "http://localhost:5050",
        "null",
    ],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

CLAUDE_MODEL = "claude-sonnet-4-6"

RATE_LIMIT  = 10
RATE_WINDOW = 3600
_rate_store: dict = defaultdict(list)

def check_rate_limit(ip: str):
    now   = time.time()
    start = now - RATE_WINDOW
    _rate_store[ip] = [t for t in _rate_store[ip] if t > start]
    if len(_rate_store[ip]) >= RATE_LIMIT:
        raise HTTPException(429, "Troppe richieste. Riprova tra un'ora.")
    _rate_store[ip].append(now)

ADMIN_TOKEN = os.environ.get("POLISIM_ADMIN_TOKEN", "admin-polisim-2026")

LOG_FILE = "/opt/polisim/logs/simulazioni.jsonl"
os.makedirs("/opt/polisim/logs", exist_ok=True)

def log_sim(tipo, dominio, tema, segmento, tester="", ip=""):
    entry = {
        "ts": datetime.now().isoformat(), "tipo": tipo,
        "dominio": dominio, "tema": tema[:80],
        "segmento": segmento[:50], "tester": tester, "ip": ip,
    }
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

# ── Segmenti predefiniti ──────────────────────────────────────────
SEGMENTI = {
    "giovani_precari_sud": {
        "label": "Giovani precari del Sud",
        "eta": "18-30 anni", "area": "Sud e Isole",
        "istruzione": "diploma o laurea non spendibile",
        "lavoro": "precario, disoccupato, NEET",
        "preoccupazioni_core": ["non riuscire a costruire un futuro qui","dover emigrare per sopravvivere","lavoro a chiamata e partite IVA a 500 euro"],
        "barriere_comunicative": ["sfiducia totale nella politica","linguaggio politico percepito come vuoto"],
        "tone_of_voice": "diretto, senza retorica, concreto",
        "esempio_vita": "laurea in tasca, lavora part-time, pensa di andare in Germania",
        "trigger_positivi": ["concretezza", "numeri reali", "rabbia legittimata"],
        "trigger_negativi": ["paternalismo", "retorica del sacrificio"],
    },
    "casalinghe_sud": {
        "label": "Casalinghe disoccupate del Sud",
        "eta": "40-60 anni", "area": "Sud e Isole",
        "istruzione": "media inferiore o diploma",
        "lavoro": "fuori dal mercato formale, lavoro di cura",
        "preoccupazioni_core": ["arrivare a fine mese","figli emigrati o disoccupati","sanità che non funziona","dipendenza economica"],
        "barriere_comunicative": ["non si sente il target della politica","diffidenza verso chi parla bene","linguaggio tecnico non accessibile"],
        "tone_of_voice": "caldo, rispettoso, riconosce il lavoro invisibile",
        "esempio_vita": "tre figli, uno in Germania — ogni mese fa i conti",
        "trigger_positivi": ["riconoscimento lavoro di cura", "benefici in euro", "sanità e figli"],
        "trigger_negativi": ["linguaggio ideologico", "promesse vaghe", "tono paternalistico"],
    },
    "operai_nord": {
        "label": "Operai e artigiani del Nord",
        "eta": "35-55 anni", "area": "Nord Italia",
        "istruzione": "diploma tecnico",
        "lavoro": "manifattura, artigianato, PMI",
        "preoccupazioni_core": ["automazione che prende il mio posto","tasse che soffocano chi lavora","pensione sempre più lontana"],
        "barriere_comunicative": ["diffidenza verso la sinistra","retorica ecologista nemica del lavoro"],
        "tone_of_voice": "rispettoso del lavoro manuale, concreto",
        "esempio_vita": "turno dalle 6, mutuo da pagare, figlio all'università",
        "trigger_positivi": ["sicurezza del lavoro", "meno tasse", "rispetto per chi lavora"],
        "trigger_negativi": ["assistenzialismo", "ambientalismo punitivo"],
    },
    "laureati_urbani": {
        "label": "Laureati urbani progressisti",
        "eta": "25-45 anni", "area": "Grandi città",
        "istruzione": "laurea o master",
        "lavoro": "professioni intellettuali, tech, freelance",
        "preoccupazioni_core": ["qualità della democrazia","cambiamento climatico","affitti insostenibili"],
        "barriere_comunicative": ["scetticismo verso partiti nuovi","esigenza di dati non slogan"],
        "tone_of_voice": "diretto, basato su dati, rispettoso dell'intelligenza",
        "esempio_vita": "startup, 1200€ affitto, vota ma è deluso",
        "trigger_positivi": ["dati verificabili", "coerenza tra parole e azioni"],
        "trigger_negativi": ["semplificazioni", "populismo"],
    },
    "astensionisti_valoriali": {
        "label": "Astensionisti valoriali",
        "eta": "25-50 anni", "area": "Tutto il paese",
        "istruzione": "varia, spesso media-alta",
        "lavoro": "vario",
        "preoccupazioni_core": ["corruzione sistemica","guerra e riarmo","crisi democratica"],
        "barriere_comunicative": ["non vota da anni per principio","cerca discontinuità radicale"],
        "tone_of_voice": "onesto sui limiti, trasparente",
        "esempio_vita": "ha smesso di votare nel 2018",
        "trigger_positivi": ["onestà sui limiti", "trasparenza radicale"],
        "trigger_negativi": ["promesse mirabolanti", "attacchi agli avversari"],
    },
    "pensionati_centro_nord": {
        "label": "Pensionati del Centro-Nord",
        "eta": "65+ anni", "area": "Centro-Nord",
        "istruzione": "varia", "lavoro": "pensionati",
        "preoccupazioni_core": ["sanità che peggiora","sicurezza","pensione che non basta"],
        "barriere_comunicative": ["fedeltà a partiti storici","diffidenza verso il nuovo"],
        "tone_of_voice": "rassicurante, rispettoso dell'esperienza",
        "esempio_vita": "1100€ pensione, 8 mesi di attesa per la visita",
        "trigger_positivi": ["sanità concreta", "sicurezza", "rispetto per l'esperienza"],
        "trigger_negativi": ["cambiamento radicale", "tecnologia imposta"],
    },
    "donatori_lasciti": {
        "label": "Potenziali donatori lascito testamentario",
        "eta": "60-80 anni", "area": "Centro-Nord",
        "istruzione": "media-alta", "lavoro": "pensionati, ex professionisti",
        "preoccupazioni_core": ["eredità di senso dopo la vita","fiducia nell'organizzazione","non creare problemi ai familiari"],
        "barriere_comunicative": ["argomento delicato — morte non si nomina","sfiducia verso ONG dopo scandali","complessità percepita atto notarile"],
        "tone_of_voice": "caldo, dignitoso, parla di continuità — mai di morte",
        "esempio_vita": "ex insegnante, dona da anni, vuole che qualcosa duri",
        "trigger_positivi": ["continuità impegno", "semplicità atto", "fiducia costruita"],
        "trigger_negativi": ["linguaggio burocratico", "pressione", "menzione morte"],
    },
    "responsabili_csr": {
        "label": "Responsabili CSR aziende",
        "eta": "35-55 anni", "area": "Grandi città",
        "istruzione": "laurea, spesso master", "lavoro": "manager, budget CSR",
        "preoccupazioni_core": ["ritorno reputazionale dimostrabile al CDA","evitare greenwashing","rendicontazione ESG precisa"],
        "barriere_comunicative": ["ONG percepite come lente e opache","difficoltà misurare impatto in KPI"],
        "tone_of_voice": "professionale, basato su dati, parla la lingua del business",
        "esempio_vita": "presenta 3 partnership CSR al board — vuole dati e semplicità",
        "trigger_positivi": ["ROI reputazionale misurabile", "report ESG-ready", "processo semplice"],
        "trigger_negativi": ["tempi lunghi", "solo emozione senza dati"],
    },
    "donne_25_40_urbane": {
        "label": "Donne 25-40 urbane consapevoli",
        "eta": "25-40 anni", "area": "Grandi città",
        "istruzione": "diploma o laurea", "lavoro": "dipendente o freelance, reddito medio",
        "preoccupazioni_core": ["ingredienti sicuri e trasparenti","accessibilità — non solo per chi se lo può permettere","autenticità del brand"],
        "barriere_comunicative": ["sfinimento da green/pink washing","sfiducia verso promesse senza dati"],
        "tone_of_voice": "autentico, non perfetto, scelte reali non ideali",
        "esempio_vita": "legge gli ingredienti, segue creator beauty consapevole",
        "trigger_positivi": ["trasparenza ingredienti", "prezzo accessibile certificato"],
        "trigger_negativi": ["promesse ambientali vaghe", "modelli irraggiungibili"],
    },
    "imprenditori_pmi": {
        "label": "Imprenditori e titolari PMI",
        "eta": "40-60 anni", "area": "Nord e Centro",
        "istruzione": "diploma o laurea", "lavoro": "titolari aziende 5-50 dipendenti",
        "preoccupazioni_core": ["burocrazia che soffoca","credito difficile","concorrenza sleale"],
        "barriere_comunicative": ["nessuno pensa davvero a chi fa impresa","allergia a promesse non mantenute"],
        "tone_of_voice": "rispettoso del rischio d'impresa, pratico",
        "esempio_vita": "officina 12 dipendenti, ogni venerdì firma gli F24",
        "trigger_positivi": ["burocrazia ridotta in ore", "credito accessibile"],
        "trigger_negativi": ["più tasse", "regolamentazioni aggiuntive"],
    },
}

# -- Arricchimento empirico SEGMENTI da JSON (ESS R11 + TRIPOL IT) --
def _inject_dati_empirici():
    import os, json as _json
    _p = '/opt/polisim/data/itanes_profili_enriched.json'
    if not os.path.exists(_p):
        return
    try:
        _e = _json.load(open(_p, encoding='utf-8')).get('segmenti_api_empirici', {})
        [SEGMENTI[k].__setitem__('dati_empirici', v) for k, v in _e.items() if k in SEGMENTI]
    except Exception:
        pass

_inject_dati_empirici()
# -------------------------------------------------------------------


METRICHE_CFG = {
    "partito":      {"metriche": ["risonanza_emotiva","credibilita","differenziazione","rischio_rigetto"], "label_score": "Score politico"},
    "ong_lasciti":  {"metriche": ["fiducia_istituzionale","calore_relazionale","semplicita_percepita","disagio_tema"], "label_score": "Score propensione lascito"},
    "ong_corporate":{"metriche": ["ritorno_reputazionale","misurabilita_impatto","semplicita_operativa","rischio_greenwashing"], "label_score": "Score propensione partnership"},
    "brand":        {"metriche": ["propensione_acquisto","fiducia_brand","differenziazione","rischio_rigetto"], "label_score": "Score propensione acquisto"},
}

TIPI_FRAMING = [
    ("ECONOMICO", "impatto concreto su soldi, lavoro, tasse, risparmio"),
    ("VALORIALE",  "giustizia, etica, dignità, appartenenza, futuro"),
    ("PRATICO",    "cosa cambia nella vita quotidiana — azioni concrete"),
    ("EMOTIVO",    "paura, speranza, orgoglio, riconoscimento identitario"),
]

# ── Dati elettorali ───────────────────────────────────────────────
_sondaggi_live = _carica_sondaggi_live()
SONDAGGI_2026 = _sondaggi_live if _sondaggi_live else {
    "FDI_pct":28.3,"PD_pct":22.2,"M5S_pct":12.6,
    "LEGA_pct":7.0,"FI_pct":8.2,"AVS_pct":6.5,
    "AZ_IV_pct":5.5,"FN_pct":3.4,"ALTRI_pct":3.6,
}
QUOTE_2022 = {
    "FDI_pct":26.0,"PD_pct":19.1,"M5S_pct":15.4,"LEGA_pct":8.9,
    "FI_pct":8.1,"AVS_pct":3.6,"AZ_IV_pct":5.6,"FN_pct":0.0,"ALTRI_pct":13.3,
}
MAPPA_COAL = {
    "CDX": ["FDI_pct","LEGA_pct","FI_pct","FN_pct"],
    "CSX": ["PD_pct","AVS_pct"],
    "M5S": ["M5S_pct"],
    "CENTRO": ["AZ_IV_pct"],
}
COAL_LABEL = {"CDX":"Centrodestra","CSX":"Centrosinistra","M5S":"M5S","CENTRO":"Centro","ALTRI":"Altri"}

SCENARI = {
    "centrale":        {"desc":"Sondaggi attuali (maggio 2026)","delta":{}},
    "ottimistico_cdx": {"desc":"CDX recupera (+2 FdI, +1 Lega)","delta":{"FDI_pct":+2.0,"LEGA_pct":+1.0,"PD_pct":-1.5,"M5S_pct":-1.5}},
    "ottimistico_csx": {"desc":"Campo largo consolida (+3 PD, +2 M5S)","delta":{"PD_pct":+3.0,"M5S_pct":+2.0,"FDI_pct":-2.5,"LEGA_pct":-1.0,"FI_pct":-0.5,"AVS_pct":+1.0}},
    "frammentazione":  {"desc":"Partiti medi crescono, grandi calano","delta":{"FDI_pct":-3.0,"PD_pct":-2.0,"M5S_pct":+1.0,"AZ_IV_pct":+2.0,"AVS_pct":+1.0,"FN_pct":+1.0}},
}

PROFILI = {
    "q_italia":      {"nome":"Q-Italia","descrizione":"Partito pro-AI, pacifista, progressista, anti-corruzione, reddito di base","temi":["intelligenza artificiale bene comune","pacifismo razionale","reddito di base","giustizia rigenerativa","trasparenza algoritmica"],"posizionamento":{"sinistra_destra":3,"libertario_autoritario":2,"europeista_sovranista":3,"pacifista_atlantista":1,"verde_produttivista":3},"bacino_target":["astensionisti valoriali","ex-M5S","giovani","progressisti delusi PD"]},
    "italia_verde":  {"nome":"Italia Verde","descrizione":"Ambientalista radicale, decrescita, diritti animali","temi":["transizione ecologica","decrescita","diritti animali","rinnovabili 100%"],"posizionamento":{"sinistra_destra":2,"libertario_autoritario":2,"europeista_sovranista":2,"pacifista_atlantista":2,"verde_produttivista":1},"bacino_target":["giovani urbani","AVS elettori","attivisti clima"]},
    "italia_sovrana":{"nome":"Italia Sovrana","descrizione":"Sovranista, uscita euro, controllo immigrazione","temi":["sovranità monetaria","controllo frontiere","reindustrializzazione"],"posizionamento":{"sinistra_destra":8,"libertario_autoritario":7,"europeista_sovranista":9,"pacifista_atlantista":6,"verde_produttivista":8},"bacino_target":["FdI moderati","Lega del Sud","astensionisti destra"]},
}

# ── Collegi macro-area (per regioni non ancora processate con ISTAT) ──
COLLEGI_MACRO = {
    "camera": {
        "Nord-Ovest": {"n_collegi":38,"CDX":28,"CSX":5,"M5S":0,"margine_medio":12.5,"n_bilico":8},
        "Nord-Est":   {"n_collegi":27,"CDX":22,"CSX":3,"M5S":0,"margine_medio":14.2,"n_bilico":5},
        "Centro":     {"n_collegi":14,"CDX":14,"CSX":0,"M5S":0,"margine_medio":24.9,"n_bilico":4},
        "Sud":        {"n_collegi":36,"CDX":22,"CSX":4,"M5S":8,"margine_medio":8.1,"n_bilico":14},
        "Sud-Isole":  {"n_collegi":18,"CDX":8,"CSX":2,"M5S":7,"margine_medio":6.4,"n_bilico":9},
    },
    "senato": {
        "Nord-Ovest": {"n_collegi":19,"CDX":14,"CSX":3,"M5S":0,"margine_medio":12.8,"n_bilico":4},
        "Nord-Est":   {"n_collegi":14,"CDX":12,"CSX":2,"M5S":0,"margine_medio":13.9,"n_bilico":3},
        "Centro":     {"n_collegi":14,"CDX":9,"CSX":4,"M5S":0,"margine_medio":9.2,"n_bilico":6},
        "Sud":        {"n_collegi":18,"CDX":11,"CSX":2,"M5S":4,"margine_medio":7.8,"n_bilico":7},
        "Sud-Isole":  {"n_collegi":9,"CDX":4,"CSX":1,"M5S":3,"margine_medio":6.1,"n_bilico":4},
    }
}

# ── Dati reali ISTAT 2021 + Eligendo 2022 per collegi Camera Lazio ──
# v2.1: margini calibrati su risultati reali invece di stime da proxy demografici
COLLEGI_REALI_LAZIO = {
    "Lazio 1 - U01": {"pop_2021":379841,"densita_ab_kmq":8359.2,"var_pop_2011_2021_pct":1.11,"margine_reale_2022":10.0,"vincitore_2022":"CIANI PAOLO (CDX)","bilico":True},
    "Lazio 1 - U02": {"pop_2021":369727,"densita_ab_kmq":2516.2,"var_pop_2011_2021_pct":2.84,"margine_reale_2022":2.8,"vincitore_2022":"MATONE SIMONETTA (CDX)","bilico":True},
    "Lazio 1 - U03": {"pop_2021":482229,"densita_ab_kmq":3421.3,"var_pop_2011_2021_pct":6.52,"margine_reale_2022":21.0,"vincitore_2022":"RAMPELLI FABIO (CDX)","bilico":False},
    "Lazio 1 - U04": {"pop_2021":399840,"densita_ab_kmq":3993.6,"var_pop_2011_2021_pct":1.33,"margine_reale_2022":0.5,"vincitore_2022":"MORASSUT ROBERTO (CDX)","bilico":True},
    "Lazio 1 - U05": {"pop_2021":464989,"densita_ab_kmq":1104.5,"var_pop_2011_2021_pct":8.39,"margine_reale_2022":15.3,"vincitore_2022":"BATTILOCCHIO ALESSANDRO (CDX)","bilico":False},
    "Lazio 1 - U06": {"pop_2021":366560,"densita_ab_kmq":1022.7,"var_pop_2011_2021_pct":8.29,"margine_reale_2022":8.1,"vincitore_2022":"CIOCCHETTI LUCIANO (CDX)","bilico":True},
    "Lazio 1 - U07": {"pop_2021":469225,"densita_ab_kmq":1209.5,"var_pop_2011_2021_pct":9.52,"margine_reale_2022":16.7,"vincitore_2022":"FRENI FEDERICO (CDX)","bilico":False},
    "Lazio 1 - U08": {"pop_2021":464077,"densita_ab_kmq":748.0,"var_pop_2011_2021_pct":7.12,"margine_reale_2022":32.9,"vincitore_2022":"TAJANI ANTONIO (CDX)","bilico":False},
    "Lazio 1 - U09": {"pop_2021":425388,"densita_ab_kmq":272.4,"var_pop_2011_2021_pct":3.51,"margine_reale_2022":33.9,"vincitore_2022":"PALOMBI ALESSANDRO (CDX)","bilico":False},
    "Lazio 2 - U01": {"pop_2021":408026,"densita_ab_kmq":108.4,"var_pop_2011_2021_pct":0.86,"margine_reale_2022":39.1,"vincitore_2022":"ROTELLI MAURO (CDX)","bilico":False},
    "Lazio 2 - U02": {"pop_2021":446032,"densita_ab_kmq":106.7,"var_pop_2011_2021_pct":1.75,"margine_reale_2022":34.7,"vincitore_2022":"TRANCASSINI PAOLO (CDX)","bilico":False},
    "Lazio 2 - U03": {"pop_2021":354864,"densita_ab_kmq":250.6,"var_pop_2011_2021_pct":5.87,"margine_reale_2022":43.4,"vincitore_2022":"COLOSIMO CHIARA (CDX)","bilico":False},
    "Lazio 2 - U04": {"pop_2021":345984,"densita_ab_kmq":153.5,"var_pop_2011_2021_pct":-4.94,"margine_reale_2022":45.2,"vincitore_2022":"RUSPANDINI MASSIMO (CDX)","bilico":False},
    "Lazio 2 - U05": {"pop_2021":337899,"densita_ab_kmq":184.4,"var_pop_2011_2021_pct":-0.10,"margine_reale_2022":45.4,"vincitore_2022":"OTTAVIANI NICOLA (CDX)","bilico":False},
}

def _swing_coalizioni(quote_sc):
    def agg(q):
        return {c: sum(q.get(p,0) for p in ps) for c,ps in MAPPA_COAL.items()}
    c22 = agg(QUOTE_2022); csc = agg(quote_sc)
    return {c: csc[c]-c22[c] for c in MAPPA_COAL}

def _swing_collegi_lazio(quote_sc):
    """Calcola seggi Camera Lazio con margini reali Eligendo 2022."""
    sw = _swing_coalizioni(quote_sc)
    seggi = {"CDX":0,"CSX":0,"M5S":0,"CENTRO":0}
    for den, dati in COLLEGI_REALI_LAZIO.items():
        margine = dati["margine_reale_2022"]
        sw_avv = max((sw.get(c,0)-sw.get("CDX",0) for c in MAPPA_COAL if c!="CDX"), default=0)
        if sw_avv > margine/2:
            best_avv = max((c for c in MAPPA_COAL if c!="CDX"), key=lambda c: sw.get(c,0))
            seggi[best_avv] += 1
        else:
            seggi["CDX"] += 1
    return seggi

def _proporzionale(quote_sc, n_seggi, soglia=3.0):
    ammessi = {p:v for p,v in quote_sc.items() if isinstance(v,(int,float)) and v>=soglia and p.endswith('_pct')}
    tot = sum(ammessi.values())
    if tot==0: return {c:0 for c in ["CDX","CSX","M5S","CENTRO","ALTRI"]}
    prop_p={}; resto={}; ass=0
    for p,v in ammessi.items():
        qr=(v/tot)*n_seggi; prop_p[p]=int(qr); resto[p]=qr-int(qr); ass+=prop_p[p]
    for p in sorted(resto,key=resto.get,reverse=True)[:n_seggi-ass]:
        prop_p[p]+=1
    coal_p={"CDX":0,"CSX":0,"M5S":0,"CENTRO":0,"ALTRI":0}
    for p,s in prop_p.items():
        trovato=False
        for c,ps in MAPPA_COAL.items():
            if p in ps: coal_p[c]+=s; trovato=True; break
        if not trovato: coal_p["ALTRI"]+=s
    return coal_p

def _simula_camera(quote_sc):
    sw = _swing_coalizioni(quote_sc)
    seggi_uni = {"CDX":0,"CSX":0,"M5S":0,"CENTRO":0}

    # Lazio — dati reali collegio per collegio (v2.1)
    seggi_lazio = _swing_collegi_lazio(quote_sc)
    for coal, n in seggi_lazio.items():
        seggi_uni[coal] = seggi_uni.get(coal,0) + n

    # Altre macro-aree con modello macro (Centro escluso — già calcolato con Lazio)
    for macro, dati in COLLEGI_MACRO["camera"].items():
        if macro == "Centro":
            continue  # Lazio già calcolato sopra con dati reali
        marg = dati.get("margine_medio",10)
        n_bil = dati.get("n_bilico",0)
        for coal_att in ["CDX","CSX","M5S"]:
            n_vinti = dati.get(coal_att,0)
            if n_vinti == 0: continue
            sw_avv = max((sw.get(c,0)-sw.get(coal_att,0) for c in MAPPA_COAL if c!=coal_att), default=0)
            if sw_avv > marg/2:
                cambia = min(int(n_bil * sw_avv / (marg+0.1)), n_vinti)
                seggi_uni[coal_att] += max(0, n_vinti-cambia)
                best_avv = max((c for c in MAPPA_COAL if c!=coal_att), key=lambda c: sw.get(c,0))
                seggi_uni[best_avv] = seggi_uni.get(best_avv,0) + cambia
            else:
                seggi_uni[coal_att] += n_vinti

    prop = _proporzionale(quote_sc, 245)
    totali = {c: seggi_uni.get(c,0)+prop.get(c,0) for c in ["CDX","CSX","M5S","CENTRO"]}
    totali["ALTRI"] = prop.get("ALTRI",0)
    return totali, seggi_uni, prop, sw

def _simula_senato(quote_sc):
    sw = _swing_coalizioni(quote_sc)
    seggi_uni = {"CDX":0,"CSX":0,"M5S":0,"CENTRO":0}
    for macro, dati in COLLEGI_MACRO["senato"].items():
        marg = dati.get("margine_medio",10)
        n_bil = dati.get("n_bilico",0)
        for coal_att in ["CDX","CSX","M5S"]:
            n_vinti = dati.get(coal_att,0)
            if n_vinti == 0: continue
            sw_avv = max((sw.get(c,0)-sw.get(coal_att,0) for c in MAPPA_COAL if c!=coal_att), default=0)
            if sw_avv > marg/2:
                cambia = min(int(n_bil * sw_avv / (marg+0.1)), n_vinti)
                seggi_uni[coal_att] += max(0, n_vinti-cambia)
                best_avv = max((c for c in MAPPA_COAL if c!=coal_att), key=lambda c: sw.get(c,0))
                seggi_uni[best_avv] = seggi_uni.get(best_avv,0) + cambia
            else:
                seggi_uni[coal_att] += n_vinti
    prop = _proporzionale(quote_sc, 122)
    totali = {c: seggi_uni.get(c,0)+prop.get(c,0) for c in ["CDX","CSX","M5S","CENTRO"]}
    totali["ALTRI"] = prop.get("ALTRI",0)
    return totali, seggi_uni, prop, sw

# ── Modelli Pydantic ──────────────────────────────────────────────
class OptimizeRequest(BaseModel):
    tema: str
    dominio: str = "partito"
    segmento_key: str = "giovani_precari_sud"
    segmento_custom: Optional[dict] = None
    regione: str = "Italia"
    org_nome: str = "Organizzazione"
    org_desc: str = ""
    tester: str = ""

class BuildSegmentRequest(BaseModel):
    eta: str; area: str; istruzione: str; occupazione: str
    genere: str = "tutti"; regione: str = ""; dominio: str = "partito"; nome: str = ""; tester: str = ""

class SimulateRequest(BaseModel):
    modalita: str = "B"
    scenario: str = "centrale"
    profilo: str = "q_italia"
    profilo_custom: Optional[dict] = None
    sondaggi_custom: Optional[dict] = None
    tester: str = ""

# ── Endpoints ─────────────────────────────────────────────────────
@app.get("/api/health")
def health():
    return {"status":"ok","version":"2.1.0","model":CLAUDE_MODEL,
            "timestamp":datetime.now().isoformat(),
            "istat_lazio":"attivo — 14 collegi Camera con margini reali Eligendo 2022"}

@app.get("/api/segments")
def get_segments():
    return {
        "segmenti": {k:{"label":v["label"],"area":v["area"],"eta":v["eta"]} for k,v in SEGMENTI.items()},
        "domini": list(METRICHE_CFG.keys()),
    }

@app.get("/api/logs")
def get_logs(token: str = "", limit: int = 100):
    if token != ADMIN_TOKEN: raise HTTPException(403,"Non autorizzato")
    if not os.path.exists(LOG_FILE): return {"logs":[],"totale":0}
    with open(LOG_FILE,"r",encoding="utf-8") as f: lines = f.readlines()
    logs = []
    for line in lines[-limit:]:
        try: logs.append(json.loads(line.strip()))
        except: pass
    return {"logs":list(reversed(logs)),"totale":len(lines)}

@app.get("/api/istat/lazio")
def get_istat_lazio():
    """Dati ISTAT 2021 + margini reali Eligendo 2022 per collegi Camera Lazio."""
    bilico = [{"collegio":k,"margine":v["margine_reale_2022"],"vincitore":v["vincitore_2022"]}
              for k,v in COLLEGI_REALI_LAZIO.items() if v["bilico"]]
    return {
        "fonte": "ISTAT Censimento 2021 + Eligendo 2022",
        "aggiornamento": "aprile 2026",
        "n_collegi": 14,
        "margine_medio": 24.9,
        "collegi_bilico": bilico,
        "nota": "Lazio rappresenta ~55% della popolazione del Centro elettorale Camera",
    }

@app.post("/api/optimize")
async def optimize(req: OptimizeRequest, request: Request):
    ip = request.client.host if request.client else "unknown"
    check_rate_limit(ip)
    if req.segmento_custom: seg = req.segmento_custom
    elif req.segmento_key in SEGMENTI: seg = SEGMENTI[req.segmento_key]
    else: raise HTTPException(400, f"Segmento '{req.segmento_key}' non trovato")
    cfg = METRICHE_CFG.get(req.dominio, METRICHE_CFG["partito"])
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    framing_desc = "\n".join([f"  {i+1}. {n}: {d}" for i,(n,d) in enumerate(TIPI_FRAMING)])
    acronimi_note = "\nIMPORTANTE: Non usare acronimi come RUB o UBI — usa sempre il nome per esteso."
    prompt1 = f"""Sei un esperto di comunicazione strategica italiana.
ORGANIZZAZIONE: {req.org_nome} [{req.dominio}]
{req.org_desc}
TEMA: {req.tema}
SEGMENTO TARGET: {seg.get('label','')}
Età: {seg.get('eta','')} | Area: {seg.get('area','')} (regione: {req.regione})
Istruzione: {seg.get('istruzione','')} | Lavoro: {seg.get('lavoro','')}
PREOCCUPAZIONI REALI:
{chr(10).join(f'  - {p}' for p in seg.get('preoccupazioni_core',[]))}
BARRIERE COMUNICATIVE:
{chr(10).join(f'  - {b}' for b in seg.get('barriere_comunicative',[]))}
TONE OF VOICE: {seg.get('tone_of_voice','')}
TRIGGER POSITIVI: {', '.join(seg.get('trigger_positivi',[]))}
TRIGGER NEGATIVI: {', '.join(seg.get('trigger_negativi',[]))}
{(lambda d: (
    "DATI EMPIRICI SEGMENTO (ESS R11 2023-24 + TRIPOL IT 2021-22 — usa per calibrare il tono):\n"
    f"  Fiducia istituzionale: {d['fiducia_istituzionale_0_10']}/10 | Soddisfazione democrazia: {d['soddisfazione_democrazia_0_10']}/10\n"
    f"  Issue immigrazione: {d['issue_immigrazione_0_10']}/10 (0=negativo 10=positivo) | Anti-establishment: {d['pct_anti_establishment']}% | Polarizzazione affettiva: {d.get('polarizzazione_affettiva_0_10_TRIPOL','n.d.')}/10"
) if (d := seg.get('dati_empirici')) else "")(None)}
{(lambda c: (
    "CALIBRAZIONE NGO (Meta Ad Library IT — maggio 2026):\n"
    f"  Tono vincente: {c['finding_lasciti']['tono_vincente'] if 'finding_lasciti' in c else c.get('finding_fidelity',{}).get('tono_vincente_fidelity','')}\n"
    f"  Tono da evitare: {c['finding_lasciti']['tono_da_evitare'] if 'finding_lasciti' in c else c.get('finding_fidelity',{}).get('tono_da_evitare','')}\n"
    f"  Hook pattern: {c['finding_lasciti']['hook_pattern'] if 'finding_lasciti' in c else c.get('finding_fidelity',{}).get('hook_pattern_corporate','')}"
) if (c := seg.get('dati_empirici',{}).get('calibrazione_meta_ads')) else "")(None)}
{acronimi_note}
Genera 4 varianti con framing diversi:
{framing_desc}
REGOLE: contenuto diverso per ogni framing, non solo tono. Max 3 frasi per variante.
Rispondi SOLO con JSON:
{{"varianti":[{{"framing":"ECONOMICO","titolo":"5-8 parole","testo":"max 3 frasi","tono":"una parola","parole_chiave":["kw1","kw2"]}}]}}"""
    try:
        msg1 = client.messages.create(model=CLAUDE_MODEL, max_tokens=1500, messages=[{"role":"user","content":prompt1}])
        raw1 = msg1.content[0].text.strip().replace("```json","").replace("```","").strip()
        varianti = json.loads(raw1).get("varianti",[])
    except Exception as e: raise HTTPException(500,f"Errore generazione: {str(e)}")
    time.sleep(1)
    metriche = cfg["metriche"]
    varianti_txt = "\n\n".join([f"VAR {i+1} [{v.get('framing','?')}]:\n{v.get('titolo','')}\n{v.get('testo','')}" for i,v in enumerate(varianti)])
    prompt2 = f"""Valuta questi messaggi per: {seg.get('label','')}
Preoccupazioni: {', '.join(seg.get('preoccupazioni_core',[])[:3])}
Trigger negativi: {', '.join(seg.get('trigger_negativi',[])[:3])}
Regione: {req.regione}
MESSAGGI:
{varianti_txt}
Assegna 0-10 su: {', '.join(metriche)}
Rispondi SOLO con JSON:
{{"valutazioni":[{{"variante":1,"framing":"ECONOMICO",{','.join(f'"{m}":7' for m in metriche)},"score_complessivo":6.85,"insight":"una frase"}}],"raccomandazione":"2 frasi","avvertenza_segmento":"1 frase"}}"""
    try:
        msg2 = client.messages.create(model=CLAUDE_MODEL, max_tokens=1200, messages=[{"role":"user","content":prompt2}])
        raw2 = msg2.content[0].text.strip().replace("```json","").replace("```","").strip()
        result = json.loads(raw2)
        valutazioni = result.get("valutazioni",[])
        for i,val in enumerate(valutazioni):
            if i < len(varianti):
                val.update({k:varianti[i].get(k,"") for k in ["titolo","testo","tono","parole_chiave"]})
    except Exception as e: raise HTTPException(500,f"Errore valutazione: {str(e)}")
    log_sim("optimize",req.dominio,req.tema,seg.get("label",""),tester=req.tester,ip=ip)
    return {
        "segmento": seg.get("label",""), "dominio": req.dominio,
        "tema": req.tema, "regione": req.regione,
        "label_score": cfg["label_score"], "metriche": metriche,
        "varianti": sorted(valutazioni, key=lambda x: x.get("score_complessivo",0), reverse=True),
        "raccomandazione": result.get("raccomandazione",""),
        "avvertenza": result.get("avvertenza_segmento",""),
        "timestamp": datetime.now().isoformat(),
    }

@app.post("/api/build-segment")
async def build_segment(req: BuildSegmentRequest, request: Request):
    ip = request.client.host if request.client else "unknown"
    check_rate_limit(ip)
    istat_path = "/opt/polisim/data/istat_profili_segmenti.json"
    dati_istat_str = ""
    if os.path.exists(istat_path):
        with open(istat_path,"r",encoding="utf-8") as f: istat_data = json.load(f)
        profili = istat_data.get("profili",{})
        mapping = {
            ("18-30","sud","diploma","disoccupato"): "giovani_precari_sud",
            ("18-30","sud","media","inattivo"):       "giovani_precari_sud",
            ("45-60","sud","media","inattivo"):       "casalinghe_sud",
            ("30-45","nord","diploma","occupato"):    "operai_nord",
            ("45-60","nord","diploma","occupato"):    "operai_nord",
            ("60+","nord","tutti","pensionato"):      "pensionati_centro_nord",
            ("60+","centro","tutti","pensionato"):    "pensionati_centro_nord",
        }
        key = (req.eta, req.area.lower(), req.istruzione.lower(), req.occupazione.lower())
        istat_key = mapping.get(key)
        if istat_key and istat_key in profili:
            p = profili[istat_key]; dati = []
            if p.get("dimensione_stima"):           dati.append(f"Dimensione: {p['dimensione_stima']}k persone")
            if p.get("tasso_disoccupazione_pct"):   dati.append(f"Tasso disoccupazione: {p['tasso_disoccupazione_pct']}%")
            if p.get("neet_migliaia"):              dati.append(f"NEET: {p['neet_migliaia']}k")
            if p.get("famiglie_povere_pct"):        dati.append(f"Famiglie povere: {p['famiglie_povere_pct']}%")
            dati_istat_str = "\n".join(f"  - {d}" for d in dati)
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    prompt = f"""Costruisci il profilo psicografico di questo segmento demografico italiano.
PARAMETRI: Età: {req.eta} | Area: {req.area} | Regione: {req.regione or req.area}
Istruzione: {req.istruzione} | Occupazione: {req.occupazione} | Genere: {req.genere} | Dominio: {req.dominio}
DATI ISTAT 2021:
{dati_istat_str or "  (dati aggregati non disponibili per questa combinazione)"}
Rispondi SOLO con JSON compatto:
{{"label":"nome max 5 parole","eta":"{req.eta}","area":"{req.area}","istruzione":"{req.istruzione}","lavoro":"condizione lavorativa","preoccupazioni_core":["p1","p2","p3","p4"],"barriere_comunicative":["b1","b2","b3"],"tone_of_voice":"tono","canali":"media prevalenti","esempio_vita":"situazione concreta","trigger_positivi":["t1","t2","t3"],"trigger_negativi":["n1","n2","n3"],"fonte_dati":"ISTAT 2021"}}"""
    try:
        msg = client.messages.create(model=CLAUDE_MODEL, max_tokens=2000, messages=[{"role":"user","content":prompt}])
        raw = msg.content[0].text.strip().replace("```json","").replace("```","").strip()
        if not raw.endswith("}"): raw = raw[:raw.rfind("}")+1] if "}" in raw else raw+"}"
        profilo = json.loads(raw)
        if req.nome: profilo["label"] = req.nome
        if dati_istat_str: profilo["dati_istat"] = dati_istat_str
        profilo["_params"] = req.dict()
        log_sim("build_segment",req.dominio,f"{req.eta}/{req.area}",profilo.get("label",""),tester=req.tester,ip=ip)
        return profilo
    except Exception as e: raise HTTPException(500,f"Errore build segment: {str(e)}")

@app.post("/api/simulate")
async def simulate(req: SimulateRequest, request: Request):
    ip = request.client.host if request.client else "unknown"
    check_rate_limit(ip)
    if req.modalita == "B":
        sc = SCENARI.get(req.scenario, SCENARI["centrale"])
        snd = dict(req.sondaggi_custom or SONDAGGI_2026)
        for p,d in sc["delta"].items(): snd[p] = max(0, snd.get(p,0)+d)
        tot = sum(v for v in snd.values() if isinstance(v,(int,float)))
        snd = {k: round(v/tot*100,1) if isinstance(v,(int,float)) else v for k,v in snd.items()}
        tot_cam,uni_cam,prop_cam,sw = _simula_camera(snd)
        tot_sen,uni_sen,prop_sen,_  = _simula_senato(snd)
        prop3_cam = _proporzionale(snd,392,3.0)
        prop3_sen = _proporzionale(snd,196,3.0)
        maggioranze = []
        for coal,label in COAL_LABEL.items():
            if coal in ["ALTRI"]: continue
            cam_ok = tot_cam.get(coal,0) > 196
            sen_ok = tot_sen.get(coal,0) > 98
            if cam_ok and sen_ok: maggioranze.append({"coalizione":label,"camera":tot_cam.get(coal,0),"senato":tot_sen.get(coal,0),"status":"doppia_maggioranza"})
            elif cam_ok or sen_ok: maggioranze.append({"coalizione":label,"camera":tot_cam.get(coal,0),"senato":tot_sen.get(coal,0),"status":"maggioranza_parziale"})
        quote_coal = {}
        for coal,partiti in MAPPA_COAL.items():
            quote_coal[COAL_LABEL[coal]] = round(sum(snd.get(p,0) for p in partiti if isinstance(snd.get(p,0),(int,float))),1)
        partiti_sorted = sorted([(p.replace("_pct",""),v) for p,v in snd.items() if isinstance(v,(int,float)) and v>=0.5], key=lambda x: x[1], reverse=True)
        log_sim("simulate_B",req.scenario,"seggi_2027","tutti",tester=req.tester,ip=ip)
        return {
            "modalita":"B","scenario":req.scenario,"scenario_desc":sc["desc"],
            "sondaggi":snd,"partiti":partiti_sorted,"quote_coalizioni":quote_coal,
            "swing":{COAL_LABEL.get(k,k):round(v,1) for k,v in sw.items()},
            "camera":{"totale_seggi":392,"maggioranza":196,"uninominali":147,"proporzionali":245,"seggi":{COAL_LABEL.get(k,k):v for k,v in tot_cam.items()},"seggi_uninominali":{COAL_LABEL.get(k,k):v for k,v in uni_cam.items()},"seggi_prop3":{COAL_LABEL.get(k,k):v for k,v in prop3_cam.items()}},
            "senato":{"totale_seggi":196,"maggioranza":98,"uninominali":74,"proporzionali":122,"seggi":{COAL_LABEL.get(k,k):v for k,v in tot_sen.items()},"seggi_uninominali":{COAL_LABEL.get(k,k):v for k,v in uni_sen.items()},"seggi_prop3":{COAL_LABEL.get(k,k):v for k,v in prop3_sen.items()}},
            "maggioranze":maggioranze,
            "nota":"v2.1 — Lazio Camera: 14 collegi con margini reali Eligendo 2022. Altre regioni: modello macro. Errore stimato ±3-5 seggi per camera.",
            "timestamp":datetime.now().isoformat(),
        }
    elif req.modalita == "A":
        if req.profilo_custom: profilo = req.profilo_custom
        else:
            profilo = PROFILI.get(req.profilo)
            if not profilo: raise HTTPException(400,f"Profilo '{req.profilo}' non trovato.")
        client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
        pos = profilo.get("posizionamento",{})
        assi = []
        if pos.get("sinistra_destra",5)<=3: assi.append("sinistra")
        elif pos.get("sinistra_destra",5)>=7: assi.append("destra")
        else: assi.append("centro")
        if pos.get("pacifista_atlantista",5)<=2: assi.append("pacifista")
        if pos.get("europeista_sovranista",5)>=7: assi.append("sovranista")
        if pos.get("europeista_sovranista",5)<=3: assi.append("europeista")
        if pos.get("verde_produttivista",5)<=3: assi.append("ambientalista")
        # Calcola quota di riferimento per il partito
        quota_ref = profilo.get('quota_attuale', 3.5)
        quota_min = round(max(0.5, quota_ref * 0.7), 1)
        quota_max = round(quota_ref * 1.3, 1)
        # Temi = messaggio da testare (passato dal frontend come p-temi)
        temi_testati = ', '.join(profilo.get('temi',[]))
        prompt = f"""Sei un esperto di comportamento elettorale italiano 2026.

PARTITO: {profilo['nome']} — {profilo['descrizione']}
POSIZIONAMENTO STORICO: {', '.join(assi)}
BACINO TARGET: {', '.join(profilo.get('bacino_target',['non specificato']))}
QUOTA ATTUALE (sondaggi maggio 2026): {quota_ref}%

MESSAGGIO DA TESTARE: {temi_testati}

CONTESTO MAGGIO 2026: FdI 28.3%, PD 22.2%, M5S 12.6%, FI 8.2%, AVS 6.5%, Lega 7.0%, FN 3.4%, AZ/IV 5.5%.

REGOLA CRITICA: valuta la coerenza tra il MESSAGGIO DA TESTARE e i valori storici del partito.
- Se il messaggio è IN CONTRADDIZIONE con i valori del partito (es. pena di morte per un partito garantista, sovranismo per un partito europeista), la quota_nazionale_pct deve essere INFERIORE alla quota attuale di {quota_ref}% — anche significativamente.
- Se il messaggio è COERENTE con i valori del partito, la quota può restare vicina o superiore a {quota_ref}%.
- Non descrivere il messaggio come "favorevole" al partito se è in contraddizione con la sua identità storica.
- Le note_strategiche devono spiegare esplicitamente se c'è coerenza o contraddizione.

Rispondi SOLO con JSON:
{{"quota_nazionale_pct":{quota_ref},"range_pessimistico_pct":{quota_min},"range_ottimistico_pct":{quota_max},"erosione_per_partito":{{"FDI_pct":0.3,"PD_pct":0.8,"M5S_pct":0.9,"LEGA_pct":0.2,"FI_pct":0.2,"ASTENSIONE":0.8}},"profilo_geografico":{{"Nord":1.1,"Centro":1.2,"Sud":0.8}},"note_strategiche":"2 frasi che spiegano coerenza o contraddizione del messaggio con i valori del partito","seggi_stimati_camera":4,"seggi_stimati_senato":2,"supera_soglia_3pct":true}}"""
        try:
            msg = client.messages.create(model=CLAUDE_MODEL, max_tokens=700, messages=[{"role":"user","content":prompt}])
            raw = msg.content[0].text.strip().replace("```json","").replace("```","").strip()
            stima = json.loads(raw)
        except Exception as e: raise HTTPException(500,f"Errore stima Claude: {str(e)}")
        geo = stima.get("profilo_geografico",{"Nord":1.0,"Centro":1.0,"Sud":1.0})
        quota = stima.get("quota_nazionale_pct",3.5)
        regioni_stima = [{"regione":r,"macro_area":m,"quota":round(quota*geo.get(g,1.0),1)} for r,m,g in [("Lombardia","Nord-Ovest","Nord"),("Piemonte","Nord-Ovest","Nord"),("Liguria","Nord-Ovest","Nord"),("Valle d'Aosta","Nord-Ovest","Nord"),("Veneto","Nord-Est","Nord"),("Friuli-VG","Nord-Est","Nord"),("Trentino-AA","Nord-Est","Nord"),("Emilia-Romagna","Nord-Est","Nord"),("Toscana","Centro","Centro"),("Marche","Centro","Centro"),("Umbria","Centro","Centro"),("Lazio","Centro","Centro"),("Campania","Sud","Sud"),("Puglia","Sud","Sud"),("Basilicata","Sud","Sud"),("Calabria","Sud","Sud"),("Abruzzo","Sud","Sud"),("Molise","Sud","Sud"),("Sicilia","Sud-Isole","Sud"),("Sardegna","Sud-Isole","Sud")]]
        log_sim("simulate_A",req.profilo,profilo["nome"],"nuovo_partito",tester=req.tester,ip=ip)
        return {
            "modalita":"A","profilo":profilo["nome"],"descrizione":profilo["descrizione"],
            "quota_nazionale_pct":stima.get("quota_nazionale_pct"),
            "range_pessimistico_pct":stima.get("range_pessimistico_pct"),
            "range_ottimistico_pct":stima.get("range_ottimistico_pct"),
            "supera_soglia_3pct":stima.get("supera_soglia_3pct"),
            "seggi_stimati_camera":stima.get("seggi_stimati_camera",0),
            "seggi_stimati_senato":stima.get("seggi_stimati_senato",0),
            "erosione":stima.get("erosione_per_partito",{}),
            "note_strategiche":stima.get("note_strategiche",""),
            "regioni":regioni_stima,
            "elettori_totali":51424729,
            "voti_stimati":int(51424729*stima.get("quota_nazionale_pct",3.5)/100),
            "soglia_3pct_voti":int(51424729*0.03),
            "timestamp":datetime.now().isoformat(),
        }


# ── Endpoint valuta-coerenza ──────────────────────────────────────
class CoerenzaRequest(BaseModel):
    nome: str
    temi: str
    bacino: str
    asse: float
    coalizione: str
    quota_naz: float
    collegio: str
    forza_locale: float

@app.post("/api/valuta-coerenza")
async def valuta_coerenza(req: CoerenzaRequest, request: Request):
    prompt = f"""Sei un esperto di comunicazione politica italiana. Valuta la coerenza tra un messaggio politico e il contesto territoriale.

PARTITO: {req.nome}
POSIZIONAMENTO (1=sinistra, 10=destra): {req.asse}
COALIZIONE: {req.coalizione}
TEMI/MESSAGGIO TESTATO: {req.temi}
BACINO ELETTORALE: {req.bacino}
COLLEGIO TARGET: {req.collegio}
FORZA LOCALE COALIZIONE: {req.forza_locale:.1f}%

Valuta:
1. La coerenza tra i temi/messaggio e i valori dichiarati del partito
2. La coerenza tra il messaggio e la cultura politica del collegio
3. Il rischio che il messaggio venga percepito come contraddittorio o opportunistico dall'elettorato target

REGOLA CRITICA: nel JSON usa SOLO virgolette doppie. Non usare mai apostrofi nei valori stringa JSON.
Rispondi SOLO con JSON:
{{"score_coerenza": 0.85, "rischio_rigetto": "basso", "impatto_pp": 1.2, "motivazione": "2-3 frasi di spiegazione", "alert": ""}}

Valori attesi:
- score_coerenza: da 0.0 (totale incoerenza) a 1.0 (perfetta coerenza)
- rischio_rigetto: "basso", "medio", "alto", "critico"
- impatto_pp: stima realistica in punti percentuali (negativo se dannoso)
- motivazione: spiegazione concisa
- alert: stringa vuota se score >= 0.5, altrimenti messaggio di allerta esplicito e dettagliato

IMPORTANTE: se i temi sono in totale contraddizione con il posizionamento del partito (es. temi di estrema destra per un partito di sinistra, o viceversa), score_coerenza deve essere <= 0.10 e rischio_rigetto deve essere "critico"."""

    try:
        client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
        msg = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = msg.content[0].text.strip()
        raw = raw.replace("```json","").replace("```","").strip()
        s = raw.find("{"); e = raw.rfind("}")
        if s > -1 and e > -1: raw = raw[s:e+1]
        try:
            result = json.loads(raw)
        except Exception:
            try:
                from json_repair import repair_json
                result = json.loads(repair_json(raw))
            except Exception:
                import re as _re
                clean = _re.sub(r"(?<=[a-zA-ZÀ-ɏ])'(?=[a-zA-ZÀ-ɏ])", " ", raw)
                result = json.loads(clean)
        return result
    except Exception as e:
        raise HTTPException(500, f"Errore valutazione coerenza: {str(e)}")


# ── Endpoint optimize-civic (ONG / Sindacati / Enti civici) ──────
class OptimizeCivicRequest(BaseModel):
    nome: str
    valori: str = ""
    messaggio: str
    target: str = ""
    contesto: str = ""
    tipo: str = "ong_lasciti"
    canale: str = ""
    engagement_level: str = ""

@app.post("/api/optimize-civic")
async def optimize_civic(req: OptimizeCivicRequest, request: Request):
    tipo_label = {
        "ong_lasciti":        "ONG — campagna lasciti testamentari",
        "ong_corporate":      "ONG — partnership corporate / CSR",
        "ong_raccolta_fondi": "ONG — raccolta fondi (donazione singola / emergenza)",
        "ong_advocacy":       "ONG — advocacy e sensibilizzazione pubblica",
        "ong_fidelizzazione": "ONG — fidelizzazione e retention donatori",
        "ong_volontariato":   "ONG — recruitment volontari e coinvolgimento",
        "sindacato":          "Sindacato confederale — comunicazione istituzionale",
    }.get(req.tipo, "Organizzazione civica")

    # ── Istruzioni canale ────────────────────────────────────────────
    canale_note = ""
    if req.canale:
        canale_istruzioni = {
            "social": (
                "CANALE: Social media (Instagram/Facebook/X). "
                "VINCOLI FORMATO: hook obbligatorio nei primi 5 parole, max 280 caratteri per X o max 3 righe per IG/FB, "
                "nessun gergo tecnico, una sola call-to-action esplicita. "
                "Le varianti devono essere immediatamente comprensibili senza contesto."
            ),
            "email": (
                "CANALE: Email / newsletter. "
                "VINCOLI FORMATO: oggetto email distinto dal corpo (max 60 caratteri), "
                "testo può svilupparsi in 3-5 frasi, CTA chiara nel finale, "
                "tono più articolato rispetto ai social è accettabile."
            ),
            "volantino": (
                "CANALE: Materiale cartaceo / volantino. "
                "VINCOLI FORMATO: headline dominante (max 8 parole), sottotitolo opzionale (max 15 parole), "
                "corpo brevissimo (max 2 frasi). Deve funzionare senza contesto e a colpo d'occhio."
            ),
            "discorso": (
                "CANALE: Discorso pubblico / assemblea. "
                "VINCOLI FORMATO: linguaggio orale, frasi brevi e ritmate, "
                "ripetizione intenzionale accettabile, costruzione emotiva progressiva, "
                "finale con appello diretto all'azione collettiva."
            ),
            "video": (
                "CANALE: Video / spot. "
                "VINCOLI FORMATO: i primi 3 secondi sono critici — l'hook deve catturare prima che il testo compaia. "
                "Max 30 parole totali se è uno spot, struttura: problema → emozione → soluzione → CTA. "
                "Nessuna spiegazione: mostrare, non raccontare."
            ),
            "comunicato": (
                "CANALE: Comunicato stampa. "
                "VINCOLI FORMATO: struttura piramidale invertita (notizia principale in apertura), "
                "tono istituzionale e sobrio, citazione diretta attribuibile a un portavoce, "
                "nessun linguaggio promozionale o emotivo eccessivo."
            ),
        }
        canale_note = "\n" + canale_istruzioni.get(req.canale, f"CANALE: {req.canale}.")

    # ── Istruzioni engagement ────────────────────────────────────────
    engagement_note = ""
    if req.engagement_level:
        engagement_istruzioni = {
            "prospect": (
                "PROFILO RELAZIONALE: Prospect — persona che non conosce ancora l'organizzazione. "
                "VINCOLI: presentare la missione in modo accessibile, spiegare il problema che l'organizzazione affronta, "
                "costruire fiducia prima di qualsiasi richiesta. Evitare riferimenti interni o gergo della causa."
            ),
            "sostenitore": (
                "PROFILO RELAZIONALE: Sostenitore attivo — conosce la missione, ha già mostrato interesse o partecipato. "
                "VINCOLI: NON spiegare cos'è il problema o la missione — lo sa già. "
                "Focus su aggiornamenti, impatto concreto già prodotto, prossimi passi specifici. "
                "Tono: tra pari, non evangelizzazione."
            ),
            "donatore_ricorrente": (
                "PROFILO RELAZIONALE: Donatore ricorrente — sostiene economicamente l'organizzazione con continuità. "
                "VINCOLI: NON spiegare la missione o il problema — è un insider. "
                "Riconoscere implicitamente il suo contributo già dato. "
                "Focus su impatto specifico del suo sostegno, non su appelli generici. "
                "Tono: gratitudine autentica, non retorica da fundraising."
            ),
            "legacy_prospect": (
                "PROFILO RELAZIONALE: Legacy prospect — donatore con lunga relazione (tipicamente over 60, anzianità >5 anni). "
                "VINCOLI CRITICI: NON spiegare la missione, NON usare appelli emotivi generici sulla causa. "
                "Il legame con l'organizzazione è già consolidato — il messaggio deve parlare di eredità, "
                "continuità del proprio impatto nel tempo, riconoscimento del legame speciale. "
                "Il tema della morte/lascito va trattato con naturalezza adulta, non evitato né enfatizzato. "
                "Tono: intimo, diretto, rispettoso dell'autonomia decisionale."
            ),
            "a_rischio": (
                "PROFILO RELAZIONALE: Donatore a rischio abbandono — ha ridotto o interrotto il supporto. "
                "VINCOLI: NON ignorare il silenzio recente, ma non rimproverare. "
                "Riconoscere che il mondo è cambiato, l'organizzazione pure, riaprire il dialogo. "
                "Focus su cosa è cambiato in positivo, non su senso di colpa. "
                "Tono: riapertura del dialogo, non sollecito."
            ),
        }
        engagement_note = "\n" + engagement_istruzioni.get(req.engagement_level, "")

    # ── Istruzioni specifiche per verticale ─────────────────────────
    verticale_note = {
        "ong_lasciti": (
            "\nVERTICALE LASCITI: Il messaggio opera in un contesto ad altissima delicatezza. "
            "La propensione al lascito cresce con fiducia istituzionale consolidata e senso di continuità valoriale. "
            "Evitare urgenza, pressione, o appelli alla colpa. Il destinatario deve sentirsi libero di scegliere."
        ),
        "ong_raccolta_fondi": (
            "\nVERTICALE RACCOLTA FONDI: Il messaggio deve produrre una donazione concreta e misurabile. "
            "L'urgenza è accettabile se autentica (emergenza reale). "
            "L'impatto deve essere tangibile e specifico ('20€ = 1 pasto per 10 giorni'), non generico. "
            "La CTA deve essere unica, chiara, e con attrito minimo."
        ),
        "ong_advocacy": (
            "\nVERTICALE ADVOCACY: Il messaggio deve spostare opinione o mobilitare azione collettiva. "
            "La chiarezza del problema è più importante della soluzione. "
            "Attenzione al rischio polarizzazione: messaggi troppo divisivi alienano i moderati. "
            "L'emozione è un amplificatore, non il contenuto."
        ),
        "ong_corporate": (
            "\nVERTICALE CORPORATE/CSR: Il destinatario è un decision maker aziendale. "
            "Il linguaggio deve essere business-oriented: ROI reputazionale, impatto misurabile, rischio ESG. "
            "Evitare toni emotivi o moralistici — parlare di valore condiviso, non di beneficenza."
        ),
        "ong_fidelizzazione": (
            "\nVERTICALE FIDELIZZAZIONE: Il donatore conosce già la causa. "
            "Il messaggio deve rinforzare il senso di appartenenza e mostrare l'impatto personale già generato. "
            "Non vendere la causa — celebrare il percorso condiviso. "
            "L'obiettivo è upgrading o mantenimento, non conversione."
        ),
        "ong_volontariato": (
            "\nVERTICALE VOLONTARIATO: Il messaggio deve abbassare la soglia percepita di impegno. "
            "Le barriere principali sono tempo, competenze richieste, e paura di non essere 'abbastanza'. "
            "Il senso di comunità è più motivante dell'impatto astratto. "
            "Essere concreti sull'impegno richiesto: quando, dove, per quanto tempo."
        ),
    }.get(req.tipo, "")

    prompt = f"""Sei un esperto di comunicazione istituzionale e analisi del framing per organizzazioni civiche.

ORGANIZZAZIONE: {req.nome}
TIPO: {tipo_label}
VALORI / MISSIONE: {req.valori or "Non specificati"}
MESSAGGIO DA ANALIZZARE: {req.messaggio}
TARGET: {req.target or "Non specificato"}
CONTESTO: {req.contesto or "Non specificato"}{canale_note}{engagement_note}{verticale_note}

Esegui un'analisi completa in quattro parti:

PARTE 1 — Coerenza valoriale
Valuta la coerenza tra il messaggio e i valori dichiarati dell'organizzazione.

PARTE 2 — Analisi Entman (1993)
Applica le quattro dimensioni del frame theory di Entman:
- Definizione del problema: come il messaggio definisce la situazione
- Diagnosi delle cause: a chi/cosa attribuisce responsabilità
- Valutazione morale: il giudizio etico implicito
- Soluzione proposta: l'azione suggerita al destinatario
Per ogni dimensione: analisi + forza del frame (forte/debole/neutro) + suggerimento di miglioramento.

PARTE 3 — Alert e motivazione
Se il messaggio è incoerente con i valori, fornisci un alert esplicito.

PARTE 4 — Tre varianti del messaggio (max 30 parole ciascuna, concrete e pronte all uso)

REGOLA CRITICA: nel JSON usa SOLO virgolette doppie. Non usare mai apostrofi nei valori stringa JSON.
Rispondi SOLO con JSON:
{{"score_coerenza": 0.75, "rischio_rigetto": "basso", "impatto_pp": 1.5, "motivazione": "spiegazione 2-3 frasi", "alert": "stringa vuota se score >= 0.5, altrimenti allerta esplicita", "entman": {{"problema": {{"analisi": "testo", "forza": "forte", "suggerimento": "testo"}}, "causa": {{"analisi": "testo", "forza": "neutro", "suggerimento": "testo"}}, "valutazione": {{"analisi": "testo", "forza": "forte", "suggerimento": "testo"}}, "soluzione": {{"analisi": "testo", "forza": "debole", "suggerimento": "testo"}}}}, "varianti": [{{"tipo": "Framing economico", "testo": "testo variante", "score": 0.85, "note": "perché funziona meglio"}}, {{"tipo": "Framing valoriale", "testo": "testo variante", "score": 0.80, "note": "perché funziona meglio"}}, {{"tipo": "Framing azione", "testo": "testo variante", "score": 0.78, "note": "perché funziona meglio"}}]}}

IMPORTANTE: i valori numerici devono essere realistici per il contesto civico (non politico-elettorale). impatto_pp si riferisce all'efficacia comunicativa stimata, non a voti."""

    try:
        client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
        msg = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1200,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = msg.content[0].text.strip()
        raw = raw.replace("```json","").replace("```","").strip()
        s = raw.find("{"); e = raw.rfind("}")
        if s > -1 and e > -1: raw = raw[s:e+1]
        try:
            result = json.loads(raw)
        except Exception:
            try:
                from json_repair import repair_json
                result = json.loads(repair_json(raw))
            except Exception:
                import re as _re
                clean = _re.sub(r"(?<=[a-zA-ZÀ-ɏ])'(?=[a-zA-ZÀ-ɏ])", " ", raw)
                result = json.loads(clean)
        # Seconda chiamata per le varianti (separata per non troncare)
        messaggio_orig = req.messaggio
        nome_org = req.nome
        tipo_org = req.tipo
        score = result.get("score_coerenza", 0.5)
        
        # Genera varianti solo se il messaggio non è totalmente incoerente
        if score >= 0.15:
            try:
                prompt_var = f"""Sei un esperto di comunicazione civica. Proponi 3 varianti migliorative di questo messaggio per {nome_org} ({tipo_org}).

MESSAGGIO ORIGINALE: {messaggio_orig}
SCORE COERENZA ATTUALE: {score:.2f}{canale_note}{engagement_note}{verticale_note}

Rispondi SOLO con JSON:
{{"varianti": [{{"tipo": "Framing economico", "testo": "max 25 parole", "score": 0.85, "note": "1 frase"}}, {{"tipo": "Framing valoriale", "testo": "max 25 parole", "score": 0.80, "note": "1 frase"}}, {{"tipo": "Framing azione", "testo": "max 25 parole", "score": 0.78, "note": "1 frase"}}]}}

REGOLA JSON: usa SOLO virgolette doppie, niente apostrofi."""
                msg_var = client.messages.create(
                    model=CLAUDE_MODEL, max_tokens=600,
                    messages=[{"role": "user", "content": prompt_var}]
                )
                raw_var = msg_var.content[0].text.strip().replace("```json","").replace("```","").strip()
                sv = raw_var.find("{"); ev = raw_var.rfind("}")
                if sv > -1 and ev > -1: raw_var = raw_var[sv:ev+1]
                var_result = json.loads(raw_var)
                varianti_gen = var_result.get("varianti", [])
                metriche_map = {
                    "ong_lasciti":        ["fiducia_istituzionale","calore_relazionale","semplicita_percepita","delicatezza_tema"],
                    "ong_corporate":      ["ritorno_reputazionale","misurabilita_impatto","semplicita_operativa","rischio_greenwashing"],
                    "ong_raccolta_fondi": ["urgenza_percepita","impatto_concreto","fiducia_organizzazione","chiarezza_cta"],
                    "ong_advocacy":       ["risonanza_valoriale","capacita_mobilitazione","chiarezza_messaggio","rischio_polarizzazione"],
                    "ong_fidelizzazione": ["riconoscimento_donatore","senso_appartenenza","impatto_personale","propensione_upgrading"],
                    "ong_volontariato":   ["accessibilita_percepita","coinvolgimento_emotivo","chiarezza_impegno","senso_comunita"],
                    "sindacato":          ["risonanza_target","credibilita_istituzionale","chiarezza_messaggio","capacita_mobilitazione"],
                }
                label_map = {
                    "ong_lasciti":        "Score propensione lascito",
                    "ong_corporate":      "Score partnership CSR",
                    "ong_raccolta_fondi": "Score conversione donazione",
                    "ong_advocacy":       "Score efficacia advocacy",
                    "ong_fidelizzazione": "Score retention donatore",
                    "ong_volontariato":   "Score recruitment volontari",
                    "sindacato":          "Score comunicazione civica",
                }
                metriche = metriche_map.get(req.tipo, metriche_map["sindacato"])
                label_sc = label_map.get(req.tipo, "Score comunicazione")
                varianti_txt = chr(10).join([f"VAR {i+1} [{v.get('framing','?')}]: {v.get('testo','')}" for i,v in enumerate(varianti_gen)])
                metriche_str = ", ".join(metriche)
                import json as _json
                tmpl = {"valutazioni":[{"variante":1,"framing":"X","score_complessivo":7,"insight":"insight"}],"raccomandazione":"testo raccomandazione","avvertenza":"testo avvertenza"}
                for m in metriche:
                    tmpl["valutazioni"][0][m] = 7
                # Aggiungi 2 varianti vuote al template
                for i in [2,3]:
                    entry = {"variante":i,"framing":"X","score_complessivo":6,"insight":"insight"}
                    for m in metriche:
                        entry[m] = 6
                    tmpl["valutazioni"].append(entry)
                lines_v2 = [
                    "Valuta questi messaggi per: " + req.nome + " (" + label_sc + ")",
                    "TARGET: " + req.target,
                    "",
                    varianti_txt,
                    "",
                    "Assegna 0-10 su: " + metriche_str,
                    "REGOLA CRITICA: compila TUTTI i campi del JSON inclusi raccomandazione e avvertenza con testo reale, non lasciare i valori di esempio.",
                    "Usa SOLO virgolette doppie nel JSON.",
                    "Rispondi SOLO con JSON:",
                    _json.dumps(tmpl, ensure_ascii=False)
                ]
                prompt_v2 = chr(10).join(lines_v2)
                msg_v2 = client.messages.create(model=CLAUDE_MODEL, max_tokens=1000, messages=[{"role":"user","content":prompt_v2}])
                raw_v2 = msg_v2.content[0].text.strip().replace("```json","").replace("```","").strip()
                sv2 = raw_v2.find("{"); ev2 = raw_v2.rfind("}")
                if sv2>-1 and ev2>-1: raw_v2 = raw_v2[sv2:ev2+1]
                try:
                    from json_repair import repair_json
                    val_result = json.loads(repair_json(raw_v2))
                except Exception:
                    val_result = {"valutazioni":[], "raccomandazione":"", "avvertenza":""}
                import sys as _sys
                print(f"DEBUG val_result keys: {list(val_result.keys())}", file=_sys.stderr)
                print(f"DEBUG raccomandazione: {repr(val_result.get('raccomandazione',''))[:100]}", file=_sys.stderr)
                valutazioni = val_result.get("valutazioni", [])
                for i, val in enumerate(valutazioni):
                    if i < len(varianti_gen):
                        val["testo"] = varianti_gen[i].get("testo","")
                        val["tipo"] = varianti_gen[i].get("framing", "Variante " + str(i+1))
                        val["score"] = round(val.get("score_complessivo",5)/10, 2)
                        val["note"] = val.get("insight","")
                        val["metriche_dettaglio"] = {m: val.get(m,0) for m in metriche}
                result["varianti"] = sorted(valutazioni, key=lambda x: x.get("score_complessivo",0), reverse=True)
                result["raccomandazione"] = val_result.get("raccomandazione","")
                result["avvertenza"] = val_result.get("avvertenza","")
                result["metriche"] = metriche
                result["label_score"] = label_sc
            except Exception:
                result["varianti"] = []
        else:
            result["varianti"] = []
        
        return result
    except Exception as e:
        raise HTTPException(500, f"Errore optimize-civic: {str(e)}")


# ── Endpoint sondaggi live da PolitPro ──────────────────────────
@app.get("/api/sondaggi")
def get_sondaggi():
    import os, json as _json
    path = "/opt/polisim/data/sondaggi_correnti.json"
    try:
        if os.path.exists(path):
            with open(path) as f:
                d = _json.load(f)
            return d
    except Exception:
        pass
    # Fallback hardcoded maggio 2026
    return {
        "aggiornato": "2026-05-08",
        "fonte": "fallback hardcoded",
        "partiti": {
            "FDI": 28.3, "PD": 22.2, "M5S": 12.6,
            "FI": 8.2, "LEGA": 7.0, "AVS": 6.5,
            "AZ": 3.1, "IV": 2.4, "FN": 3.4, "ALTRI": 6.3
        }
    }
