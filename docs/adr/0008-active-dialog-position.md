# Only the applied active dialog has a playback position

Each dialog could have kept its own bookmark, and Apply could have cleared that bookmark every time. Line edits only reach the robot through Apply, so a blanket clear would throw away the rule that the position follows the line.

Only the applied active dialog has a position, identified by the last robot line
that actually started speaking. Apply reconciles that position by stable line id
instead of clearing it for every edit. Inactive dialogs are a library, not a set
of independent bookmarks.

The app and Standby share the position on the robot so a rehearsal continues
after the app lets go. Persisting separate positions in the two runtimes would
make handoff repeat or skip lines. Detailed edit, restart, and idle-reset rules
are in [behavior](../behavior.md#dialog-playback).
