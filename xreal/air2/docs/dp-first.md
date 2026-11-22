# Air 2 Pro DP-first audio

These experimental MCU builds target the Air 2 Pro with the kit's patched Air 2-family DP 1140 image. Both request DP audio once per MCU power-up. On an addressed USB host, the immediate variant requests the switch on the first poll and the delayed variant waits about five seconds. Actual startup and display retraining times depend on the host.

Only the Air 2 Pro has been tested with DP-first. The flasher rejects both variants on the standard Air 2 even though it shares the Pro’s stock firmware. The original Air needs a separate port for direct USB hosts. [Model compatibility](../../../docs/README.md)

After DP audio starts, hold **Brightness +** for four seconds and release to select USB audio and restore the composite USB device. The IMU, HID control, VSYNC and application updater depend on that device. Reopen an app that lost its connection. Hold the same button again to return to DP audio. A cold reconnect rearms the preference. If a powered adapter keeps the MCU running through a source change, the current choice lasts for that power session. Switching to USB on a power-only connection still requires a USB data cable and host for those interfaces to work.

## Immediate DP on a direct Motorola or ASUS connection

Choose `--dp-first-immediate` to request the stock DP transition on the **first policy poll after USB SET_ADDRESS**. This removes the five-second startup grace period on a direct USB data connection. USB addressing, EDID renegotiation and the stock amplifier startup still take time. On a connection without USB addressing the existing converter timeout, retraining and saved-gain handling still apply. An address of zero alone cannot distinguish a slow USB host from a power-only adapter.

Compared with the five-second DP-first image, this revision changes one payload byte at MCU container offset `0x1DC5A` and four CRC bytes. The Thumb instruction compares the incremented addressed-host counter with 1 instead of 166. The session latch, both route checks, the four-second key, and the MCU/DP update handlers are byte-identical. The prior five-second image was successfully flashed on the first pair, and the ASUS read its MCU and DP versions after the button restored HID. Those hardware observations apply only to that earlier image.

In a fresh checkout, complete the [Windows setup and baseline builds](motorola-audio.md#windows-build-and-offline-verification) first. Then run these commands from the repository root to build and verify the immediate image offline.

```powershell
$p = '.\.venv\Scripts\python.exe'
$stock = 'firmware/air2/09.1.00.180_20240507.bin'
$image = 'air2-pro-mcu-dp-first-immediate.bin'
& $p xreal/air2/build_mcu.py --src $stock --dp-first-immediate --out $image
& $p xreal/air2/build_mcu.py --src $stock --dp-first-immediate --verify $image
& $p xreal/mcu_flash.py --image $image --verify-only
```

For a one-pair upgrade from the five-second variant, keep the second pair unplugged. Connect the first pair directly to the ASUS, let its present DP mode settle, then hold Brightness + for four seconds to get USB. Follow the installation steps below with `$image` set to the **immediate** file above. After FINISH and JUMP_TO_APP, the fast transition may hide USB before Windows can read the version. Keep the cable connected. If the flasher prompts, hold Brightness + for four seconds once DP is active. It retries the APP check and version read only.

Test three cold reconnects to the Motorola with the same stereo clip, then check that one four-second hold on the ASUS restores the APP version read and actual tracking. After testing, the previous five-second behavior can be restored in APP mode using the same dry run and manual flash procedure with `air2-pro-mcu-dp-first.bin`. BOOT recovery still requires the exact official stock MCU.

All 20 offline tests and both flasher self-tests passed. The immediate image matched a separate rebuild and passed the flasher's exact-image verification.

The immediate image was flashed on one Air 2 Pro. The Motorola Signature played clean audio on a cold connection, but Smart Connect launched Mobile desktop despite the Experience Hub preference. The Hub remained selectable. On the ASUS, switching back to USB let the updater read the MCU version and the IMU delivered 32 distinct reports. Time to first sound, in-app tracking and bootloader recovery remain unverified.

## Experimental USB-to-DP button recovery

During a Beam test, the glasses switched from DP to USB. After the next four-second hold, the Beam still saw the USB headset. Unplugging at the glasses and reconnecting restored automatic DP. The host trace could not establish where the button action stopped.

The Air 2 Pro `--usb-to-dp-on-hold` MCU derives from the ATR4 v2 build below. In USB mode it calls the existing audio toggle once the firmware recognizes a four-second **Brightness +** hold, and consumes the release. A successful switch can briefly blank the display before the button is released. DP-to-USB still switches on release. Emulation covers the missed-release case and leaves the effect on this Beam failure to a hardware test.

```powershell
$p = '.\.venv\Scripts\python.exe'
$image = 'air2-pro-mcu-usb-to-dp-on-hold.bin'
& $p xreal/air2/build_mcu.py --src firmware/air2/09.1.00.180_20240507.bin --usb-to-dp-on-hold --out $image
& $p xreal/mcu_flash.py --image $image --verify-only
```

Connect only the test Air 2 Pro directly to the ASUS and select USB audio. Dry-run `& $p xreal/mcu_flash.py --image $image` and confirm Air 2 Pro APP PID `0x0432` and the USB-to-DP image label. When ready to write, repeat with `--flash` and keep the cable attached through the returned APP version check. This trial updates only the MCU. Follow the [stock recovery procedure](#future-updates-and-rollback) if the glasses enter BOOT.

On the Beam, hold **Brightness +** for four seconds and release to enter USB, wait for USB playback, then hold until DP begins switching. Release the key and confirm the picture and stereo audio return while the glasses stay plugged in. Repeat three times. Use `air2-pro-mcu-audio-bridge-trace-v2.bin` for MCU rollback.

In the first hardware trial, the ASUS retrained before the wearer released the key. Windows lost the USB headset, retained the NVIDIA DP audio output after release, and played all three reference tones clearly. The same pair entered USB audio on the original Beam, then stayed on the USB headset after a six-second hold. The Beam recorded no USB disconnect from that hold. The cause of the Beam-specific failure remains unresolved.

The follow-up `--usb-to-dp-stable-hold` MCU changes one payload byte at `0x07336` from the first trial image. The shared GPIO interrupt can report the same pressed key during a display event. The stock scanner rewrites the press timestamp on every such report, so repeated interrupts can keep a long hold below four seconds. The new branch skips that timestamp write for an already tracked press. The separate double-press state still timestamps the second press.

In emulation, repeated interrupts every 250 ms kept the first trial image in USB through a six-second hold. The stable-timer image switched at four seconds and remained in DP after release. In a hardware trial with the stable-timer image, the Beam again entered USB audio and stayed there after a six-second hold. The Type-C partner stayed attached and the Beam logged no USB disconnect during the hold. The firmware change fixes the reproduced timer behavior, and the Beam failure still needs a trace from the MCU.

Build with `--usb-to-dp-stable-hold` and use `air2-pro-mcu-usb-to-dp-stable-hold.bin` for the same dry run and test. The first trial image `air2-pro-mcu-usb-to-dp-on-hold.bin` is the MCU rollback for this revision.

### Dedicated USB and DP buttons

The Air 2 Pro `--audio-route-buttons` MCU builds on ATR4 v2 and retains immediate DP-first startup. Hold **Brightness +** for four seconds to select **USB audio** or **Brightness −** for four seconds to select **DisplayPort audio**. Each key leaves its destination selected on subsequent holds. The switch starts while held, with release as a fallback if the dispatcher missed the four-second poll. Short presses and the two-second 2D/3D and brightness/volume shortcuts still use the stock handlers. The existing adapter and no-USB session locks apply.

```powershell
$p = '.\.venv\Scripts\python.exe'
$image = 'air2-pro-mcu-audio-route-buttons.bin'
& $p xreal/air2/build_mcu.py --src firmware/air2/09.1.00.180_20240507.bin --audio-route-buttons --out $image
& $p xreal/mcu_flash.py --image $image --verify-only
```

Use the one-pair MCU dry run above. On the Beam, start in DP, hold **Brightness +** and check for the USB headset. Then hold **Brightness −** and check that DP sound and picture return. Each route can briefly blank the display while it retrains. For MCU rollback to the previous trial, use `air2-pro-mcu-usb-to-dp-stable-hold.bin`.

In the first hardware trial, the four-second **Brightness +** hold restored the Windows USB headset and MCU control interface after the update. On the original Beam, the same hold registered **USB-Audio - XREAL Air 2 Pro**. The wearer reported that holding **Brightness −** for four seconds only lowered brightness. The Beam still listed the USB headset and logged no USB disconnect during that attempt. USB-to-DP switching remains unresolved on the Beam.

#### V2 physical button timing

The Air 2 Pro `--audio-route-buttons-v2` MCU times the two rocker buttons from the GPIO expander. It checks on the usual interrupt, every 20 ms when the scanner is running without an interrupt, and at the first scan that reaches the four-second deadline. **Brightness +** starts the USB transition and **Brightness −** starts the DP transition while the button is still held. Release at the threshold also works if it lands in the same scan. The existing route transition can take longer to restore sound and picture.

```powershell
$p = '.\.venv\Scripts\python.exe'
$image = 'air2-pro-mcu-audio-route-buttons-v2.bin'
& $p xreal/air2/build_mcu.py --src firmware/air2/09.1.00.180_20240507.bin --audio-route-buttons-v2 --out $image
& $p xreal/mcu_flash.py --image $image --verify-only
```

Emulation covers both directions with missing interrupts and the stock hold dispatcher bypassed. On one Air 2 Pro, the **Brightness +** hold restored Windows USB after the V2 flash. On the original Beam, **Brightness +** registered the USB headset. The **Brightness −** hold disconnected that headset and the DP link trained successfully again while the Type-C partner stayed attached. Android's AudioTrack played the full 24-second test clip after returning to DP. Audibility through the speakers still needs wearer confirmation. For rollback to the first dedicated-button trial, use `air2-pro-mcu-audio-route-buttons.bin`.

### Experimental Beam USB-to-DP switch without retraining

On Beam OS 384, a USB-to-DP switch during VLC playback removed the glasses' USB device. EVA treated the detach as an unplug and exited. The DP display disconnected and reconnected. When EVA returned to the foreground, Beam OS removed VLC's task and force-stopped the process.

This paired Air 2 Pro trial advertises the stereo LPCM EDID in both audio modes and skips the MCU's DP retrain request on USB-to-DP. The Beam still sees USB detach, and VLC may pause when Android removes the USB output. The test is whether the display stays connected, VLC survives, and DP audio comes through the speakers. The EDID change can also affect which output other hosts select when both audio devices are present.

```powershell
$p = '.\.venv\Scripts\python.exe'
$mcu = 'air2-pro-mcu-audio-route-buttons-v2.bin'
$dp = 'air2-pro-dp-audio-mute-probe.bin'
& $p -m xreal.air2.build_beam_audio_switch --mcu $mcu --dp $dp
```

Use one test Air 2 Pro in USB audio mode on the PC. Check both images with their flashers in dry-run mode. Apply the experimental DP image first, then the experimental MCU image. To restore the baseline, restore the route-buttons v2 MCU first and then the mute-probe DP image.

In the hardware trial on November 20, 2026, Windows detected both USB and DP audio after the update. During VLC playback on Beam OS 384, the USB-to-DP switch removed the USB headset at 11:03:50. EVA logged the USB detach and exited. The Beam logged DP audio connected at 11:03:51 and force-stopped VLC when EVA resumed at 11:03:52. The Beam logged no display hotplug during this switch. Removing the DP retrain did not prevent EVA from returning to Home and stopping playback. Speaker output during the handoff remains unverified.

Add `--skip-retrain-both-ways` to also skip the retrain when switching from DP to USB. Use a fresh output directory. The builder writes `air2-pro-mcu-beam-audio-switch-no-retrain.bin` and the same Beam DP image. If that DP image is already installed, only the MCU needs updating. Emulation confirms the route changes and USB initialization still run in both directions. To roll back, flash `air2-pro-mcu-beam-audio-switch.bin`.

On Beam OS 384, a DP-to-USB switch with this MCU kept the picture visible, restored sound, and resumed VLC in the same foreground task. On the reverse switch, USB detached and EVA took the foreground. Keepalive returned to the same VLC task about ten seconds after USB detached. The Beam then reported active VLC audio on the DP route.

### Experimental DP-to-USB black cover handoff

The cover-handoff MCU derives from the Air 2 Pro no-retrain MCU. On DP → USB it defers the stock OLED reopen until the Beam acknowledges an opaque overlay frame or three seconds elapse. Keepalive 1.28 sends and verifies the acknowledgement over the Air 2 Pro USB control interface when it has USB access. Its existing foreground and pause checks still control the fade.

Build it with `python -m xreal.air2.build_beam_cover_handoff --mcu <no-retrain-MCU> --out <candidate>`. The companion Beam DP image is the same one used with the no-retrain MCU. The no-retrain MCU is the rollback image.

On Beam OS 384, Keepalive 1.28 committed the opaque cover and received the glasses' acknowledgement 150 ms later. VLC stayed paused through the 400 ms fade. The wearer confirmed the visual result on the Air 2 Pro test pair.

The `--reverse` option takes the exact cover-handoff image above and adds a USB → DP cover handshake. A recognized Brightness − hold leaves USB connected while Keepalive 1.33 checks for a pending request. Keepalive acknowledges the request after the black frame commits. The glasses leave USB after the acknowledgement reply or after three seconds. The hold still performs the full transition if a host settings command already selected DP on the bridge while leaving the USB controller enabled. The original cover-handoff MCU is the rollback image.

On Beam OS 384, Keepalive 1.33 received the glasses' acknowledgement 184 ms before the kernel reported USB disconnect. Just Player returned to the foreground paused and Keepalive faded it in over 400 ms. A separate Brightness − hold from a black stock home screen in USB mode restored the home screen in DP with the cable still connected.

Use `--restore-usb-retrain` with the exact bidirectional cover-handoff image for the stock Home rendering trial. It restores the stock DP → USB retrain request before USB initialization. The bidirectional image without this change is the rollback for the trial.

On November 21, 2026, with Keepalive 1.33 on Beam OS 384, this image switched from DP to USB while stock Home was in the foreground. The stock launcher still drew black, and NRSDK reported that it could not get the Android surface.

## Experimental DisplayPort audio recovery

On the Beam, a short VLC UI sound could disappear after the idle click while the movie started with sound. The same glasses played two stereo tone groups eight seconds apart without a click on the ASUS NVIDIA port, where the Windows audio idle timeouts were disabled. The sustained movie silence and the loss of sound when switching to 3D still need separate reproduction.

The stock DP 1140 audio state machine checks the audio PLL on poll 3, recalibrates on poll 4 if needed, and checks again on poll 5. A late lock can leave output disabled until the eight-bit counter wraps or the source changes state. This opt-in variant checks the same lock bit on polls 5 through 255. It changes three payload bytes at `0x84CA–0x84CC`, the container CRC, and the bank0 tag. It targets late-lock silence and keeps the existing mute behavior. Beam and 3D recovery are unverified on hardware.

```powershell
$p = '.\.venv\Scripts\python.exe'
$image = 'air2-pro-dp-audio-relock.bin'
& $p xreal/air2/build_dp.py --src firmware/air2/1140 --audio-relock --out $image
& $p xreal/air2/build_dp.py --src firmware/air2/1140 --audio-relock --verify $image
```

The DP flasher accepts this exact image on the Air 2 Pro APP PID only. With one pair connected to the ASUS in USB audio mode, run `& $p xreal/dp_flash.py --image $image` to check the device and transfer plan. The published `air2-dp.bin` can restore the preceding behavior. Use the same Beam connection, movie, and audio mode to compare starts from idle and audio recovery after 2D→3D→2D. Record delayed clicks separately from silence and confirm that the 3D picture has returned before judging audio recovery.

On the Beam with the late-lock image installed, VLC started with sound. After switching to 3D, the picture returned and movie and UI audio stayed silent. The Brightness + mode-switch handler toggles E0B6 for display retraining and preserves the E0B8 DP-audio selection. The bridge's existing retrain hook leaves the cached audio phase and sample rate in `0x06EA–0x06EB`. The audio detector can ignore an invalid sample-rate reading during a retrain. If the source resumes at the same rate, the old cache can suppress a fresh initialization.

The separate `--mode-audio-reset` image retains the late-lock retry. In the existing DP-only E0B6 retrain hook it clears both audio bytes and disables I²S output through the stock audio routine. The next valid audio stream then goes through the stock initialization sequence.

On one Air 2 Pro with that image, switching from 2D to 3D during VLC playback brought back the picture and left the movie silent. Reopening the movie in 3D restored normal-volume DP audio. On another switch, pausing for a few seconds and resuming restored the same audio in place. Automatic recovery during playback is still unresolved.

A live trace on the original Beam reproduced the silence. The Rockchip driver logged successful four-lane DisplayPort link training and the display reported 3840 × 1080 at 60 Hz. The same VLC track stayed active on the same AudioFlinger output during silence. The mixer remained out of standby, wrote another 529,408 frames between two silent samples, and reported nonzero signal power. With VLC paused, the mixer entered standby. Resuming reactivated the same track on the same output and the listener confirmed that sound returned. This places the observed failure downstream of Android's mixer. The trace cannot establish whether the Beam transmitter sent audio packets or whether the glasses' bridge and amplifier accepted them.

On a subsequent original Beam test with Keepalive off, a brief VLC pause left 3D silent while the primary output stayed awake. After an approximately four-second pause, the HAL logged `do_out_standby`, and sound returned on resume. The same standby-and-resume sequence preserved audible playback on the return to 2D. Rockchip's public 4.19 Analogix driver resets the DP transmitter during bridge enable and configures audio from separate PCM startup and hardware-parameter callbacks. Lost transmitter audio configuration on a modeset fits the Beam observations. Confirming it requires the matching Beam kernel or a DP main-link capture. [Rockchip Analogix driver](https://github.com/rockchip-linux/kernel/blob/develop-4.19/drivers/gpu/drm/bridge/analogix/analogix_dp_core.c)

The Beam Audio Keepalive app version 1.4 has a separate **Start 2D/3D audio recovery** control. It requests transient Android audio focus for 4.5 seconds when the Beam switches between 1920 × 1080 and 3840 × 1080 during playback. Starting recovery stops the silence loop so the audio output can reach standby. On the original Beam, VLC resumed with sound in both directions without a manual pause. Recovery starts from the app after reboot and relies on the player resuming when it regains audio focus.

```powershell
$image = 'air2-pro-dp-mode-audio-reset.bin'
& $p xreal/air2/build_dp.py --src firmware/air2/1140 --mode-audio-reset --out $image
& $p xreal/air2/build_dp.py --src firmware/air2/1140 --mode-audio-reset --verify $image
& $p xreal/dp_flash.py --image $image
```

The exact late-lock image `air2-pro-dp-audio-relock.bin` is available for rollback after a trial.

### Capture a silent 3D switch on the original Beam

The Air 2 Pro `--audio-trace` MCU image derives from the immediate DP-first MCU. It samples the bridge in DP audio mode about every 250 ms and on the 2D/3D edge. It captures the latest 2D sample, first 3D sample, first sample at least two seconds into 3D, and latest 3D sample. The sample contains the elapsed time modulo 65,536 ms, I²C validity bits, the mode selector, the incoming rate detector at `C67C`, the PLL status at `E2EB`, and the I²S output controls at `D423` and `D424`. The header includes the last MCU SmartPA state in each mode. The diagnostic reads status only. The rate detector and lock bit cannot establish sample contents or prove audio packets were sent.

Reserve one test pair and leave its DP firmware on the same image used for the reproduction. Build the diagnostic MCU and run the flasher's read-only checks from the repository root.

```powershell
$p = '.\.venv\Scripts\python.exe'
$traceMcu = 'air2-pro-mcu-audio-trace.bin'
& $p xreal/air2/build_mcu.py --src firmware/air2/09.1.00.180_20240507.bin --audio-trace --out $traceMcu
& $p xreal/mcu_flash.py --image $traceMcu --verify-only
& $p xreal/mcu_flash.py --image $traceMcu
```

1. With the diagnostic MCU and Beam reader installed, start the same movie in 2D DP audio. Switch to 3D with the two-second hold and leave the movie running in silence for at least three seconds.
2. **Keep the glasses plugged into the Beam.** Hold Brightness + for four seconds and release to select USB audio. The MCU stops sampling and retains the existing trace in RAM. Disconnecting power clears the trace.
3. Open the reader and approve the Android USB permission prompt. The reader displays the four samples and logs them under `XrealAudioTrace`. Once Beam ADB is connected, the activity can also be opened with `adb -s <beam> shell am start -n org.xrealdiag.audiotrace/.TraceActivity` and read with `adb -s <beam> logcat -d -s XrealAudioTrace:I`.
4. Repeat from 2D for a comparison trace. This time pause and resume in 3D until audio returns, then switch to USB and read the new trace. The expected tests are a silent trace and a recovered trace from the same hardware.

The reader sends one USB control request to the Air 2 Pro APP PID. Build it with `beam_trace/build.ps1` and the Android SDK platform and build tools 36 plus JDK 17. The SDK and JDK locations and APK output are parameters. The diagnostic MCU and app are unverified together on hardware. The previous immediate DP-first MCU image is available for rollback.

### Capture the idle click in 2D

The separate `--audio-idle-trace` MCU image uses the same bridge registers and USB readout. After two seconds of sampled audio clock lock it saves the last locked sample, the first valid unlocked sample, and the first valid unlocked sample at least two seconds later. It also records the latest sample. Each sample includes the MCU's amplifier state. A failed PLL read cannot trigger a loss event. Use reader version 1.1 or later for the `ATR2` format.

Build the image, verify it, and run the flasher in read-only mode with a single Air 2 Pro connected to the ASUS in USB audio mode. This laptop previously crashed in its Intel USB audio driver during display cycling. Keep the monitor setup steady during the route change and firmware transfer.

```powershell
$p = '.\.venv\Scripts\python.exe'
$idleMcu = 'air2-pro-mcu-audio-idle-trace.bin'
& $p xreal/air2/build_mcu.py --src firmware/air2/09.1.00.180_20240507.bin --audio-idle-trace --out $idleMcu
& $p xreal/mcu_flash.py --image $idleMcu --verify-only
& $p xreal/mcu_flash.py --image $idleMcu
```

1. After the dry run detects Air 2 Pro APP PID `0x0432` and verifies the transfer plan, run `& $p xreal/mcu_flash.py --image $idleMcu --flash`. Keep the cable attached until verification finishes. Use the four-second **Brightness +** hold if prompted to restore USB after the MCU restarts.
2. Move the glasses to the Beam. Use **2D DisplayPort audio**, turn **Keepalive off**, and play `xreal-beam-idle-click-test.wav` once in VLC with repeat off. Confirm the beeps are audible.
3. Let playback finish. Listen for the click, then give the trace another three seconds to save the later sample. If there is no click, wait ten seconds after playback ends. Stay in 2D DisplayPort and keep the glasses powered throughout.
4. Hold **Brightness +** for four seconds and release once to enter USB audio. Open **Air 2 Pro Audio Trace** and allow USB access if prompted. Save the four displayed rows. If **Last locked** is missing, the logger did not observe a sustained lock and this run cannot establish the active baseline.

The firmware records register snapshots at about 250 ms intervals. The MCU amplifier byte is a software state, and the trace does not record the analog click itself. The idle variant passed firmware emulation and still needs its first hardware test.

### Capture both amplifiers through playback and idle

The `--audio-timed-trace` MCU captures both detected SmartPA devices at I²C addresses `0x34` and `0x35` once per second in DisplayPort audio mode. It keeps the latest 28 samples regardless of audio lock and freezes the history in USB audio mode. Each row reports PLL lock, clock stability and loss, output switching, hard mute, and chip and amplifier power-down bits. An unavailable row means at least one hardware read failed or that amplifier was absent. The firmware reads `SYSST` and `SYSCTRL` through the stock 16-bit bus helper and skips the read-to-clear interrupt register. Reader version 1.2 decodes the four `ATR3` pages. [AW88266A register map](https://atta.szlcsc.com/upload/public/pdf/source/20220923/0F57DA988EE590AA4ADC6573D3E5CBA6.pdf)

Use the Air 2 Pro flash procedure above with `--audio-timed-trace` and `air2-pro-mcu-audio-timed-trace.bin`. On the Beam, run the same 2D DisplayPort clip with Keepalive off. Let it end. About three seconds after the click, hold **Brightness +** for four seconds and release without unplugging. If there's no click, begin the hold ten seconds after playback ends. Read **Air 2 Pro timed amplifier trace (ATR3)** on the Beam, then record whether the click was audible. The reader shows milliseconds relative to the newest sample. A one-second interval can miss short transitions. The timed variant passed firmware emulation and still needs the physical capture.

### Experimental amplifier mute on clock loss

With the clock-hold bridge image and Keepalive off, the Beam test still produced the delayed click. Both amplifiers lost their clock in standby and recovered it on the next clip, which was audible.

Build the candidate MCU with `--audio-clock-mute` and output `air2-pro-mcu-audio-clock-mute.bin`. The flasher accepts this exact image on Air 2 Pro. The candidate checks amplifier clock status during DisplayPort audio and uses the hardware mute bit when it detects an unstable or absent clock. It releases a mute it applied after two consecutive clock-stable, switching and fault-free readings. The stock amplifier lifecycle owns USB audio and power-down. The existing reader still decodes the one-second ATR3 capture, including the hardware mute bit. [AW88266A register map](https://atta.szlcsc.com/upload/public/pdf/source/20220923/0F57DA988EE590AA4ADC6573D3E5CBA6.pdf)

Keep the same DP bridge image for the first comparison so only the MCU differs. With Keepalive off, check that the first clip plays, listen through the Beam's standby transition, and confirm the next clip is audible from its first tone. Repeat short plays with a standby gap so a full transition stays in the ATR3 window before the USB switch. The one-second trace cannot tell whether the mute can precede the exact edge that causes the click. The previous `air2-pro-mcu-audio-timed-trace.bin` is the MCU rollback image.

### Measure amplifier transitions within the standby gap

On the Beam with the clock-mute MCU and Keepalive off, both beeps played clearly after standby. The wearer still heard a slight click at startup and a delayed click at shutdown. ATR3 showed both amplifiers muted during clock loss and unmuted on stable playback. Awinic's shutdown timing keeps the input clock valid while the chip is shut down. [AW88266A power sequencing](https://atta.szlcsc.com/upload/public/pdf/source/20220923/0F57DA988EE590AA4ADC6573D3E5CBA6.pdf)

Build `--audio-edge-trace` as `air2-pro-mcu-audio-edge-trace.bin` for the Air 2 Pro. It samples both amplifiers before the existing mute handler, at most once every 8 ms. It saves each status change and a one-second heartbeat in the same ATR3 format. The actual spacing depends on the MCU loop. Run the two-beep clips with standby gaps and freeze the ring by switching to USB audio during a repeat. Check whether clock instability appears while either amplifier is still switching, and compare switching and mute at restart. Changes between two reads require finer measurement. Use `air2-pro-mcu-audio-clock-mute.bin` to return to the one-second sampler.

### Compare the DP receiver with the amplifier

Use `--audio-bridge-trace` on both builders to build `air2-pro-dp-audio-bridge-trace-v2.bin` and `air2-pro-mcu-audio-bridge-trace-v2.bin`, and use reader 1.4. ATR4 v1 reported every bridge sample as unavailable on the Beam. The publisher depended on a conditional audio-state handler and used the MCU's E0C2 control byte. The v1 reader discarded the byte and the error status whenever the expected marker was missing, so those samples cannot identify the exact failure.

ATR4 v2 publishes from the bridge main loop through its E085 display status byte and preserves the display flag. It reports the stock audio rate detector, PLL flag, and I²S pin configuration when the DP audio route is active. The MCU reads the bridge status first and amplifier `0x34` second. The reader distinguishes a failed page select, a failed read, a missing marker, and an inactive DP audio route from a valid reading. The trace logs status changes and a one-second heartbeat during regular polling.

With Keepalive off, repeat the two-beep clip across confirmed standby gaps. Freeze the capture by selecting USB audio during a repeat. Verify that the receiver reports `RATE=present` during playback. After playback, look for `RATE=absent` while the subsequently sampled amplifier clock remains stable and switching, then measure the interval to its first clock-loss sample. A change observed in the same row gives no separable lead at the sampling cadence. The detector reports a recognized rate rather than the time of the last audio packet. The pin field reports configuration, and the amplifier clock flags report what reached the amplifier. Restore the prior clock-hold DP and edge-trace MCU images to roll back both measurements.

### Test same-rate DP audio resume

Build `--audio-fast-resume` as `air2-pro-dp-audio-fast-resume.bin` for the Air 2 Pro. Use it with the ATR4 v2 MCU already installed. In the Beam trace, the amplifier briefly regained its clock at startup. The bridge then gated its I²S pins during its rate-change routine, and the amplifier reported another 381 ms interruption. This candidate retains the configured rate during DP standby. If that same rate returns with the pins configured and the PLL locked, playback resumes without resetting I²S. A different rate, disabled pins, or three valid polls without lock uses the existing acquisition routine. Cold start and link retrain also use the existing acquisition routine.

Run the same Keepalive-off clip across confirmed hardware standby. Check the full first beep and listen for both clicks. In ATR4, check whether the extra startup clock loss while `I2S_PINS=0` is gone. In the v2 baseline, the amplifier lost its clock before the bridge's PLL or rate status changed. Fast resume addresses the extra startup interruption. The initial clock loss at shutdown still needs a separate fix. Reflash `air2-pro-dp-audio-bridge-trace-v2.bin` to restore the baseline trace.

In the live fast-resume test, the first beep was clear and both clicks remained audible. One restart kept the clock stable, while another still showed a 474 ms interruption with the pins disabled. At the intervening standby, the rate detector went absent 192 ms after the amplifier reported clock loss. The trace does not capture the exact rate or the resume decision that led to the remaining startup reset.

### Probe for a DisplayPort mute signal

Build `--audio-mute-probe` as `air2-pro-dp-audio-mute-probe.bin` for the Air 2 Pro. The bridge reports bit 4 of `E730`, `E735`, `E736`, and `E737` through the existing ATR4 v2 MCU. These are candidate addresses near the firmware's stream-attribute reads. The LT7911UXC register for VB-ID has yet to be identified. [DisplayPort receiver VB-ID documentation](https://docs.amd.com/api/khub/documents/~QfuvsQALKbAJ6xGxEiccQ/content)

Repeat the Keepalive-off clips in DP audio and switch to USB audio during a repeat. Launch Beam reader 1.5 with `adb shell am start -n org.xrealdiag.audiotrace/.TraceActivity --ez muteProbe true` to decode the candidate bits beside the amplifier clock. Check that a candidate changes consistently in both directions and that its shutdown edge appears while the subsequently sampled amplifier still has a stable clock. A same-row edge does not establish usable lead time. Even a candidate with repeatable timing needs independent confirmation of its meaning. This trace samples amplifier status and does not timestamp PCM onset.

Across two live Beam captures with Keepalive off, all four candidate bits stayed at zero across recorded clock losses and recoveries. The first beep sounded complete and both clicks were audible. These captures ruled out an observable transition at the sampled addresses during the test. A direct measurement of the actual AudioMute flag still requires its receiver register or a main-link capture.

To restore the standard ATR4 receiver fields, flash `air2-pro-dp-audio-fast-resume.bin` and launch the reader without the `muteProbe` extra.

## Windows DP audio troubleshooting

These results are from one ASUS laptop and an Air 2 Pro running the immediate DP-first build, with the desktop mirrored to the glasses at 1080p60. The two USB-C video ports use different GPUs. In Windows **Settings → System → Display → Advanced display**, select the glasses and check which adapter drives them.

### Intel Iris Xe — picture returns, playback device missing

Windows had cached the no-audio EDID extension (header `02 03 0C 01`). A direct read over the DisplayPort AUX channel found that the glasses were already advertising stereo LPCM at 48 kHz (header `02 03 14 41`). Restarting the Intel graphics adapter once confirmed the diagnosis when Windows reread the EDID and created the playback device.

**Fix.** In Registry Editor as administrator, set binary value `1` under `HKLM\SYSTEM\CurrentControlSet\Enum\DISPLAY\MRG3135\<Intel monitor instance>\Device Parameters\EDID_OVERRIDE`. Use the complete, checksum-valid 128-byte extension read from these glasses in DP audio mode. Value `1` replaces only the CTA extension and preserves the base display timings. On the tested laptop, the scheduled task `XREAL Air 2 Pro DP Audio` applies the same block to newly configured Intel instances of this monitor. After a cold reconnect, **Air 2 Pro (HD Audio Driver for Display Audio)** became active automatically, without a graphics adapter restart. This verified endpoint detection on Intel. The listening tests below used NVIDIA. [Microsoft’s EDID override format](https://learn.microsoft.com/en-us/windows-hardware/drivers/display/overriding-monitor-edids)

For this kit DP image, the measured block starts with `02 03 14 41 23 09 04 01 83 01 00 00 65 03 0C 00 10 00 41 04`, followed by 107 zero bytes and the checksum byte `28`. To identify the registry instance, open the Air 2 Pro monitor in Device Manager and check **Details → Device instance path** and **Parent**. The tested Intel parent starts with `PCI\VEN_8086`. Create `EDID_OVERRIDE` if absent and preserve any pre-existing override. Cold-reconnect to apply the change.

**Undo.** On the tested laptop, disable or remove that scheduled task first so it cannot reapply the override. Remove only the binary value `1` installed by this fix from each affected Intel Air 2 Pro instance, then cold-reconnect the glasses. If you remove the task entirely, its local script at `C:\Program Files\XREAL Air 2 Pro DP Audio\Apply-AudioProfile.ps1` can also be removed.

### NVIDIA RTX 4080 Laptop GPU — playback device appears, sound clips or goes silent

Use the glasses’ native EDID on this port. An early trial with the Intel workaround made Windows list a DP output even while the glasses were in USB audio mode. After removing that override, a direct hardware read on a cold reconnect confirmed the stereo DP profile and Windows created **Air 2 Pro (NVIDIA High Definition Audio)**.

**Fix.** The NVIDIA High Definition Audio driver (1.4.6.6) had four-second idle timeouts on AC and battery. We observed the audio device enter D3 between streams. On the tested laptop we changed these settings in Windows.

1. In Device Manager, open **Sound, video and game controllers → NVIDIA High Definition Audio → Properties → Details → Driver key**. Also copy **Device instance path** from the same Details tab.
2. In Registry Editor as administrator, open `HKLM\SYSTEM\CurrentControlSet\Control\Class\<driver key>\PowerSettings` and record the original values. Set **PerformanceIdleTime** (AC) and **ConservationIdleTime** (battery) to four binary bytes **00 00 00 00** each. Keep **IdlePowerState** at its existing value.
3. Run `pnputil /restart-device "<audio device instance path>"` from an elevated terminal. This restarts the audio device. The setting affects all outputs of that NVIDIA audio device and may increase idle power use. [Microsoft’s PortCls power settings](https://learn.microsoft.com/en-us/windows-hardware/drivers/audio/portcls-registry-power-settings)

**Undo.** Restore both saved binary values (**04 00 00 00** for each on this ASUS) and restart the same NVIDIA audio device.

**What we heard.** We unplugged at the glasses for ten seconds, reconnected to the same NVIDIA port and left the buttons alone. The listener heard both three-note groups in full across a ten-second break, then all three notes again after a minute idle. The audio device stayed in D0 during the idle check. A later cold reconnect produced a slight crackle on the middle note. On the final cold reconnect, the same three notes played cleanly from the start with the clip buffered before playback, and the player reported no underrun. The earlier test did not record underruns, so the cause of that single crackle remains open.

## Build or verify on Windows

Run PowerShell from the repository root. To prepare your official inputs, baseline images and local Python environment, follow the [Windows environment setup and official input hash table](motorola-audio.md#windows-build-and-offline-verification) first. Keep a separate copy of the official files.

```powershell
$p = '.\.venv\Scripts\python.exe'
$stock = 'firmware/air2/09.1.00.180_20240507.bin'
$image = 'air2-pro-mcu-dp-first.bin'
& $p xreal/air2/build_mcu.py --src $stock --dp-first --out $image
& $p xreal/air2/build_mcu.py --src $stock --dp-first --verify $image
& $p xreal/mcu_flash.py --image $image --verify-only
& $p xreal/air2/build_mcu.py --src $stock --verify air2-mcu.bin
& $p xreal/air2/build_dp.py --src firmware/air2/1140 --verify air2-dp.bin
& $p -m unittest discover -s xreal/air2/tests -v
```

| Image | SHA-256 | Container CRC |
|---|---|---|
| Experimental DP-first MCU | `39819E2083911E59AEA844EE484DB52DE8B5B0C9A49BD7EC317DF80F7BD521E3` | `0x29CB1C30` |
| Immediate DP-first MCU | `DCF383BE0684FB0EBC6126CA1AD031594DB4D77F9B16BE6D5C248EA69F5769ED` | `0xCEE99416` |
| Kit USB-first MCU for rollback | `950CA9535AFBD02C40D97A167DB06ECFAEEBC35F6ADCCDED81829CC44A8BE4C9` | `0xCFB740BE` |
| Kit DP 1140 used with both MCUs | `46556947E81DD7EBBD7F26B2541B63E0362804C166C020639DA908D2ABB2F486` | `0xA86203B1` |

## First installation and hardware trial

1. Leave the second pair disconnected. Connect the test pair directly to the ASUS with a data-capable cable. Confirm from your installation records that this pair has the published kit DP build above. The version string `1140` is shared with stock. This update needs only the MCU. Close software using the glasses' HID interfaces.
2. In the PowerShell session above, run `& $p xreal/mcu_flash.py --image $image` for a **dry run**. Confirm **Air 2 Pro APP PID 0x0432**, the expected MCU version, the experimental image SHA above and the Air 2 Pro transfer plan. On the original USB-first firmware a fresh direct laptop connection supplies USB. If the pair is already running this variant, let DP audio settle, then hold Brightness + four seconds to return to USB first.
3. **Manual write**, only when ready to test this pair: `& $p xreal/mcu_flash.py --image $image --flash`. Keep the cable connected through FINISH, JUMP_TO_APP and the returning APP version check. If the transfer finished and the flasher asks for USB again, use the four-second hold after DP audio is active. The extra wait retries only device identification and the version read.
4. On the Motorola, fully unplug and reconnect the test pair without a button press. Allow the brief display retrain. Play a known stereo clip at nonzero volume. Confirm clean sound from both glasses speakers, especially the right. Repeat on three cold connections. Record failures, including silence or playback from the handset.
5. On a direct data-capable connection, hold Brightness + for four seconds after DP playback starts. On the ASUS, run `& $p xreal/mcu_flash.py` to confirm `0x0432`, control interface 4 and a successful version read. Check USB audio, the actual tracking app and host controls. Wait well beyond the startup delay and confirm USB stays available. Hold again and confirm DP audio returns. A later cold reconnect should choose DP again.
6. On the test pair, check local brightness, dimming, native RGB, your 2D refresh rates, Full-SBS 3D and the VITURE adapter as applicable. Compare with the second pair on its existing firmware. Record any repeated blackouts, loss of picture or inability to return to USB before considering a second installation.

## Future updates and rollback

Use a direct PC USB data connection. Let DP settle, select USB with the four-second hold, then dry-run the updater to confirm the APP PID. After manual selection the startup timer is consumed. MCU image transfer runs in the separate bootloader. DP updates use APP HID. The PREPARE handlers also cancel a pending automatic transition if an update starts in the initial USB window.

To restore the preceding kit behavior when APP is reachable, select USB, dry-run `& $p xreal/mcu_flash.py --image air2-mcu.bin`, then manually run `& $p xreal/mcu_flash.py --image air2-mcu.bin --flash`. The same kit DP image serves both MCUs.

If the MCU application cannot expose USB, the kit documents holding the button while connecting USB to enter BOOT. Confirm **Air 2 Pro BOOT PID 0x0431** before recovery. In BOOT the flasher allows only exact official stock. With the verified official MCU in place the manual recovery command is `& $p xreal/mcu_flash.py --restore --flash`. Once APP returns, you can restore the kit MCU or follow the [full vendor rollback instructions](../../../README.md#restore-official-xreal-air-family-firmware). After an interrupted transfer follow the kit's power-cycle procedure. Bootloader recovery has not been exercised on the DP-first test pair.

## Implementation and verification

The builder first verifies the official input, constructs the normal published MCU and checks its complete hash and all original display contracts. Seven additional guarded records produce the DP-first output. They affect 97 payload byte positions plus the four-byte CRC compared to that baseline. The variant-specific flasher entry accepts only its pinned digest for the Air 2 Pro. The normal version, PID, container, recovery-file and BOOT gates still apply.

The first hook at MCU file offset `0x0314A` sends the existing startup poll to a helper in the zero-filled slot at `0x1DC48`. When the host has assigned a USB address, the helper uses the existing count and session byte. At the deadline it marks the session complete, checks cached and live E0B8 and calls the stock button handler at `0x185E8` only when both indicate USB. This protects against toggling a host back out of DP. On an inconsistent or unexpected nonzero route the helper leaves the setting alone for that session.

Without an assigned address the helper resumes the existing E08B eligibility check. The branch at `0x0315A` uses the timed converter transition for eligible adapter attention as well. This avoids setting the original adapter-specific manual inhibit. The variant suppresses the manual-lock store at `0x0FD4E` while retaining the converter's saved-gain and delayed second-B6 behavior. These changes need physical verification on each adapter.

The key dispatch call at `0x0ACE0`, MCU PREPARE at `0x0DA5C` and DP PREPARE at `0x0DEA6` each latch the session and tail-call the original handler with its inputs intact. The DP-first build adds no nonvolatile write, USB identifier, audio register value or DP bridge patch.

Sixteen offline tests passed, including emulated keyed DP → USB → DP, early manual override, converter fallback, refusal to toggle an existing DP route, both PREPARE paths, exact patch boundaries, model and BOOT rejection, and a simulated post-transfer USB retry. Both flasher self-tests and byte-for-byte baseline rebuilds passed. Peripheral, timing and selected callee behavior are stubbed. See the immediate-build hardware results above for the limits of physical testing.
