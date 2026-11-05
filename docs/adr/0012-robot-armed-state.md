# The robot owns the standby switch between connections

The arm chord must work with or without the NAO Fair app, so the switch cannot live only in the app. The robot stores and announces the switch; on connection the app adopts it before sending the rest of the assignment, and while connected it follows later arm chords. Apply can still change the switch. A change identifier prevents an assignment prepared before a chord from silently undoing it; a rejected assignment is retried after the app has read the robot's state.
