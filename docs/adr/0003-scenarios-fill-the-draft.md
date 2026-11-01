# A scenario is a saved draft, not an activation

## Status

Accepted. Supersedes ADR 0002.

Operators were composing a setup out of per-kind presets and activating it straight into the applied configuration. The preset dropdowns were not the selections in Studio, so it was unclear where a fair combination is saved and where it reaches the robot.

A scenario stores the prompt selection, the model settings, the recognition and pronunciation settings, and the interaction mappings directly. Prompt pieces are content ids, so a later load uses their latest wording. Model, recognition, pronunciation, and interaction values are the values at save time. Voice and audio stay outside a scenario. Loading a scenario fills the browser draft and selects it. Übernehmen is what reaches the robot, and a running conversation restarts only when that apply includes the language draft. There is no per-kind preset. Loading over unsaved edits discards them or saves the selected scenario first. It does not merge sections.

## Considered options

- Keep per-kind presets as the slots inside a setup.
- Make activation write the applied configuration.
- Merge changed sections into the scenario being loaded.
