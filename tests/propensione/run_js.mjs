// run_js.mjs — esegue il motore RFML JS del dashboard su un dataset CSV e
// serializza l'output in JSON, per il confronto di equivalenza con il Python.
//
// Come funziona
// -------------
// Il motore vive dentro propensione_dashboard.html come <script> inline di sole
// dichiarazioni (nessun init al load). Carichiamo l'HTML in jsdom con
// runScripts:"dangerously" e INIETTIAMO un piccolo harness DENTRO lo stesso
// <script>, subito prima di </script>: così condivide lo scope lessicale delle
// variabili `let anaData/txData/...` e della `const mapState`, che non sono
// proprieta di window e non sarebbero raggiungibili da uno <script> separato.
//
// L'harness replica VERBATIM la sequenza di eseguiCalcolo() (righe ~1371-1385),
// saltando solo la parte DOM (progress bar, renderAll, goStep).
//
// NB: lo script esterno XLSX (CDN) non viene caricato (resources non abilitate):
// non serve, e dsParseXLSX non viene mai chiamata.

import { readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { JSDOM } from 'jsdom';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO = join(__dirname, '..', '..');

const htmlPath = join(REPO, 'propensione_dashboard.html');
const anaPath = join(__dirname, 'donors_anagrafica_test.csv');
const txPath = join(__dirname, 'donors_transazioni_test.csv');
// Segmento sotto-soglia: lascito_dichiarato ha <5 positivi -> calcolaLift deve
// NON calcolare il lift (sottoSoglia:true). Copre la simmetria con il Python.
const anaSsPath = join(__dirname, 'donors_anagrafica_sottosoglia.csv');
const txSsPath = join(__dirname, 'donors_transazioni_sottosoglia.csv');
// Segmento denominatore-zero: tutti i positivi (>=soglia) cadono nel top 20% per
// score, 0 nel resto -> nessun termine di paragone, lift non definito.
const anaDzPath = join(__dirname, 'donors_anagrafica_denomzero.csv');
const txDzPath = join(__dirname, 'donors_transazioni_denomzero.csv');
// Robustezza CSV (Compito 1): a-capo DENTRO un campo citato (il parser deve
// leggere la riga intera, 0 esclusioni) e riga genuinamente malformata (n° campi
// != intestazione -> esclusa e contata). Python (csv.DictReader) e' l'oracolo.
const anaQnPath = join(__dirname, 'donors_anagrafica_quotednewline.csv');
const txQnPath = join(__dirname, 'donors_transazioni_quotednewline.csv');
const anaMalPath = join(__dirname, 'donors_anagrafica_malformed.csv');
const txMalPath = join(__dirname, 'donors_transazioni_malformed.csv');
// Compito 2 (robustezza scala): un donatore (ND002) con TUTTE le date non valide
// -> saltato da entrambe le implementazioni (verifica che il fix degli spread
// Math.max/min non alteri il percorso "nessuna data valida").
const anaNvPath = join(__dirname, 'donors_anagrafica_novaliddate.csv');
const txNvPath = join(__dirname, 'donors_transazioni_novaliddate.csv');
// Compito 4 (strato territoriale): 5 donatori x 5 obiettivi -> i 7 stati, con
// ZONE_POSTALI reale e con ZONE_POSTALI svuotata (tutti in dati_non_caricati).
const anaTerrPath = join(__dirname, 'donors_anagrafica_territori.csv');
// Arrotondamento: donatore con punteggio riattivazione su .x5 (21.25) -> 21.3
// (Math.round, mezzo verso +infinito), non 21.2. Confronto a tolleranza zero.
const anaArrPath = join(__dirname, 'donors_anagrafica_arrotondamento.csv');
const txArrPath = join(__dirname, 'donors_transazioni_arrotondamento.csv');
const outPath = join(__dirname, 'js_output.json');

const html = readFileSync(htmlPath, 'utf8');
const anaCsv = readFileSync(anaPath, 'utf8');
const txCsv = readFileSync(txPath, 'utf8');
const anaSsCsv = readFileSync(anaSsPath, 'utf8');
const txSsCsv = readFileSync(txSsPath, 'utf8');
const anaDzCsv = readFileSync(anaDzPath, 'utf8');
const txDzCsv = readFileSync(txDzPath, 'utf8');
const anaQnCsv = readFileSync(anaQnPath, 'utf8');
const txQnCsv = readFileSync(txQnPath, 'utf8');
const anaMalCsv = readFileSync(anaMalPath, 'utf8');
const txMalCsv = readFileSync(txMalPath, 'utf8');
const anaNvCsv = readFileSync(anaNvPath, 'utf8');
const txNvCsv = readFileSync(txNvPath, 'utf8');
const anaTerrCsv = readFileSync(anaTerrPath, 'utf8');
const anaArrCsv = readFileSync(anaArrPath, 'utf8');
const txArrCsv = readFileSync(txArrPath, 'utf8');

// Harness iniettato nello stesso <script> del motore (condivide lo scope).
const harness = `
/* ===== HARNESS EQUIVALENZA (iniettato da run_js.mjs) ===== */
try {
  csvAna = parseCSV(globalThis.__ANA_CSV__);
  csvTx  = parseCSV(globalThis.__TX_CSV__);
  // Mapping identita: le intestazioni del CSV di test sono gia canoniche.
  // NB: NON mappiamo ha_risposto_campagna_lasciti -> calcolaLift ricade su
  // lascito_dichiarato, la stessa colonna usata da valida_lift in Python.
  for (const f of ANA_FIELDS) if (csvAna.headers.includes(f.key)) mapState.ana[f.key] = f.key;
  for (const f of TX_FIELDS)  if (csvTx.headers.includes(f.key))  mapState.tx[f.key]  = f.key;

  // --- replica di eseguiCalcolo(), senza la parte DOM ---
  anaData = buildRenamed(csvAna, mapState.ana, ANA_FIELDS);
  txData  = buildRenamed(csvTx,  mapState.tx,  TX_FIELDS);
  const __fascia = Object.fromEntries(anaData.map(r=>[r.donor_id,(r.fascia_eta||'').trim()]));
  const __cap    = Object.fromEntries(anaData.map(r=>[r.donor_id,(r.cap||'').trim()]));
  const __sg   = calcolaSegnali(anaData, txData);
  const __prop = calcolaProp(__sg, __fascia, __cap);
  const __ctx  = calcolaCtx(anaData);
  risultati = Object.keys(__sg).map(did=>({donor_id:did,...__prop[did],obiettivo_top:OBIETTIVI.reduce((a,b)=>__prop[did][a]>__prop[did][b]?a:b),score_top:maxArr(OBIETTIVI.map(o=>__prop[did][o])),...__sg[did],...__ctx[did],molt_lascito:molt(__cap[did],'lascito'),molt_upgrade:molt(__cap[did],'upgrade'),molt_sostegno_continuativo:molt(__cap[did],'sostegno_continuativo'),molt_riattivazione:molt(__cap[did],'riattivazione'),molt_one_off:molt(__cap[did],'one_off_emergenza')}));
  liftPerOb = {};
  for (const ob of OBIETTIVI) liftPerOb[ob] = calcolaLift(risultati, ob);

  // --- dataset ausiliari (sotto-soglia, denominatore-zero): rieseguo la catena
  // e prendo il lift lascito. calcolaLift legge anaData/mapState globali: li
  // reimposto nel helper (dopo aver gia calcolato il lift del dataset
  // principale, nessun clobber su 'risultati', che resta quello principale).
  const __liftLascitoDi = (anaStr, txStr) => {
    csvAna = parseCSV(anaStr); csvTx = parseCSV(txStr);
    mapState.ana = {}; mapState.tx = {};
    for (const f of ANA_FIELDS) if (csvAna.headers.includes(f.key)) mapState.ana[f.key] = f.key;
    for (const f of TX_FIELDS)  if (csvTx.headers.includes(f.key))  mapState.tx[f.key]  = f.key;
    anaData = buildRenamed(csvAna, mapState.ana, ANA_FIELDS);
    txData  = buildRenamed(csvTx,  mapState.tx,  TX_FIELDS);
    const fa = Object.fromEntries(anaData.map(r=>[r.donor_id,(r.fascia_eta||'').trim()]));
    const ca = Object.fromEntries(anaData.map(r=>[r.donor_id,(r.cap||'').trim()]));
    const sg = calcolaSegnali(anaData, txData);
    const pr = calcolaProp(sg, fa, ca);
    const ris = Object.keys(sg).map(did=>({donor_id:did,...pr[did]}));
    return calcolaLift(ris, 'lascito');
  };
  const liftSottoSoglia = __liftLascitoDi(globalThis.__ANA_SS_CSV__, globalThis.__TX_SS_CSV__);
  const liftDenomZero  = __liftLascitoDi(globalThis.__ANA_DZ_CSV__, globalThis.__TX_DZ_CSV__);

  // --- robustezza CSV (Compito 1): per ciascun dataset restituisco n. righe
  // lette, righe escluse (conteggio campi) e i punteggi per donatore. Usa
  // parseCSV (lo scanner RFC allineato a csv.DictReader) e la stessa catena.
  const __scoreDataset = (anaStr, txStr) => {
    const ca = parseCSV(anaStr), ct = parseCSV(txStr);
    mapState.ana = {}; mapState.tx = {};
    for (const f of ANA_FIELDS) if (ca.headers.includes(f.key)) mapState.ana[f.key] = f.key;
    for (const f of TX_FIELDS)  if (ct.headers.includes(f.key))  mapState.tx[f.key]  = f.key;
    const aD = buildRenamed(ca, mapState.ana, ANA_FIELDS);
    const tD = buildRenamed(ct, mapState.tx, TX_FIELDS);
    const fa = Object.fromEntries(aD.map(r=>[r.donor_id,(r.fascia_eta||'').trim()]));
    const cp = Object.fromEntries(aD.map(r=>[r.donor_id,(r.cap||'').trim()]));
    const sg = calcolaSegnali(aD, tD);
    const pr = calcolaProp(sg, fa, cp);
    const cx = calcolaCtx(aD);
    const don = {};
    for (const did of Object.keys(sg)) {
      don[did] = {donor_id:did, ...pr[did],
        obiettivo_top:OBIETTIVI.reduce((a,b)=>pr[did][a]>pr[did][b]?a:b),
        score_top:maxArr(OBIETTIVI.map(o=>pr[did][o])),
        ...sg[did], ...cx[did],
        molt_lascito:molt(cp[did],'lascito'), molt_upgrade:molt(cp[did],'upgrade'),
        molt_sostegno_continuativo:molt(cp[did],'sostegno_continuativo'),
        molt_riattivazione:molt(cp[did],'riattivazione'), molt_one_off:molt(cp[did],'one_off_emergenza')};
    }
    return {n_ana:ca.rows.length, escluse_ana:ca.escluse, n_tx:ct.rows.length, escluse_tx:ct.escluse, donatori:don};
  };
  const quotednewline = __scoreDataset(globalThis.__ANA_QN_CSV__, globalThis.__TX_QN_CSV__);
  const malformed     = __scoreDataset(globalThis.__ANA_MAL_CSV__, globalThis.__TX_MAL_CSV__);
  const novaliddate   = __scoreDataset(globalThis.__ANA_NV_CSV__, globalThis.__TX_NV_CSV__);
  const arrotondamento = __scoreDataset(globalThis.__ANA_ARR_CSV__, globalThis.__TX_ARR_CSV__);

  // Compito 2: semantica dell'helper maxArr/minArr che sostituisce lo spread.
  // Deve coincidere con Math.max/Math.min: vuoto -> ∓Infinity; un NaN -> NaN.
  // (Serializzo a stringa: JSON trasforma Infinity/NaN in null.)
  const helperSemantics = {
    maxEmpty: String(maxArr([])), minEmpty: String(minArr([])),
    maxNaN: String(maxArr([1, NaN, 2])), minNaN: String(minArr([1, NaN, 2])),
    maxVal: String(maxArr([3, 1, 2])), minVal: String(minArr([3, 1, 2])),
  };

  // Compito 4: matrice {donor_id:{obiettivo:stato}} dei 7 stati territoriali.
  // Dipende solo da CAP e obiettivo (territorio()), non dai segnali.
  const __statiTerr = (anaStr) => {
    const ca = parseCSV(anaStr); mapState.ana = {};
    for (const f of ANA_FIELDS) if (ca.headers.includes(f.key)) mapState.ana[f.key] = f.key;
    const aD = buildRenamed(ca, mapState.ana, ANA_FIELDS);
    const out = {};
    for (const r of aD) { out[r.donor_id] = {}; for (const ob of OBIETTIVI) out[r.donor_id][ob] = territorio((r.cap||'').trim(), ob).stato; }
    return out;
  };
  const statiReale = __statiTerr(globalThis.__ANA_TERR_CSV__);
  // tabella vuota: svuoto ZONE_P in loco (const = binding, oggetto mutabile), poi ripristino.
  const __zbak = JSON.parse(JSON.stringify(ZONE_P));
  for (const k in ZONE_P) delete ZONE_P[k];
  const statiVuota = __statiTerr(globalThis.__ANA_TERR_CSV__);
  for (const k in ZONE_P) delete ZONE_P[k]; Object.assign(ZONE_P, __zbak);
  const statiTerritoriali = { reale: statiReale, vuota: statiVuota };

  globalThis.__OUT__ = JSON.stringify({ risultati, lift: liftPerOb, liftSottoSoglia, liftDenomZero, quotednewline, malformed, novaliddate, arrotondamento, helperSemantics, statiTerritoriali });
} catch (e) {
  globalThis.__ERR__ = String((e && e.stack) || e);
}
`;

// Inietta prima della </script> che chiude il <script> inline del motore.
// (Lo <script src=...> XLSX ha attributi: la stringa esatta "<script>" non lo matcha.)
const openIdx = html.indexOf('<script>');
if (openIdx === -1) { console.error('Impossibile trovare il <script> inline del motore.'); process.exit(2); }
const closeIdx = html.indexOf('</script>', openIdx);
const injectedHtml = html.slice(0, closeIdx) + '\n' + harness + '\n' + html.slice(closeIdx);

const dom = new JSDOM(injectedHtml, {
  runScripts: 'dangerously',
  // resources non abilitate di proposito: XLSX dal CDN non viene scaricato.
  beforeParse(window) {
    window.__ANA_CSV__ = anaCsv;
    window.__TX_CSV__ = txCsv;
    window.__ANA_SS_CSV__ = anaSsCsv;
    window.__TX_SS_CSV__ = txSsCsv;
    window.__ANA_DZ_CSV__ = anaDzCsv;
    window.__TX_DZ_CSV__ = txDzCsv;
    window.__ANA_QN_CSV__ = anaQnCsv;
    window.__TX_QN_CSV__ = txQnCsv;
    window.__ANA_MAL_CSV__ = anaMalCsv;
    window.__TX_MAL_CSV__ = txMalCsv;
    window.__ANA_NV_CSV__ = anaNvCsv;
    window.__TX_NV_CSV__ = txNvCsv;
    window.__ANA_TERR_CSV__ = anaTerrCsv;
    window.__ANA_ARR_CSV__ = anaArrCsv;
    window.__TX_ARR_CSV__ = txArrCsv;
  },
});

const w = dom.window;
if (w.__ERR__) {
  console.error('Errore nel motore JS:\n' + w.__ERR__);
  process.exit(1);
}
if (!w.__OUT__) {
  console.error('Il motore JS non ha prodotto output (__OUT__ assente).');
  process.exit(1);
}

const out = JSON.parse(w.__OUT__);
writeFileSync(outPath, JSON.stringify(out, null, 2), 'utf8');
console.log(`JS: ${out.risultati.length} donatori -> ${outPath}`);
