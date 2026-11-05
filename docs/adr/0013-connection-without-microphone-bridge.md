# A connection does not include the microphone bridge

## Status

Accepted. Renumbered from the duplicate `0011-connection-without-microphone-bridge`
to give each decision a unique id; the decision is unchanged.

The fair still needs gesture tests, speech, and motions when the microphone bridge is not installed. Failing the whole connect made that robot look unreachable.

A connection is the robot attached over NAOqi. The conversation waits for the
microphone stream; a failed conversation or bridge redial leaves the robot
connected. NAOqi health is checked independently. Keeping the bridge probe as a
connect gate would hide a live robot behind a missing bridge. Current lifecycle
rules are in [behavior](../behavior.md#connection-conversation-and-transcript).
