---
description: Ray Cluster starten
allowed-tools: Bash
---

Starte den Ray Cluster.

**1. Prüfe ob bereits aktiv:**
```bash
ray status 2>&1 | head -3
```

Falls bereits aktiv, melde dies und beende.

**2. Starte Ray:**
```bash
cd /Users/zimmermannb/git/kodosumi-nextgen/kodosumi && dotenv run -- ray start --head
```

**3. Verifiziere:**
```bash
sleep 2 && ray status 2>&1 | head -5
```

Gib Zusammenfassung:
- **Status**: GESTARTET / BEREITS AKTIV / FEHLER
- **Dashboard**: http://localhost:8265
- **Nächster Schritt**: Bei Erfolg → `/spooler-start`
