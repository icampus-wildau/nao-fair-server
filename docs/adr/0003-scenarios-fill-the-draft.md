# A scenario is a saved draft, not an activation

## Status

Accepted. Supersedes [ADR 0002](0002-setups-read-latest-presets.md).

Operators were composing a setup out of per-kind presets and activating it straight into the applied configuration. The preset dropdowns were not the selections in Studio, so it was unclear where a fair combination is saved and where it reaches the robot.

A scenario stores prompt selections, model settings, recognition and pronunciation
settings, and interaction mappings directly. Prompt pieces are content ids, so a
later load uses their latest wording; the other values are captured at save time.
Robot/audio settings stay outside a scenario. Loading fills the browser draft and
selects it; saving stores that draft. Explicit Übernehmen changes the applied
configuration. This separates a reusable combination from the decision to run it.
Current Apply and interruption rules are in [behavior](../behavior.md#configuration-and-scenarios).

## Considered options

- Keep per-kind presets as the slots inside a setup.
- Make activation write the applied configuration.
- Merge changed sections into the scenario being loaded.
