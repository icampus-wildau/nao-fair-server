# A trigger gesture is classified before its action runs

A trigger used to fire once, on the press, and that press had one action. Dialogs need a repeat and a step back, and operators also want a tap, a double tap, and a long press to do different things.

The action runs only once the gesture is known. A long press is a hold of 600ms and fires while the sensor is still held; the release is not also a tap. A double tap is a second press within 400ms of the release. A tap fires when those 400ms pass with no second press. Contact bounce shorter than about 50ms is not a second press. The same classification applies while the NAO Fair app holds the triggers and in Standby.

The alternative was to run the first press immediately and, if the trigger is pressed again within about two seconds, stop that action and run a different one. That can correct a spoken line, and it cannot undo starting a conversation or playing a wave. Waiting two seconds before every tap would also make the robot hesitate. Repeat line on tap and previous line on double tap is an assignment, not a special kind of action.
