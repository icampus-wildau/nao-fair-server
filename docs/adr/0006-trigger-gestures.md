# A trigger gesture is classified before its action runs

A trigger used to fire once, on the press, and that press had one action. Dialogs need a repeat and a step back, and operators also want a tap, a double tap, and a long press to do different things.

The action runs only once the gesture is known, both in the connected app and in
Standby. Long press fires while held; release is not also a tap. Tap waits for a
possible second press only when double tap has an enabled action. Current timing
and dispatch rules are in [behavior](../behavior.md#gestures-and-actions).

The alternative was to run the first press immediately and, if the trigger is pressed again within about two seconds, stop that action and run a different one. That can correct a spoken line, and it cannot undo starting a conversation or playing a wave. Waiting two seconds before every tap would also make the robot hesitate. Repeat line on tap and previous line on double tap is an assignment, not a special kind of action.
