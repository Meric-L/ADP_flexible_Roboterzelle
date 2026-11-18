# Project documentation

Entry point for the ADP robot cell: what the system does, how to install and use it, and where each detail is documented.

## Start here

| I want to … | Read |
| --- | --- |
| Understand Layer 1 and Layer 2 in five minutes | [layers.md](layers.md) |
| Install the vision Pi, the Part 10 program and webskillcomposition | [usage/installation.md](usage/installation.md) |
| Run a minimal example | [usage/quickstart.md](usage/quickstart.md) |
| Integrate a device or program so the backend discovers it | [usage/integration.md](usage/integration.md) |
| Know why something is built the way it is | [adr/README.md](adr/README.md) |
| Check whether someone already works on a topic | [work-plans/README.md](work-plans/README.md) |

## Components

How each part works, in brief, with links to the code reference.

| Component | Repo | README |
| --- | --- | --- |
| Vision server (Pi, OPC UA, OPC 40100) | this repo, `src/vision_server/` | [components/vision-server/README.md](components/vision-server/README.md) |
| Part 10 program (`VisionProgram`) | this repo, `src/ua_program/` | [components/part10/README.md](components/part10/README.md) |
| AprilTag localisation library | this repo, `src/tagloc/` | [components/tagloc/README.md](components/tagloc/README.md) |
| webskillcomposition backend | webskillcomposition, `backend/` | [components/webskill-backend/README.md](components/webskill-backend/README.md) |
| webskillcomposition frontend | webskillcomposition, `frontend/` | [components/webskill-frontend/README.md](components/webskill-frontend/README.md) |

## Folders

| Folder | Content |
| --- | --- |
| [components/](components/) | One README per component: how it works, where to read more |
| [usage/](usage/) | Installation, quickstart, integration |
| [interfaces/](interfaces/) | OPC UA interfaces: [vision server in brief](interfaces/vision-server-interface-short.md), [full reference](interfaces/vision-server-interface.md), [Part 10 program](interfaces/part10-program-interface.md) |
| [topics/](topics/) | Topic documentation: vision system, integration, AprilTag localisation, Layer 2 rules, presentation |
| [code/](code/README.md) | Per-file and per-function code reference |
| [adr/](adr/README.md) | Architecture decision records |
| [analyses/](analyses/README.md) | Analyses of measurements and experiments |
| [work-plans/](work-plans/README.md) | Running and finished work plans with their interfaces |
| [images/](images/) | Images used by the documents |
