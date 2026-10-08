# Bus FCE – orari da fermata a fermata

App per Android (PWA) che mostra gli orari delle autolinee della Ferrovia Circumetnea scegliendo partenza e arrivo.
I dati vengono estratti in automatico dai PDF pubblicati su <https://www.circumetnea.it/le-nostre-linee/> e si aggiornano da soli quando FCE pubblica un nuovo orario.

## Come funziona

```
sito FCE ──► pipeline/update.py ──► pipeline/parse_pdf.py ──► normalize.py ──► validate.py ──► docs/data/*.json ──► app (docs/)
            (trova i PDF nuovi)     (legge le tabelle)        (fermate, regole)  (controlli)      (dati per l'app)
```

- **Parser generico**: non contiene regole per singole linee. Ricostruisce la griglia delle tabelle dai bordi del PDF e gestisce celle unite, doppi orari arrivo/partenza (es. `06.19 / 06.20`), le `x` di transito, le note scritte in verticale e i refusi come `7,3`.
- **Regole dei giorni** lette dal PDF: corse scolastiche (`S`, `*`), sospese il sabato (`C`, note tipo "le corse 354 - 356 sono sospese il sabato"), bus sostitutivi del treno, servizio sospeso domenica e festivi.
- **Sicurezza**: se un nuovo PDF non supera i controlli, i dati vecchi restano pubblicati, l'app mostra un avviso e ricevi una notifica.
- **Versioni**: se FCE pubblica un orario in anticipo ("in vigore dal 5 ottobre"), l'app passa al nuovo orario da sola in quella data.

## Struttura

| Percorso | Contenuto |
|---|---|
| `pipeline/` | codice Python (scraper, parser, normalizzazione, validazione) |
| `config/aliases.yaml` | nomi delle fermate e varianti da unire |
| `config/rules.yaml` | regole per linea (es. tratte senza servizio locale sulla linea via A18) |
| `config/calendar.yaml` | festivi e calendario scolastico siciliano **(da aggiornare ogni anno)** |
| `config/sources.yaml` | pagina del sito FCE e categorie di PDF da elaborare |
| `docs/` | l'app (HTML/JS) e i dati generati in `docs/data/` |
| `pdf_inbox/` | metti qui un PDF a mano per farlo elaborare |
| `pdf_archive/` | PDF già elaborati |
| `state/reports/` | report di ogni elaborazione: corse scartate, avvisi, elenco fermate |
| `prepara_github.bat` | crea `Desktop\FCE_github`, la copia da caricare su GitHub |
| `tests/` | controlli automatici, inclusi orari verificati a mano sulla linea Randazzo–Catania via A18 |

## Uso sul PC (Windows)

1. Installa Python 3.11 o più recente da <https://www.python.org/downloads/> spuntando **"Add python.exe to PATH"**.
2. Doppio clic su `installa.bat` (solo la prima volta).
3. Doppio clic su `aggiorna.bat`: controlla il sito FCE ed elabora i PDF nuovi (utile per provare; online lo fa GitHub da solo).
4. Doppio clic su `avvia_app.bat`: apre l'app nel browser su <http://localhost:8000>.

Solo come piano di emergenza, se un giorno il download automatico smettesse di funzionare: scarica il PDF dal sito FCE, mettilo in `pdf_inbox\` e rilancia `aggiorna.bat` (o caricalo nella cartella `pdf_inbox` del repository).

Comando diretto per un singolo PDF:

```
.venv\Scripts\python -m pipeline.build --pdf percorso\del\file.pdf
```

## Pubblicazione online con aggiornamento automatico (consigliato)

Una volta configurato non devi più fare niente: GitHub controlla il sito FCE ogni 2 ore (di giorno) più una volta di notte. Se trova un PDF nuovo o sostituito lo scarica, lo converte, lo verifica e ripubblica l'app. Tu apri la pagina e vedi gli orari aggiornati.

1. Crea un account su <https://github.com> e un **nuovo repository pubblico** (GitHub Pages gratuito richiede un repository pubblico).
2. Doppio clic su `prepara_github.bat`: crea sul Desktop la cartella **`FCE_github`**, una copia pulita senza file locali. Poi con **GitHub Desktop**: *File → Add local repository* → scegli `FCE_github` → *Publish repository* (togli la spunta "Keep this code private"). Non caricare i file trascinandoli sul sito di GitHub: la cartella nascosta `.github` verrebbe saltata.
3. Nel repository: **Settings → Pages → Build and deployment → Source = "GitHub Actions"**.
4. **Settings → Actions → General → Workflow permissions**: seleziona "Read and write permissions" e salva.
5. Vai su **Actions → "Aggiorna orari FCE" → Run workflow**. Alla fine trovi l'indirizzo dell'app nel riquadro del job (di solito `https://TUONOME.github.io/NOMEREPO/`).

Il primo avvio scarica tutti gli orari autolinee presenti sul sito (anche quelli già pubblicati con data futura). Da lì in poi:
- un PDF nuovo viene rilevato entro circa 2 ore;
- un PDF sostituito con lo stesso titolo viene rilevato entro un giorno (confronto dell'impronta del file);
- se il nuovo PDF non supera i controlli (o non si riesce a scaricare), l'app continua a mostrare l'orario precedente e in cima alla pagina compare un **allarme rosso** fisso, con il link al PDF ufficiale e il motivo. L'allarme sparisce da solo appena un PDF valido viene pubblicato;
- se GitHub non riesce a raggiungere il sito FCE, l'app mostra da quanti giorni non c'è un controllo riuscito.

Ogni giorno viene salvata la data dell'ultimo controllo: questo tiene attivo il repository, perché GitHub sospende i workflow pianificati dopo 60 giorni senza attività.

### Notifiche Telegram (facoltative)

1. Su Telegram scrivi a **@BotFather**, comando `/newbot`, e copia il **token**.
2. Scrivi un messaggio qualsiasi al tuo nuovo bot, poi apri `https://api.telegram.org/bot<TOKEN>/getUpdates` e copia il numero `chat.id`.
3. Nel repository: **Settings → Secrets and variables → Actions → New repository secret**: crea `TELEGRAM_BOT_TOKEN` e `TELEGRAM_CHAT_ID`.

Riceverai un messaggio quando un nuovo orario viene pubblicato, quando un PDF non supera i controlli o quando il sito non risponde.

## Uso dell'app

- L'ora di partenza predefinita è **quella attuale**: la lista mostra i bus da adesso in poi e si aggiorna da sola ogni minuto. Se cambi l'orario, compare il pulsante **Adesso** per tornare all'ora corrente.
- Quando riapri l'app (anche il giorno dopo) riparte da oggi e da adesso, e controlla se sono stati pubblicati orari nuovi.
- Se FCE pubblica un orario con data futura, l'app usa automaticamente quello giusto per il giorno scelto.
- Per i paesi con più fermate c'è la voce **"<Paese> – tutte le fermate"** (es. "Catania – tutte le fermate"): come arrivo vale la prima fermata del paese raggiunta dal bus, come partenza la prima in cui passa. Il paese di ogni fermata si ricava dal nome; le eccezioni sono in `config/aliases.yaml` (sezione `comuni`).
- Se non c'è un bus diretto, o con un cambio si arriva prima, l'app propone viaggi con **un cambio**. Non propone cambi tra due corse della stessa linea, attese oltre 90 minuti o viaggi oltre 3 ore.

## Installare l'app su Android

Apri l'indirizzo GitHub Pages con **Chrome**, menu ⋮ → **Aggiungi a schermata Home** (o "Installa app"). Funziona anche offline con l'ultimo orario scaricato.

## Manutenzione

- **Ogni estate**: aggiorna `anni_scolastici` in `config/calendar.yaml` con il calendario della Regione Sicilia (servono per le corse scolastiche). Per il 2026/27 la fine lezioni è riportata in modo discordante (9 o 10 giugno): verificala a maggio.
- **Dopo ogni nuovo orario**: dai un'occhiata a `state/reports/report-AAAA-MM-GG.json`. Il campo `fermate` elenca tutte le fermate trovate: se due nomi indicano la stessa fermata, aggiungi una variante in `config/aliases.yaml`.
- **Note particolari** che non stanno nelle tabelle (es. "non si effettua servizio per salita passeggeri") vanno in `config/rules.yaml`. Se la nota compare in un PDF ma manca la regola, il report lo segnala.
- **Test**: `python -m tests.test_orari`. I "golden test" confrontano alcune corse della linea via A18 con il PDF del 15 settembre 2026 controllato a mano.

## Limiti noti

- La struttura della pagina FCE e il download diretto del PDF sono stati verificati, ma la prima esecuzione da GitHub è il vero test: se il sito bloccasse i server di GitHub, lo vedrai nell'avviso dell'app e nella notifica Telegram.
- Il PDF del 5 ottobre 2026 introduce fermate nuove (es. "Terminal Fontana", "Monte Palma"): dopo il primo aggiornamento controlla l'elenco `fermate` nel report e, se serve, unisci i doppioni in `config/aliases.yaml`.
- Sono elaborati solo i PDF **autolinee**. Treni e metro si possono aggiungere in `config/sources.yaml`, ma il parser non è stato verificato su quei PDF.
- Le tabelline di collegamento (es. "Collegamento Castiglione–Linguaglossa") indicano solo gli orari di partenza: l'arrivo è stimato con la durata in `config/rules.yaml` (`collegamenti`) e nell'app è mostrato con "~".
- Non vengono lette alcune note scritte in verticale dentro le colonne (es. "partenza Ospedale Biancavilla"), né l'elenco delle sotto-fermate di Ragalna, che nel PDF non ha orari. Gli orari delle fermate in tabella sono comunque corretti.
- Le feste patronali sono mostrate solo come avviso: i PDF FCE non dicono se il servizio cambia.
- La ricerca trova i bus diretti e i viaggi con **un cambio** alla stessa fermata (minimo 3 minuti, anche immediato per le navette di collegamento, attesa massima 90 minuti, viaggio massimo 3 ore). Non considera cambi a piedi tra fermate diverse.
- In caso di dubbio fa fede l'orario ufficiale FCE.
