# Operator secrets live outside configuration

## Status

Accepted

The app needs OpenAI and Gladia secrets, while configuration can be copied and
reused. Operator secrets therefore live in a separate server file and the API
returns only their source status. Operator values override environment values;
deleting one restores the environment fallback. This keeps configuration reuse
from carrying credentials while allowing unattended environment-based startup.

The bridge token and robot SSH password are separate concerns. Current write
and client-refresh semantics are in [behavior](../behavior.md#persistence-prompts-and-secrets).
