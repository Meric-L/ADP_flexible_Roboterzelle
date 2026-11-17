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
* `/doc` - The LaTeX source files of the written thesis.
* `/doc/projektdoku` - The technical project documentation as Markdown (see below).
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
* `/data` - Calibration files and recordings. Machine-specific, **not** versioned.
* `/tests` - `PYTHONPATH=src python3 -m unittest discover -s tests -t .`

## Technical documentation

| Document | Purpose |
| --- | --- |
| [`doc/projektdoku/apriltag-lokalisierung.md`](doc/projektdoku/apriltag-lokalisierung.md) | Concept: what AprilTags mean here, how Layer 1 and Layer 2 use the same functionality |
| [`doc/projektdoku/apriltag-referenz.md`](doc/projektdoku/apriltag-referenz.md) | Function and CLI reference, file formats, payload |
| [`doc/projektdoku/apriltag-e2e-test.md`](doc/projektdoku/apriltag-e2e-test.md) | **How to test everything end to end** — from the PC to the Raspberry Pi camera |
| [`doc/projektdoku/vision-server-interface.md`](doc/projektdoku/vision-server-interface.md) | OPC UA interface for the backend (OPC 40100) |
| [`doc/projektdoku/part10-programm-schnittstelle.md`](doc/projektdoku/part10-programm-schnittstelle.md) | **Part 10 program and mDNS** — the generic operator interface of the cell and discoverability on the network |
| [`doc/projektdoku/vision-system.md`](doc/projektdoku/vision-system.md) | Current state of the vision system |
| [`doc/projektdoku/code-dokumentation.md`](doc/projektdoku/code-dokumentation.md) | **Code documentation**: overview of the code, OPC UA connections and entry points; details per area under `doc/projektdoku/code/` |
| [`doc/projektdoku/altlasten-zelle.md`](doc/projektdoku/altlasten-zelle.md) | Clean-up audit of this repo: dead code, duplications, stopgap solutions, guidelines, test gaps |
| [`doc/projektdoku/altlasten-wsc.md`](doc/projektdoku/altlasten-wsc.md) | Clean-up audit of our share of webskillcomposition, plus the world-tag chain across both repos |
| [`doc/projektdoku/altlasten.md`](doc/projektdoku/altlasten.md) | What is still in there and why, and when it will be removed |
| [`concept/offene_punkte.md`](concept/offene_punkte.md) | Open concept questions of the localization |
