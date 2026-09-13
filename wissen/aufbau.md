# Physischer Aufbau

Zellengeometrie, Kameras und Halterungen, Referenz-Tags, Hardware-Architektur
und die konzeptionelle Scan-Kette. Zuständig für alles, was in der Zelle
physisch steht oder das Zusammenspiel der Bereiche festlegt.

## Kernfakten

- Labor-Roboterzelle, ca. **5x5 m**, rekonfigurierbar — Module werden umgestellt,
  deshalb muss die Lokalisierung wiederholbar laufen.
- Untersucht werden drei Technologiepfade: **optische Markererkennung**,
  **topologische Erkennung** (Nahbereich an mechanischen Schnittstellen, z. B.
  Near Field Magnetic Positioning) und **UWB**.
- Die Scan-Kette ist **zweistufig**: Layer 1 (Deckenkamera) liefert eine grobe
  Modulposition, Layer 2 (AprilTag am Modul) die genaue Pose. Davor liegt eine
  Kalibrierungsphase.
- Referenz-Tags im Raum: ein **ArUco-Board an Boden/Wand** als Welt-Tag, plus
  ein **eigener AprilTag am Robotertisch**. Beide sind Hardware-seitig
  vorhanden, ihre Rolle in der Ablaufkette ist noch nicht festgelegt.
- Anforderung **Update-Loop**: die Kette soll wiederholbar laufen, zeitgesteuert
  (alle 60 min) und auf manuellen Trigger.

## Tiefendokumente

| Dokument | Inhalt |
| --- | --- |
| [Hardware-Architektur v2](<../hardware/Hardware_Architektur_v2(2).drawio.png>) | Komponenten und Verdrahtung der Zelle |
| [Ablaufdiagramm v2](../concept/Ablaufdiagramm_v2.drawio.png) | Kalibrierung → Layer 1 → Layer 2, inkl. Fehlerpfade |
| [`concept/offene_punkte.md`](../concept/offene_punkte.md) | **Sechs offene Konzeptlücken** plus Anforderungen an den Update-Loop. Vor Konzeptarbeit lesen. |
| [`README.md`](../README.md) | Projektfokus und Repo-Struktur |

## Offene Fragen

Nicht hier duplizieren — die Liste lebt in
[`concept/offene_punkte.md`](../concept/offene_punkte.md). Kurzfassung der
Themen: Koordinatensystem-Kette Kamera → Welt, Übergabeformat Layer 1 → Layer 2,
Repositionierungsstrategie bei nicht gefundenem Tag, Rolle des Robotertisch-Tags,
Fehlerbehandlung bei Layer-1-Ausfall, Abbruchkriterium für Vollständigkeit.

Wird eine dieser Fragen geklärt, kommt die Entscheidung als Log-Eintrag hierher
**und** der Punkt wird in `offene_punkte.md` als geklärt markiert.

## Log

<!-- Neue Einträge unten anhängen. Format siehe .claude/skills/projektwissen/SKILL.md -->

### 2026-09-13 — John — Gemeinsame Wissensstruktur eingeführt
**Status:** erledigt
**Betrifft:** aufbau, vision, opcua

Bisher lagen Erkenntnisse in getrennten Chats, dadurch liefen die
Arbeitsstände auseinander. Neu: `CLAUDE.md` im Repo-Root als Index, drei
Bereichsdateien unter `wissen/`, Skill `projektwissen` zum Anhängen von
Einträgen.

**Konsequenz:** Erkenntnisse, die andere betreffen, gehören ab jetzt ins Log
der passenden Bereichsdatei und werden committet — nicht nur in den Chat.
