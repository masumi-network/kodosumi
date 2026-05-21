---
description: Kodosumi Spooler neustarten
allowed-tools: Bash
---

Starte den Spooler neu.

**1. Stoppe Spooler:**
```bash
cd /Users/zimmermannb/git/kodosumi-nextgen/kodosumi && koco spool --stop 2>&1
```

**2. Warte kurz:**
```bash
sleep 2
```

**3. Starte Spooler:**
```bash
cd /Users/zimmermannb/git/kodosumi-nextgen/kodosumi && koco spool --start
```

**4. Verifiziere:**
```bash
sleep 2 && cd /Users/zimmermannb/git/kodosumi-nextgen/kodosumi && koco spool --status
```

Gib Zusammenfassung:
- **Status**: NEUGESTARTET / FEHLER
- **Bei Fehler**: Prüfe `/ray-status`
