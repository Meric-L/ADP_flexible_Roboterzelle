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

### 2. Repository klonen (einmalig, pro PC)
Das lädt das gesamte Projekt auf deinen PC:
```bash
git clone git@github.com:Meric-L/ADP_flexible_Roboterzelle.git
cd ADP_flexible_Roboterzelle
```

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

---

## Was wird mit ShareLaTeX synchronisiert?

**Nur der Ordner `latex/`.** Darin liegen ausschließlich die Dateien, die die Ausarbeitung direkt nutzt (`.tex`, `.bib`, Bilder, Logos). Die Markdown-Doku in `doc/` und alles andere bleibt nur auf GitHub und landet **nicht** auf ShareLaTeX.

Technisch ist `latex/` ein `git subtree`: ShareLaTeX sieht den Inhalt von `latex/` als sein Projekt-Wurzelverzeichnis.

**Es wird nichts überschrieben:** Kein Befehl pusht mit `--force`. Hat jemand auf ShareLaTeX weitergeschrieben, lehnt ShareLaTeX deinen Push ab; dann zuerst `git pullsl`, danach erneut pushen. Gelöschte Dateien werden nur gelöscht, wenn jemand sie wirklich gelöscht hat (normaler Git-Merge).

Die Befehle wirken immer auf den Branch, auf dem du gerade bist. Für die Ausarbeitung am besten auf `main` arbeiten.

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

---

## Die wichtigsten Befehle im Überblick

| Befehl | Was er macht |
|---|---|
| `git pull` | Holt den aktuellen Stand von GitHub |
| `git pullsl` | Holt Änderungen von ShareLaTeX nach `latex/` (Merge, löscht nichts ungewollt) |
| `git pushsl` | Lädt `latex/` zu ShareLaTeX hoch (ohne `--force`) |
| `git pushall` | Lädt den aktuellen Branch zu GitHub + `latex/` zu ShareLaTeX hoch |
| `git add latex/` | Markiert Änderungen an der Ausarbeitung zum Speichern |
| `git commit -m "..."` | Speichert die markierten Änderungen lokal |
| `git status` | Zeigt welche Dateien geändert wurden |
| `git log --oneline` | Zeigt die letzten gespeicherten Stände |

---

## Goldene Regeln

1. **Immer zuerst `git pull`** bevor du anfängst zu arbeiten — sonst riskierst du Konflikte
2. **Commit-Messages auf Deutsch oder Englisch**, aber immer aussagekräftig — z.B. `"Kapitel 2 Einleitung ergänzt"` statt `"update"`
3. **Nicht gleichzeitig** lokal und auf ShareLaTeX an derselben Datei arbeiten

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
