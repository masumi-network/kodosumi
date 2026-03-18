---
description: Ray Cluster stoppen
allowed-tools: Bash
---

Stoppe den Ray Cluster.

**WARNUNG**: Dies beendet alle laufenden Jobs!

**1. Stoppe Ray:**
```bash
ray stop
```

**2. Verifiziere:**
```bash
sleep 1 && ray status 2>&1 | head -3
```

Gib Zusammenfassung:
- **Status**: GESTOPPT / FEHLER
- **Hinweis**: Spooler und Panel verlieren Ray-Verbindung
