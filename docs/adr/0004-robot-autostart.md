# The robot keeps one autostart assignment and a lease

The app has to leave a NAO able to speak and gesture from touch sensors after the
laptop is gone. The microphone bridge only streams audio, so a separate boot-started
Python 2.7 process stores one Standby assignment on the robot.

The app transfers and acknowledges that assignment through its existing NAOqi
session. A renewable lease gives the app exclusive trigger ownership and expires
on the robot's clock if the laptop disappears. Persisted assignment plus expiring
ownership lets Standby recover without depending on the laptop or a cloud service.
A failed assignment push keeps the previous assignment and the connection.

Current timing, assignment fields, and supported Standby actions are in
[behavior](../behavior.md#standby-and-armed). Robot ownership of Armed is refined
by [ADR 0012](0012-robot-armed-state.md). Program installation is a separate SSH
operation in [ADR 0005](0005-program-install-uses-ssh.md).
