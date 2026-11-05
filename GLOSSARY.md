# NAO Fair

Language shared by the operator app, server, frontend, and robot Standby.
Current behavior is documented in [docs/behavior.md](docs/behavior.md); decision
reasons are in the [ADR index](docs/adr/README.md).

## App and connection

**NAO Fair app**:
The staff application that configures and runs one NAO at a fair.
_Avoid_: Konsole, console, Messekonsole

**Operator**:
A staff member who configures and runs the robot. Visitors do not use the NAO Fair app.
_Avoid_: User, admin, visitor

**Robot**:
The NAO currently attached to the NAO Fair app.
_Avoid_: Fleet, device

**Connection**:
The attachment to a robot over NAOqi, independent of microphone availability and conversation state.
_Avoid_: Simulator, demo mode, session

**Robot address**:
The network address used to connect to a robot.
_Avoid_: Robot name, known robot

**Known robot**:
A successfully connected robot address remembered together with the name that robot reported.
_Avoid_: History, favorite, remembered address

## Secrets

**Operator secret**:
A cloud API secret submitted by an operator and kept on the server.
_Avoid_: Key in the page, token

**Environment secret**:
A cloud API secret supplied through the server environment, used when no operator secret is stored for that slot.
_Avoid_: Config key

## Configuration

**Draft**:
Editable configuration that has not yet become the applied configuration.
_Avoid_: Unsaved file

**Applied configuration**:
The configuration currently used by the backend.
_Avoid_: Selected scenario

**Scenario**:
A named combination of prompt selections, model and recognition settings, pronunciation rules, and interaction mappings.
_Avoid_: Setup, fair-preset, Preset, profile, snapshot

**Selected scenario**:
The scenario last loaded into the draft, even if the draft has since changed.
_Avoid_: Active setup, applied configuration

**Apply**:
Making a draft become the applied configuration; labeled Übernehmen in the app.
_Avoid_: Save, activate

**Save scenario**:
Storing the draft combination as a scenario; labeled Aktualisieren when updating the selected scenario.
_Avoid_: Apply, übernehmen, laden

**Load scenario**:
Replacing the draft with a stored scenario and selecting it.
_Avoid_: Apply, übernehmen, save scenario

**Reset**:
Discarding draft changes and returning to the applied configuration; labeled Zurücksetzen.
_Avoid_: Factory reset, disconnect

## Interactions

**Trigger**:
One of the seven assignable physical sensors: three head sensors, two hand sensors, and two foot bumpers.
_Avoid_: Chest button, event

**Gesture**:
A tap, double tap, or long press on a trigger, with its own enabled state and action.
_Avoid_: Click, event

**Tap**:
A short press and release classified as one gesture; labeled Tippen.
_Avoid_: Single press, click, short press

**Double tap**:
Two nearby presses of the same trigger classified as one gesture; labeled Doppeltipp.
_Avoid_: Double click

**Long press**:
A sustained press classified as one gesture; labeled Gedrückt halten.
_Avoid_: Hold

**Action**:
The single behavior assigned to one gesture of a trigger.
_Avoid_: Sequence, macro

**Dialog**:
An ordered collection of robot lines and planning lines in the interaction mappings.
_Avoid_: Conversation context, script, sequence

**Active dialog**:
The one applied dialog used by next line, previous line, repeat line, and dialog reset.
_Avoid_: Selected scenario, assigned dialog

**Dialog reset**:
An action that clears the active dialog position and interrupts speech while preserving the conversation and its context.
_Avoid_: Reset, idle reset, Zurücksetzen

**Reset announcement**:
Optional plain words spoken after a dialog reset, separate from the dialog lines.
_Avoid_: Line, delivery

**Fixed phrase**:
The words assigned to a Text sprechen gesture, spoken with one delivery.
_Avoid_: Line, reset announcement

**Next-line yield**:
The per-gesture choice to ignore next line while a line of the active dialog is speaking; labeled Laufende Zeile nicht unterbrechen.
_Avoid_: Queue, interrupt lock

**Line**:
One entry in a dialog: a robot utterance with a delivery, or a planning line.
_Avoid_: Snippet, phrase, prompt

**Planning line**:
An unspoken entry recording the human side of a dialog for planning; labeled Mensch.
_Avoid_: Visitor, Besucher, speaker

**Delivery**:
How a robot line or fixed phrase is spoken: words alone, with an accompanying motion, or as animated speech; labeled Wiedergabe.
_Avoid_: Animation tag, posture

**Accompanying motion**:
A wave left, wave right, nod, or shake performed with speech.
_Avoid_: Posture, animated speech

**Animated speech**:
Speech with body movements chosen by the robot while it talks.
_Avoid_: Animation tag, accompanying motion

**Wrap**:
The choice to treat a dialog as a loop; labeled Am Ende von vorn.
_Avoid_: Repeat, cycle

**Idle reset**:
The inactivity period after which the active dialog forgets its playback position.
_Avoid_: Context reset, listening timeout

## Conversation and audio

**Conversation**:
The visitor’s exchange with the language pipeline, distinct from scripted dialog playback and fixed phrases.
_Avoid_: Pipeline, audio pipeline

**Conversation state**:
One of the four backend-reported phases: Idle, Listening, Thinking, or Speaking.
_Avoid_: Screen mode

**Conversation context**:
The exchange retained by the model for the current conversation.
_Avoid_: Transcript, history

**Transcript**:
Finished visitor and robot utterances retained in server memory for browser display.
_Avoid_: Conversation context, history, log, interim

**Microphone bridge**:
The robot process that streams its front microphone to the laptop.
_Avoid_: Audio pipeline, pipeline, service

## Standby

**Autostart process**:
The robot process that performs Standby.
_Avoid_: Service, NAOqi service, Autostart

**Standby assignment**:
The interaction mappings, speaker volume, speech speed, TTS language, and pronunciation rules kept on the robot for Standby.
_Avoid_: Fallback config, button texts, offline preset

**Standby**:
The robot executing its stored assignment when the app does not hold the triggers; labeled Autostart in the app.
_Avoid_: Fallback service, offline mode

**Armed**:
The persistent switch allowing Standby to respond to assigned gestures; labeled Aktiviert im Autostart.
_Avoid_: Service enabled, activated

**Arm chord**:
Holding both foot bumpers together to toggle Armed, independently of assignable gestures.
_Avoid_: Arm toggle, gesture, configurable action

**Arm announcement**:
The words Autostart ein or Autostart aus spoken after an arm chord changes Armed.
_Avoid_: Fixed phrase, line, reset announcement

**Suppressed**:
Standby yielding the triggers to the connected app while its lease is held.
_Avoid_: Disconnected, paused
