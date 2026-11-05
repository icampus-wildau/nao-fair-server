# Current NAO Fair behavior

Reference for changes to configuration, runtime, and Standby. Definitions belong
in [the glossary](../GLOSSARY.md); reasons belong in [ADRs](adr/README.md).
Payload fields and validation ranges are defined by `nao_fair/models.py` and
`catalog.py`; HTTP and WebSocket routes are in `app.py`.

## Configuration and scenarios

- The browser holds drafts. **Übernehmen** applies them; **Zurücksetzen** returns
  to the applied values. Both editing and Apply work without a connected robot.
- A scenario stores the language draft and interaction mappings, including
  dialogs, the active dialog, and Armed. Robot/audio settings stay outside it.
  Prompt selections refer to content ids: loading uses the latest saved wording.
  Other scenario values are captured at save time.
- Loading a scenario replaces the Studio drafts and selects it. Saving or
  updating a scenario stores those drafts. Neither operation applies them.
  The selected scenario can differ from both the draft and the applied state.
- Saving content changes the library. Applying the language draft captures the
  selected wording; later library edits do not silently retune the pipeline.
- The prompt order is personality, system prompt, selected knowledge in order,
  then local context. HTML comments are stripped and `{robot_name}` is filled
  at runtime. A separate injector adds the clock. TTS replacements and STT
  vocabulary are processing inputs, not prompt sections.

| Operation | Runtime effect |
|---|---|
| Apply `robot` or `llm` | Rebuild the robot pipeline, return to Idle, discard model context, keep transcript; stop a browser microphone test |
| Apply `interactions` | Update mappings and reconcile dialog position; keep the conversation and context |
| Direct volume/speed slider | Persist on release and retune audio, keeping the conversation and context |
| Load/save scenario or save library text | No change to applied runtime configuration |

Apply checks `expected_revision`; a stale revision returns HTTP 409. Events update
the applied state in all windows. A dirty draft in another window is retained
with a notice; a clean draft follows the new state. Studio applies language and
interactions through their separate API calls, so a combined Studio Apply is not
one atomic request.

Old preset/setup tables and `active_setup_id` remain for compatibility. Existing
setups are migrated to scenarios by `store.py`/`scenarios.py`; current CRUD is
`/api/scenarios`. There is no setup-activation route.

Sources: `runtime.py`, `compose.py`, `scenarios.py`; tests in
`test_mvp_validation.py`, `test_prompt_snapshot.py`, `test_operator_ux.py`.

## Connection, conversation, and transcript

- A successful NAOqi connection is enough for speech, motion, and gesture tests.
  The microphone bridge is needed for a robot conversation, not for attaching
  the robot. Bridge redial or conversation failure keeps the NAOqi connection;
  a failed NAOqi health check disconnects it.
- Disconnect stops speech and conversation, cancels a running gesture, clears
  model context and transcript, closes the laptop's bridge socket, and releases
  Standby. It preserves applied configuration and the current posture. The robot
  bridge process stays up. Switching robot addresses first disconnects.
- An unfinished connect can be aborted. A late success must not attach that
  robot. A connection failure retains the attempted address and failure but no
  robot name; a clean disconnect has no failure.
- Known robots are server data: up to 15 successful addresses with reported
  names, newest first. Choosing an address does not connect; removing a known
  robot does not disconnect. A connection shows a robot name only while attached.
- The four conversation states come from the backend. A disconnected app shows
  Idle. Opening/reconnecting a browser receives a `picture` with connection,
  applied configuration, conversation state, known robots, and finished
  transcript lines. Press flashes, drafts, and secret values are not replayed.
- Transcript lives in server memory. Disconnect, a different robot, clearing
  model context (including its idle timeout), or restarting the server clears
  it. Apply keeps it; a browser reload receives the retained lines. Stopping a
  conversation without clearing context keeps the transcript until a clear.
- Browser microphone tests use the applied cloud pipeline with browser audio and
  speech. They start only on operator action, permit one test at a time, and
  require no robot connection. The deterministic test driver is a separate
  mechanism used only by the test server.

Sources: `runtime.py`, `robot/live.py`, `browser_audio.py`; tests in
`test_pending_connection.py`, `test_disconnect.py`, `test_audio_and_probe.py`,
`test_pipeline_lifecycle.py`, `test_event_stream.py`, `test_browser_pipeline.py`.

## Gestures and actions

- Each of the seven triggers has independent tap, double-tap, and long-press
  bindings, each with its own action and enabled flag. Disabling retains its
  action. Empty bindings do nothing. The chest button is not assignable.
- Classify before dispatch. Long press fires at 600 ms (or on release if a tick
  has not yet observed it). When double tap has an enabled action, tap waits
  400 ms after release and double tap fires on the second press. With no enabled
  double-tap action, tap fires on release. Bounce below 50 ms is ignored.
- The same classification is used by the app and Standby. The arm chord consumes
  both bumpers so that it does not also dispatch their assigned gestures.
- A fixed phrase queues behind other fixed phrases; the same action does not
  stack. A dialog line cuts it off. Stopping speech drops queued phrases.

Sources: `touch.py`, `runtime.py`, `robot/live.py`; robot counterparts in
`nao-fair-autostart/nao_fair_autostart/`; tests in `test_dialog.py`,
`test_speak_queue.py`, `test_armed_sync.py` and autostart `test_touch.py`.

## Dialog playback

- At most one dialog is active. Next, previous, repeat, and reset use it; with
  none active they do nothing. Planning lines (`human`, labeled Mensch) are
  skipped, have no spoken delivery, and never become the last spoken line.
- Only the applied active dialog has a position. Position changes when speech
  starts, not merely when requested. Apply preserves it by line id when the
  active dialog stays the same; deleting that line falls back to a surviving
  preceding robot line, or clears the position if none remains. Switching the
  active dialog or selecting none clears it.
- App and Standby share the position. A server restart preserves the robot's
  position; an autostart-process restart clears it.
- With Wrap off, advancing past an end says nothing and stays. New dialogs
  created in the UI have Wrap on and idle reset off. Enabling idle reset in the
  UI starts at five minutes; the API model's omitted-field defaults are separate
  (`wrap=False`, `idle_reset_secs=300`).
- Only an action that speaks a dialog line postpones idle reset. A silent move
  beyond an end, an empty repeat, reset, and its announcement do not postpone it.
- Delivery is plain speech, speech with one accompanying motion (wave left,
  wave right, nod, shake), or animated speech. Sit/stand are standalone actions.
- Next-line yield is on by default, including older bindings without the field.
  While an active-dialog line is speaking, that gesture is dropped, not queued.
  Yield ends with the words, even if an accompanying motion continues. It also
  applies to gesture tests. Previous/repeat still interrupt.
- Dialog reset clears position and interrupts dialog speech, playing/queued fixed
  phrases, and a conversation reply, including one not yet spoken. Conversation
  and context stay. An accompanying speech motion returns to its starting pose
  and releases stiffness; a standalone motion keeps running.
- A reset announcement is optional plain text; whitespace means silence. Reset
  during the announcement stops and repeats it. Next line interrupts it and
  starts the first robot line; previous/repeat leave it playing; fixed phrases
  wait until it finishes.

Sources: `dialog.py`, `runtime.py`, `robot/live.py`, frontend `DialogsPanel.vue`;
tests in `test_dialog.py`, autostart `test_dialog_playback.py`,
`test_dialog_speakers.py`, `test_motion_playback.py`.

## Standby and Armed

- The separate Python 2.7 autostart process stores one assignment on the robot.
  It contains applied interactions, Armed, volume, speech speed, TTS language,
  and TTS replacements. NAOqi/ALMemory transfers it with acknowledgement. A failed
  push keeps the previous assignment and does not invalidate the connection.
- The app renews its trigger lease about once per second. An explicit release
  or 20 seconds without renewal lets Standby respond when Armed. Bridge redial
  retains the lease. Older applied app configurations without the Armed field
  default to on; the robot assignment contract itself requires an `armed` boolean.
- Both foot bumpers must join before either long press and stay held together
  for at least two seconds. The chord toggles Armed; release both before another
  toggle. It works while the app holds the lease too, provided autostart runs.
- The robot persists Armed and announces Autostart ein/aus. On connect the app
  adopts the robot's switch before pushing its assignment, and follows later
  changes. Apply can change it silently. A change id guards against stale writes;
  a rejected write is retried after reading robot state.
- Standby cannot start a cloud conversation. `start_conversation`,
  `toggle_conversation`, and `stop_and_clear` do nothing there;
  `stop_conversation` stops speech.
- Installing bridge/autostart is separate from assignment sync: the app downloads
  a release and installs it over SSH. The password is used for that install only;
  accepted host keys are remembered. See [ADR 0005](adr/0005-program-install-uses-ssh.md).

Sources: `robot/autostart.py`, `robot/live.py`, autostart `contract.py`, `engine.py`,
`service.py`; tests in `test_autostart.py`, `test_armed_sync.py`,
`test_standby_integration.py`, autostart `test_service_handoff.py`.

## Persistence, prompts, and secrets

- A fresh database gets built-in settings, a generic German system prompt, and
  a short, friendly personality. Knowledge, local context, TTS rules, STT vocabulary,
  dialogs, scenarios, and gesture actions start empty. A local context selection
  can be `null`; composition skips it and reference validation checks it only
  when selected. System prompt and personality remain required.
- `seed.py` initializes once, using prompt constants from `defaults.py` and
  settings from `booth/config.py`. Existing databases retain their content and
  applied state; changing defaults only affects new databases. There is no server
  `config.toml`, prompt-file loading, or `NAO_FAIR_CONFIG`/`NAO_FAIR_PROMPTS` override.
  Use the operator UI/API for persisted configuration; process settings remain
  in `settings.py`.
- The separate standalone Pipecat app still reads its own config and prompt files
  on each start. Those files do not configure the Fair server.
- Operator cloud keys live in a separate server file. Operator values override
  environment values; deleting one restores environment fallback. Blank writes
  retain stored values. APIs return status only (`missing`, `environment`,
  `console`); a new value is used when the next client is constructed.
- The local app has no login. Default binding is localhost; Docker uses host
  networking with that same binding. Deployment reasons are in the wrapper's
  [Docker ADR](https://github.com/icampus-wildau/nao-fair/blob/main/docs/adr/0002-production-docker-host-network.md).

Sources: `seed.py`, `defaults.py`, `booth/config.py`, `settings.py`, `secrets.py`;
tests in `test_defaults.py`, `test_config_secrets_seed.py`, `test_prompt_snapshot.py`.
