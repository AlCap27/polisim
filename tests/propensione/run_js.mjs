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
const outPath = join(__dirname, 'js_output.json');

const html = readFileSync(htmlPath, 'utf8');
const anaCsv = readFileSync(anaPath, 'utf8');
const txCsv = readFileSync(txPath, 'utf8');
const anaSsCsv = readFileSync(anaSsPath, 'utf8');
const txSsCsv = readFileSync(txSsPath, 'utf8');

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
  risultati = Object.keys(__sg).map(did=>({donor_id:did,...__prop[did],obiettivo_top:OBIETTIVI.reduce((a,b)=>__prop[did][a]>__prop[did][b]?a:b),score_top:Math.max(...OBIETTIVI.map(o=>__prop[did][o])),...__sg[did],...__ctx[did],molt_lascito:molt(__cap[did],'lascito'),molt_upgrade:molt(__cap[did],'upgrade'),molt_sostegno_continuativo:molt(__cap[did],'sostegno_continuativo'),molt_riattivazione:molt(__cap[did],'riattivazione'),molt_one_off:molt(__cap[did],'one_off_emergenza')}));
  liftPerOb = {};
  for (const ob of OBIETTIVI) liftPerOb[ob] = calcolaLift(risultati, ob);

  // --- segmento sotto-soglia: rieseguo la catena sul 2o dataset e prendo il
  // lift lascito. calcolaLift legge anaData/mapState globali: li reimposto qui
  // (dopo aver gia calcolato il lift del dataset principale, nessun clobber).
  csvAna = parseCSV(globalThis.__ANA_SS_CSV__);
  csvTx  = parseCSV(globalThis.__TX_SS_CSV__);
  mapState.ana = {}; mapState.tx = {};
  for (const f of ANA_FIELDS) if (csvAna.headers.includes(f.key)) mapState.ana[f.key] = f.key;
  for (const f of TX_FIELDS)  if (csvTx.headers.includes(f.key))  mapState.tx[f.key]  = f.key;
  anaData = buildRenamed(csvAna, mapState.ana, ANA_FIELDS);
  txData  = buildRenamed(csvTx,  mapState.tx,  TX_FIELDS);
  const __fasciaSs = Object.fromEntries(anaData.map(r=>[r.donor_id,(r.fascia_eta||'').trim()]));
  const __capSs    = Object.fromEntries(anaData.map(r=>[r.donor_id,(r.cap||'').trim()]));
  const __sgSs   = calcolaSegnali(anaData, txData);
  const __propSs = calcolaProp(__sgSs, __fasciaSs, __capSs);
  const __risSs = Object.keys(__sgSs).map(did=>({donor_id:did,...__propSs[did]}));
  const liftSottoSoglia = calcolaLift(__risSs, 'lascito');

  globalThis.__OUT__ = JSON.stringify({ risultati, lift: liftPerOb, liftSottoSoglia });
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
