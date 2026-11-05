# Next line yields per gesture, and dialog reset quiets the robot without ending the conversation

A later line can interrupt one the visitor is still hearing. Next-line yield
therefore belongs to each gesture and defaults to on. A dialog-wide guard would
force every next-line gesture to wait; per-gesture configuration lets an operator
keep tap yielding and configure a long press to interrupt instead.

Dialog reset clears the shared position and takes the voice, including pending
conversation replies, while preserving the conversation and context. Ending the
conversation instead would throw away the exchange the model is using. Current
speech, motion, announcement, and yield rules are in
[behavior](../behavior.md#dialog-playback).
