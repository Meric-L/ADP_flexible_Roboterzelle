# Git-Anleitung für dieses Projekt

Dieses Dokument erklärt wie du das Projekt über Git verwaltest — auch wenn du noch nie mit Git gearbeitet hast.

---

## Was ist Git?

Git ist ein Versionierungssystem. Es speichert jeden Stand deiner Dateien, sodass du jederzeit auf ältere Versionen zurückgehen kannst. Alle Änderungen werden in einem zentralen Online-Repository (GitHub) gespeichert. Der Ordner `latex/` (die schriftliche Ausarbeitung) wird zusätzlich mit ShareLaTeX synchronisiert.

---

## Voraussetzungen — Zugang beantragen

Bevor du loslegen kannst, brauchst du Zugang zu beiden Systemen. Bitte den Projektverantwortlichen darum:

| System | Was du brauchst | Wie |
|---|---|---|
| **GitHub** | GitHub-Account + Einladung zum Repo | Account erstellen auf github.com, dann Benutzernamen mitteilen |
| **ShareLaTeX** | TU-Account + Einladung zum Projekt | TU-Darmstadt-Login reicht, Projektverantwortlicher lädt dich über den Share-Button ein |

---

## Einmalige Einrichtung

### 1. Git installieren
- **Windows**: https://git-scm.com/download/win → Installer ausführen
- **Mac**: Terminal öffnen → `git --version` → Git wird automatisch angeboten
- **Linux**: `sudo apt install git`

### 2. Repository klonen und auf den Branch der Ausarbeitung wechseln (einmalig, pro PC)
Das lädt das gesamte Projekt auf deinen PC und wechselt auf den Branch, auf dem die Ausarbeitung liegt:
```bash
git clone git@github.com:Meric-L/ADP_flexible_Roboterzelle.git
cd ADP_flexible_Roboterzelle
git switch latex-ausarbeitung
```

> **Wichtig:** Die Ausarbeitung liegt auf dem Branch **`latex-ausarbeitung`**, nicht auf `main`.
> Auf `main` gibt es keinen Ordner `latex/`, und das dortige `setup.sh` ist veraltet: Es richtet ein `pushall` ein,
> das ShareLaTeX mit `--force` **überschreibt**. Das Setup-Skript (Schritt 5) deshalb nur auf `latex-ausarbeitung` ausführen.

### 3. Git-Identität setzen (einmalig, pro PC)
```bash
git config --global user.name "Dein Name"
git config --global user.email "deine@email.de"
```

### 4. ShareLaTeX Git-Token holen (einmalig)
Für die Synchronisation mit ShareLaTeX brauchst du ein persönliches Token — **jeder braucht sein eigenes**.

> ShareLaTeX → oben rechts auf deinen Namen klicken → **Account-Einstellungen** → Abschnitt **Git Integration** → Token anzeigen/generieren

Wenn git nach Zugangsdaten für ShareLaTeX fragt:
- **Username:** `git` (nicht deine E-Mail-Adresse!)
- **Passwort:** dein Token

Damit du das nicht jedes Mal eingeben musst: `git config --global credential.helper store` (speichert den Token im Klartext in `~/.git-credentials`).

### 5. Setup-Skript ausführen (einmalig, pro PC)
Das Skript richtet die ShareLaTeX-Verbindung und die Befehle `pullsl`, `pushsl` und `pushall` ein. Wer das Setup schon früher ausgeführt hat: **noch einmal ausführen**, die alten Befehle funktionieren nicht mehr.

**Windows:** `setup.bat` doppelklicken

**Mac / Linux:**
```bash
./setup.sh
```

### 6. Rebase für diesen Branch abschalten (einmalig, pro PC)
`latex/` hängt per `git subtree` an ShareLaTeX. Ein `git pull --rebase` (oder der „Sync“-Knopf in VS Code, wenn bei dir `pull.rebase = true` gesetzt ist) zerlegt diese Verknüpfung: Dateien landen plötzlich im Hauptordner statt in `latex/`, und es entstehen lauter Konflikte. Deshalb für diesen Branch normale Merges einstellen:
```bash
git config branch.latex-ausarbeitung.rebase false
```
Steckst du schon mitten in so einem Rebase („interaktives Rebase im Gange“), bricht `git rebase --abort` ihn ab, ohne dass etwas verloren geht.

---

## Was wird mit ShareLaTeX synchronisiert?

**Nur der Ordner `latex/`.** Darin liegen ausschließlich die Dateien, die die Ausarbeitung direkt nutzt (`.tex`, `.bib`, Bilder, Logos). Die Markdown-Doku in `doc/` und alles andere bleibt nur auf GitHub und landet **nicht** auf ShareLaTeX.

Technisch ist `latex/` ein `git subtree`: ShareLaTeX sieht den Inhalt von `latex/` als sein Projekt-Wurzelverzeichnis.

**Es wird nichts überschrieben:** Kein Befehl pusht mit `--force`. Hat jemand auf ShareLaTeX weitergeschrieben, lehnt ShareLaTeX deinen Push ab; dann zuerst `git pullsl`, danach erneut pushen. Gelöschte Dateien werden nur gelöscht, wenn jemand sie wirklich gelöscht hat (normaler Git-Merge).

Die Befehle wirken immer auf den Branch, auf dem du gerade bist. Für die Ausarbeitung immer auf **`latex-ausarbeitung`** arbeiten (prüfen mit `git status`, erste Zeile).

---

## Täglicher Workflow

### Szenario A — Du arbeitest lokal an der Ausarbeitung

**Bevor du anfängst:** aktuellen Stand von GitHub und ShareLaTeX holen:
```bash
git pull
git pullsl
```

**Wenn du fertig bist:**
```bash
git add latex/
git commit -m "Kurze Beschreibung was du gemacht hast"
git pushall
```

`git pushall` lädt deinen Branch zu **GitHub** und den Ordner `latex/` zu **ShareLaTeX** hoch.

---

### Szenario B — Jemand hat auf ShareLaTeX gearbeitet

```bash
git pullsl
git push
```

`git pullsl` merged die Änderungen von ShareLaTeX in `latex/`, `git push` bringt sie zu GitHub.

---

### Wenn `git pushall` / `git pushsl` abgelehnt wird

Meldung wie `rejected ... (fetch first)` heißt: Auf ShareLaTeX gibt es neuere Änderungen. Dann:
```bash
git pullsl
git pushsl
```

### Wenn `git pullsl` mit „working tree has modifications“ abbricht

`git pullsl` braucht einen sauberen Stand. Erst eigene Änderungen committen, dann erneut:
```bash
git add latex/
git commit -m "..."
git pullsl
```
Meldet es den Fehler, obwohl `git status` nichts anzeigt, hilft vorher `git update-index --refresh` (VS Code hat dann nur Zeitstempel berührt).

### Wenn `git pullsl` einen Konflikt meldet

Oft ist der Konflikt nur **scheinbar**: `git subtree` wählt manchmal eine zu alte gemeinsame Basis. Hast du die betroffene Datei seit deinem letzten `pushall` **lokal nicht verändert**, ist die ShareLaTeX-Fassung einfach die neuere. Dann:
```bash
git checkout --theirs -- "latex/chapters/<Datei>.tex"
git add "latex/chapters/<Datei>.tex"
git commit -m "latex: Stand von ShareLaTeX übernommen"
```
Hast du die Datei dagegen selbst geändert, die Konfliktstellen (`<<<<<<<` … `>>>>>>>`) in VS Code von Hand zusammenführen. Im Zweifel nachfragen, statt eine Seite blind zu übernehmen.

---

## Die wichtigsten Befehle im Überblick

| Befehl | Was er macht |
|---|---|
| `git pull` | Holt den aktuellen Stand von GitHub |
| `git pullsl` | Holt Änderungen von ShareLaTeX nach `latex/` (Merge, löscht nichts ungewollt) |
| `git pushsl` | Lädt `latex/` zu ShareLaTeX hoch (ohne `--force`) |
| `git pushall` | Lädt den aktuellen Branch zu GitHub + `latex/` zu ShareLaTeX hoch |
| `git switch latex-ausarbeitung` | Wechselt auf den Branch der Ausarbeitung |
| `git add latex/` | Markiert Änderungen an der Ausarbeitung zum Speichern |
| `git commit -m "..."` | Speichert die markierten Änderungen lokal |
| `git status` | Zeigt welche Dateien geändert wurden |
| `git log --oneline` | Zeigt die letzten gespeicherten Stände |

---

## Goldene Regeln

1. **Immer zuerst `git pull` und `git pullsl`** bevor du anfängst zu arbeiten — sonst riskierst du Konflikte
2. **Commit-Messages auf Deutsch oder Englisch**, aber immer aussagekräftig — z.B. `"Kapitel 2 Einleitung ergänzt"` statt `"update"`
3. **Nicht gleichzeitig** lokal und auf ShareLaTeX an derselben Datei arbeiten
4. **Nicht den „Sync“-Knopf von VS Code** für diesen Branch benutzen, sondern `git pullsl` / `git pushall` im Terminal
5. **Nichts aus dem PDF kopieren und in die `.tex` einfügen:** Aus `--` wird dabei ein Unicode-Minus „−“, an dem pdfLaTeX abbricht (danach fehlen auch alle Zitate, weil biber nicht mehr läuft)

---

## Ausarbeitung lokal schreiben (VS Code)

Du kannst die Ausarbeitung statt auf ShareLaTeX auch lokal in VS Code schreiben. Die Einstellungen dafür liegen schon im Repo (`.vscode/settings.json`).

**Einmalig:**
- LaTeX installieren — Linux: `sudo apt install texlive-full latexmk biber`; Windows: MiKTeX oder TeX Live
- In VS Code die Erweiterung **LaTeX Workshop** installieren
- VS Code aktuell halten — ist es zu alt, lädt VS Code eine ältere LaTeX-Workshop-Version, und die PDF-Ansicht bleibt leer (Linux: `sudo apt update && sudo apt install code`)

**Arbeiten:**
- Den **Repo-Ordner** in VS Code öffnen (nicht einen übergeordneten Ordner, sonst greifen die Einstellungen nicht)
- `latex/PLCM-Thesis.tex` oder ein Kapitel öffnen
- **Bauen:** Speichern (baut automatisch) oder `Strg+Alt+B`; das erste Bauen dauert ca. 30–40 s, danach reichen meist wenige Sekunden
- **PDF anzeigen:** `Strg+Alt+V`; `Strg`+Klick im PDF springt zur Stelle im Quelltext
- Die PDF und alle Hilfsdateien landen in `latex/build/`, das per `.gitignore` weder auf GitHub noch auf ShareLaTeX kommt

**Schreibregeln, auf die wir uns geeinigt haben:**
- Werte mit Einheit immer mit `siunitx`: `\SI{3}{\milli\metre}`, Bereiche `\SIrange{5}{30}{\centi\metre}`, Prozent `\SI{19}{\percent}`, Euro `\SI{1000}{\euro}`
- Verweise mit Bezeichnung und geschütztem Leerzeichen: `Abbildung~\ref{...}`, `Tabelle~\ref{...}`, `Abschnitt~\ref{...}` — `\ref` allein setzt nur die nackte Nummer
- Quellen ohne Autor (Normen, Datenblätter) bekommen in `beispielbib.bib` ein `shorthand = {Nummer}`, fortlaufend in Zitierreihenfolge
- Wer mit KI schreibt (z. B. Claude Code): Commits, die `latex/` ändern, bekommen **keine** `Co-Authored-By: Claude`-Zeile (siehe `CLAUDE.md`), die Nutzung gehört aber ins KI-Hilfsmittel-Verzeichnis der Arbeit

---

## Literatur mit Zotero (zotero-cli und Claude-Skill)

Mit dem Kommandozeilen-Werkzeug `zotero-cli` lassen sich Zotero-Bibliotheken aus dem Terminal durchsuchen, Volltexte lesen, Einträge per DOI anlegen und BibTeX für `latex/beispielbib.bib` exportieren. Für Claude Code gibt es dazu einen **Skill**, damit Claude die Bibliothek selbst nutzen kann („such mir Paper zu X und trag sie ein“).

### 1. Installieren (einmalig, pro PC)
Benötigt [`uv`](https://docs.astral.sh/uv/) (Linux/Mac: `curl -LsSf https://astral.sh/uv/install.sh | sh`):
```bash
uv tool install zotero-mcp-server
```
Das bringt die Befehle `zotero-cli` und `zotero-mcp` mit.

### 2. Zugang über die Zotero-Web-API (ohne Zotero Desktop)
Im lokalen Standardmodus braucht `zotero-cli` die laufende Zotero-Desktop-App. Über die Web-API geht es ohne:
1. Auf https://www.zotero.org/settings/keys einen privaten Key anlegen. Leserechte reichen zum Suchen; zum Anlegen von Einträgen zusätzlich **„Allow write access“**. Für eine Gruppenbibliothek unter „Default Group Permissions“ Zugriff erlauben.
2. Einrichten (IDs und Key einsetzen):
   ```bash
   # eigene Bibliothek: User-ID steht auf der Keys-Seite oben ("Your user ID for use in API calls is ...")
   zotero-mcp setup --no-local --no-claude --skip-semantic-search --library-type user --library-id <USER-ID> --api-key <KEY>
   # gemeinsame Team-Bibliothek: Gruppen-ID steht in der URL der Gruppe auf zotero.org
   zotero-mcp setup --no-local --no-claude --skip-semantic-search --library-type group --library-id <GRUPPEN-ID> --api-key <KEY>
   ```
   Bei der Frage nach WebDAV einfach Enter drücken (= nein), außer eure PDFs liegen auf WebDAV.
3. Testen: `zotero-cli config` und `zotero-cli get recent`

Der Key steht danach in `~/.config/zotero-mcp/config.json` — **jeder nimmt seinen eigenen Key, und die Datei kommt nie ins Repo**.

Einschränkung der Web-API: PDF-Volltexte gibt es nur, wenn die Dateien über den Zotero-Speicher synchronisiert sind; Metadaten, Notizen und Tags gehen immer.

### 3. Skill für Claude Code installieren
```bash
zotero-mcp install-skill --target claude-user   # für alle deine Projekte
# oder nur für dieses Repo:
zotero-mcp install-skill --target claude
```
Danach erkennt Claude Code Anfragen zu Zotero, Literatur und Zitaten und nutzt `zotero-cli` von selbst.

### 4. Nützliche Befehle
| Befehl | Was er macht |
|---|---|
| `zotero-cli search "AprilTag pose"` | Bibliothek durchsuchen |
| `zotero-cli add doi 10.1109/JIOT.2016.2579198` | Eintrag per DOI anlegen |
| `zotero-cli get bibtex <ITEM-KEY>` | BibTeX für `beispielbib.bib` ausgeben |
| `zotero-cli --help` | alle Befehle |

---

## Projektstruktur

```
ADP_flexible_Roboterzelle/
├── latex/        ← LaTeX-Ausarbeitung (wird mit ShareLaTeX synchronisiert)
│   ├── PLCM-Thesis.tex
│   ├── chapters/
│   ├── appendix/
│   ├── Bilder/
│   └── ...
├── doc/          ← Projektdoku (Markdown), nur auf GitHub
├── src/          ← Quellcode (Sensorik, Algorithmen)
├── hardware/     ← CAD-Dateien, Schaltpläne
└── data/         ← Messdaten, Testdatensätze
```
