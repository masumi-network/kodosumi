---
description: Gesamtstatus aller Kodosumi Services
allowed-tools: Bash
---

Prüfe alle Kodosumi Services auf einmal.

**1. Ray Cluster:**
```bash
ray status 2>&1 | head -5
```

**2. Spooler:**
```bash
cd /Users/zimmermannb/git/kodosumi-nextgen/kodosumi && koco spool --status 2>&1
```

**3. Admin Panel:**
```bash
curl -s -o /dev/null -w "%{http_code}" http://localhost:3370/health/ 2>/dev/null || echo "000"
```

Gib eine Übersichtstabelle:

| Service | Status |
|---------|--------|
| Ray     | OK/ERROR |
| Spooler | OK/ERROR |
| Panel   | OK/ERROR |

Bei Fehlern: Empfehle die passenden Start-Commands.
