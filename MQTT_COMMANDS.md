# MQTT Commands

Questa lista descrive tutti i comandi MQTT supportati dal firmware attuale.

## Topic usati

- Pubblica i comandi su: `command`
- Ricevi stato su: `info`
- Ricevi log/errori su: `log`

## 1) Richiesta stato

Payload su `command`:

```json
{
  "action": "get_info"
}
```

Effetto:
- Pubblica su `info` lo stato corrente (ora, relay, programmi, stato sequenza).

Esempio di output su `info`:

```json
{
  "reason": "on_demand",
  "time": "2026-04-28T06:30:00",
  "rainMode": false,
  "relays": [false, true, false, false, false, false, false, false],
  "programs": [
    {
      "id": "irrigazione_mattina",
      "startHour": 6,
      "startMinute": 30,
      "enabled": true,
      "sequence": [
        { "relay": 2, "duration": 10 },
        { "relay": 4, "duration": 7 }
      ]
    }
  ],
  "sequenceRunning": false
}
```

Oppure, se un programma e in esecuzione:

```json
{
  "reason": "on_demand",
  "time": "2026-04-28T06:30:00",
  "rainMode": false,
  "relays": [false, true, false, false, false, false, false, false],
  "programs": [
    {
      "id": "irrigazione_mattina",
      "startHour": 6,
      "startMinute": 30,
      "enabled": true,
      "sequence": [
        { "relay": 2, "duration": 10 },
        { "relay": 4, "duration": 7 }
      ]
    }
  ],
  "sequenceRunning": true,
  "runningProgramId": "irrigazione_mattina",
  "runningStep": 3
}
```

## 2) Crea o aggiorna un programma

Payload su `command`:

```json
{
  "action": "set_program",
  "id": "irrigazione_mattina",
  "start": { "hour": 6, "minute": 30 },
  "enabled": true,
  "sequence": [
    { "relay": 2, "duration": 10 },
    { "relay": 4, "duration": 7 },
    { "relay": 1, "duration": 5 }
  ]
}
```

Regole:
- `id`: identificativo univoco programma.
- `start.hour`: 0..23
- `start.minute`: 0..59
- `enabled`: true/false
- `sequence`: lista step in serie
- Ogni step:
  - `relay`: 1..8 (accettato anche 0..7 internamente)
  - `duration`: minuti (> 0)

Effetto:
- Salva (o aggiorna) il programma in `/programs.json`.
- All'orario di start, esegue la sequenza in serie (un relay per volta).

Esempio di output su `info` dopo il salvataggio:

```json
{
  "reason": "program_saved",
  "time": "2026-04-28T06:31:10",
  "relays": [false, false, false, false, false, false, false, false],
  "programs": [
    {
      "id": "irrigazione_mattina",
      "startHour": 6,
      "startMinute": 30,
      "enabled": true,
      "sequence": [
        { "relay": 2, "duration": 10 },
        { "relay": 4, "duration": 7 },
        { "relay": 1, "duration": 5 }
      ]
    }
  ],
  "sequenceRunning": false
}
```

## 3) Abilita un programma

Payload su `command`:

```json
{
  "action": "enable_program",
  "id": "irrigazione_mattina"
}
```

Effetto:
- Imposta `enabled = true` per il programma.

Esempio di output su `info`:

```json
{
  "reason": "program_toggled",
  "time": "2026-04-28T06:32:00",
  "relays": [false, false, false, false, false, false, false, false],
  "programs": [
    {
      "id": "irrigazione_mattina",
      "startHour": 6,
      "startMinute": 30,
      "enabled": true,
      "sequence": [
        { "relay": 2, "duration": 10 }
      ]
    }
  ],
  "sequenceRunning": false
}
```

## 4) Disabilita un programma

Payload su `command`:

```json
{
  "action": "disable_program",
  "id": "irrigazione_mattina"
}
```

Effetto:
- Imposta `enabled = false` per il programma.

Esempio di output su `info`:

```json
{
  "reason": "program_toggled",
  "time": "2026-04-28T06:33:00",
  "relays": [false, false, false, false, false, false, false, false],
  "programs": [
    {
      "id": "irrigazione_mattina",
      "startHour": 6,
      "startMinute": 30,
      "enabled": false,
      "sequence": [
        { "relay": 2, "duration": 10 }
      ]
    }
  ],
  "sequenceRunning": false
}
```

## 5) Elimina un programma

Payload su `command`:

```json
{
  "action": "delete_program",
  "id": "irrigazione_mattina"
}
```

Effetto:
- Rimuove il programma da memoria e da `/programs.json`.

Esempio di output su `info`:

```json
{
  "reason": "program_deleted",
  "time": "2026-04-28T06:34:00",
  "relays": [false, false, false, false, false, false, false, false],
  "programs": [],
  "sequenceRunning": false
}
```

## 6) Stop immediato sequenza in corso

Payload su `command`:

```json
{
  "action": "stop_program"
}
```

Effetto:
- Ferma subito la sequenza attiva e spegne il relay eventualmente acceso da sequenza.

Esempio di output su `info`:

```json
{
  "reason": "program_stopped",
  "time": "2026-04-28T06:35:00",
  "relays": [false, false, false, false, false, false, false, false],
  "programs": [
    {
      "id": "irrigazione_mattina",
      "startHour": 6,
      "startMinute": 30,
      "enabled": true,
      "sequence": [
        { "relay": 2, "duration": 10 }
      ]
    }
  ],
  "sequenceRunning": false
}
```

## 7) Avvio manuale di un programma per test

Payload su `command`:

```json
{
  "action": "start_program",
  "id": "irrigazione_mattina"
}
```

Effetto:
- Avvia subito il programma indicato, senza attendere l'orario pianificato.
- Segue la sequenza definita nello stesso modo dell'avvio automatico.
- Se c'e una sequenza in corso, viene fermata prima dell'avvio del test.
- Questo comando e pensato per test manuali.

Esempio di output su `info`:

```json
{
  "reason": "program_test_started",
  "time": "2026-04-28T07:10:00",
  "rainMode": false,
  "relays": [false, true, false, false, false, false, false, false],
  "programs": [
    {
      "id": "irrigazione_mattina",
      "startHour": 6,
      "startMinute": 30,
      "enabled": true,
      "sequence": [
        { "relay": 2, "duration": 10 },
        { "relay": 4, "duration": 7 }
      ]
    }
  ],
  "sequenceRunning": true,
  "runningProgramId": "irrigazione_mattina",
  "runningStep": 1
}
```

Esempio di output su `log` se il programma non esiste:

```json
{
  "error": "Program id not found"
}
```

## 8) Comando manuale relay (fallback)

Payload su `command`:

```json
{
  "relay": 2,
  "active": true
}
```

Oppure per spegnere:

```json
{
  "relay": 2,
  "active": false
}
```

Effetto:
- Aggiorna manualmente un relay.
- Se era in corso una sequenza automatica, la sequenza viene fermata.

Esempio di output su `info` dopo accensione manuale:

```json
{
  "reason": "relay_updated",
  "time": "2026-04-28T06:36:00",
  "relays": [false, true, false, false, false, false, false, false],
  "programs": [
    {
      "id": "irrigazione_mattina",
      "startHour": 6,
      "startMinute": 30,
      "enabled": true,
      "sequence": [
        { "relay": 2, "duration": 10 }
      ]
    }
  ],
  "sequenceRunning": false
}
```

Esempio di output su `log` in caso di errore JSON:

```json
{
  "error": "Invalid JSON format"
}
```

## 9) Rain mode (blocco impianto)

Payload su `command`:

```json
{
  "action": "set_rain_mode",
  "enabled": true
}
```

Per riattivare l'impianto:

```json
{
  "action": "set_rain_mode",
  "enabled": false
}
```

Effetto:
- Se `enabled: true`, i programmi automatici non partono.
- Se c'e una sequenza in corso, viene fermata.
- Il flag resta salvato (persistente) anche dopo riavvio.

Esempio di output su `info`:

```json
{
  "reason": "rain_mode_updated",
  "time": "2026-04-28T07:00:00",
  "rainMode": true,
  "relays": [false, false, false, false, false, false, false, false],
  "programs": [
    {
      "id": "irrigazione_mattina",
      "startHour": 6,
      "startMinute": 30,
      "enabled": true,
      "sequence": [
        { "relay": 2, "duration": 10 }
      ]
    }
  ],
  "sequenceRunning": false
}
```

Esempio di output su `log` se provi ad accendere manualmente con rain mode attivo:

```json
{
  "error": "Rain mode active: manual start blocked"
}
```

## 10) Imposta i nomi dei settori (relay)

Payload su `command`:

```json
{
  "action": "set_sector_names",
  "names": [
    "Prato Nord", "Siepe Est", "Fiori", "Orto",
    "Settore 5", "Settore 6", "Settore 7", "Settore 8"
  ]
}
```

Regole:
- L'array `names` deve contenere esattamente 8 stringhe.

Effetto:
- Aggiorna i nomi dei settori e li salva persistentemente in `/settings.json`.
- Da questo momento l'evento `get_info` conterrà un nuovo array JSON `"sectorNames"` con questi nomi (parallelo all'array `"relays"`).



## 11) Esporta Backup Completo

Payload su `command`:

```json
{
  "action": "get_backup"
}
```

Effetto:
- Genera ed invia su `info` un pacchetto JSON di backup contenente tutte le impostazioni globali (`settings`) e tutti i programmi salvati (`programs`).

Esempio di output su `info`:

```json
{
  "reason": "backup",
  "time": "2026-08-30T10:00:00",
  "settings": {
    "rainMode": false,
    "sectorNames": [
      "Settore 1 sud dinamici",
      "Settore 2 sud giro casa/nord pozzo statici",
      "Settore 3 ovest dinamici e statici lato nord giardinetto",
      "Settore 4 aiuole nord e bonsai grandi",
      "Settore 5 dinamici Giordano e serra casa",
      "Settore 6 statici casa vecchia lato ovest ed est",
      "Settore 7",
      "Settore 8"
    ]
  },
  "programs": [
    {
      "id": "irrigazione_mattina",
      "startHour": 6,
      "startMinute": 30,
      "enabled": true,
      "sequence": [
        { "relay": 1, "duration": 10 },
        { "relay": 2, "duration": 15 }
      ]
    }
  ]
}
```

## 12) Ripristina Backup Completo

Payload su `command`:

```json
{
  "action": "restore_backup",
  "settings": {
    "rainMode": false,
    "sectorNames": [
      "Settore 1 sud dinamici",
      "Settore 2 sud giro casa/nord pozzo statici",
      "Settore 3 ovest dinamici e statici lato nord giardinetto",
      "Settore 4 aiuole nord e bonsai grandi",
      "Settore 5 dinamici Giordano e serra casa",
      "Settore 6 statici casa vecchia lato ovest ed est",
      "Settore 7",
      "Settore 8"
    ]
  },
  "programs": [
    {
      "id": "irrigazione_mattina",
      "startHour": 6,
      "startMinute": 30,
      "enabled": true,
      "sequence": [
        { "relay": 1, "duration": 10 },
        { "relay": 2, "duration": 15 }
      ]
    }
  ]
}
```

Effetto:
- Ferma eventuali sequenze in corso.
- Sovrascrive persistentemente `/settings.json` e `/programs.json` su LittleFS.
- Ricarica immediatamente la configurazione in memoria senza bisogno di riavviare.
- Pubblica l'evento con `"reason": "backup_restored"` su `info` e conferma su `log`.

## 13) Tipi di "reason" nel payload info

Il campo `"reason"` inviato nel feed `info` descrive l'evento che ha scatenato la pubblicazione dello stato. Ecco l'elenco completo di tutte le *reason* esistenti:

| Reason | Quando viene inviato |
| ------ | -------------------- |
| `connected` / `reconnected` | All'avvio del dispositivo o dopo aver ristabilito la connessione al broker MQTT. |
| `on_demand` | In risposta al comando esplicito `get_info`. |
| `backup` | In risposta al comando esplicito `get_backup`. |
| `backup_restored` | A seguito del ripristino di un backup con successo tramite `restore_backup`. |
| `relay_updated` | Accensione/Spegimento di un singolo relay tramite comando manuale. |
| `rain_mode_updated` | Modifica dello stato del Rain Mode (abilitato/disabilitato). |
| `rain_mode_blocked` | Quando si tenta di accendere manualmente un relay, ma il Rain Mode è attivo. |
| `sector_names_updated` | A seguito di un salvataggio con successo dei nomi dei settori con `set_sector_names`. |
| `program_saved` | Un programma è stato creato o aggiornato (`set_program`). |
| `program_deleted` | Un programma è stato eliminato (`delete_program`). |
| `program_toggled` | Un programma è stato abilitato o disabilitato (`enable_program` / `disable_program`). |
| `program_started` | Un programma è partito in modo automatico (all'orario schedulato). |
| `program_test_started` | Un programma è stato avviato manualmente per test (`start_program`). |
| `program_test_failed` | Fallimento di `start_program` (es. `id` non esistente). |
| `program_step` | Transizione a una nuova zona (step successivo) durante l'esecuzione di un programma. |
| `step_pause` | Pausa tecnica di transizione tra lo spegnimento di un settore e l'avvio del successivo in un programma. |
| `program_completed` | Un programma in esecuzione è giunto al termine naturale di tutti i suoi step. |
| `program_stopped` | Un programma in esecuzione è stato fermato in modo forzato (`stop_program` o spegnimento relè manuale). |

## Note utili

- Se il JSON e invalido, il firmware pubblica errore su `log`.
- I relay sono active LOW a livello hardware, ma lato comando `active: true` significa ON.
- Ogni programma parte al massimo una volta al giorno (per ogni `id`).
- `rainMode` e persistente e viene incluso in ogni payload `info`.

## Esempi di output aggiuntivi

### Avvio programma

```json
{
  "reason": "program_started",
  "time": "2026-04-28T06:30:00",
  "relays": [false, true, false, false, false, false, false, false],
  "programs": [
    {
      "id": "irrigazione_mattina",
      "startHour": 6,
      "startMinute": 30,
      "enabled": true,
      "sequence": [
        { "relay": 2, "duration": 10 },
        { "relay": 4, "duration": 7 }
      ]
    }
  ],
  "sequenceRunning": true,
  "runningProgramId": "irrigazione_mattina",
  "runningStep": 1
}
```

### Pausa tra step

```json
{
  "reason": "step_pause",
  "time": "2026-04-28T06:40:00",
  "relays": [false, false, false, false, false, false, false, false],
  "programs": [
    {
      "id": "irrigazione_mattina",
      "startHour": 6,
      "startMinute": 30,
      "enabled": true,
      "sequence": [
        { "relay": 2, "duration": 10 },
        { "relay": 4, "duration": 7 }
      ]
    }
  ],
  "sequenceRunning": true,
  "runningProgramId": "irrigazione_mattina",
  "runningStep": 2
}
```

### Fine programma

```json
{
  "reason": "program_completed",
  "time": "2026-04-28T06:55:00",
  "relays": [false, false, false, false, false, false, false, false],
  "programs": [
    {
      "id": "irrigazione_mattina",
      "startHour": 6,
      "startMinute": 30,
      "enabled": true,
      "sequence": [
        { "relay": 2, "duration": 10 },
        { "relay": 4, "duration": 7 }
      ]
    }
  ],
  "sequenceRunning": false
}
```

