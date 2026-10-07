# Banco di prova — equivalenza Python ⇄ JS (Modulo Propensione RFML)

Verifica che le **due implementazioni** del motore di scoring facciano la stessa
cosa sugli stessi input:

- **JS** — `propensione_dashboard.html`, funzioni `calcolaSegnali` / `calcolaProp`
  / `calcolaCtx` / `calcolaLift` (righe ~1289-1359).
- **PY** — `polisim_propensione.py`, funzioni `calcola_segnali` /
  `calcola_propensione` / `calcola_contesto` / `valida_lift`.

> ⚠️ **Questo NON è la baseline storica del modulo.** Il dataset è un banco di
> prova sintetico, costruito APPOSTA per far emergere le divergenze. I numeri
> prodotti qui **non** vanno confrontati con output pubblicati in passato:
> servono solo a confrontare le due implementazioni *fra loro*.

## Come si esegue

```bash
cd tests/propensione
npm install        # una volta: installa jsdom
python equivalence_test.py
```

Il test:
1. lancia `node run_js.mjs` → `js_output.json`;
2. lancia `python run_py.py` → `py_output.json`;
3. confronta donatore per donatore, campo per campo;
4. scrive `divergenze_report.txt` ed esce con **code 1 se divergono**, 0 se
   equivalenti entro tolleranza.

Da rieseguire dopo ogni correzione: quando il motore JS e quello Python saranno
allineati, il test passerà (exit 0).

## Tolleranza (perché questi valori)

Gli output sono arrotondati, quindi la tolleranza è fissata a **1 ULP
dell'output arrotondato** — il massimo scarto che una differenza di
arrotondamento cross-linguaggio può produrre (Python `round()` = half-to-even,
JS `Math.round()` = half-up):

| campo | arrotondamento | tolleranza |
|---|---|---|
| `prop_*`, `score_top` | 1 decimale | **0.1** |
| `R`, `F`, `M_livello`, `M_trend`, `L` | 4 decimali | **1e-4** |
| `molt_*` | 4 decimali | **1e-4** |
| tassi lift (%) | 1 decimale | **0.1** |
| lift (rapporto) | 2 decimali | **0.01** |
| `_recency_days`, `_n_tx`, campi categorici | — | **esatto** |

Scarti **oltre** 1 ULP = divergenza logica → FAIL. Scarti non nulli ma **entro**
tolleranza = avviso (livello-arrotondamento), non fanno fallire.

## Nota sul delimitatore (divergenza D0)

Il dataset usa `;` come separatore, perché i casi-limite europei (`1.000,50`,
`10,50`) contengono virgole che un file `,`-delimitato spezzerebbe. Il parser JS
(`parseCSV`) **auto-rileva** `;` vs `,`; il modulo Python (`leggi_csv`) **hardcoda
`,`** e sul file `;` collasserebbe tutto in un'unica colonna. È una divergenza
reale (D0). Per isolare le divergenze *numeriche* da questo problema di I/O,
`run_py.py` legge con `delimiter=';'` e chiama le funzioni pure del modulo.

## Mappa dataset → divergenza stressata

Il dataset è minimale (15 donatori) e ogni riga punta a un bersaglio preciso:

| donor | cosa stressa | effetto atteso |
|---|---|---|
| D001-D005 | dati puliti, `lascito_dichiarato="1"` | controllo + 5 positivi "veri" |
| D006 | date **tutte** `YYYY/MM/DD` (non supportato da `pD`) | JS scarta il donatore; PY lo tiene → **row-set divergente** |
| D007 | date **miste** (2 valide + 2 `YYYY/MM/DD`) | JS 2 tx, PY 4 tx → R/F/M/trend diversi |
| D008 | importi con **separatore migliaia** `1.000,50` | PY→0.0 (ValueError), JS→1.0 (`parseFloat` tronca) |
| D009 | importi con **coda non numerica** `50abc` | PY→0.0, JS→50 (`parseFloat` legge il prefisso) |
| D010 | data **a una cifra** `2024-3-5` | PY la parsa (`strptime`), JS no (regex `\d{2}`) |
| D011 | `lascito_dichiarato="1.0"` | PY conta positivo, JS no (`==='1'`) |
| D012 | `lascito_dichiarato="2"` | PY conta positivo, JS no |
| D013 | `lascito_dichiarato="SI"` | **entrambi** escludono (`parse_float("SI")=0`) |
| D014 | `lascito_dichiarato="true"` | **entrambi** escludono |
| D015 | 2 transazioni (<3) | ramo `costanza=0.3`, nessun trend |

> Nota: D013/D014 dimostrano che l'ipotesi iniziale su `"SI"`/`"true"` **non**
> è una divergenza in questo codice: `parse_float` non li parsa, quindi il
> Python li tratta come 0 — come il JS. Le vere divergenze di conteggio sono
> `"1.0"`, `"2"` e simili (numerici ≠ stringa `"1"`).

## File

| file | ruolo |
|---|---|
| `donors_anagrafica_test.csv` / `donors_transazioni_test.csv` | dataset `;`-delimitato |
| `run_js.mjs` | esegue il motore JS via jsdom → `js_output.json` |
| `run_py.py` | esegue il motore Python → `py_output.json` |
| `equivalence_test.py` | orchestratore + confronto + verdetto |
| `divergenze_report.txt` | report leggibile dell'ultima esecuzione |
