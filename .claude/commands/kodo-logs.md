---
description: Übergreifende Kodosumi Log-Analyse
allowed-tools: Bash, Read
---

Analysiere alle Kodosumi Logs auf Fehler und Warnungen.

**1. Spooler Log:**

Lies die Datei: `/Users/zimmermannb/git/kodosumi-nextgen/kodosumi/data/spooler.log` (letzte 100 Zeilen)

**2. Panel Log:**

Lies die Datei: `/Users/zimmermannb/git/kodosumi-nextgen/kodosumi/data/app.log` (letzte 100 Zeilen)

**3. Audit Log (falls vorhanden):**

Lies die Datei: `/Users/zimmermannb/git/kodosumi-nextgen/kodosumi/data/audit.log` (letzte 50 Zeilen)

**4. Analysiere alle Logs:**

Suche nach ERROR, WARNING, Exception, Traceback, failed, timeout

**5. Gib Gesamtübersicht:**

| Log | Errors | Warnings |
|-----|--------|----------|
| Spooler | X | X |
| Panel | X | X |
| Audit | X | X |

**Kritische Probleme (über alle Logs):**
1. [Schwerwiegendstes Problem]
2. [Zweitschwerwiegendstes]
3. [Drittschwerwiegendstes]

**Systemzustand:** GESUND / WARNUNG / KRITISCH

**Empfehlungen:**
- [Priorisierte Maßnahmen]
