# Git guide for this project

This document explains how you manage the project with Git — even if you have never worked with Git before.

---

## What is Git?

Git is a version control system. It stores every state of your files, so you can go back to older versions at any time. All changes are stored in a central online repository (GitHub) and automatically synchronized with ShareLaTeX.

---

## Prerequisites — request access

Before you can start, you need access to both systems. Ask the project lead for it:

| System | What you need | How |
|---|---|---|
| **GitHub** | GitHub account + invitation to the repo | Create an account at github.com, then tell the project lead your username |
| **ShareLaTeX** | TU account + invitation to the project | The TU Darmstadt login is enough; the project lead invites you via the Share button |

---

## One-time setup

### 1. Install Git
- **Windows**: https://git-scm.com/download/win → run the installer
- **Mac**: open Terminal → `git --version` → Git is offered automatically
- **Linux**: `sudo apt install git`

### 2. Clone the repository (once, per PC)
This downloads the entire project to your PC:
```bash
git clone git@github.com:Meric-L/ADP_flexible_Roboterzelle.git
cd ADP_flexible_Roboterzelle
```

### 3. Set your Git identity (once, per PC)
```bash
git config --global user.name "Your Name"
git config --global user.email "your@email.de"
```

### 4. Get a ShareLaTeX Git token (once)
For synchronization with ShareLaTeX you need a personal token — **everyone needs their own**.

> ShareLaTeX → click your name at the top right → **Account-Einstellungen** (account settings) → section **Git Integration** → show/generate token

Use this token as your **password** when git asks for credentials for ShareLaTeX. Your username is your TU Darmstadt email address.

### 5. Run the setup script (once, per PC)
The script sets up the ShareLaTeX connection and all commands automatically.

**Windows:** double-click `setup.bat`

**Mac / Linux:**
```bash
./setup.sh
```

---

## Daily workflow

### Scenario A — You work locally (on your PC)

**Before you start:** always fetch the current state from GitHub and ShareLaTeX first:
```bash
git pullsl
```

**When you are done:** save your changes and upload them:
```bash
git add doc/
git commit -m "Short description of what you did"
git pushall
```

`git pushall` uploads your changes to **GitHub** and **ShareLaTeX** at the same time.

---

### Scenario B — Someone has worked on ShareLaTeX

If changes were made on ShareLaTeX, you fetch them with a single command:
```bash
git pullsl
```

That fetches the changes from ShareLaTeX, updates `doc/` and pushes everything to GitHub.

---

## The most important commands at a glance

| Command | What it does |
|---|---|
| `git pull` | Fetches the current state from GitHub only |
| `git add doc/` | Marks changes in `doc/` for saving |
| `git commit -m "..."` | Saves the marked changes locally |
| `git pushall` | Uploads everything to GitHub + ShareLaTeX |
| `git pullsl` | Fetches changes from ShareLaTeX → GitHub |
| `git status` | Shows which files were changed |
| `git log --oneline` | Shows the last saved states |

---

## Golden rules

1. **Always run `git pull` first** before you start working — otherwise you risk conflicts
2. **Commit messages in German or English**, but always meaningful — e.g. `"Chapter 2 introduction added"` instead of `"update"`
3. **Do not work on the same file locally and on ShareLaTeX at the same time**

---

## Project structure

```
ADP_flexible_Roboterzelle/
├── doc/          ← LaTeX document (synchronized with ShareLaTeX)
│   ├── PLCM-Thesis.tex
│   ├── chapters/
│   ├── appendix/
│   ├── Bilder/
│   └── ...
├── src/          ← source code (sensors, algorithms)
├── hardware/     ← CAD files, circuit diagrams
└── data/         ← measurement data, test datasets
```
