# Operator secrets live outside presets

## Status

Accepted

## Context

The console needs an OpenAI secret and a Gladia secret. A fair setup is copied, activated, and may later be exported. The bridge token and the robot password are not part of this.

## Decision

Operator secrets are stored in their own file, not in presets, setups, the applied configuration, or the picture. The API reports only `missing`, `environment`, or `console`. A value written from the console overrides the environment secret for that slot. Deleting the operator secret makes the environment secret apply again. A blank write does not clear a stored value. The new value is read the next time that client is opened.

## Consequences

Restarting the process keeps a console secret. Copying a preset or a setup cannot leak it. The environment can still supply a secret for an unattended start.
