# Open points – localization concept

## Conceptual gaps

### 1. Coordinate system chain
The world tags (ArUco board on floor/wall) exist as a hardware component, but do not appear in the flow diagram.
- **Open:** When and how is the transformation camera CS → world CS established?
- **Open:** Is the world tag referenced during the calibration phase or at every Layer 1 scan?

### 2. Handover Layer 1 → Layer 2
Layer 1 provides a rough module position — but the robot needs a concrete TCP target pose.
- **Open:** In what format is the rough position handed over (pixel coordinate, 3D pose in the world coordinate system, direct robot target pose)?
- **Open:** Who performs the coordinate transformation world CS → robot base CS?

### 3. Repositioning strategy Layer 2
Layer 2 has a repositioning path in case no AprilTag is found — the strategy itself is undefined.
- **Open:** Maximum number of attempts before aborting?
- **Open:** Search pattern (spiral search, fixed offsets, random)?

### 4. Role of the robot table tag
The robot table has its own AprilTag, which does not explicitly appear in the flow diagram.
- **Open:** When is this tag detected?
- **Open:** Is it used for calibration, as a Layer 2 reference point, or for validation?

### 5. Error handling on Layer 1 failure
If the ceiling camera detects no module, there is a `To Fix` path — the further behavior is unclear.
- **Open:** Can Layer 2 be started without a Layer 1 result (blind search)?
- **Open:** Is the module skipped, or does the error block the entire scan?

### 6. Termination criterion / completeness
The process ends with "Map complete, all positions detected".
- **Open:** Who/what defines the expected target number of modules?
- **Open:** Is the number of modules configured statically or determined dynamically from Layer 1?

---

## Requirement: update loop

The entire chain (calibration → Layer 1 → Layer 2) should be repeatable.

**Trigger:**
- Time-based: automatically every 60 minutes
- Manual: on explicit user input (e.g. console command or GUI button)

**Still to be clarified:**
- Behavior when a scan is running and a new trigger fires:
  - [ ] Queue the new trigger
  - [ ] Abort the running scan and restart it
  - [ ] Ignore the trigger until the scan is complete
- Is the calibration phase repeated in every loop pass, or only at the first start?
- Should position deltas between two passes be logged (change detection)?
