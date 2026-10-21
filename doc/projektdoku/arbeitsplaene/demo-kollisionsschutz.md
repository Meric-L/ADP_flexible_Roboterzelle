# DEMO-Kollisionsschutz FR3: 20 cm Abstand zu Tisch und Modulen

**Status:** fertig — 21.10.2026 (Code und Tests; am Roboter nicht getestet)
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
- Hand-Auge-Automatik (`HandEyeAutoPanel`, Hand-Auge-Tisch);
- Spin-Test (`EndEffectorSpinTest`).

Manuelle Fahrten (Ziehen, Slider, IK-Gizmo, Actions-Panel) bleiben unverändert.

## Regeln (tatsächlich umgesetzt)

- Abstand 20 cm (`DEMO_CLEARANCE_M`) zu allem: Tisch, Boden, Modulen, unbekannten Tags. Geprüft werden Gelenke, Flansch, Greiferspitze (`FR3_TOOL_OFFSET_M`) und Kamera.
- Zielmodul nur auf dem **letzten** Teilstück 5 cm (`DEMO_LOOSE_CLEARANCE_M`). Bei Hand-Auge ist das Ziel `Wagen`, bei Anker-Zielen `Boden` + `Wagen`.
- Start-Verletzungen gelten locker, bis der Arm frei ist (Wegfahren). Auf dem Direktweg muss ein Nicht-Ziel-Start-Hindernis bis zum Ende frei werden, sonst Transit.
- Gedrehte Module werden im Modul-KS geprüft, nicht in einer achsparallelen Hülle.
- Transit-Haltung FR3 `[j1, 0, 0, -1.6, 0, 1.6, π/4]` (Greiferspitze ≈ 0,51 m über der Basis).
- `centreOnTag` und Spin-Test fahren ohne Transit (`transit: false`). Ist der Direktweg blockiert, wird nicht gefahren.
- Der Anfahrplan überspringt blockierte Stopps.
- Layer 2 und Hand-Auge melden „Weg kommt X näher als 20 cm“.
- Nur FR3 und nur wenn der Schalter an ist; sonst altes Verhalten.
- Manuelle Fahrten und `callRobotGoto` bleiben unverändert.

## Bereits gelesen

- `doc/projektdoku/vision-system-integration.md` (Frontend-Architektur, `ws`-Protokoll)
- `doc/projektdoku/arbeitsplaene/layer2-lauf-hand-pi.md` (Layer-2-Fahrziele, `moveFlangeTo`)
- `doc/projektdoku/arbeitsplaene/zellen-ueberwachung.md` (Layer 1, Zellmodell)
- `doc/projektdoku/arbeitsplaene/modell-flaechen-tag-platzierung.md` (Modulkatalog, Modell-Bounds)

## Betroffene Dateien

Alle unter `webskillcomposition/frontend/src/features/`:

- `cell-modules/model/cellClearance.ts` (+ Test)
- `cell-modules/model/demoRoute.ts` (neu, + Test)
- `cell-modules/model/useDemoClearance.ts` (neu)
- `cell-modules/model/cellModules.ts` (`unknownTags`, additiv)
- `cell-modules/model/robotDrive.ts` (`driveRoute`, `driveSafe`)
- `cell-modules/model/centreOnTag.ts`, `endEffectorSpin.ts`, `approachPlan.ts`
- `cell-modules/components/ApproachPlanPanel.tsx`, `HandEyeAutoPanel.tsx`, `HandEyeCalibrationWindow.tsx`, `EndEffectorSpinTest.tsx`, `Layer2RunPanel.tsx` (+ Tests), `RouteList.tsx`
- `viewport/model/sceneState.ts` (`unknownTags` im Szenenzustand)

## Schnittstellen

**Der wichtigste Abschnitt.** Die Demo ändert keine Schnittstelle nach außen.
Die TypeScript-Signaturen unten sind die **tatsächlichen** Stände nach der Umsetzung.

### OPC UA

Keine Änderung. Weiterhin ein `go_to` mit Gelenkwinkeln je Teilstück. Kein neuer
Befehl, kein neuer Knoten.

### Python

Keine Änderung.

### TypeScript

```ts
// model/cellClearance.ts
export const DEMO_CLEARANCE_M = 0.2;        // Abstand aller Armpunkte zu Tisch, Boden, Modulen, unbekannten Tags
export const DEMO_LOOSE_CLEARANCE_M = 0.05; // lockerer Abstand für Hindernisse, denen der Arm schon zu nah steht
export interface KeepOut { minX; maxX; minY; maxY; topZ; floorZ; modules?: KeepOutModule[] } // Basis-KS
export interface KeepOutModule {
  id?: string;               // Katalog-moduleId, `unknown-<tagId>`; ohne id gilt label
  label: string;             // Name für Meldungen
  min: Vec3; max: Vec3;      // Box im eigenen KS
  fromBase?: Pose;           // T_box_base: Basis-KS -> KS der Box (gedrehte Boxen)
}
export function collision(points: Vec3[], keepOut: KeepOut, clearanceM?: number, loose?: ReadonlySet<string>): string | null;
// Id des ersten getroffenen Hindernisses ("Wagen", "Boden", moduleId, "unknown-<tagId>") oder null.
// loose: diese Ids gelten nur mit DEMO_LOOSE_CLEARANCE_M. Ohne clearanceM: altes Verhalten (Tisch 5 cm, Boden 10 cm).
export function armPoints(kinematics, angles: number[], flangeToCamera: Pose | null, toolOffsetM?: number): Vec3[];
export function interpolate(from: number[], to: number[], steps: number): number[][];

// model/demoRoute.ts  (Kopfkommentar: DEMO, ersetzt durch richtige Lösung)
export const DEMO_COLLISION_GUARD_ENABLED = true;  // false: altes Verhalten überall
export const FR3_TOOL_OFFSET_M = 0.1034;
export const DEMO_ROUTE_STEPS = 20;
export function demoAppliesTo(modelId: string | null | undefined): boolean;  // Schalter an und modelId fr3 / fr3_wagon
export function moduleKeepOuts(
  modules: Pick<PlacedCellModule, "moduleId" | "name" | "isRobot" | "bounds" | "worldPosition" | "worldOrientation">[],
  unknownTags: UnknownTag[],
  baseInWorld: Pose,        // T_world_base
  floorZWorld: number,
): KeepOutModule[];         // Roboter übersprungen; unbekannte Tags: 0,2 x 0,2 m vom Boden bis 10 cm über den Tag
export function demoTransitPose(jointNames: string[], from: number[], j1: number): number[];
export interface DemoClear {          // Prüffunktion
  (angles: number[], loose?: ReadonlySet<string>): string | null; // Id des ersten Treffers oder null
  labelOf?(id: string): string;       // Name zu einer Id für Meldungen
}
export function violatedAt(clear: DemoClear, angles: number[]): Set<string>;  // alle Hindernisse, denen angles zu nah kommt
export function demoLegHit(from: number[], to: number[], clear: DemoClear, steps: number,
  loose: Set<string> /* wird verändert */, fixedLoose?: ReadonlySet<string>): string | null;
export function demoBlockedMessage(hit: string, clear?: DemoClear): string;
  // "Weg kommt <Name> näher als 20 cm"
export type DemoRoute = { legs: number[][] } | { blocked: string; hit: string };
export function planDemoRoute(
  jointNames: string[],
  from: number[],
  to: number[],
  clear: DemoClear,
  steps?: number,                    // Default DEMO_ROUTE_STEPS
  options?: { target?: string[] },   // Ziel-Ids: nur auf dem letzten Teilstück locker
): DemoRoute;
// { legs }: Direktweg (ein Teilstück) oder T(J1_start), T(J1_ziel), Ziel.
// { blocked, hit }: Direktweg blockiert (hit) und Transit ebenfalls; blocked ist die Meldung.

// model/useDemoClearance.ts
export function demoBaseInWorld(cellModules: PlacedCellModule[]): Pose | null;
export function buildDemoClearance(args: {
  modelId; entryBounds; kinematics: FlangeKinematics | null; flangeToCamera: Pose | null;
  cellModules: PlacedCellModule[]; unknownTags: UnknownTag[]; catalog: CellModuleDefinition[];
  baseInWorld?: Pose | null;
}): DemoClear | null;      // null: Demo gilt nicht (Modell, Schalter, Kinematik, Umriss fehlt)
export function forwardDemoClear(get: () => DemoClear | null): DemoClear;  // stabil, fragt get() je Aufruf
export function useDemoClearance(
  robot: { model: { id: string } | undefined; entry: Pick<CellModuleDefinition, "bounds"> | undefined; kinematics: FlangeKinematics | null },
  cellModules: PlacedCellModule[],
  unknownTags: UnknownTag[],
  flangeToCamera?: Pose | null,
  baseInWorld?: Pose | null,
): DemoClear | null;

// model/robotDrive.ts
export interface RobotDriveDeps { /* ... */ demoClear?: DemoClear; }  // gesetzt: Wege werden geprüft
export type JointDriveOutcome =
  | ...
  | { status: "failed"; reason: string; blockedBy?: string };       // blockedBy: Hindernis, das den Weg sperrt
export type DriveTarget = ... & { moduleId?: string; demoTarget?: string[]; purpose?: Layer2Purpose };
export function driveRoute(deps: RobotDriveDeps, legs: number[][], seed: number[]): Promise<JointDriveOutcome>;
// Fährt Teilstücke nacheinander, stoppt beim ersten gescheiterten (Ergebnis des letzten Teilstücks).
export function driveSafe(deps: RobotDriveDeps, angles: number[], seed: number[],
  options?: { transit?: boolean /* Default true */; target?: string[] }): Promise<JointDriveOutcome>;
// Ohne deps.demoClear: driveJoints. Mit: Direktweg, sonst Transit; transit:false nur Direktweg.

// model/cellModules.ts (additiv)
export interface PlacementResult { /* ... */ unknownTags: UnknownTag[]; }  // UnknownTag: { tagId: number; position: Vec3 }

// viewport/model/sceneState.ts (additiv)
export interface ViewportSceneState { /* ... */ unknownTags: UnknownTag[]; }

// model/centreOnTag.ts: Parameter demoTarget?: string[] (Default [])
// model/endEffectorSpin.ts: Teilstücke über driveSafe(..., { transit: false })
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
| Transit blockiert | `blocked` mit Hindernis, z. B. „Weg kommt MOD-A näher als 20 cm“. Es wird nicht gefahren, `blockedBy` ist gesetzt. |
| `transit: false` und Direktweg blockiert | Nicht fahren, `blockedBy` gesetzt (centreOnTag, Spin-Test). |
| Teilstück scheitert (Fehler von `driveJoints`) | Abbruch; Rest wird nicht gefahren. |

## Vorgehen

Umgesetzt in dieser Reihenfolge: Modell (`cellClearance.ts`), Route
(`demoRoute.ts`, `useDemoClearance.ts`), `unknownTags` und `driveRoute`/`driveSafe`,
Einbindung in Layer 2, Anfahrplan, Hand-Auge-Automatik, Spin-Test und
`centreOnTag` (Kommentar `// DEMO-Kollisionsschutz` an jeder Aufrufstelle), Tests.

## Grenzen

- Nur Punkte, keine Linkdicke (FR3 ca. 7 cm): der echte Abstand zur Oberfläche ist eher ca. 13 cm.
- Boxen statt echter Formen.
- Der Weg wird als gerade Linie im Gelenkraum angenommen, nicht geprüft.
- Layer-1-Posen sind einige cm ungenau.
- Task-Programme auf dem Roboter-Server sind nicht abgedeckt.
- Manuelle Fahrten sind absichtlich nicht abgedeckt.
- Vorschauen (Layer-2-Plan, Hand-Auge `planViews`) zeigen die Demo-Sperre nicht (bewusst offen).
- Kamera wird in Layer 2, Hand-Auge und Spin-Test nicht als Punkt geprüft (die Greiferspitze liegt vorn).

## Offene Fragen

- Ist die `go_to`-Bahn wirklich linear im Gelenkraum? Am Roboter messen: zwei
  Stellungen langsam (0,1) hin und her fahren, `robotJointState`-Strom
  aufzeichnen, prüfen, dass jedes Gelenk ungefähr linear fährt (Annahme der Prüfung).
- Messposen nah an Modulen werden evtl. abgelehnt. Am Roboter sehen.
- Pappkarton als Modul ins Zellmodell stellen und darüber hinweg fahren lassen.

## Abweichungen vom Plan

- Zielmodul locker statt „gleicher Abstand für alle Module“ (Nutzerentscheidung 21.10.2026). Sonst wären Messposen nicht fahrbar.
- Anker-Ziele (`Boden`, `Wagen`) und Hand-Auge-Tisch (`Wagen`) ebenfalls locker.
- Start-Verletzungen locker bis frei statt sofort streng.
- Prüfung gedrehter Boxen im Modul-KS statt achsparalleler Hülle.
- `planDemoRoute` hat `jointNames` als ersten Parameter; Rückgabe `DemoRoute` mit `legs` bzw. `blocked` + `hit`.
- `driveRoute` gibt `JointDriveOutcome` zurück statt `void`; zusätzlich `driveSafe` mit `transit` und `target`.
- `transit: false` für `centreOnTag` und Spin-Test.
- Hand-Auge-Automatik fährt jetzt mit `AUTO_DRIVE_MAX_SPEED`.
- Review-Ablauf: Umsetzung Sonnet, Review Opus (2 Runden), Doku Haiku.

## Verifikation

- `tsc --noEmit` sauber.
- `vitest run`: 476 Tests grün (54 Dateien).

## Für die spätere richtige Lösung

- Echte Modulkörper statt Boxen (Modellgeometrie aus dem Modulkatalog).
- Gleicher Abstand für alle Module ersetzen durch modulspezifische Abstände.
- Unbekannte Tags als echte Hindernisse statt fester 0,2-m-Box.
- Linkdicke des FR3 in die Prüfung aufnehmen.
- Bahnprüfung entlang der tatsächlichen Trajektorie statt Stützstellen im Gelenkraum.
- Gemeinsame Kollisionsprüfung für manuelle Fahrten und Task-Programme.

## Nach Abschluss

- [x] Status auf `fertig`, tatsächliche Schnittstellen eingetragen.
- [x] Zeile im Index `doc/projektdoku/arbeitsplaene/README.md` aktualisiert.
- [ ] Dauerhaftes Fachwissen in die passende Fach-MD unter `doc/projektdoku/` übernommen.
