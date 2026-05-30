# Git-Anleitung für dieses Projekt

Dieses Dokument erklärt wie du das Projekt über Git verwaltest — auch wenn du noch nie mit Git gearbeitet hast.

---

## Was ist Git?

Git ist ein Versionierungssystem. Es speichert jeden Stand deiner Dateien, sodass du jederzeit auf ältere Versionen zurückgehen kannst. Alle Änderungen werden in einem zentralen Online-Repository (GitHub) gespeichert und automatisch mit ShareLaTeX synchronisiert.

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

Dieses Token verwendest du als **Passwort**, wenn git nach Zugangsdaten für ShareLaTeX fragt. Dein Benutzername ist deine TU-Darmstadt E-Mail-Adresse.

### 5. Setup-Skript ausführen (einmalig, pro PC)
Das Skript richtet die ShareLaTeX-Verbindung und alle Befehle automatisch ein.

**Windows:** `setup.bat` doppelklicken

**Mac / Linux:**
```bash
./setup.sh
```

---

## Täglicher Workflow

### Szenario A — Du arbeitest lokal (auf deinem PC)

**Bevor du anfängst:** Immer zuerst den aktuellen Stand aus GitHub und ShareLaTeX holen:
```bash
git pullsl
```

**Wenn du fertig bist:** Änderungen speichern und hochladen:
```bash
git add doc/
git commit -m "Kurze Beschreibung was du gemacht hast"
git pushall
```

`git pushall` lädt deine Änderungen gleichzeitig zu **GitHub** und **ShareLaTeX** hoch.

---

### Szenario B — Jemand hat auf ShareLaTeX gearbeitet

Wenn Änderungen auf ShareLaTeX gemacht wurden, holst du diese mit einem einzigen Befehl:
```bash
git pullsl
```

Das holt die Änderungen aus ShareLaTeX, aktualisiert `doc/` und pusht alles zu GitHub.

---

## Die wichtigsten Befehle im Überblick

| Befehl | Was er macht |
|---|---|
| `git pull` | Holt den aktuellen Stand nur von GitHub |
| `git add doc/` | Markiert Änderungen in `doc/` zum Speichern |
| `git commit -m "..."` | Speichert die markierten Änderungen lokal |
| `git pushall` | Lädt alles zu GitHub + ShareLaTeX hoch |
| `git pullsl` | Holt Änderungen aus ShareLaTeX → GitHub |
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
├── doc/          ← LaTeX-Dokument (wird mit ShareLaTeX synchronisiert)
│   ├── PLCM-Thesis.tex
│   ├── chapters/
│   ├── appendix/
│   ├── Bilder/
│   └── ...
├── src/          ← Quellcode (Sensorik, Algorithmen)
├── hardware/     ← CAD-Dateien, Schaltpläne
└── data/         ← Messdaten, Testdatensätze
```
