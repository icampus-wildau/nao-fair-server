# A setup reads the latest preset content

## Status

Superseded by [ADR 0003](0003-scenarios-fill-the-draft.md). Historical terminology:
presets and setup activation are no longer the current workflow.

## Context

An operator saves a preset, then later activates a setup that names it. The running robot may already be using an older applied configuration. Pinning a preset revision would freeze a tested fair, but the product decision is to follow the latest saved content.

## Decision at the time

Setup activation read the latest saved content of its referenced presets instead
of pinning revisions. Saving a preset left the applied configuration unchanged;
the next activation picked up the edit. This favored maintaining shared content
over freezing a previously tested combination. ADR 0003 replaced that composition
and activation model with saved scenario drafts and explicit Apply.
