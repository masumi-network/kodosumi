---
description: Kodosumi Spooler stoppen
allowed-tools: Bash
---

Stoppe den Spooler.

**1. Stoppe Spooler:**
```bash
koco spool --stop
```

**2. Verifiziere:**
```bash
sleep 1 && koco spool --status
```

Gib Zusammenfassung:
- **Status**: GESTOPPT / BEREITS INAKTIV / FEHLER
- **Hinweis**: Laufende Jobs werden nicht mehr überwacht
