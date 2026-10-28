# What the booth process can change without a restart

The booth process is `nao_pipecat/main.py` in `/home/vasc9380/git/Wildau/NAO/nao-pipe-cat-devcontainer`. `main` calls `AppConfig.load` once and passes that object into `run_robot_session`. The `while True` loop in `run_robot_session` can open a new NAOqi connection and a new `PipecatSession` without exiting, and it keeps the same `AppConfig` and the same IP. A running `PipecatSession` already rebuilds the joined system instruction on every user turn. The model name, the four timeouts, speaker volume, speech speed, the Gladia session settings, the TTS replacement rules, and the sensor callbacks stay as they were when that session was built. The microphone client can drop and dial the same bridge URL again inside that session. The process has no path that points a live session, or the next session in the same process, at a different robot address.

`--simulate` uses `SimulationSession` instead of `run_robot_session`. That path shares the system-instruction and model behavior below. It does not open NAOqi, the microphone bridge, Gladia, TTS, volume, or the sensors.

The installed `naoqi_control`, `nao_websocket_bridge_client`, and `nao_websocket_bridge_protocol` copies in the booth `.venv` match the sibling checkouts cited here.

## Joined system instruction

A running session replaces it on every user turn.

`PipecatSession.run` builds an `EnvironmentContextInjector` with `EnvironmentContextInjector.from_nao_controller`. The robot name on that injector is `nao_controller.robot_name` (or `"NAO"`), and `mention_wave_tool` is `False`. `SimulationSession.run` builds the same injector with the name from `--robot-name`.

`EnvironmentContextInjector.process_frame` (`nao_pipecat/processors/context_injector.py`), for each downstream `LLMContextFrame`, calls `NaoSystemInstruction.build_system_instruction` and pushes an `LLMUpdateSettingsFrame` whose delta is `OpenAIResponsesLLMSettings(system_instruction=...)` before it forwards the context frame. `build_system_instruction` reads the prompt files again on that call:

- `prompts/personality.md` via `_load_personality`
- `prompts/answering_hints.md` via `_load_answering_hints`
- `prompts/event_context.md` via `_load_event_context` (omitted when the file is missing or empty)
- `prompts/local_context.md` via `_load_local_context`
- `prompts/technical_context.md` via `_load_technical_context`

`_load_text_file` uses `Path.read_text`. `{robot_name}` is substituted from the name stored on the injector. The function then appends the current Europe/Berlin clock. With `mention_wave_tool=False`, `_load_technical_context` does not append `_WAVE_INSTRUCTION`.

The frame is handled by `LLMService.process_frame` and `LLMService._update_settings` in the installed Pipecat package (`pipecat/services/llm_service.py`). A `system_instruction` change replaces `_base_system_instruction` and `_compose_system_instruction` writes the result back onto `_settings.system_instruction`. The booth LLM is `OpenAIResponsesHttpLLMService`. Its `_process_context` (`pipecat/services/openai/responses/llm.py`) passes `self._settings.system_instruction` into `get_llm_invocation_params` for that HTTP request. The injector sits immediately upstream of the LLM in both `PipecatSession.run` and `SimulationSession.run`, so the update is queued ahead of the context frame for that turn.

## Model name

The model name is the `llm_model` string from the single `AppConfig.load` in `main`. `AppConfig.load` (`nao_pipecat/config.py`) reads `config.toml` through `_resolve_config_path` and then `_apply_env_overrides`. An `LLM_MODEL` environment variable replaces the file value at that load. `main` does not call `AppConfig.load` again.

`PipecatSession.__init__` copies it to `self._llm_model`. `PipecatSession.run` passes it to `OpenAIResponsesHttpLLMService.Settings(model=self._llm_model)`. `run_simulation` passes `app_config.llm_model` into `SimulationSession`, and `SimulationSession.run` uses the same settings constructor. The supervisor loop constructs each new `PipecatSession` with the original `app_config`.

The only `LLMUpdateSettingsFrame` the booth sends sets `system_instruction`. It does not set `model`. `OpenAIResponsesHttpLLMService._build_response_params` sends `self._settings.model` on each Responses API call, so each call uses the model from construction. `AIService._update_settings` (`pipecat/services/ai_service.py`) would apply a `model` field and call `set_model_name` if a delta contained one. The booth never sends that delta.

## The four timeouts

Listening, context reset, thinking, and unmute delay are read by `AppConfig.load` as `listening_timeout_secs`, `context_reset_timeout_secs`, `thinking_timeout_secs`, and `unmute_delay_secs`. `PipecatSession.__init__` passes those four fields into `NaoNlpManager`. `NaoNlpManager.__init__` (`nao_pipecat/nao/nao_nlp_manager.py`) stores them on `_listening_timeout_secs`, `_context_reset_timeout_secs`, `_thinking_timeout_secs`, and `_unmute_delay_secs`.

A running session only reads those attributes:

- `_listening_timeout_handler` sleeps `_listening_timeout_secs`, then moves from listening to idle when the user is not speaking
- `_context_reset_timeout_handler` sleeps `_context_reset_timeout_secs`, then calls `LLMContext.set_messages([])` while idle
- `_thinking_timeout_handler` sleeps `_thinking_timeout_secs`, then interrupts and returns to listening
- `unmute_microphone` calls `NAOSpeechFilter.unmute(delay=self._unmute_delay_secs)`

Nothing in the booth assigns those four attributes after `NaoNlpManager.__init__`. The next session in `run_robot_session` constructs a new manager from the same `AppConfig` object.

## Speaker volume and speech speed

`output_volume` and `tts_speed` are read by the same `AppConfig.load`. `run_robot_session` applies them by calling `NaoController.prepare(volume=app_config.output_volume, speech_speed=app_config.tts_speed, posture=app_config.posture)` before `PipecatSession(nao, config=app_config)`.

`NaoController.prepare` (`naoqi_control/robot.py`) runs `_prepare` on the qi worker. `_prepare` calls `ALAudioDevice.setOutputVolume` with the volume and `Tts.set_speed` with the speed. `Tts.set_speed` (`naoqi_control/tts.py`) calls `ALTextToSpeech.setParameter("speed", speed)`. Later speech goes through `speak_text` → `NaoController.speak` → `Tts.say_async` → `ALTextToSpeech.say`, which uses the speed already stored on the robot. `speak_text` takes the text and a timeout. It does not take a volume or a speed.

`Audio.set_output_volume` and `Tts.set_volume` exist on the controller. The booth process calls neither. The only volume and speed writes are the ones inside `_prepare`. `run_robot_session` calls `prepare` again when it opens the next NAOqi connection, with the same `app_config` numbers. A pipeline that is already running does not call `prepare`.

## Gladia language, endpointing, utterance length, audio enhancer, and custom vocabulary

`PipecatSession.run` reads `gladia_language`, `gladia_endpointing`, `gladia_max_utterance_secs`, and `gladia_audio_enhancer` from the process `AppConfig` and passes them into one `PausableGladiaSTTService` (`nao_pipecat/services/pausable_gladia.py`, a subclass of Pipecat `GladiaSTTService`). The language string becomes a `Language` value (`Language.DE` when the string is `de` and is not a `Language` member, otherwise `Language.EN`). That value is placed in `LanguageConfig(languages=[language], code_switching=False)`. Endpointing is `Settings.endpointing`. Utterance length is `Settings.maximum_duration_without_endpointing`. The audio enhancer is `PreProcessingConfig(audio_enhancer=...)`. The same constructor also takes `region=self._config.gladia_region`.

Custom vocabulary is not an `AppConfig` field. `PipecatSession.run` builds a list literal — `NAO`, `Telematik`, `TH Wildau`, `Wildau`, `RoboticLab`, `EDIH`, `Stop`, `Stopp` — and appends `robot_name` when that name is non-empty and not already in the list. The list is `CustomVocabularyConfig(vocabulary=..., default_intensity=0.6)` with `custom_vocabulary=True`. The same `RealtimeProcessingConfig` sets a fixed spelling dictionary, `{"NAO": ["Nau", "Now"]}`.

`PausableGladiaSTTService.resume_billing` and `_reopen_live_session` open a later Gladia live session by clearing `_session_url` and calling `_connect`. `GladiaSTTService._connect` (`pipecat/services/gladia/stt.py`) calls `_prepare_settings` when `_session_url` is empty, and `_prepare_settings` copies `endpointing`, `maximum_duration_without_endpointing`, `language_config`, `pre_processing`, and `realtime_processing` from `self._settings`. Those are the settings object built in `PipecatSession.run`. The booth never writes those fields again. `GladiaSTTService._update_settings` stores a delta and leaves the active Gladia session as it is. The booth does not call it.

A new `PipecatSession` after `session.run` returns builds a new Gladia service from the same `AppConfig` and a new list literal, plus the robot name reported by that connection's `ALSystem.robotName` in `NaoController.__init__`.

## TTS replacement rules

The rules are fixed when `nao_pipecat/processors/nao_speech_processor.py` is imported.

`_TTS_REPLACEMENTS` is a module dict of punctuation substitutions. `_load_tts_word_replacements` reads `prompts/tts_replacements.txt` once, at import, and `_tts_word_transform = replace_text(...)` compiles those pairs. Pipecat `replace_text` (`pipecat/utils/text/transforms/replacements.py`) compiles the patterns when it is called. `NAOSpeechOutput._speak_batch` calls `_normalize_tts` for each spoken chunk. `_normalize_tts` applies the module dict and then `_tts_word_transform`. It does not read the file again. The next session in the same process uses the same module objects. A new process is what imports the module again.

## Callbacks on the seven sensors

`NaoController.__init__` builds `Buttons`, and `Buttons.__init__` (`naoqi_control/buttons.py`) creates seven `Button` objects. Each `Button.__init__` subscribes to `ALMemory` and connects the signal to `Button._handler`:

| Attribute | ALMemory key | Callback registered by the booth |
| --- | --- | --- |
| `front_head` | `FrontTactilTouched` | `NaoNlpManager._on_front_head_button` |
| `middle_head` | `MiddleTactilTouched` | none |
| `back_head` | `RearTactilTouched` | `NaoNlpManager._on_back_head_button` |
| `left_bumper` | `LeftBumperPressed` | `NaoNlpManager._on_left_bumper_button` |
| `right_bumper` | `RightBumperPressed` | `NaoNlpManager._on_right_bumper_button` |
| `left_hand` | `HandLeftBackTouched` | none |
| `right_hand` | `HandRightBackTouched` | none |

`NaoNlpManager.__init__` registers the four callbacks with `Button.on_pressed`. `on_pressed` appends to `_on_pressed_callbacks`. `Button` has no method that removes or replaces an entry. `_handler` schedules every callback currently in that list. The four booth handlers are `_on_front_head_button` → `on_front_head_pressed`, `_on_back_head_button` → `on_back_head_pressed`, and the two bumper handlers, which call `Motion.wave` for `left` and `right`. Middle head, left hand, and right hand stay subscribed, with empty callback lists, because the booth never calls `on_pressed` for them.

Those `on_pressed` calls happen in `NaoNlpManager.__init__`, which `PipecatSession.__init__` runs once. A running session does not call `on_pressed` again. The stop-phrase path calls the method `on_back_head_pressed` from `EnvironmentContextInjector` and `StopPhraseProcessor`. That is a direct call, not a new sensor binding. The next loop in `run_robot_session` closes the old controller and constructs a new one, then a new `NaoNlpManager` appends the same four methods on the new buttons.

## Microphone-bridge client

The booth does not call `iter_bridge_messages` (`nao_websocket_bridge_client/stream.py`). That helper opens `websockets.connect` and closes it when the async-with block ends. The process that actually carries microphone audio is Pipecat's `WebsocketClientTransport`.

`PipecatSession.__init__` builds the URL with `websocket_url` (`nao_websocket_bridge_protocol/__init__.py`) from `NaoController.ip`, `AppConfig.audio_bridge_port`, and `nao_bridge_token`. `PipecatSession.run` passes that URL to `WebsocketClientTransport` and calls `install_single_flight_connect` on `transport._session`. Input and output both call `connect` on start (`WebsocketClientInputTransport.start` and `WebsocketClientOutputTransport.start` in `pipecat/transports/websocket/client.py`). `install_single_flight_connect` holds one lock around those calls. If a socket object is still stored and `is_connected` is false, the wrapper clears `_websocket` before calling the original `connect`. `WebsocketClientSession.connect` returns immediately when `_websocket` is already set. Otherwise it calls `websocket_connect` with `open_timeout=10`, stores the socket, and starts `_client_task_handler`.

The socket closes in two ways.

Pipeline shutdown: `PipecatSession.stop` sets `_stopped`, cancels the audio reconnect task, and calls `PipelineWorker.cancel`. `PipelineWorker._cancel` queues a `CancelFrame`. `BaseInputTransport.process_frame` and the output transport call `cancel` on that frame. `WebsocketClientInputTransport.cancel` and `WebsocketClientOutputTransport.cancel` call `WebsocketClientSession.disconnect`. `setup` increments `_leave_counter` once for input and once for output. `disconnect` decrements it and, once the counter is no longer positive and a socket is stored, cancels the receive task, calls `websocket.close`, and sets `_websocket` to `None`. `stop` and `cleanup` on those transports call `disconnect` as well. `PipecatSession.run` also sets `_stopped` and cancels the reconnect task in its `finally` when `WorkerRunner.run` returns.

Drop during the session: `_client_task_handler` ends its receive loop and the transport emits `on_disconnected`. The handler registered in `PipecatSession.run` calls `_handle_audio_disconnected`. Unless the session is already stopped, a reconnect task is already running, or the socket is connected again, that starts `_audio_reconnect_loop`. The loop sleeps using `next_reconnect_delay` from `nao_websocket_bridge_client/reconnect.py`, then `_reopen_audio_socket` / `_dial_audio_socket` closes the current websocket, sets `_websocket` to `None`, and calls the original `connect`. The URL stays `WebsocketClientSession._uri`. The pipeline, the LLM context, and the Gladia settings object stay in place. `_handle_audio_disconnected` ignores a late `on_disconnected` while that reconnect task is still running, and it returns immediately when `is_connected` is already true.

Between robot sessions, `run_robot_session` calls `wait_until_tcp_accepts` (`nao_websocket_bridge_client/reconnect.py`) on the same IP and `audio_bridge_port`. That probes TCP. It does not open the microphone websocket. The next `PipecatSession.run` opens a new transport.

## Robot address

The process cannot attach to a new robot address without exiting.

`main` chooses the address once: `--ip` when it is present, otherwise `config.nao_ip` from `AppConfig.load`. Ports are `--port` or `config.nao_ports`. `run_robot_session` keeps that `ip` and `nao_ports` for the whole loop. Every attempt calls `_open_nao(ip, nao_ports, loop)`, which constructs `NaoController(ip, ports, loop)`. `NaoController.__init__` sets `self.ip` and connects with `_connect_session` to `tcp://{ip}:{port}`. The booth never assigns `NaoController.ip` again. `PipecatSession.__init__` copies that IP into the bridge URL, and `WebsocketClientSession` stores the URL as `_uri` for every later `connect`.

After `session.run` returns, the loop speaks the reconnect line when a connection had been up, calls `_close_nao` (`motion.cancel`, `restore`, `close`), waits until the audio-bridge port accepts, and opens another `NaoController` at the same IP. A different address is a new process, started with a different `--ip`, `NAO_IP`, or `config.toml`, because those are read only in `parse_args` and `AppConfig.load`.
