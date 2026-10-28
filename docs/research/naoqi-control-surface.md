# Which robot facts and motions naoqi-control already exposes

Sources read for this note:

- Library: `/home/vasc9380/git/Wildau/NAO/naoqi-control` (package `naoqi_control`)
- Booth app: `/home/vasc9380/git/Wildau/NAO/nao-pipe-cat-devcontainer` (package `nao_pipecat`)

`NaoController` connects to NAOqi and holds `tts`, `audio`, `motion`, `buttons`, `camera`, and `leds` (`naoqi_control/robot.py` lines 92–142). The booth constructs that object in `_open_nao` (`nao_pipecat/main.py` lines 130–133). This note names the function for each operator fact or motion below and says whether the booth already calls it. It does not propose a fair-server design.

The qi services this library opens are `ALSystem`, `ALAutonomousLife`, `ALBehaviorManager`, `ALTextToSpeech`, `ALAudioDevice`, `ALRobotPosture`, `ALMotion`, `ALVideoDevice`, `ALLeds`, and `ALMemory` (`naoqi_control/robot.py` lines 117–142, `naoqi_control/tts.py` line 19, `naoqi_control/audio.py` line 10, `naoqi_control/motion.py` lines 137–141, `naoqi_control/camera.py` line 39, `naoqi_control/leds.py` line 15, `naoqi_control/buttons.py` line 56).

## Robot name

`NaoController.__init__` stores `self.robot_name` from `ALSystem.robotName()` (`naoqi_control/robot.py` lines 115–121). There is no setter and no later refresh method.

The booth reads that attribute. Startup logs it (`nao_pipecat/main.py` lines 223–227). The speech session appends it to the recognition vocabulary (`nao_pipecat/pipecat_session.py` lines 195–197) and passes the controller into `EnvironmentContextInjector.from_nao_controller`, which reads `nao_controller.robot_name` (`nao_pipecat/pipecat_session.py` lines 240–245, `nao_pipecat/processors/context_injector.py` lines 182–191). Stop-phrase matching also reads `nao.robot_name` (`nao_pipecat/processors/stop_phrase.py` lines 158–162).

## Battery

The library has no battery function. None of the services listed above is a battery service, and the package has no `battery` name on `NaoController`, `Audio`, `Tts`, `Motion`, or `Buttons`. The booth does not call one.

## Volume

Two different volumes exist.

- Master speaker volume: `Audio.get_output_volume` reads it and `Audio.set_output_volume` sets it, both on `ALAudioDevice` (`naoqi_control/audio.py` lines 6–19). The README shows the same pair (`naoqi-control/README.md` lines 76–81).
- TTS gain, 0 to 1, separate from the speaker amp: `Tts.set_volume` calls `ALTextToSpeech.setVolume` (`naoqi_control/tts.py` lines 135–137). There is no TTS-volume getter.

`NaoController.prepare` also writes the master volume, by calling `self.audio.service.setOutputVolume(volume)` directly rather than `Audio.set_output_volume` (`naoqi_control/robot.py` lines 191–225).

The booth calls `prepare` with `volume=app_config.output_volume` (`nao_pipecat/main.py` lines 234–240). `AppConfig.output_volume` defaults to 80 and must be 0–100 (`nao_pipecat/config.py` lines 25–49). The committed booth `config.toml` sets `output_volume = 50` (`nao-pipe-cat-devcontainer/config.toml` line 9). The booth does not call `Audio.get_output_volume`, `Audio.set_output_volume`, or `Tts.set_volume`.

## Speech speed

`Tts.set_speed` sets the `ALTextToSpeech` parameter `"speed"` (`naoqi_control/tts.py` lines 127–129). There is no getter. `prepare` calls `self.tts.set_speed(speech_speed)` after the posture step (`naoqi_control/robot.py` lines 227–230). The README says 100 is normal NAO speech (`naoqi-control/README.md` lines 44 and 68–69).

The booth does not call `Tts.set_speed`. It passes `speech_speed=app_config.tts_speed` into `prepare` (`nao_pipecat/main.py` lines 234–240). `AppConfig.tts_speed` defaults to 90 and must be 50–400 (`nao_pipecat/config.py` lines 26–51). The committed booth `config.toml` sets `tts_speed = 80` (`nao-pipe-cat-devcontainer/config.toml` line 11).

## Voice

The library has no function that selects or reports a TTS voice. The wrapped `ALTextToSpeech` calls are `say`, `stopAll`, `setLanguage`, `setVolume`, and `setParameter` (`naoqi_control/tts.py` lines 24–32, 112–145). `Tts.set_pitch_shift` sets the parameter `pitchShift` (`naoqi_control/tts.py` lines 139–141). `Tts.set_parameter` forwards whatever name the caller supplies (`naoqi_control/tts.py` lines 143–145); the library source never names a voice parameter. The word "voice" in `say_until` means the utterance being cut off (`naoqi_control/tts.py` lines 71–73).

The booth does not call `set_pitch_shift` or `set_parameter`.

## Language

`Tts.set_language` calls `ALTextToSpeech.setLanguage` (`naoqi_control/tts.py` lines 131–133). The docstring example is `"German"`. There is no language getter.

The booth does not call `Tts.set_language`. Its `gladia_language` setting (default `"de"`, committed value `"de"`) is the speech-recognition language passed to Gladia (`nao_pipecat/config.py` line 37, `nao-pipe-cat-devcontainer/config.toml` line 37, `nao_pipecat/pipecat_session.py` lines 179–205). That is not `Tts.set_language`.

## Posture

`Motion.get_posture` returns `ALRobotPosture.getPosture()` (`naoqi_control/motion.py` lines 23–25 and 134–138). `Motion.go_to_posture(name, speed=0.5)` stiffens `"Body"` and calls `goToPosture` (`naoqi_control/motion.py` lines 27–29 and 143–145). The docstring and README name `"Sit"` and `"Stand"` as examples (`naoqi_control/motion.py` line 28, `naoqi-control/README.md` lines 89–91). Any string is forwarded; the library does not enumerate NAOqi posture names.

`prepare` reads the current posture. If it is already `"Sit"` or `"SitRelax"`, it leaves that posture. Otherwise it stiffens the body and calls `goToPosture(posture, 0.5)` (`naoqi_control/robot.py` lines 201–218). The `posture` argument defaults to `"Sit"` (`naoqi_control/robot.py` lines 191–196).

The booth does not call `Motion.get_posture` or `Motion.go_to_posture`. It passes `posture=app_config.posture` into `prepare` (`nao_pipecat/main.py` lines 234–240). `AppConfig.posture` defaults to `"Sit"` (`nao_pipecat/config.py` line 27). The committed booth `config.toml` sets `posture = "Sit"`, with `SitRelax` only in a comment (`nao-pipe-cat-devcontainer/config.toml` lines 14–15).

## Button events

`Buttons` subscribes to seven `ALMemory` events and keeps one `Button` for each (`naoqi_control/buttons.py` lines 21–44):

| Attribute | ALMemory event |
| --- | --- |
| `front_head` | `FrontTactilTouched` |
| `middle_head` | `MiddleTactilTouched` |
| `back_head` | `RearTactilTouched` |
| `left_hand` | `HandLeftBackTouched` |
| `right_hand` | `HandRightBackTouched` |
| `left_bumper` | `LeftBumperPressed` |
| `right_bumper` | `RightBumperPressed` |

Each `Button` connects `ALMemory.subscriber(...).signal` to `_handler` (`naoqi_control/buttons.py` lines 56–63). The event methods are `on_pressed`, `on_released`, `wait_for_button_press`, and `wait_for_button_release`. `is_pressed` is the latest value (`naoqi_control/buttons.py` lines 64–113). The README lists the same seven attributes (`naoqi-control/README.md` lines 142–154).

The booth registers `on_pressed` on four of them (`nao_pipecat/nao/nao_nlp_manager.py` lines 79–82):

- `front_head`: yes. Press toggles listening (`nao_pipecat/nao/nao_nlp_manager.py` lines 332–359).
- `middle_head`: no.
- `back_head`: yes. Press returns to idle and clears context (`nao_pipecat/nao/nao_nlp_manager.py` lines 336–378).
- `left_hand`: no.
- `right_hand`: no.
- `left_bumper`: yes. Press calls `motion.wave(["left"])` (`nao_pipecat/nao/nao_nlp_manager.py` lines 324–341).
- `right_bumper`: yes. Press calls `motion.wave(["right"])` (`nao_pipecat/nao/nao_nlp_manager.py` lines 328–344).

The booth does not call `on_released`, `wait_for_button_press`, `wait_for_button_release`, or read `is_pressed`.

## Chest button

This library does not subscribe to a chest button. The only `ALMemory` subscriptions are the seven names in the table above (`naoqi_control/buttons.py` lines 21–44 and 61–63). No chest event name appears in the package.

## Motions

Named gestures live on `Motion`. Each has a method that returns immediately and an `_and_wait` method that waits (`naoqi_control/motion.py` lines 73–105, `naoqi-control/README.md` lines 109–117).

| Asked motion | Function | What it does | Booth |
| --- | --- | --- | --- |
| Wave | `Motion.wave` / `Motion.wave_and_wait` | Greeting wave on `"left"` and/or `"right"`, built by `Animation.wave` (`naoqi_control/motion.py` lines 73–79, `naoqi_control/animation.py` lines 176–200) | Yes. Foot bumpers call `motion.wave` (`nao_pipecat/nao/nao_nlp_manager.py` lines 340–344). The `wave` tool also calls `nao.motion.wave` (`nao_pipecat/nao/wave_tool.py` lines 15–37), and the session registers that tool (`nao_pipecat/pipecat_session.py` lines 107–111). The booth does not call `wave_and_wait`. |
| Nod | `Motion.nod` / `Motion.nod_and_wait` | Small yes-nod on `HeadPitch`, built by `Animation.nod` (`naoqi_control/motion.py` lines 91–97, `naoqi_control/animation.py` lines 230–241) | Yes. The `nod` tool calls `nao.motion.nod()` (`nao_pipecat/nao/head_tool.py` lines 11–15). The booth does not call `nod_and_wait`. |
| Head shake | `Motion.shake` / `Motion.shake_and_wait` | Small no-shake on `HeadYaw`, built by `Animation.shake` (`naoqi_control/motion.py` lines 99–105, `naoqi_control/animation.py` lines 244–255) | Yes. The `shake` tool calls `nao.motion.shake()` (`nao_pipecat/nao/head_tool.py` lines 31–35). The booth does not call `shake_and_wait`. |
| Sit | `Motion.go_to_posture("Sit")`, or `prepare(posture="Sit")` | Named posture via `ALRobotPosture.goToPosture` (`naoqi_control/motion.py` lines 27–29 and 143–145, `naoqi_control/robot.py` lines 210–218) | The booth does not call `go_to_posture`. It does call `prepare` with `posture` defaulting to `"Sit"` (`nao_pipecat/main.py` lines 234–240, `nao_pipecat/config.py` line 27, `nao-pipe-cat-devcontainer/config.toml` line 14). `prepare` skips the move when the robot is already `Sit` or `SitRelax`. |
| Stand | `Motion.go_to_posture("Stand")` | Same path; `"Stand"` is the documented example (`naoqi_control/motion.py` line 28, `naoqi-control/README.md` lines 89–91) | No. The booth never calls `go_to_posture`, and its posture value is `"Sit"`. |
| Rest posture | No rest-posture function | See below | No dedicated call |
| Stop | `Motion.stop`, `Motion.cancel`, and `NaoController.stop_speaking` | See below | Mixed; see below |

`Animation.play` captures the current joint angles in a local named `rest` and, when `return_to_origin` is true (the default), interpolates back to those angles and then releases stiffness (`naoqi_control/animation.py` lines 71–84 and 96–119, capture at lines 121–124). Wave, nod, shake, and farewell all use that default, so each gesture returns to the pose it started from. That is not a command to a named rest posture. `prepare` recognizes the posture string `"SitRelax"` only as "already sitting" (`naoqi_control/robot.py` lines 213–214). `go_to_posture` would forward `"SitRelax"` if a caller passed it, and the library does not wrap that string in its own rest method.

The same module also exposes `Motion.farewell` / `farewell_and_wait`, a shorter arm gesture (`naoqi_control/motion.py` lines 81–89, `naoqi_control/animation.py` lines 203–227). The booth calls `nao.motion.farewell` from the `farewell` tool (`nao_pipecat/nao/wave_tool.py` lines 63–85), which the session registers (`nao_pipecat/pipecat_session.py` lines 107–111).

Stop is three different calls:

- `Motion.stop` calls `ALMotion.stopMove` (`naoqi_control/motion.py` lines 47–49 and 164–165). The booth does not call it.
- `Motion.cancel` cancels background gesture tasks and waits until the qi worker is idle (`naoqi_control/motion.py` lines 107–116). The booth calls `nao.motion.cancel()` while closing the robot (`nao_pipecat/main.py` lines 161–167). `NaoController.close` also calls `motion.cancel` after stopping speech (`naoqi_control/robot.py` lines 263–277), and the booth then calls `close` (`nao_pipecat/main.py` lines 168–173).
- `NaoController.stop_speaking` forwards to `Tts.stop`, which calls `ALTextToSpeech.stopAll` (`naoqi_control/robot.py` lines 259–261, `naoqi_control/tts.py` lines 112–117). `Tts.stop_async` runs that on the qi worker (`naoqi_control/tts.py` lines 119–125). The booth calls `nao.stop_speaking()` from `NaoNlpManager.stop_speaking` (`nao_pipecat/nao/nao_nlp_manager.py` lines 382–383), from the speech-output processor (`nao_pipecat/processors/nao_speech_processor.py` line 378), and on keyboard interrupt (`nao_pipecat/main.py` lines 136–141).
