# Welttags aus einer Quelle, im Frontend pflegbar

**Status:** geplant — 30.10.2026
**Verantwortlich:** Meric, `Agent: Welttags eine Quelle`
**Thema:** apriltag
**Branch:** webskillcomposition: neuer Branch `welttags-eine-quelle` von `Ungetestet` (Hauptteil); dieses Repo: `welttags-eine-quelle` von `apriltag` (nur Schritt 3 und 4)

## Ziel

Die Welttags (IDs, Lage im Welt-KS, Größe, Anker) gibt es heute viermal unabhängig: `config/tagmap.json` auf
jedem Pi, der Seed in `frontend/src/features/cell-modules/config/moduleCatalog.ts`, die `WORLD-…`-Einträge in
`backend/catalog/module_catalog.json` und die Anker-ID im Tag-Tool. Wer einen Welttag verschiebt oder neu
vermisst, muss alle Kopien von Hand nachziehen. Vergisst er eine, rechnet das Frontend die Roboterbasis aus
einer anderen Welt als die Module, und Roboter→Modul wird falsch (Analyse: [`../altlasten-wsc.md`](../altlasten-wsc.md),
Abschnitt 10).

Hinterher:

1. Es gibt **eine** Quelle: die Tag-Map auf den Pis (`wsc.vision.tagmap/2`). Alle anderen Stellen lesen sie nur.
2. Welttags werden **im Frontend** angezeigt und bearbeitet (hinzufügen, entfernen, verschieben, Größe, Anker)
   und mit einem Klick auf **beide** Pis geschrieben. Das Frontend zeigt, ob Decken- und Hand-Pi dieselbe Karte haben.
3. Die Basiskorrektur beim Anfahren rechnet mit den Lagen, die der Pi benutzt, nie mit einer Frontend-Kopie.
4. Optional: Eine Neuvermessung der Welttags wird aus dem Frontend angestoßen statt mit dem Offline-CLI.

## Bereits gelesen

- [`../altlasten-wsc.md`](../altlasten-wsc.md), Abschnitt 10 „Welttag-Kette über beide Repos“: Inventar der vier Kopien, Fehlerfortpflanzung, Empfehlung
- [`apriltag-tagmap-ueber-opcua.md`](apriltag-tagmap-ueber-opcua.md): `SetTagMap`/`TagMapJson`, Größenregel, Fehlerfälle
- [`apriltag-welttag-konzept.md`](apriltag-welttag-konzept.md): Welt-KS, Anker, Rolle `world`
- [`../vision-server-interface.md`](../vision-server-interface.md): Tag-Map-Methoden im Adressraum
- `src/vision_server/runner.py` (`set_tag_map`, `_publish_tag_map`), `src/tagloc/tagmap.py` (`tag_map_from_json`, `tag_map_to_json`, `place_tags`), `src/tagloc/identity.py` (`tag_map_identity`), `config/tagmap.json`

## Betroffene Dateien

webskillcomposition (Hauptteil):

- `frontend/src/features/opcua-server/config/piServers.ts`: Bindings `tagMapJsonNodeId`, `setTagMapNodeId`
- neu `frontend/src/features/opcua-server/model/worldTagMap.ts` (+ Test): Parsen, Serialisieren, Vergleichen
- neu `frontend/src/features/opcua-server/model/useWorldTagMap.ts` (+ Test): Karte beider Pis lesen, schreiben, Gleichstand
- neu `frontend/src/features/cell-modules/components/WorldTagEditor.tsx`: Tabelle und 3D-Vorschau
- `frontend/src/features/cell-modules/config/moduleCatalog.ts`: Seed `WORLD-583…586` und `WORLD_TAG_IDS` entfallen
- `frontend/src/features/cell-modules/model/catalogValidation.ts`: `MAX_TAG_ID` und Welttag-IDs aus der Pi-Karte
- `frontend/src/features/cell-modules/model/catalogStore.ts`, `components/ModuleCatalogModal.tsx`: Welttag-Eingabe raus, Verweis auf den Editor
- `frontend/src/features/viewport/model/sceneState.ts`: `worldTags` aus der Pi-Karte
- `frontend/src/features/cell-modules/components/ApproachPlanPanel.tsx`, `model/approachPlan.ts`, `model/tagServo.ts`: Anker und `baseCorrection` mit den Pi-Lagen
- `frontend/src/tag-tool/components/ExportPanel.tsx`: Anker-ID aus der Karte statt `0`
- `backend/catalog/module_catalog.json` und die Katalog-Validierung im Backend: `WORLD-…`-Einträge entfernen (Migration)

Dieses Repo (nur Schritt 3 und 4):

- `src/vision_server/payload.py`, `src/vision_server/layer2/run.py`: Feld `tagMapId`
- Schritt 4: neue Methode `SurveyWorldTags` (berührt `runner.py`, erst nach Merge von `usecase-foto`)
- `doc/projektdoku/vision-server-interface.md`

## Schnittstellen

### OPC UA (vorhanden, unverändert, auf beiden Pis)

| Element | NodeId | Typ | Richtung | Bedeutung |
| --- | --- | --- | --- | --- |
| `SetTagMap` | `ns=<vision>;s=<VisionSystemName>.SetTagMap` | Methode | Aufruf | ersetzt die **ganze** Karte, schreibt `config/tagmap.json` |
| `TagMapJson` | `ns=<vision>;s=VisionMachine.TagMapJson` | Variable (String) | Lesen/Abo | aktuell geladene Karte als JSON |

```
SetTagMap(TagMapJson : String) -> (Error : Int32)
  0 OK                 Karte aktiv und gespeichert
  2 INVALID_ARGUMENT   kein JSON, falsches Schema, unbekannte Rolle (Karte unverändert)
  3 BUSY               Job, Kalibrierung, Layer-2-Lauf oder Hand-Auge-Sitzung aktiv (Karte unverändert)
  6 INTERNAL           Datei nicht schreibbar; Karte trotzdem aktiv, nach Neustart wieder die alte
```

Die Pi-Karte enthält heute nur Welttags (Rolle `world`). Weil `SetTagMap` die ganze Karte ersetzt, schreibt das
Frontend immer die **vollständige** Karte: lesen, ändern, ganz zurückschreiben. Einträge anderer Rollen, die
ein Pi künftig enthält, bleiben dabei unverändert erhalten.

### Daten: `wsc.vision.tagmap/2` (unverändert)

```json
{
  "schema": "wsc.vision.tagmap/2",
  "frameId": "world",
  "anchorTagId": 583,
  "tagFamily": "tag36h11",
  "tags": [
    { "tagId": 583, "role": "world", "sizeM": 0.2,
      "poseInWorld": { "position": [0.0, 0.0, 0.0], "orientation": [0.0, 0.0, 0.0, 1.0] } }
  ]
}
```

`position` in Metern, `orientation` als Quaternion `[x, y, z, w]`, `sizeM` ist die Kantenlänge des schwarzen Quadrats.

### TypeScript (neu, webskillcomposition)

```ts
// frontend/src/features/opcua-server/model/worldTagMap.ts
export interface WorldTag { tagId: number; role: string; sizeM: number; poseInWorld: Pose }
export interface WorldTagMap {
  schema: "wsc.vision.tagmap/2"; frameId: string; anchorTagId: number; tagFamily: string; tags: WorldTag[];
}
/** Wirft bei falschem Schema oder fehlenden Feldern; unbekannte Felder bleiben erhalten. */
export function parseTagMap(json: string): WorldTagMap;
export function serializeTagMap(map: WorldTagMap): string;          // nach tagId sortiert
export function diffTagMaps(a: WorldTagMap, b: WorldTagMap, tolM?: number, tolDeg?: number): TagMapDiff[];

// frontend/src/features/opcua-server/model/useWorldTagMap.ts
export interface PiTagMapState { serverUrl: string; map: WorldTagMap | null; error: string | null }
export function useWorldTagMap(): {
  pis: PiTagMapState[];          // Decken-Pi und Hand-Pi
  reference: WorldTagMap | null; // Karte des Decken-Pi, Grundlage für Anzeige und Rechnung
  inSync: boolean;               // alle erreichbaren Pis haben dieselbe Karte (diffTagMaps leer)
  save(map: WorldTagMap): Promise<{ serverUrl: string; error: number | string }[]>;
};
```

`save` schreibt nacheinander an Decken- und Hand-Pi, liest danach `TagMapJson` beider Pis zurück und meldet je
Pi das Ergebnis. Ist ein Pi nicht erreichbar oder `BUSY`, wird nichts zurückgerollt, aber `inSync` ist `false`
und der Editor zeigt „Hand-Pi nicht aktualisiert, erneut senden“.

### Payload (Schritt 3, additiv)

| Feld | Wo | Inhalt |
| --- | --- | --- |
| `tagMapId` | Layer-1-Ergebnis (`payload.py`), Layer-2-Report (`layer2/run.py`) | `tag_map_identity()` der benutzten Karte, z. B. `tagmap#3f2a…` |

Das Frontend sperrt den Anker, wenn die `tagMapId` von Decken- und Hand-Pi verschieden sind.

### OPC UA (Schritt 4, Vorschlag, erst nach Merge von `usecase-foto`)

```
SurveyWorldTags(RequestJson : String) -> (ResultJson : String, Error : Int32)
  Request: { "frames": 7, "anchorTagId": 583 }
  Result:  { "schema": "wsc.vision.tagmap-survey/1", "proposed": <Karte wsc.vision.tagmap/2>,
             "residualMm": { "<tagId>": number }, "framesUsed": number }
```

Misst die Welttag-Lagen aus mehreren Deckenbildern mit `tagloc.tagmap.place_tags` und **schreibt nichts**. Das
Frontend zeigt den Vorschlag als Differenz zur aktuellen Karte; übernommen wird er nur über `SetTagMap`.

### Fehlerfälle

| Fall | Verhalten |
| --- | --- |
| Ein Pi offline | Editor zeigt die Karte des erreichbaren Pi, Speichern nur mit Warnung; `inSync = false` |
| Karten der Pis verschieden beim Laden | Editor zeigt die Differenz; Knopf „Karte des Decken-Pi auf Hand-Pi übertragen“; Anker gesperrt |
| `BUSY` | Meldung „Pi beschäftigt (Job, Kalibrierung, Layer 2)“, später erneut senden |
| `INVALID_ARGUMENT` | lokale Prüfung hätte das verhindern müssen; Fehler anzeigen, nichts übernehmen |
| `INTERNAL` | „aktiv, aber nicht gespeichert; nach Neustart wieder die alte Karte“ |
| Welttag-ID kollidiert mit einem Modultag im Katalog | Katalog-Validierung meldet es; Speichern der Karte gesperrt |
| Backend-Katalog mit alten `WORLD-…`-Einträgen | Migration entfernt sie beim Laden, einmal protokolliert |

## Vorgehen

0. **Sofort, ohne Code:** Frontend-Seed (`sizeM` 0,198 auf 0,2) und Backend-Katalog auf die Pi-Karte bringen (`apriltag`, Commit `09f4f1a`), beide Pis auf denselben Stand; siehe „Offene Aufgaben auf den Pis“ in [`aufraeumen-und-code-handbuch.md`](aufraeumen-und-code-handbuch.md).
1. **Lesen (Frontend):** `worldTagMap.ts` mit Tests, Bindings, `useWorldTagMap`; `sceneState.worldTags`, Anker und `baseCorrection` aus der Pi-Karte; Seed, `WORLD_TAG_IDS`, `MAX_TAG_ID` und Tag-Tool-Anker aus der Karte ableiten. Ab hier kann keine Frontend-Kopie mehr abweichen.
2. **Pflegen (Frontend):** `WorldTagEditor` (Tabelle: ID, Anker, x/y/z, Gier/Neigung, Größe; hinzufügen und entfernen; 3D-Vorschau über `WorldTagMarkers`), Speichern auf beide Pis, Gleichstandsanzeige, Übertragen-Knopf; Welttag-Eingaben aus dem Modulkatalog entfernen, Backend-Katalog migrieren.
3. **Pi:** `tagMapId` in Layer-1-Ergebnis und Layer-2-Report (additiv), Fach-MD nachziehen; Frontend sperrt den Anker bei Abweichung.
4. **Optional:** `SurveyWorldTags` auf dem Decken-Pi und „Welttags neu einmessen“ im Editor.

Tests: vitest für `parseTagMap`, `serializeTagMap`, `diffTagMaps`, `useWorldTagMap` mit Attrappen-Controller und
`baseCorrection` mit Pi-Lagen; in diesem Repo ein Payload-Test für `tagMapId`.

## Offene Fragen

- Wer darf Welttags ändern? Vorschlag: Bestätigungsdialog mit Differenz, kein Rollenkonzept.
- Eigener Dialog „Zelle / Welttags“ oder Abschnitt im Modulkatalog? Vorschlag: eigener Dialog, der Modulkatalog verlinkt nur.
- Toleranzen für „gleich“ in `diffTagMaps` (Vorschlag 0,1 mm und 0,01°).
- Überschneidung mit dem Aufräumen: Sitzung B fasst den `moduleCatalog.ts`-Seed (D13/B6) und die Welttag-Kette ausdrücklich nicht an; dieser Plan übernimmt den Welttag-Teil davon.
- Schritt 4 berührt `runner.py` (gerade heiß, `usecase-foto` ungemergt), also erst danach.

## Abweichungen vom Plan

Wird während der Umsetzung gefüllt.

## Nach Abschluss

- Status auf `fertig`, tatsächliche Schnittstellen eintragen.
- Abschnitt 10 in `altlasten-wsc.md` und Fund B15 in `altlasten-zelle.md` als erledigt markieren.
- `vision-server-interface.md` um `tagMapId` (und ggf. `SurveyWorldTags`) ergänzen.
