# MIP-003 Input Schema Gap-Analyse

**Datum:** 2026-03-02
**Status:** Analyse abgeschlossen

## Übersicht: Aktueller Support

| Status | Anzahl | Details |
|--------|--------|---------|
| ✅ Vollständig unterstützt | 20 | Alle Basis-Typen |
| ⚠️ Teilweise unterstützt | 2 | file, validations |
| ❌ Nicht unterstützt | 1 | Server-side Validation |

---

## ✅ VOLLSTÄNDIG UNTERSTÜTZT (20 Typen)

| MIP-003 Type | Kodosumi Mapping | Validations |
|--------------|------------------|-------------|
| `text` | `InputText` | min, max, nonempty ✓ |
| `textarea` | `InputArea` | min, max, nonempty ✓ |
| `number` | `InputNumber` | min, max, integer ✓ |
| `boolean` | `Checkbox` | optional ✓ |
| `email` | `InputEmail` | email format auto ✓ |
| `password` | `InputPassword` | min, max ✓ |
| `tel` | `InputTel` | tel-pattern ✓ |
| `url` | `InputURL` | url format auto ✓ |
| `date` | `InputDate` | min, max (YYYY-MM-DD) ✓ |
| `datetime-local` | `InputDateTime` | min, max ✓ |
| `time` | `InputTime` | min, max ✓ |
| `month` | `InputMonth` | min, max ✓ |
| `week` | `InputWeek` | min, max ✓ |
| `color` | `InputColor` | ✓ |
| `range` | `InputRange` | min, max, step ✓ |
| `hidden` | `InputHidden` | value ✓ |
| `search` | `InputSearch` | min, max ✓ |
| `radio` | `InputRadio` | values ✓ |
| `option` | `Select` | values, min, max ✓ |
| `none` | `DisplayInfo` | description only ✓ |

---

## ⚠️ TEILWEISE UNTERSTÜTZT

### 1. `file` Type

| MIP-003 Feature | Kodosumi Status | Gap |
|-----------------|-----------------|-----|
| `outputFormat: "url"` | ✅ Supported | - |
| `accept` (MIME types) | ❌ Missing | Validation fehlt |
| `maxSize` | ❌ Missing | Validation fehlt |
| `multiple` | ✅ Supported | - |

**Aufwand:** Mittel (2-4h)
- `accept` in forms.py `InputFiles` hinzufügen
- Validierung in serve.py implementieren
- Schema conversion erweitern

### 2. Custom Pattern Validation

| MIP-003 | Kodosumi | Gap |
|---------|----------|-----|
| `format: nonempty` | ✅ `^\S+$` | - |
| `format: integer` | ✅ `^\d+$` | - |
| `format: email` | ✅ auto | - |
| `format: url` | ✅ auto | - |
| `format: tel-pattern` | ✅ auto | - |
| Custom regex | ❌ Lost | MIP-003 unterstützt keine custom regex |

**Note:** Dies ist eine MIP-003 Limitation, nicht Kodosumi.

---

## ❌ NICHT UNTERSTÜTZT

### Server-Side Input Validation in Sumi

**Aktueller Flow:**
```
Client → Sumi start_job → proxy_forward → Agent (validation here!)
                ↓
         NO VALIDATION (nur Hash berechnen)
```

**MIP-003 Erwartung:**
```
Client → Sumi start_job → VALIDATE against schema → Agent
                ↓
         Return 400 if invalid
```

**Aufwand:** Hoch (1-2 Tage)
- Schema abrufen vor Job-Start
- `input_data` gegen Schema validieren
- Typ-spezifische Validatoren implementieren
- Error Response formatieren

**Side Effects:**
- Latenz: +1 Request (Schema fetch) oder Caching nötig
- Breaking Change: Aktuell ungültige Requests, die Agents abfangen, würden früher failen

---

## Zusammenfassung: Implementierungsprioritäten

| Feature | Aufwand | Side Effects | Empfehlung |
|---------|---------|--------------|------------|
| `file.accept` validation | 2-4h | Keine | ✅ Umsetzen |
| `file.maxSize` validation | 2-4h | Keine | ✅ Umsetzen |
| Server-side validation | 1-2 Tage | Latenz, Breaking | ⚠️ Optional |

---

## Was NICHT geändert werden muss

1. **Panel/Admin UI** - Unberührt, nur Sumi API
2. **Agent Code** - Unberührt, Validation bleibt dort
3. **Input Type Mapping** - Bereits komplett (alle 23 Typen)
4. **Bidirektionale Conversion** - Funktioniert korrekt

---

## Referenzen

- MIP-003 Spec: https://github.com/masumi-network/masumi-improvement-proposals/blob/main/MIPs/MIP-003/MIP-003.md
- Input Types: https://github.com/masumi-network/masumi-improvement-proposals/blob/main/MIPs/MIP-003/MIP-003-Attachement-01.md
- Kodosumi Schema: `kodosumi/service/sumi/schema.py`
- Kodosumi Forms: `kodosumi/service/inputs/forms.py`
