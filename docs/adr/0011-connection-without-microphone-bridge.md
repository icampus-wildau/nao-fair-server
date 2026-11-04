# A connection does not include the microphone bridge

The fair still needs gesture tests, speech, and motions when the microphone bridge is not installed. Failing the whole connect made that robot look unreachable.

A connection is the robot attached over NAOqi. The conversation stays closed until the microphone bridge socket is up, and a conversation path that never starts, ends, or throws leaves the robot connected. Only a failed NAOqi health check ends the connection. The alternative was to keep the bridge probe as a connect gate, which hides a live robot behind a missing bridge.
