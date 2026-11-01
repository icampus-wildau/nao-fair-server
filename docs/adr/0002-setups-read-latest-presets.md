# A setup reads the latest preset content

## Status

Superseded by ADR 0003.

## Context

An operator saves a preset, then later activates a setup that names it. The running robot may already be using an older applied configuration. Pinning a preset revision would freeze a tested fair, but the product decision is to follow the latest saved content.

## Decision

Presets are not revision-pinned. Activating a setup reads each preset as it is at that moment and writes that into the applied configuration. Saving a preset does not change the applied configuration and does not retune a running robot. Draft settings reach the live robot when the operator applies one screen or activates a setup. Volume and speech speed use the direct audio controls described below. Both do that even when no robot is connected. When a conversation pipeline is running, robot and LLM Apply and setup activation rebuild it, stop the conversation, and discard model context. Interaction Apply changes the runtime sensor mappings without rebuilding the pipeline or ending the conversation. The transcript stays.

## Consequences

Editing a preset changes the next activation, not the robot that is already running. Apply can make the applied configuration differ from the active setup. The active setup id remains the setup that was last activated.

## Direct audio controls

Volume and speech speed are committed when the slider is released, including while disconnected. These partial updates preserve the conversation and model context and retune connected audio without rebuilding the pipeline. Other draft settings still require Apply.
