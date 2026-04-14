"""
PoliSim — FastAPI Backend v2.0
==============================
Endpoints:
  POST /api/optimize       → genera + valuta varianti messaggio
  POST /api/build-segment  → costruisce segmento custom su ISTAT
  GET  /api/health         → stato del server
  GET  /api/segments       → lista segmenti disponibili
  GET  /api/logs           → log simulazioni (solo admin)
"""

import os, json, time
from datetime import datetime
from collections import defaultdict
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import anthropic

app = FastAPI(title="PoliSim API", version="2.0.0")

# ── CORS ──────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://polisim.dev",
        "https://www.polisim.dev",
        "http://localhost:3000",
        "http://localhost:5050",
        "null",  # file:// locale per test
    ],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

CLAUDE_MODEL = "claude-sonnet-4-6"

# ── Rate limiting — 10 richieste per IP ogni ora ──────────────────
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

# ── Admin token ───────────────────────────────────────────────────
ADMIN_TOKEN = os.environ.get("POLISIM_ADMIN_TOKEN", "admin-polisim-2026")

# ── Log ───────────────────────────────────────────────────────────
LOG_FILE = "/opt/polisim/logs/simulazioni.jsonl"
os.makedirs("/opt/polisim/logs", exist_ok=True)

def log_sim(tipo: str, dominio: str, tema: str, segmento: str,
            tester: str = "", ip: str = ""):
    entry = {
        "ts":       datetime.now().isoformat(),
        "tipo":     tipo,
        "dominio":  dominio,
        "tema":     tema[:80],
        "segmento": segmento[:50],
        "tester":   tester,
        "ip":       ip,
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
        "preoccupazioni_core": [
            "non riuscire a costruire un futuro qui",
            "dover emigrare per sopravvivere",
            "lavoro a chiamata e partite IVA a 500 euro",
        ],
        "barriere_comunicative": [
            "sfiducia totale nella politica",
            "linguaggio politico percepito come vuoto",
        ],
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
        "preoccupazioni_core": [
            "arrivare a fine mese",
            "figli emigrati o disoccupati",
            "sanità che non funziona",
            "dipendenza economica",
        ],
        "barriere_comunicative": [
            "non si sente il target della politica",
            "diffidenza verso chi parla bene",
            "linguaggio tecnico non accessibile",
        ],
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
        "preoccupazioni_core": [
            "automazione che prende il mio posto",
            "tasse che soffocano chi lavora",
            "pensione sempre più lontana",
        ],
        "barriere_comunicative": [
            "diffidenza verso la sinistra",
            "retorica ecologista nemica del lavoro",
        ],
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
        "preoccupazioni_core": [
            "qualità della democrazia",
            "cambiamento climatico",
            "affitti insostenibili",
        ],
        "barriere_comunicative": [
            "scetticismo verso partiti nuovi",
            "esigenza di dati non slogan",
        ],
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
        "preoccupazioni_core": [
            "corruzione sistemica",
            "guerra e riarmo",
            "crisi democratica",
        ],
        "barriere_comunicative": [
            "non vota da anni per principio",
            "cerca discontinuità radicale",
        ],
        "tone_of_voice": "onesto sui limiti, trasparente",
        "esempio_vita": "ha smesso di votare nel 2018",
        "trigger_positivi": ["onestà sui limiti", "trasparenza radicale"],
        "trigger_negativi": ["promesse mirabolanti", "attacchi agli avversari"],
    },
    "pensionati_centro_nord": {
        "label": "Pensionati del Centro-Nord",
        "eta": "65+ anni", "area": "Centro-Nord",
        "istruzione": "varia",
        "lavoro": "pensionati",
        "preoccupazioni_core": [
            "sanità che peggiora",
            "sicurezza",
            "pensione che non basta",
        ],
        "barriere_comunicative": [
            "fedeltà a partiti storici",
            "diffidenza verso il nuovo",
        ],
        "tone_of_voice": "rassicurante, rispettoso dell'esperienza",
        "esempio_vita": "1100€ pensione, 8 mesi di attesa per la visita",
        "trigger_positivi": ["sanità concreta", "sicurezza", "rispetto per l'esperienza"],
        "trigger_negativi": ["cambiamento radicale", "tecnologia imposta"],
    },
    "donatori_lasciti": {
        "label": "Potenziali donatori lascito testamentario",
        "eta": "60-80 anni", "area": "Centro-Nord",
        "istruzione": "media-alta",
        "lavoro": "pensionati, ex professionisti",
        "preoccupazioni_core": [
            "eredità di senso dopo la vita",
            "fiducia nell'organizzazione",
            "non creare problemi ai familiari",
        ],
        "barriere_comunicative": [
            "argomento delicato — morte non si nomina",
            "sfiducia verso ONG dopo scandali",
            "complessità percepita atto notarile",
        ],
        "tone_of_voice": "caldo, dignitoso, parla di continuità — mai di morte",
        "esempio_vita": "ex insegnante, dona da anni, vuole che qualcosa duri",
        "trigger_positivi": ["continuità impegno", "semplicità atto", "fiducia costruita"],
        "trigger_negativi": ["linguaggio burocratico", "pressione", "menzione morte"],
    },
    "responsabili_csr": {
        "label": "Responsabili CSR aziende",
        "eta": "35-55 anni", "area": "Grandi città",
        "istruzione": "laurea, spesso master",
        "lavoro": "manager, budget CSR",
        "preoccupazioni_core": [
            "ritorno reputazionale dimostrabile al CDA",
            "evitare greenwashing",
            "rendicontazione ESG precisa",
        ],
        "barriere_comunicative": [
            "ONG percepite come lente e opache",
            "difficoltà misurare impatto in KPI",
        ],
        "tone_of_voice": "professionale, basato su dati, parla la lingua del business",
        "esempio_vita": "presenta 3 partnership CSR al board — vuole dati e semplicità",
        "trigger_positivi": ["ROI reputazionale misurabile", "report ESG-ready", "processo semplice"],
        "trigger_negativi": ["tempi lunghi", "solo emozione senza dati"],
    },
    "donne_25_40_urbane": {
        "label": "Donne 25-40 urbane consapevoli",
        "eta": "25-40 anni", "area": "Grandi città",
        "istruzione": "diploma o laurea",
        "lavoro": "dipendente o freelance, reddito medio",
        "preoccupazioni_core": [
            "ingredienti sicuri e trasparenti",
            "accessibilità — non solo per chi se lo può permettere",
            "autenticità del brand",
        ],
        "barriere_comunicative": [
            "sfinimento da green/pink washing",
            "sfiducia verso promesse senza dati",
        ],
        "tone_of_voice": "autentico, non perfetto, scelte reali non ideali",
        "esempio_vita": "legge gli ingredienti, segue creator beauty consapevole",
        "trigger_positivi": ["trasparenza ingredienti", "prezzo accessibile certificato"],
        "trigger_negativi": ["promesse ambientali vaghe", "modelli irraggiungibili"],
    },
    "imprenditori_pmi": {
        "label": "Imprenditori e titolari PMI",
        "eta": "40-60 anni", "area": "Nord e Centro",
        "istruzione": "diploma o laurea",
        "lavoro": "titolari aziende 5-50 dipendenti",
        "preoccupazioni_core": [
            "burocrazia che soffoca",
            "credito difficile",
            "concorrenza sleale",
        ],
        "barriere_comunicative": [
            "nessuno pensa davvero a chi fa impresa",
            "allergia a promesse non mantenute",
        ],
        "tone_of_voice": "rispettoso del rischio d'impresa, pratico",
        "esempio_vita": "officina 12 dipendenti, ogni venerdì firma gli F24",
        "trigger_positivi": ["burocrazia ridotta in ore", "credito accessibile"],
        "trigger_negativi": ["più tasse", "regolamentazioni aggiuntive"],
    },
}

METRICHE_CFG = {
    "partito": {
        "metriche": ["risonanza_emotiva", "credibilita", "differenziazione", "rischio_rigetto"],
        "label_score": "Score politico",
    },
    "ong_lasciti": {
        "metriche": ["fiducia_istituzionale", "calore_relazionale", "semplicita_percepita", "disagio_tema"],
        "label_score": "Score propensione lascito",
    },
    "ong_corporate": {
        "metriche": ["ritorno_reputazionale", "misurabilita_impatto", "semplicita_operativa", "rischio_greenwashing"],
        "label_score": "Score propensione partnership",
    },
    "brand": {
        "metriche": ["propensione_acquisto", "fiducia_brand", "differenziazione", "rischio_rigetto"],
        "label_score": "Score propensione acquisto",
    },
}

TIPI_FRAMING = [
    ("ECONOMICO", "impatto concreto su soldi, lavoro, tasse, risparmio"),
    ("VALORIALE",  "giustizia, etica, dignità, appartenenza, futuro"),
    ("PRATICO",    "cosa cambia nella vita quotidiana — azioni concrete"),
    ("EMOTIVO",    "paura, speranza, orgoglio, riconoscimento identitario"),
]

# Acronimi politici da NON usare — sostituzioni automatiche nel prompt
ACRONIMI_VIETATI = {
    "RUB": "RdB (Reddito di Base)",
    "UBI": "Reddito Universale di Base",
}

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
    eta: str
    area: str
    istruzione: str
    occupazione: str
    genere: str = "tutti"
    regione: str = ""
    dominio: str = "partito"
    nome: str = ""
    tester: str = ""

# ── Endpoints ─────────────────────────────────────────────────────
@app.get("/api/health")
def health():
    return {"status": "ok", "version": "2.0.0", "model": CLAUDE_MODEL,
            "timestamp": datetime.now().isoformat()}

@app.get("/api/segments")
def get_segments():
    return {
        "segmenti": {k: {"label": v["label"], "area": v["area"], "eta": v["eta"]}
                     for k, v in SEGMENTI.items()},
        "domini": list(METRICHE_CFG.keys()),
    }

@app.get("/api/logs")
def get_logs(token: str = "", limit: int = 100):
    """Endpoint admin — restituisce ultimi N log."""
    if token != ADMIN_TOKEN:
        raise HTTPException(403, "Non autorizzato")
    if not os.path.exists(LOG_FILE):
        return {"logs": [], "totale": 0}
    with open(LOG_FILE, "r", encoding="utf-8") as f:
        lines = f.readlines()
    logs = []
    for line in lines[-limit:]:
        try:
            logs.append(json.loads(line.strip()))
        except:
            pass
    return {"logs": list(reversed(logs)), "totale": len(lines)}

@app.post("/api/optimize")
async def optimize(req: OptimizeRequest, request: Request):
    ip = request.client.host if request.client else "unknown"
    check_rate_limit(ip)

    # Carica segmento
    if req.segmento_custom:
        seg = req.segmento_custom
    elif req.segmento_key in SEGMENTI:
        seg = SEGMENTI[req.segmento_key]
    else:
        raise HTTPException(400, f"Segmento '{req.segmento_key}' non trovato")

    cfg    = METRICHE_CFG.get(req.dominio, METRICHE_CFG["partito"])
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    framing_desc = "\n".join([f"  {i+1}. {n}: {d}" for i,(n,d) in enumerate(TIPI_FRAMING)])

    # Nota acronimi per il modello
    acronimi_note = "\nIMPORTANTE: Non usare acronimi come RUB o UBI — usa sempre il nome per esteso: Reddito di Base (RdB) o Reddito Universale di Base."

    # Step 1 — Genera varianti
    prompt1 = f"""Sei un esperto di comunicazione strategica italiana.

ORGANIZZAZIONE: {req.org_nome} [{req.dominio}]
{req.org_desc}

TEMA: {req.tema}

SEGMENTO TARGET: {seg.get('label', '')}
Età: {seg.get('eta','')} | Area: {seg.get('area','')} (regione: {req.regione})
Istruzione: {seg.get('istruzione','')} | Lavoro: {seg.get('lavoro','')}

PREOCCUPAZIONI REALI:
{chr(10).join(f'  - {p}' for p in seg.get('preoccupazioni_core',[]))}

BARRIERE COMUNICATIVE:
{chr(10).join(f'  - {b}' for b in seg.get('barriere_comunicative',[]))}

TONE OF VOICE: {seg.get('tone_of_voice','')}
ESEMPIO DI VITA: {seg.get('esempio_vita','')}
TRIGGER POSITIVI: {', '.join(seg.get('trigger_positivi',[]))}
TRIGGER NEGATIVI DA EVITARE: {', '.join(seg.get('trigger_negativi',[]))}
{acronimi_note}

Genera 4 varianti con framing diversi:
{framing_desc}

REGOLE: contenuto diverso per ogni framing, non solo tono. Max 3 frasi per variante.

Rispondi SOLO con JSON:
{{"varianti":[{{"framing":"ECONOMICO","titolo":"5-8 parole","testo":"max 3 frasi","tono":"una parola","parole_chiave":["kw1","kw2"]}}]}}"""

    try:
        msg1   = client.messages.create(model=CLAUDE_MODEL, max_tokens=1500,
                    messages=[{"role":"user","content":prompt1}])
        raw1   = msg1.content[0].text.strip().replace("```json","").replace("```","").strip()
        varianti = json.loads(raw1).get("varianti", [])
    except Exception as e:
        raise HTTPException(500, f"Errore generazione varianti: {str(e)}")

    time.sleep(1)

    # Step 2 — Valuta
    metriche     = cfg["metriche"]
    varianti_txt = "\n\n".join([
        f"VAR {i+1} [{v.get('framing','?')}]:\n{v.get('titolo','')}\n{v.get('testo','')}"
        for i,v in enumerate(varianti)])

    prompt2 = f"""Valuta questi messaggi per il segmento: {seg.get('label','')}
Preoccupazioni: {', '.join(seg.get('preoccupazioni_core',[])[:3])}
Trigger negativi: {', '.join(seg.get('trigger_negativi',[])[:3])}
Regione: {req.regione}

MESSAGGI:
{varianti_txt}

Assegna 0-10 su: {', '.join(metriche)}

Rispondi SOLO con JSON:
{{"valutazioni":[{{"variante":1,"framing":"ECONOMICO",{','.join(f'"{m}":7' for m in metriche)},"score_complessivo":6.85,"insight":"una frase"}}],"raccomandazione":"2 frasi","avvertenza_segmento":"1 frase"}}"""

    try:
        msg2   = client.messages.create(model=CLAUDE_MODEL, max_tokens=1200,
                    messages=[{"role":"user","content":prompt2}])
        raw2   = msg2.content[0].text.strip().replace("```json","").replace("```","").strip()
        result = json.loads(raw2)
        valutazioni = result.get("valutazioni", [])
        for i,val in enumerate(valutazioni):
            if i < len(varianti):
                val.update({k: varianti[i].get(k,"") for k in
                            ["titolo","testo","tono","parole_chiave"]})
    except Exception as e:
        raise HTTPException(500, f"Errore valutazione: {str(e)}")

    log_sim("optimize", req.dominio, req.tema, seg.get("label",""),
            tester=req.tester, ip=ip)

    return {
        "segmento":      seg.get("label",""),
        "dominio":       req.dominio,
        "tema":          req.tema,
        "regione":       req.regione,
        "label_score":   cfg["label_score"],
        "metriche":      metriche,
        "varianti":      sorted(valutazioni,
                                key=lambda x: x.get("score_complessivo",0), reverse=True),
        "raccomandazione": result.get("raccomandazione",""),
        "avvertenza":    result.get("avvertenza_segmento",""),
        "timestamp":     datetime.now().isoformat(),
    }

@app.post("/api/build-segment")
async def build_segment(req: BuildSegmentRequest, request: Request):
    ip = request.client.host if request.client else "unknown"
    check_rate_limit(ip)

    istat_path   = "/opt/polisim/data/istat_profili_segmenti.json"
    dati_istat_str = ""
    if os.path.exists(istat_path):
        with open(istat_path, "r", encoding="utf-8") as f:
            istat_data = json.load(f)
        profili = istat_data.get("profili", {})
        mapping = {
            ("18-30","sud","diploma","disoccupato"):  "giovani_precari_sud",
            ("18-30","sud","media","inattivo"):       "giovani_precari_sud",
            ("45-60","sud","media","inattivo"):       "casalinghe_sud",
            ("30-45","nord","diploma","occupato"):    "operai_nord",
            ("45-60","nord","diploma","occupato"):    "operai_nord",
            ("60+","nord","tutti","pensionato"):      "pensionati_centro_nord",
            ("60+","centro","tutti","pensionato"):    "pensionati_centro_nord",
        }
        key      = (req.eta, req.area.lower(), req.istruzione.lower(), req.occupazione.lower())
        istat_key = mapping.get(key)
        if istat_key and istat_key in profili:
            p    = profili[istat_key]
            dati = []
            if p.get("dimensione_stima"):           dati.append(f"Dimensione: {p['dimensione_stima']}k persone")
            if p.get("tasso_disoccupazione_pct"):   dati.append(f"Tasso disoccupazione: {p['tasso_disoccupazione_pct']}%")
            if p.get("neet_migliaia"):              dati.append(f"NEET: {p['neet_migliaia']}k")
            if p.get("inattivi_donne_40_64_migliaia"): dati.append(f"Donne inattive 40-64: {p['inattivi_donne_40_64_migliaia']}k")
            if p.get("famiglie_povere_pct"):        dati.append(f"Famiglie povere: {p['famiglie_povere_pct']}%")
            dati_istat_str = "\n".join(f"  - {d}" for d in dati)

    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    prompt = f"""Costruisci il profilo psicografico di questo segmento demografico italiano.

PARAMETRI:
  Età: {req.eta} | Area: {req.area} | Regione: {req.regione or req.area}
  Istruzione: {req.istruzione} | Occupazione: {req.occupazione}
  Genere: {req.genere} | Dominio: {req.dominio}

DATI ISTAT 2021:
{dati_istat_str or "  (dati aggregati non disponibili per questa combinazione)"}

Rispondi SOLO con JSON compatto (stringhe max 80 caratteri):
{{"label":"nome max 5 parole","eta":"{req.eta}","area":"{req.area}","istruzione":"{req.istruzione}","lavoro":"condizione lavorativa","preoccupazioni_core":["p1","p2","p3","p4"],"barriere_comunicative":["b1","b2","b3"],"tone_of_voice":"tono","canali":"media prevalenti","esempio_vita":"situazione concreta","trigger_positivi":["t1","t2","t3"],"trigger_negativi":["n1","n2","n3"],"fonte_dati":"ISTAT 2021"}}"""

    try:
        msg  = client.messages.create(model=CLAUDE_MODEL, max_tokens=2000,
                   messages=[{"role":"user","content":prompt}])
        raw  = msg.content[0].text.strip().replace("```json","").replace("```","").strip()
        if not raw.endswith("}"):
            raw = raw[:raw.rfind("}")+1] if "}" in raw else raw + "}"
        profilo = json.loads(raw)
        if req.nome: profilo["label"] = req.nome
        if dati_istat_str: profilo["dati_istat"] = dati_istat_str
        profilo["_params"] = req.dict()
        log_sim("build_segment", req.dominio, f"{req.eta}/{req.area}",
                profilo.get("label",""), tester=req.tester, ip=ip)
        return profilo
    except Exception as e:
        raise HTTPException(500, f"Errore build segment: {str(e)}")

# ── SIMULATORE STAGE 2 — aggiungere a polisim_api.py ────────────────────────
# Incolla questo blocco PRIMA dell'istruzione `if __name__ == "__main__":` nel file polisim_api.py

# ── Dati elettorali hardcoded ─────────────────────────────────────────────────

SONDAGGI_2026 = {
    "FDI_pct":28.2,"PD_pct":21.9,"M5S_pct":13.1,
    "LEGA_pct":6.5,"FI_pct":8.4,"AVS_pct":6.8,
    "AZ_IV_pct":5.5,"FN_pct":3.6,"ALTRI_pct":5.0,
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
COAL_LABEL = {
    "CDX":"Centrodestra","CSX":"Centrosinistra","M5S":"M5S","CENTRO":"Centro","ALTRI":"Altri"
}
SCENARI = {
    "centrale":        {"desc":"Sondaggi attuali (marzo 2026)","delta":{}},
    "ottimistico_cdx": {"desc":"CDX recupera (+2 FdI, +1 Lega)","delta":{"FDI_pct":+2.0,"LEGA_pct":+1.0,"PD_pct":-1.5,"M5S_pct":-1.5}},
    "ottimistico_csx": {"desc":"Campo largo consolida (+3 PD, +2 M5S)","delta":{"PD_pct":+3.0,"M5S_pct":+2.0,"FDI_pct":-2.5,"LEGA_pct":-1.0,"FI_pct":-0.5,"AVS_pct":+1.0}},
    "frammentazione":  {"desc":"Partiti medi crescono, grandi calano","delta":{"FDI_pct":-3.0,"PD_pct":-2.0,"M5S_pct":+1.0,"AZ_IV_pct":+2.0,"AVS_pct":+1.0,"FN_pct":+1.0}},
}
PROFILI = {
    "q_italia":      {"nome":"Q-Italia","descrizione":"Partito pro-AI, pacifista, progressista, anti-corruzione, reddito di base","temi":["intelligenza artificiale bene comune","pacifismo razionale","reddito di base","giustizia rigenerativa","trasparenza algoritmica"],"posizionamento":{"sinistra_destra":3,"libertario_autoritario":2,"europeista_sovranista":3,"pacifista_atlantista":1,"verde_produttivista":3},"bacino_target":["astensionisti valoriali","ex-M5S","giovani","progressisti delusi PD"]},
    "italia_verde":  {"nome":"Italia Verde","descrizione":"Ambientalista radicale, decrescita, diritti animali","temi":["transizione ecologica","decrescita","diritti animali","rinnovabili 100%"],"posizionamento":{"sinistra_destra":2,"libertario_autoritario":2,"europeista_sovranista":2,"pacifista_atlantista":2,"verde_produttivista":1},"bacino_target":["giovani urbani","AVS elettori","attivisti clima"]},
    "italia_sovrana":{"nome":"Italia Sovrana","descrizione":"Sovranista, uscita euro, controllo immigrazione","temi":["sovranità monetaria","controllo frontiere","reindustrializzazione"],"posizionamento":{"sinistra_destra":8,"libertario_autoritario":7,"europeista_sovranista":9,"pacifista_atlantista":6,"verde_produttivista":8},"bacino_target":["FdI moderati","Lega del Sud","astensionisti destra"]},
}

# Collegi macro-area (dati reali da Eligendo 2022)
COLLEGI_MACRO = {
    "camera": {
        "Nord-Ovest": {"n_collegi":38,"CDX":28,"CSX":5,"M5S":0,"margine_medio":12.5,"n_bilico":8},
        "Nord-Est":   {"n_collegi":27,"CDX":22,"CSX":3,"M5S":0,"margine_medio":14.2,"n_bilico":5},
        "Centro":     {"n_collegi":28,"CDX":18,"CSX":8,"M5S":0,"margine_medio":9.8,"n_bilico":11},
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

def _swing_coalizioni(quote_sc):
    def agg(q):
        return {c: sum(q.get(p,0) for p in ps) for c,ps in MAPPA_COAL.items()}
    c22 = agg(QUOTE_2022)
    csc = agg(quote_sc)
    return {c: csc[c]-c22[c] for c in MAPPA_COAL}

def _proporzionale(quote_sc, n_seggi, soglia=3.0):
    ammessi = {p:v for p,v in quote_sc.items()
               if isinstance(v,(int,float)) and v>=soglia and p.endswith('_pct')}
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
    for macro, dati in COLLEGI_MACRO["camera"].items():
        marg = dati.get("margine_medio", 10)
        n_bil = dati.get("n_bilico", 0)
        for coal_att in ["CDX","CSX","M5S"]:
            n_vinti = dati.get(coal_att, 0)
            if n_vinti == 0: continue
            sw_avv = max((sw.get(c,0)-sw.get(coal_att,0) for c in MAPPA_COAL if c!=coal_att), default=0)
            if sw_avv > marg/2:
                cambia = min(int(n_bil * sw_avv / (marg+0.1)), n_vinti)
                seggi_uni[coal_att] += max(0, n_vinti-cambia)
                # Chi guadagna: coalizione con swing maggiore
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
        marg = dati.get("margine_medio", 10)
        n_bil = dati.get("n_bilico", 0)
        for coal_att in ["CDX","CSX","M5S"]:
            n_vinti = dati.get(coal_att, 0)
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

# ── Modelli Pydantic per simulatore ──────────────────────────────────────────
class SimulateRequest(BaseModel):
    modalita: str = "B"           # "A" = partito nuovo, "B" = sondaggio
    scenario: str = "centrale"    # solo per modalità B
    profilo: str = "q_italia"     # solo per modalità A (preset)
    profilo_custom: Optional[dict] = None  # partito custom libero
    sondaggi_custom: Optional[dict] = None  # override sondaggi
    tester: str = ""

# ── Endpoint simulatore ───────────────────────────────────────────────────────
@app.post("/api/simulate")
async def simulate(req: SimulateRequest, request: Request):
    ip = request.client.host if request.client else "unknown"
    check_rate_limit(ip)

    if req.modalita == "B":
        # Modalità B — distribuzione seggi per scenario
        sc = SCENARI.get(req.scenario, SCENARI["centrale"])
        snd = dict(req.sondaggi_custom or SONDAGGI_2026)
        for p,d in sc["delta"].items():
            snd[p] = max(0, snd.get(p,0)+d)
        # Normalizza a 100
        tot = sum(v for v in snd.values() if isinstance(v,(int,float)))
        snd = {k: round(v/tot*100,1) if isinstance(v,(int,float)) else v for k,v in snd.items()}

        tot_cam, uni_cam, prop_cam, sw = _simula_camera(snd)
        tot_sen, uni_sen, prop_sen, _  = _simula_senato(snd)
        prop3_cam = _proporzionale(snd, 392, 3.0)
        prop3_sen = _proporzionale(snd, 196, 3.0)

        # Verifica doppia maggioranza
        maggioranze = []
        for coal, label in COAL_LABEL.items():
            if coal in ["ALTRI"]: continue
            cam_ok = tot_cam.get(coal,0) > 196
            sen_ok = tot_sen.get(coal,0) > 98
            if cam_ok and sen_ok:
                maggioranze.append({"coalizione":label,"camera":tot_cam.get(coal,0),"senato":tot_sen.get(coal,0),"status":"doppia_maggioranza"})
            elif cam_ok or sen_ok:
                maggioranze.append({"coalizione":label,"camera":tot_cam.get(coal,0),"senato":tot_sen.get(coal,0),"status":"maggioranza_parziale"})

        # Quote coalizioni
        quote_coal = {}
        for coal, partiti in MAPPA_COAL.items():
            quote_coal[COAL_LABEL[coal]] = round(sum(snd.get(p,0) for p in partiti if isinstance(snd.get(p,0),(int,float))),1)

        # Partiti individuali ordinati
        partiti_sorted = sorted(
            [(p.replace("_pct",""), v) for p,v in snd.items() if isinstance(v,(int,float)) and v>=0.5],
            key=lambda x: x[1], reverse=True
        )

        log_sim("simulate_B", req.scenario, "seggi_2027", "tutti", tester=req.tester, ip=ip)

        return {
            "modalita": "B",
            "scenario": req.scenario,
            "scenario_desc": sc["desc"],
            "sondaggi": snd,
            "partiti": partiti_sorted,
            "quote_coalizioni": quote_coal,
            "swing": {COAL_LABEL.get(k,k): round(v,1) for k,v in sw.items()},
            "camera": {
                "totale_seggi": 392,
                "maggioranza": 196,
                "uninominali": 147,
                "proporzionali": 245,
                "seggi": {COAL_LABEL.get(k,k): v for k,v in tot_cam.items()},
                "seggi_uninominali": {COAL_LABEL.get(k,k): v for k,v in uni_cam.items()},
                "seggi_prop3": {COAL_LABEL.get(k,k): v for k,v in prop3_cam.items()},
            },
            "senato": {
                "totale_seggi": 196,
                "maggioranza": 98,
                "uninominali": 74,
                "proporzionali": 122,
                "seggi": {COAL_LABEL.get(k,k): v for k,v in tot_sen.items()},
                "seggi_uninominali": {COAL_LABEL.get(k,k): v for k,v in uni_sen.items()},
                "seggi_prop3": {COAL_LABEL.get(k,k): v for k,v in prop3_sen.items()},
            },
            "maggioranze": maggioranze,
            "nota": "Modello swing per macro-area su dati Eligendo 2022. Errore stimato ±3-5 seggi per camera.",
            "timestamp": datetime.now().isoformat(),
        }

    elif req.modalita == "A":
        # Modalità A — stima partito nuovo con Claude
        # Usa profilo_custom se fornito, altrimenti preset
        if req.profilo_custom:
            profilo = req.profilo_custom
        else:
            profilo = PROFILI.get(req.profilo)
            if not profilo:
                raise HTTPException(400, f"Profilo '{req.profilo}' non trovato. Disponibili: {list(PROFILI.keys())}")

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

        prompt = f"""Sei un esperto di comportamento elettorale italiano 2026.

PARTITO: {profilo['nome']} — {profilo['descrizione']}
Posizionamento: {', '.join(assi)}
Temi: {', '.join(profilo.get('temi',[]))}
Bacino target: {', '.join(profilo.get('bacino_target',['non specificato']))}

CONTESTO MARZO 2026:
FdI 28.2% (calo post-referendum), PD 21.9%, M5S 13.1%,
FI 8.4%, AVS 6.8%, Lega 6.5%, FN 3.6%, AZ/IV 5.5%.
Referendum giustizia: governo Meloni in difficoltà.

VINCOLI: primo tentativo raramente >5%. Bacino astensionisti: solo 20-30% mobilitabile.

Rispondi SOLO con JSON:
{{
  "quota_nazionale_pct": 3.5,
  "range_pessimistico_pct": 2.0,
  "range_ottimistico_pct": 5.5,
  "erosione_per_partito": {{"FDI_pct":0.3,"PD_pct":0.8,"M5S_pct":0.9,"LEGA_pct":0.2,"FI_pct":0.2,"ASTENSIONE":0.8}},
  "profilo_geografico": {{"Nord":1.1,"Centro":1.2,"Sud":0.8}},
  "note_strategiche": "2 frasi max",
  "seggi_stimati_camera": 4,
  "seggi_stimati_senato": 2,
  "supera_soglia_3pct": true
}}"""

        try:
            msg = client.messages.create(model=CLAUDE_MODEL, max_tokens=700,
                messages=[{"role":"user","content":prompt}])
            raw = msg.content[0].text.strip().replace("```json","").replace("```","").strip()
            stima = json.loads(raw)
        except Exception as e:
            raise HTTPException(500, f"Errore stima Claude: {str(e)}")

        # Distribuzione geografica
        geo = stima.get("profilo_geografico", {"Nord":1.0,"Centro":1.0,"Sud":1.0})
        quota = stima.get("quota_nazionale_pct", 3.5)
        regioni_stima = [
            {"regione":r,"macro_area":m,"quota":round(quota*geo.get(g,1.0),1)}
            for r,m,g in [
                ("Lombardia","Nord-Ovest","Nord"),("Piemonte","Nord-Ovest","Nord"),
                ("Liguria","Nord-Ovest","Nord"),("Valle d'Aosta","Nord-Ovest","Nord"),
                ("Veneto","Nord-Est","Nord"),("Friuli-VG","Nord-Est","Nord"),
                ("Trentino-AA","Nord-Est","Nord"),("Emilia-Romagna","Nord-Est","Nord"),
                ("Toscana","Centro","Centro"),("Marche","Centro","Centro"),
                ("Umbria","Centro","Centro"),("Lazio","Centro","Centro"),
                ("Campania","Sud","Sud"),("Puglia","Sud","Sud"),
                ("Basilicata","Sud","Sud"),("Calabria","Sud","Sud"),
                ("Abruzzo","Sud","Sud"),("Molise","Sud","Sud"),
                ("Sicilia","Sud-Isole","Sud"),("Sardegna","Sud-Isole","Sud"),
            ]
        ]

        log_sim("simulate_A", req.profilo, profilo["nome"], "nuovo_partito", tester=req.tester, ip=ip)

        return {
            "modalita": "A",
            "profilo": profilo["nome"],
            "descrizione": profilo["descrizione"],
            "quota_nazionale_pct": stima.get("quota_nazionale_pct"),
            "range_pessimistico_pct": stima.get("range_pessimistico_pct"),
            "range_ottimistico_pct": stima.get("range_ottimistico_pct"),
            "supera_soglia_3pct": stima.get("supera_soglia_3pct"),
            "seggi_stimati_camera": stima.get("seggi_stimati_camera",0),
            "seggi_stimati_senato": stima.get("seggi_stimati_senato",0),
            "erosione": stima.get("erosione_per_partito",{}),
            "note_strategiche": stima.get("note_strategiche",""),
            "regioni": regioni_stima,
            "elettori_totali": 51424729,
            "voti_stimati": int(51424729 * stima.get("quota_nazionale_pct",3.5)/100),
            "soglia_3pct_voti": int(51424729 * 0.03),
            "timestamp": datetime.now().isoformat(),
        }

    raise HTTPException(400, "Modalita non valida. Usa 'A' o 'B'.")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("polisim_api:app", host="0.0.0.0", port=8001, reload=False)
