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
(`parseCSV`) **auto-rileva** `;` vs `,`.

**D0 RISOLTO.** Il modulo Python (`leggi_csv`) prima hardcodava `,` e sul file `;`
collassava tutto in un'unica colonna (errore silenzioso: "campi tutti vuoti", non
un'eccezione). Ora `leggi_csv` **auto-rileva** il delimitatore con la stessa
euristica del dashboard (`;` se presente nell'intestazione, altrimenti `,`), quindi
`python polisim_propensione.py` gira direttamente sui CSV `;`-delimitati. Il
workaround in `run_py.py` (`delimiter=';'` sulle funzioni pure) resta ma è ormai
ridondante.

## Mappa dataset → divergenza stressata

> **STATO: tutte le divergenze D0–D7 sono risolte, il test PASSA (exit 0).** La
> colonna "cosa stressava" documenta il bersaglio *originale* di ogni riga; la
> colonna "esito dopo fix" descrive il comportamento attuale, ora identico fra
> JS e Python. Vedi la storia delle correzioni in fondo.

Ogni riga punta a un bersaglio preciso:

| donor | cosa stressava | esito dopo fix (JS = PY) |
|---|---|---|
| D001-D005 | dati puliti, `lascito_dichiarato="1"` | 5 positivi "veri" |
| D006 | date **tutte** `YYYY/MM/DD` | `pD` accetta `YYYY/MM/DD` → donatore tenuto da entrambi |
| D007 | date **miste** (2 valide + 2 `YYYY/MM/DD`) | tutte 4 le tx parsate da entrambi |
| D008 | importi `1.000,50` (migliaia+decimali) | parse europeo → `1000.50` in entrambi |
| D009 | importi con **coda non numerica** `50abc` | prefisso `50` recuperato **e contato** in entrambi |
| D010 | data **a una cifra** `2024-3-5` | `pD` accetta 1-2 cifre → parsata da entrambi |
| D011 | `lascito_dichiarato="1.0"` | `flag_positivo` → positivo (numerico ≠ 0) in entrambi |
| D012 | `lascito_dichiarato="2"` | positivo (numerico ≠ 0) in entrambi |
| D013 | `lascito_dichiarato="SI"` | `flag_positivo` → **positivo** (vocabolario testuale) in entrambi |
| D014 | `lascito_dichiarato="true"` | **positivo** (vocabolario) in entrambi |
| D015 | 2 transazioni (<3) | ramo `costanza=0.3`, nessun trend |
| D016 | importo `1.000` (punto + 3 cifre, no virgola) | **ambiguo** → scartato (0) **e contato** in entrambi |
| D017 | importo `50.5` (punto + 1 cifra) | decimale anglosassone → `50.5` in entrambi |
| D018 | importo `1.000.000` (multi-punto) | migliaia → `1000000` in entrambi |
| D019 | importo `0,5` (virgola decimale) | europeo → `0.5` in entrambi |
| D020 | `lascito_dichiarato="x"` (spunta) | positivo **tracciato a parte** (X = convenzione da verificare) |
| D021 | `lascito_dichiarato="forse"` | **ignoto** → escluso dai positivi, **non** negativo, contato |

### Segmento sotto-soglia (lift non significativo)

Oltre al banco principale (21 donatori, 10 positivi → lift calcolato), un secondo
dataset `donors_*_sottosoglia.csv` (8 donatori, **3 positivi** su
`lascito_dichiarato`) copre la soglia minima di positivi: sotto
`SOGLIA_LIFT_POSITIVI` (5, nel JSON condiviso) il lift **non** si calcola né si
mostra — con così pochi positivi il top-20% ne contiene 0-1 e il rapporto
oscilla senza base. `equivalence_test.py` verifica che JS **e** Python
dichiarino entrambi `sotto_soglia`, riportino lo stesso conteggio di positivi, e
**non** producano un numero di lift. Prima di questa correzione Python avvisava e
non calcolava, il JS calcolava comunque (asimmetria, era fuori da D0-D7).

### Lift multi-obiettivo (colonna esito per obiettivo)

`valida_lift` (Python) e `calcolaLift` (JS) misurano il lift di **ogni** obiettivo
sulla **sua** colonna esito-campagna, non solo i lasciti: la mappa
obiettivo→colonna vive in `rfml_config.json` (`ESITO_COLONNE`, con l'unico ripiego
`ESITO_FALLBACK`: `lascito`→`lascito_dichiarato`), letta da entrambi — dati, non
logica duplicata. Il banco copre i tre esiti possibili, verificando che JS e Python
concordino su ciascuno:

| obiettivo | colonna esito nel dataset | esito atteso (JS = PY) |
|---|---|---|
| `lascito` | `lascito_dichiarato` (ripiego) | **calcolato** (lift 2.83×, 10 positivi) |
| `riattivazione` | `ha_risposto_campagna_riattivazione` | **calcolato** (valori sintetici: esercita il percorso completo su un obiettivo ≠ lascito) |
| `upgrade`, `sostegno_continuativo`, `one_off_emergenza` | assente | **colonna_assente** → messaggio esplicito "lift non calcolabile, colonna esito assente" |

La colonna `ha_risposto_campagna_riattivazione` è stata aggiunta al dataset
principale solo per esercitare il lift su un obiettivo non-lascito: i suoi valori
sono sintetici, conta l'**equivalenza** JS/PY e la copertura dei tre esiti, non il
valore del lift in sé. Prima di questa correzione il Python calcolava il lift
**sempre** su `lascito_dichiarato` qualunque fosse l'obiettivo, mentre il JS usava
la colonna giusta: asimmetria latente (fuori da D0-D7) che la calibrazione sui dati
reali — fatta col Python — avrebbe reso un problema il giorno degli esiti di
riattivazione/upgrade.

### Segmento denominatore-zero (lift non definito)

Quarto esito possibile accanto a calcolato / sotto-soglia / colonna-assente:
quando i positivi superano la soglia **ma cadono tutti nel top 20% per score** (0
nel resto), il lift non è un numero — manca il termine di paragone. Né `"inf"` né
`null` direbbero cosa è successo: entrambe le implementazioni restituiscono uno
stato esplicito `denominatore_zero`/`denominatoreZero`, senza numero, con il
messaggio vero («tutti gli N positivi nel top 20%, 0 nel resto») e i **conteggi
visibili** (positivi e numerosità dei due gruppi). È il risultato che qualcuno
leggerebbe come "modello perfetto": su un segmento piccolo è quasi sempre un
artefatto. Il dataset `donors_*_denomzero.csv` (30 donatori: 5 positivi dal
profilo lascito fortissimo + 25 deboli) è costruito apposta perché i 5 positivi
occupino il top 20%; `equivalence_test.py` verifica che JS e Python concordino
sullo stato e sui conteggi (positivi 5, nel top 5/6, nel resto 0/24).

> Nota storica su D013/D014: alla baseline `parse_float("SI")=0` li faceva
> escludere da **entrambe** le implementazioni (non era un disallineamento
> JS/PY). Con D5 (helper condiviso `flag_positivo`) ora `SI`/`true` sono
> riconosciuti come positivi testuali — un falso-negativo silenzioso corretto in
> entrambe. I valori fuori vocabolario (es. `forse`, D021) non sono negativi: sono
> contati e mostrati, come per date e importi.

## File

| file | ruolo |
|---|---|
| `donors_anagrafica_test.csv` / `donors_transazioni_test.csv` | dataset `;`-delimitato (banco principale, 21 donatori) |
| `donors_anagrafica_sottosoglia.csv` / `donors_transazioni_sottosoglia.csv` | segmento con <5 positivi: lift non significativo |
| `donors_anagrafica_denomzero.csv` / `donors_transazioni_denomzero.csv` | segmento con positivi tutti nel top 20%: lift non definito (denominatore zero) |
| `run_js.mjs` | esegue il motore JS via jsdom → `js_output.json` |
| `run_py.py` | esegue il motore Python → `py_output.json` |
| `equivalence_test.py` | orchestratore + confronto + verdetto (esegue anche `build_config.py --check`) |
| `divergenze_report.txt` | report leggibile dell'ultima esecuzione |
| `../../rfml_config.json` | **fonte unica** di tabelle/costanti RFML (pesi, ISTAT, CAP, bonus) |
| `../../build_config.py` | inietta il JSON nel dashboard (blocco `<script id="rfml-config">`); `--check` verifica la sincronia |

## Storia delle correzioni

Partenza: alla baseline tutte le 14 coppie in comune divergevano + 1 row-set
(D006), e il lift era diverso. Ordine di lavoro: **prima la config condivisa, poi
le logiche.**

1. **Config condivisa (D6, segno peso gini).** Estratte tutte le tabelle/costanti
   duplicate in `rfml_config.json` (fonte unica, con `fonte_dati`/`verificato` per
   tabella). Python la legge a runtime; il dashboard la riceve iniettata da
   `build_config.py` (embed, non `fetch`: la dashboard gira offline via `file://`).
   `equivalence_test.py` esegue `build_config.py --check` e fallisce se l'HTML è
   fuori sincrono. D6 (peso `gini_penalita` canonico **positivo** 0.20 + inversione
   nella formula; il vecchio JS `-0.20` era doppia negazione) corretto una volta sola.
2. **D1+D2 (date).** `pD` (JS) riscritto: formati documentati `YYYY-MM-DD`,
   `YYYY/MM/DD`, `DD/MM/YYYY` (giorno/mese 1-2 cifre) + validazione round-trip
   contro l'overflow di `Date`. Date ignote **contate** (`contaDateNonValide` /
   `conta_date_non_valide`), mai scartate in silenzio. Chiusa la cascata date.
3. **D3+D4 (importi).** Parser europeo condiviso (`pImporto` / `parse_importo`):
   virgola → europeo; punto+3 cifre senza virgola → **ambiguo**, scartato e contato;
   punto+1/2/4+ cifre → decimale anglosassone; multi-punto a gruppi di 3 → migliaia;
   coda non numerica → prefisso recuperato **ma contato**. Chiusa la cascata importi.
4. **D5 (conteggio positivi lift).** Helper condiviso `flag_positivo` / `flagPositivo`
   (vocabolario numerico + testuale, `x` tracciato a parte, valori ignoti contati e
   mostrati **accanto al lift**).
5. **D7 (cascata min-max).** Non un bug a sé: era la somma di D1-D4 che spostava i
   range di normalizzazione. Chiusa come conseguenza.
6. **D0 (delimitatore CSV).** `leggi_csv` auto-rileva `;`/`,` come il dashboard.
7. **Soglia lift <5 positivi (fuori D0-D7).** Python avvisava e non calcolava il
   lift sotto 5 positivi; il JS lo calcolava comunque — stesso difetto di fondo di
   D0-D7 (un numero prodotto senza base sufficiente). Soglia spostata nel JSON
   condiviso (`SOGLIA_LIFT_POSITIVI`, con `fonte_dati`: convenzione statistica, non
   calibrata), JS allineato (guard + avviso **nella card del lift**, non in console),
   e coperta dal segmento sotto-soglia del banco.
8. **Lift multi-obiettivo (fuori D0-D7).** `valida_lift` (Python) usava sempre
   `lascito_dichiarato` qualunque fosse l'obiettivo; il JS usava la colonna giusta.
   Parametrizzata sulla colonna esito dell'obiettivo, con la mappa
   obiettivo→colonna (`ESITO_COLONNE` + `ESITO_FALLBACK`) spostata nel JSON
   condiviso. Colonna assente → esito **esplicito** ("lift non calcolabile, colonna
   esito assente"), né errore né silenzio, su entrambi i lati. Coperto estendendo il
   banco a `riattivazione` (colonna presente) e agli obiettivi senza colonna.
9. **Denominatore zero (fuori D0-D7).** Con `tasso_resto=0` il Python produceva la
   stringa `"inf"`, il JS `null`: due rappresentazioni, nessuna delle quali dice
   cosa è successo. Introdotto un quarto stato esplicito `denominatore_zero` —
   niente numero, messaggio vero (tutti i positivi nel top 20%) e conteggi dei due
   gruppi visibili — su entrambi i lati; eliminato l'`"inf"` dal Python. La
   condizione `if(l.lift)` nel render è diventata `if(l.lift!=null)` così un lift
   legittimo di `0` (top 20% anti-predittivo) si mostra invece di cadere nel ramo
   "non calcolabile". Coperto da `donors_*_denomzero.csv`.

Esito finale: **PASS** (exit 0), 21 vs 21 donatori, 0 divergenze logiche. Lift per
obiettivo concorde JS/PY (lascito 2.83×, riattivazione calcolato, 3 obiettivi
colonna-assente) + segmenti ausiliari concordi: sotto-soglia (3 positivi, nessun
lift) e denominatore-zero (5 positivi tutti nel top 20%, lift non definito).
Scarti residui = solo arrotondamento (`round` half-to-even vs `Math.round`
half-up), entro 1 ULP.
