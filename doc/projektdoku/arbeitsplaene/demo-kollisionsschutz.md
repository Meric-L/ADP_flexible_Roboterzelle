# DEMO-Kollisionsschutz FR3: 20 cm Abstand zu Tisch und Modulen

**Status:** in Arbeit — 21.10.2026
**Verantwortlich:** Meric, `Agent: DEMO-Kollisionsschutz automatische Fahrten`
**Thema:** frontend
**Branch:** `demo-kollisionsschutz` in `webskillcomposition` (von `Ungetestet`); Doku: `demo-kollisionsschutz-doku`

## Ziel

**DEMO, keine Lösung.** Der FR3 auf dem Wagen (`fr3`, `fr3_wagon`) hält bei den
**automatischen Fahrten** mindestens 20 cm Abstand zu eigenem Tisch, zu allen
Modulen, zu unbekannten Tags (0,2-m-Box bis zum Boden) und zum Boden. Vorher gab
es nur eine Prüfung mit 5 cm am Wagen und nur im Anfahrplan.

Die Lösung ist bewusst schlank und wird durch eine richtige Kollisionsvermeidung
ersetzt (siehe „Für die spätere richtige Lösung“).

Betroffen sind nur:
- Anfahrplan und Zentrieren (`ApproachPlanPanel`, `centreOnTag`);
- Layer-2-Lauf (`moveFlangeTo`);
- Hand-Auge-Automatik (`HandEyeAutoPanel`);
- Spin-Test (`EndEffectorSpinTest`).

Manuelle Fahrten (Ziehen, Slider, IK-Gizmo, Actions-Panel) bleiben unverändert.

## Bereits gelesen

- `doc/projektdoku/vision-system-integration.md` (Frontend-Architektur, `ws`-Protokoll)
- `doc/projektdoku/arbeitsplaene/layer2-lauf-hand-pi.md` (Layer-2-Fahrziele, `moveFlangeTo`)
- `doc/projektdoku/arbeitsplaene/zellen-ueberwachung.md` (Layer 1, Zellmodell)
- `doc/projektdoku/arbeitsplaene/modell-flaechen-tag-platzierung.md` (Modulkatalog, Modell-Bounds)

## Betroffene Dateien

Alle unter `webskillcomposition/frontend/src/features/cell-modules/`:

- `model/cellClearance.ts` (+ Test)
- `model/demoRoute.ts` (neu, + Test)
- `model/cellModules.ts` (`unknownTags`, additiv)
- `model/robotDrive.ts` (`driveRoute`)
- `components/ApproachPlanPanel.tsx`
- `components/HandEyeAutoPanel.tsx`
- `components/EndEffectorSpinTest.tsx`
- `centreOnTag.ts`
- `components/Layer2RunPanel.tsx` (+ Tests)

## Schnittstellen

**Der wichtigste Abschnitt.** Die Demo ändert keine Schnittstelle nach außen. Die
TypeScript-Signaturen sind **geplant** und werden nach der Umsetzung korrigiert.

### OPC UA

Keine Änderung. Weiterhin ein `go_to` mit Gelenkwinkeln je Teilstück. Kein neuer
Befehl, kein neuer Knoten.

### Python

Keine Änderung.

### TypeScript (geplant)

```ts
// Modul: model/cellClearance.ts
export const DEMO_CLEARANCE_M = 0.20;
// KeepOut bekommt modules: { label: string; min: Vec3; max: Vec3 }[]
export function collision(points: Vec3[], keepOut: KeepOut, clearanceM?: number): string | null;
// Rückgabe: Label des Hindernisses (z. B. "MOD-A", "Tisch", "Boden") oder null

// Modul: model/demoRoute.ts   (Kopfkommentar: DEMO, ersetzt durch richtige Lösung)
export const DEMO_COLLISION_GUARD_ENABLED: boolean;
export function moduleKeepOuts(
  modules: CellModule[],
  unknownTags: UnknownTag[],
  baseInWorld: Pose,
  floorZWorld: number,
): KeepOut[];
export function demoTransitPose(jointNames: string[], from: number[], j1: number): number[];
// FR3-Werte: [j1, 0, 0, -1.6, 0, 1.6, π/4]; restliche Gelenke aus from
export type Leg = { joints: number[] };
export type RoutePlan = { legs: Leg[] } | { blocked: string };
export function planDemoRoute(
  from: number[],
  to: number[],
  clear: (joints: number[]) => string | null,
  steps?: number,              // Default 20
): RoutePlan;
export function useDemoClearance(): (from: number[], to: number[]) => RoutePlan;

// Modul: model/robotDrive.ts
export async function driveRoute(
  deps: DriveDeps,
  legs: Leg[],
  seed: number[],
): Promise<void>;   // wirft beim ersten fehlgeschlagenen Teilstück
```

### Daten / Payload

Keine Änderung. `cellModules` bekommt additiv `unknownTags` mit Position; bestehende
Felder bleiben.

### Netz / Betrieb

Keine Änderung.

### Fehlerfälle

| Fall | Verhalten |
| --- | --- |
| Kein Layer 1 | Nur Tisch und Boden werden geprüft (keine Module, keine Tags). |
| Nicht FR3 oder `DEMO_COLLISION_GUARD_ENABLED = false` | Altes Verhalten, keine Prüfung. |
| Direktweg frei | Ein Teilstück. |
| Direktweg blockiert, Transit frei | Drei Teilstücke: `T(J1_start) → T(J1_ziel) → Ziel`. |
| Transit blockiert | `blocked` mit Hindernis, z. B. „DEMO-Kollisionsschutz: Weg kommt MOD-A näher als 20 cm“. Es wird nicht gefahren. |
| Teilstück scheitert (Fehler von `driveJoints`) | Abbruch; Rest wird nicht gefahren. |

## Vorgehen

1. `cellClearance.ts`: Abstand 20 cm, Modul-Boxen, Label in `collision()`, Greiferspitze in `armPoints`. Tests.
2. `demoRoute.ts`: Module-Boxen im Basis-KS, Transit-Haltung, `planDemoRoute`. Tests mit Stub-Kinematik.
3. `cellModules.ts`: `unknownTags` mit Position. `robotDrive.ts`: `driveRoute`. Tests.
4. Einbindung in `moveFlangeTo`, `ApproachPlanPanel`, `HandEyeAutoPanel`, `EndEffectorSpinTest`, `centreOnTag`. Kommentar `// DEMO-Kollisionsschutz` an jeder Aufrufstelle.
5. `tsc --noEmit` und `vitest run` grün, dann Commits.
6. Am Roboter: Gelenk-Linearität messen, Pappkarton-Test (siehe „Offene Fragen“).

## Grenzen

- Nur Punkte, keine Linkdicke (FR3 ca. 7 cm): der echte Abstand zur Oberfläche ist eher ca. 13 cm.
- Boxen statt echter Formen.
- Der Weg wird als gerade Linie im Gelenkraum angenommen, nicht geprüft.
- Layer-1-Posen sind einige cm ungenau.
- Task-Programme auf dem Roboter-Server sind nicht abgedeckt.
- Manuelle Fahrten sind absichtlich nicht abgedeckt.

## Offene Fragen

- Ist die `go_to`-Bahn wirklich linear im Gelenkraum? Am Roboter messen: zwei
  Stellungen langsam (0,1) hin und her fahren, `robotJointState`-Strom
  aufzeichnen, prüfen, dass jedes Gelenk ungefähr linear fährt.
- Messposen nah an Modulen werden evtl. abgelehnt, weil für alle Module der
  gleiche Abstand gilt. Das ist so gewollt, muss aber am Roboter gesehen werden.
- Pappkarton als Modul ins Zellmodell stellen und darüber hinweg fahren lassen.

## Abweichungen vom Plan

Wird nach der Umsetzung gefüllt.

## Für die spätere richtige Lösung

- Echte Modulkörper statt Boxen (Modellgeometrie aus dem Modulkatalog).
- Gleicher Abstand für alle Module ersetzen durch modulspezifische Abstände.
- Unbekannte Tags als echte Hindernisse statt fester 0,2-m-Box.
- Linkdicke des FR3 in die Prüfung aufnehmen.
- Bahnprüfung entlang der tatsächlichen Trajektorie statt Stützstellen im Gelenkraum.
- Gemeinsame Kollisionsprüfung für manuelle Fahrten und Task-Programme.

## Nach Abschluss

- [ ] Status auf `fertig`, tatsächliche Schnittstellen eingetragen.
- [ ] Zeile im Index `doc/projektdoku/arbeitsplaene/README.md` aktualisiert.
- [ ] Dauerhaftes Fachwissen in die passende Fach-MD unter `doc/projektdoku/` übernommen.
