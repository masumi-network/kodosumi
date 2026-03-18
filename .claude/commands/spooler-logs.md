---
description: Spooler Log-Analyse (Errors/Warnings)
allowed-tools: Bash, Read
---

Analysiere die Spooler Logs auf Fehler und Warnungen.

**1. Lese die letzten 200 Zeilen:**

Lies die Datei: `/Users/zimmermannb/git/kodosumi-nextgen/kodosumi/data/spooler.log`

Falls die Datei nicht existiert, melde dies.

**2. Analysiere den Inhalt:**

Suche nach:
- `ERROR` - Kritische Fehler
- `WARNING` - Warnungen
- `Exception` / `Traceback` - Python-Fehler
- `failed` / `timeout` - Verbindungsprobleme

**3. Gib Zusammenfassung:**

| Typ | Anzahl |
|-----|--------|
| Errors | X |
| Warnings | X |
| Exceptions | X |

**Top 3 Probleme:**
1. [Häufigstes Problem mit Kurzfassung]
2. [Zweithäufigstes]
3. [Dritthäufigstes]

**Zeitraum:** Von [erster Timestamp] bis [letzter Timestamp]

**Empfehlungen:** Konkrete Vorschläge zur Behebung
