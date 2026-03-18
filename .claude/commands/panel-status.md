---
description: Admin Panel Status prüfen
allowed-tools: Bash
---

Prüfe den Admin Panel Status.

Führe aus:
```bash
curl -s http://localhost:3370/health/ 2>&1 | python3 -m json.tool 2>/dev/null || echo "Panel nicht erreichbar"
```

Gib eine knappe Zusammenfassung:
- **Status**: OK (erreichbar) / ERROR (nicht erreichbar)
- **Ray**: Verbindungsstatus
- **Serve**: Ray Serve Status
- **Spooler**: Spooler-Verbindung
- **Empfehlung**: Bei Fehler → Panel mit `koco start` starten
