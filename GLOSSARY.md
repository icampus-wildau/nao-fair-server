# NAO Fair

The staff console for running one NAO robot at a university fair, and the words for the configuration it edits.

## Language

**Operator**:
A staff member who configures and runs the robot. Visitors do not use the console.
_Avoid_: User, admin, visitor

**Robot**:
The one NAO the console is connected to.
_Avoid_: Fleet, device

**Connection**:
Whether a robot is attached right now. While disconnected the console still edits prompts and settings, the robot's name is unknown, and a failure can remain until the next connect attempt.
_Avoid_: Simulator, demo mode, session

**Robot address**:
The IP address the console is aimed at. The running server decides which address it last used, and after a server restart the browser's remembered address is used.
_Avoid_: Robot name

**Operator secret**:
A cloud API secret the operator submitted through the console. It stays on the server and is never sent back to the browser.
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
Making a draft become the applied configuration. The console labels this Übernehmen. Applying does not rewrite a scenario.
_Avoid_: Save, activate

**Save scenario**:
Storing the current draft's prompt selection, model settings, recognition and pronunciation settings, and interaction mappings as a scenario. The applied configuration stays as it was. Updating the selected scenario is labeled Aktualisieren.
_Avoid_: Apply, übernehmen, laden

**Load scenario**:
Replacing the draft with a saved scenario and selecting that scenario. The applied configuration stays as it was.
_Avoid_: Apply, übernehmen, save scenario

**Reset**:
Throwing away a draft and showing the applied configuration again. The console labels this Zurücksetzen.
_Avoid_: Factory reset, disconnect

**Trigger**:
One physical sensor that can be bound to an action. The seven triggers are the front, middle, and rear of the head, the back of each hand, and the two foot bumpers.
_Avoid_: Chest button, event

**Action**:
The single behavior bound to a trigger.
_Avoid_: Sequence, macro

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
_Avoid_: Audio pipeline

**Standby assignment**:
The applied interaction mappings, speaker volume, speech speed, TTS language, and TTS replacements kept with the robot.
_Avoid_: Fallback config, button texts, offline preset

**Standby**:
The robot acting on its standby assignment when a trigger is pressed. It does this while armed and while no connection is holding the triggers.
_Avoid_: Fallback service, offline mode

**Armed**:
The operator's persistent choice that Standby respond to triggers. It is part of the interaction mappings.
_Avoid_: Service enabled, activated

**Suppressed**:
A connection holding the triggers, so Standby does not respond.
_Avoid_: Disconnected, paused
