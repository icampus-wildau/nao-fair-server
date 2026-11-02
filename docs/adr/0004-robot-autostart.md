# The robot keeps one autostart assignment and a lease

The NAO Fair app has to leave a NAO able to speak and gesture from its touch sensors when the laptop is gone. The microphone bridge cannot do that: it only streams audio while a client is connected.

The robot runs a second Python 2.7 process, started at boot the same way as the bridge. The app writes the applied interaction mappings, the autostart switch, the volume, the speech speed, the TTS language, and the TTS replacements through the NAOqi session it already holds. The process stores that document in its own file and acknowledges the write. A missing acknowledgement leaves the previous document in place and the connection still succeeds. There is no SSH step and no extra secret.

The switch is Aktiviert im Autostart. It is on unless an operator turns it off, including when an older configuration has no switch yet. While the app is connected it renews a lease about once a second. Disconnect or a detected NAOqi failure releases the lease. If renewals simply stop, the robot waits 20 seconds, using its own clock, then takes the triggers back when the switch is on. A microphone redial keeps the lease, because the app still owns the triggers.

Gespräch starten, Gespräch umschalten, and Gespräch beenden und Kontext löschen do nothing in autostart. Gespräch beenden stops speech. Installing the Autostart process from the app is a separate SSH step, described in ADR 0005.
