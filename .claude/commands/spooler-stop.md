---
description: Kodosumi Spooler stoppen
allowed-tools: Bash
---

Stoppe den Spooler.

**1. Stoppe Spooler:**
```bash
cd /Users/zimmermannb/git/kodosumi-nextgen/kodosumi && koco spool --stop
```

**2. Verifiziere:**
```bash
sleep 1 && cd /Users/zimmermannb/git/kodosumi-nextgen/kodosumi && koco spool --status
```

Gib Zusammenfassung:
- **Status**: GESTOPPT / BEREITS INAKTIV / FEHLER
- **Hinweis**: Laufende Jobs werden nicht mehr überwacht
