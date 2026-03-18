---
description: Admin Panel Log-Analyse (Errors/Warnings)
allowed-tools: Bash, Read
---

Analysiere die Admin Panel Logs auf Fehler und Warnungen.

**1. Lese die letzten 200 Zeilen:**

Lies die Datei: `/Users/zimmermannb/git/kodosumi-nextgen/kodosumi/data/app.log`

Falls die Datei nicht existiert, melde dies.

**2. Analysiere den Inhalt:**

Suche nach:
- `ERROR` - Kritische Fehler
- `WARNING` - Warnungen
- `Exception` / `Traceback` - Python-Fehler
- `500` / `404` - HTTP-Fehler
- `timeout` / `connection` - Verbindungsprobleme

**3. Gib Zusammenfassung:**

| Typ | Anzahl |
|-----|--------|
| Errors | X |
| Warnings | X |
| HTTP 5xx | X |
| HTTP 4xx | X |

**Top 3 Probleme:**
1. [Häufigstes Problem mit Kurzfassung]
2. [Zweithäufigstes]
3. [Dritthäufigstes]

**Zeitraum:** Von [erster Timestamp] bis [letzter Timestamp]

**Empfehlungen:** Konkrete Vorschläge zur Behebung
