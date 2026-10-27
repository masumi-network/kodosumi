---
description: Kodosumi Spooler starten
allowed-tools: Bash
---

Starte den Spooler.

**1. Prüfe ob bereits aktiv:**
```bash
koco spool --status 2>&1
```

Falls bereits aktiv, melde dies und beende.

**2. Starte Spooler:**
```bash
koco spool --start
```

**3. Warte und verifiziere:**
```bash
sleep 2 && koco spool --status
```

Gib Zusammenfassung:
- **Status**: GESTARTET / BEREITS AKTIV / FEHLER
- **Voraussetzung**: Ray muss laufen
- **Bei Fehler**: Prüfe `/ray-status`
