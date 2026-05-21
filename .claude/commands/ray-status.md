---
description: Ray Cluster Status prüfen
allowed-tools: Bash
---

Prüfe den Ray Cluster Status.

Führe aus:
```bash
ray status 2>&1
```

Gib eine knappe Zusammenfassung:
- **Status**: OK (Cluster läuft) / ERROR (nicht erreichbar)
- **Nodes**: Anzahl aktiver Nodes
- **Resources**: Verfügbare CPUs/GPUs/Memory
- **Empfehlung**: Bei Fehler → `ray start --head` ausführen
