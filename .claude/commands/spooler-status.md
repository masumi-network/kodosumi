---
description: Kodosumi Spooler Status prüfen
allowed-tools: Bash
---

Prüfe den Spooler Status.

Führe aus:
```bash
cd /Users/zimmermannb/git/kodosumi-nextgen/kodosumi && koco spool --status
```

Gib eine knappe Zusammenfassung:
- **Status**: OK (läuft) / ERROR (nicht aktiv)
- **PID**: Prozess-ID falls aktiv
- **Aktive Jobs**: Anzahl überwachter Runner
- **Empfehlung**: Bei Fehler → `/spooler-start` ausführen
