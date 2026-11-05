# Server decisions

Read the decision relevant to the change. Current rules are in
[behavior](../behavior.md), definitions in [the glossary](../../GLOSSARY.md).
The accepted decisions below remain in effect; ADR 0002 is historical.

| ADR | Decision |
|---|---|
| [0001](0001-operator-secrets.md) | Keep operator cloud secrets outside configuration |
| [0002](0002-setups-read-latest-presets.md) | Latest preset content on setup activation — superseded by 0003 |
| [0003](0003-scenarios-fill-the-draft.md) | Scenarios save/fill drafts; Apply changes runtime |
| [0004](0004-robot-autostart.md) | Store one robot assignment and lease the triggers |
| [0005](0005-program-install-uses-ssh.md) | Install robot programs over SSH |
| [0006](0006-trigger-gestures.md) | Classify gestures before running actions |
| [0007](0007-one-active-dialog.md) | All dialog actions use one active dialog |
| [0008](0008-active-dialog-position.md) | Only the active dialog has a shared position |
| [0009](0009-gesture-enabled.md) | Enable each gesture independently |
| [0010](0010-known-robots-on-the-server.md) | Store known robots on the server |
| [0011](0011-dialog-reset-and-next-line-yield.md) | Next-line yield is per gesture; reset preserves conversation |
| [0012](0012-robot-armed-state.md) | The robot owns Armed between connections |
| [0013](0013-connection-without-microphone-bridge.md) | Connect independently of microphone availability |

ADR 0013 previously shared number 0011 with dialog reset. It was renumbered
during consolidation; existing unique ids were retained.

Packaging/deployment decisions live in the wrapper repository:
[nao-fair ADRs](https://github.com/icampus-wildau/nao-fair/tree/main/docs/adr).
Add a new ADR for an architectural trade-off that future readers need to
understand; extend current behavior documentation for routine rules and defaults.
