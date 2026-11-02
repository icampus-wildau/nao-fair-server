# Each gesture is enabled on its own

A trigger used to have one enabled switch for its one action. A tap, a double tap, and a long press can now each have an action, and an operator may want one of them off without losing the others.

Each gesture stores its own action and its own enabled flag. Turning a gesture off keeps the action and stops it from running, in the connected app and in Standby. An empty gesture does nothing whether it is enabled or not. The alternative was one switch for the whole trigger. That cannot pause a double tap while leaving the tap in place.
