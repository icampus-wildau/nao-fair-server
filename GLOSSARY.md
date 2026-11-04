# NAO Fair

The NAO Fair app for running one NAO robot at a university fair, and the words for the configuration it edits.

## Language

**NAO Fair app**:
The staff application that configures and runs the one robot at a fair.
_Avoid_: Konsole, console, Messekonsole

**Operator**:
A staff member who configures and runs the robot. Visitors do not use the NAO Fair app.
_Avoid_: User, admin, visitor

**Robot**:
The one NAO the NAO Fair app is connected to.
_Avoid_: Fleet, device

**Connection**:
Whether a robot is attached right now. While disconnected the NAO Fair app still edits prompts and settings, the robot's name is unknown, and a failure can remain until the next connect attempt.
_Avoid_: Simulator, demo mode, session

**Robot address**:
The IP address the NAO Fair app is aimed at.
_Avoid_: Robot name, known robot

**Known robot**:
A robot address the server has connected to successfully, remembered with the name that robot reported. The same address is one known robot.
_Avoid_: History, favorite, remembered address

**Operator secret**:
A cloud API secret the operator submitted through the NAO Fair app. It stays on the server and is never sent back to the browser.
_Avoid_: Key in the page, token

**Environment secret**:
A cloud API secret supplied to the server by its environment. It applies only when no operator secret is stored for that slot.
_Avoid_: Config key

**Draft**:
Edits on a screen that the running system is not using yet.
_Avoid_: Unsaved file

**Applied configuration**:
The configuration the running backend is using now.
_Avoid_: Selected scenario

**Scenario**:
A named, described combination of the prompt selection, the model settings, the recognition and pronunciation settings, and the interaction mappings.
_Avoid_: Setup, fair-preset, Preset, profile, snapshot

**Selected scenario**:
The scenario last loaded into the draft. It remains selected when the draft no longer matches it.
_Avoid_: Active setup, applied configuration

**Apply**:
Making a draft become the applied configuration. The NAO Fair app labels this Übernehmen. Applying does not rewrite a scenario.
_Avoid_: Save, activate

**Save scenario**:
Storing the current draft's prompt selection, model settings, recognition and pronunciation settings, and interaction mappings as a scenario. The applied configuration stays as it was. Updating the selected scenario is labeled Aktualisieren.
_Avoid_: Apply, übernehmen, laden

**Load scenario**:
Replacing the draft with a saved scenario and selecting that scenario. The applied configuration stays as it was.
_Avoid_: Apply, übernehmen, save scenario

**Reset**:
Throwing away a draft and showing the applied configuration again. The NAO Fair app labels this Zurücksetzen.
_Avoid_: Factory reset, disconnect

**Trigger**:
One physical sensor. Each of its gestures can be bound to an action. The seven triggers are the front, middle, and rear of the head, the back of each hand, and the two foot bumpers.
_Avoid_: Chest button, event

**Gesture**:
How a trigger is pressed. The gestures are tap, double tap, and long press, and only one of them happens for a press. Each gesture has its own action and is enabled on its own.
_Avoid_: Click, event

**Tap**:
A release of a trigger that was not held into a long press and was not followed by another press soon enough to be a double tap. The interactions screen labels it Tippen.
_Avoid_: Single press, click, short press

**Double tap**:
Two presses of the same trigger close enough together to be one gesture. The interactions screen labels it Doppeltipp.
_Avoid_: Double click

**Long press**:
A hold of a trigger long enough to be its own gesture. Letting go does not also count as a tap. The interactions screen labels it Gedrückt halten.
_Avoid_: Hold

**Action**:
The single behavior bound to one gesture of a trigger.
_Avoid_: Sequence, macro

**Dialog**:
An ordered list of lines in the interaction mappings. A position, the line last spoken or none, exists only for the applied active dialog. The connected app and Standby share that position.
_Avoid_: Conversation context, script, sequence

**Active dialog**:
The one dialog that next line, repeat line, and previous line use. It is chosen in the interaction mappings, and those actions do not name a dialog of their own. There is at most one. The interactions screen labels it Aktiver Dialog.
_Avoid_: Selected scenario, assigned dialog

**Line**:
One entry in a dialog. It is either spoken by the robot, with one delivery, or it is a planning line.
_Avoid_: Snippet, phrase, prompt

**Planning line**:
A line that records what a person says, for the operator's planning. The robot does not speak it, it has no delivery, it is never the line last spoken, and the Dialoge section labels it Mensch.
_Avoid_: Visitor, Besucher, speaker

**Delivery**:
How the robot speaks a line that is not a planning line: the words alone, the words together with one accompanying motion, or animated speech. The interactions screen labels it Wiedergabe, with Nur sprechen, Mit Bewegung, and Animiertes Sprechen.
_Avoid_: Animation tag, posture

**Accompanying motion**:
Wave left, wave right, nod, or shake, played with a line. Sit and stand are not accompanying motions.
_Avoid_: Posture, animated speech

**Animated speech**:
The robot speaking a line and moving its body on its own while it talks. The line text stays words.
_Avoid_: Animation tag, accompanying motion

**Wrap**:
Whether a dialog treats its lines as a loop. Off, a move past either end says nothing and stays. A new dialog starts with it on. The interactions screen labels it Am Ende von vorn.
_Avoid_: Repeat, cycle

**Idle reset**:
How long the applied active dialog keeps its position after a press that spoke a line. A next line or previous line that says nothing does not postpone it, and neither does a repeat line with nothing to say. A new dialog leaves it off. Switching it on starts at five minutes. The interactions screen labels the switch Zurücksetzen des Dialogs nach Timeout and the duration Timeout, in Minuten. A Standby restart clears the position. Apply clears it only when the active dialog changes or none is active.
_Avoid_: Context reset, listening timeout

**Conversation state**:
Idle, Listening, Thinking, or Speaking, as the backend reports it.
_Avoid_: Screen mode

**Conversation context**:
The dialogue the model is using for the current conversation.
_Avoid_: Transcript, history

**Transcript**:
The finished visitor and robot lines the server still holds, which a browser receives when it opens or reconnects. A disconnect, a different robot, emptying the conversation context, or a server restart forgets them, and Apply keeps them.
_Avoid_: Conversation context, history, log, interim

**Microphone bridge**:
The process on the robot that streams its front microphone to the server.
_Avoid_: Audio pipeline, service

**Autostart process**:
The process on the robot that performs Standby. The interactions screen does not use this name.
_Avoid_: Service, NAOqi service, Autostart

**Standby assignment**:
The applied interaction mappings, speaker volume, speech speed, TTS language, and TTS replacements kept with the robot.
_Avoid_: Fallback config, button texts, offline preset

**Standby**:
The robot acting on its standby assignment when a trigger is pressed. It does this while armed and while the NAO Fair app is not holding the triggers. The interactions screen calls this Autostart.
_Avoid_: Fallback service, offline mode

**Armed**:
The operator's persistent choice that Standby respond to triggers. It is part of the interaction mappings, the interactions screen labels it Aktiviert im Autostart, and it is on unless an operator turns it off.
_Avoid_: Service enabled, activated

**Suppressed**:
A connection from the NAO Fair app holding the triggers, so Standby does not respond.
_Avoid_: Disconnected, paused
