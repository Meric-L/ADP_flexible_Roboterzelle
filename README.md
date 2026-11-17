# ADP_flexible_Roboterzelle
Theory &amp; practice: investigation and implementation of localization methods for reconfigurable robot cells.
# Localization in modular robot cells

This repository contains both the practical software implementation and the LaTeX source files of our group work on position and topology detection of mechatronic modules. 

## Project focus
The goal of the work is the investigation, evaluation and **practical implementation** of various localization technologies for use in a flexible, modular laboratory robot cell (approx. 5x5 meters). Among others, the following are considered:
* **optical marker detection:** 
* **Topological detection:** short-range localization at mechanical interfaces (e.g. Near Field Magnetic Positioning).
* **UBW detection:**

## Repository structure
The project is strictly separated into documentation and code components:

### Documentation
* `/latex` - The LaTeX source files of the written thesis (synced with ShareLaTeX, see `GIT_ANLEITUNG.md`).
* `/doc/projektdoku` - The technical project documentation as Markdown (see below).
* `/doc/auswertung` - Evaluation reports of calibration and measurement runs.
* `/concept` - Flow diagram and open concept questions.

### Implementation (source code)
* `/src/tagloc` - AprilTag localization: calibration, detection, coordinate systems.
* `/src/vision_server` - The vision system (OPC 40100 + Part 10) including the cell's server
  (`server.py`), discoverability (`discovery/`) and the vendored nodesets (`nodesets/`).
* `/src/ua_program` - Basic implementation of OPC UA programs (spec Part 10).
* `/src/jobs` - Inspection job `calibrate.py`, which the vision server starts as a subprocess.
* `/deploy` - Template of the systemd unit `opcua-server.service` for the Pis, including deployment instructions.
* `/tools` - Print sheets for tags and boards, synthetic test scenes.
* `/config` - Tag map (cell layout), versioned.
* `/hardware` - CAD files, circuit diagrams or 3D-printing models for the sensor mounts.
* `/data` - Calibration files and recordings. Only selected files are versioned (see [`code/zelle-tools-tests.md`](doc/projektdoku/code/zelle-tools-tests.md)).
* `/tests` - `PYTHONPATH=src python3 -m unittest discover -s tests -t .`

## Technical documentation

The documentation has two levels:

1. **Start here: short documentation of the current code.** Written for people who want to understand
   and use the code (team members, reviewers of the thesis). It describes only the current state on `main`.
2. **In-depth documents.** Concepts, full references, test instructions, work plans and audits. Most of
   them were written while developing with AI agents and also contain history and decisions. Read them
   when you need the details of one topic.

### 1. Start here

| Document | Purpose |
| --- | --- |
| [`doc/projektdoku/code-dokumentation.md`](doc/projektdoku/code-dokumentation.md) | **Entry point**: which parts exist, how they connect, system picture, where each part is described |
| [`doc/projektdoku/vision-server-interface-short.md`](doc/projektdoku/vision-server-interface-short.md) | **OPC UA interface of the vision server, short**: addresses, jobs, methods, results |
| [`doc/projektdoku/code/`](doc/projektdoku/code/) | Code reference per area (library, vision server, tools/tests, webskillcomposition parts) |
| [`doc/projektdoku/apriltag-e2e-test.md`](doc/projektdoku/apriltag-e2e-test.md) | How to test everything end to end, from the PC to the Raspberry Pi camera |

### 2. In-depth documents

| Document | Purpose |
| --- | --- |
| [`doc/projektdoku/vision-server-interface.md`](doc/projektdoku/vision-server-interface.md) | Full OPC UA interface reference for the backend (OPC 40100), including pitfalls and details per method |
| [`doc/projektdoku/part10-programm-schnittstelle.md`](doc/projektdoku/part10-programm-schnittstelle.md) | Part 10 program and mDNS: the generic operator interface of the cell and discoverability on the network |
| [`doc/projektdoku/apriltag-lokalisierung.md`](doc/projektdoku/apriltag-lokalisierung.md) | Concept: what AprilTags mean here, how Layer 1 and Layer 2 use the same functionality |
| [`doc/projektdoku/apriltag-referenz.md`](doc/projektdoku/apriltag-referenz.md) | Function and CLI reference, file formats, payload |
| [`doc/projektdoku/vision-system.md`](doc/projektdoku/vision-system.md) | State of the vision system |
| [`doc/projektdoku/vision-system-integration.md`](doc/projektdoku/vision-system-integration.md) | Target plan for integrating the two OPC UA vision systems (OPC 40100) into webskillcomposition |
| [`doc/projektdoku/arbeitsplaene/`](doc/projektdoku/arbeitsplaene/README.md) | Work plans of all features (planning, decisions, status) |
| [`doc/projektdoku/arbeitsplaene/altlasten-zelle.md`](doc/projektdoku/arbeitsplaene/altlasten-zelle.md) | Clean-up audit of this repo: dead code, duplications, stopgap solutions, guidelines, test gaps |
| [`doc/projektdoku/arbeitsplaene/altlasten-wsc.md`](doc/projektdoku/arbeitsplaene/altlasten-wsc.md) | Clean-up audit of our share of webskillcomposition, plus the world-tag chain across both repos |
| [`doc/projektdoku/arbeitsplaene/altlasten.md`](doc/projektdoku/arbeitsplaene/altlasten.md) | What is still in there and why, and when it will be removed |
| [`concept/offene_punkte.md`](concept/offene_punkte.md) | Open concept questions of the localization |
