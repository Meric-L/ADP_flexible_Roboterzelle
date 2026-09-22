# <Thema in einer Zeile>

**Status:** geplant | in Arbeit | fertig | verworfen — <TT.MM.JJJJ>
**Verantwortlich:** <Name> bzw. `Agent: <Aufgabenbeschreibung>`
**Thema:** opcua | vision | apriltag | mdns | frontend | sonstiges
**Branch:** <branch-name>

## Ziel

Ein bis drei Sätze: Was funktioniert hinterher, was vorher nicht ging?

## Bereits gelesen

Welche MDs unter `doc/` wurden für diesen Plan ausgewertet?
(CLAUDE.md, Abschnitt „Bevor du anfängst")

- `doc/…`

## Betroffene Dateien

Pfade, die angefasst werden. Diese Liste reserviert die Dateien faktisch —
wer sie parallel anfassen will, spricht sich vorher ab.

- `src/…`
- `tests/…`

## Schnittstellen

**Der wichtigste Abschnitt.** So konkret, dass jemand anderes ohne diesen Code
dagegen entwickeln kann. Nicht Zutreffendes streichen.

### OPC UA

| Element | NodeId / BrowsePath | Typ | Richtung | Bedeutung |
| --- | --- | --- | --- | --- |
| … | `ns=4;s=…` | … | Aufruf / Lesen / Event | … |

Methoden mit vollständiger Signatur: Eingangsargumente, Datentypen,
Rückgabewerte, Fehler-StatusCodes.

### Python

```python
# Modul: src/…
def funktion(param: Typ) -> Rueckgabe:
    """Was sie zusichert, was sie wirft."""
```

### Daten / Payload

```json
{ "feld": "Typ und Bedeutung" }
```

### Netz / Betrieb

Ports, Services, Hostnamen, mDNS-Namen, Konfigurationsdateien, systemd-Units.

### Fehlerfälle

Was passiert bei Timeout, fehlender Kamera, ungültigem Job, Abbruch?

## Vorgehen

1. …
2. …

## Offene Fragen

- …

## Abweichungen vom Plan

Wird während der Umsetzung gefüllt. Weicht die Realität vom Plan ab, wird der
Plan korrigiert — eine veraltete Schnittstellenbeschreibung ist schlimmer als
gar keine.

## Nach Abschluss

- Status auf `fertig`, tatsächliche Schnittstellen eingetragen.
- Zeile im Index `doc/arbeitsplaene/README.md` aktualisiert.
- Dauerhaftes Fachwissen in die passende Fach-MD unter `doc/` übernommen
  (nicht nur hier im Plan stehen lassen).
