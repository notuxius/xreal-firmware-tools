# Motorola Signature and Air 2 Pro audio

Two pairs reportedly crackle in the right channel on a direct Motorola connection. The same pairs play cleanly on the ASUS and through the VITURE adapter. On the Motorola, a four-second Brightness + hold gives clean sound until the next reconnect. The probed firmware explains the route change and its loss at startup. The underlying source of the USB-path distortion is still open. There is no phone audio capture, USB trace or measurement that identifies a channel-format, clock, gain or amplifier defect.

**XREAL Beam → VITURE hub → two pairs of glasses.** On the reported run, neither pair played the VLC movie on its first opening. After backing out, button presses were audible before the movie was reopened. The same movie then played through both pairs. On a fresh connection, try moving the selection on the Beam home screen. If the navigation click is audible through both pairs, open the movie and check whether it has sound on the first try.

On a later run, the user powered on the Beam and the first VLC movie playback sounded clean. After exiting VLC to the alternate menu, navigation sounds were slightly distorted. Reopening the movie produced severe distortion. This run used the alternate menu rather than Evo Launcher. Which pairs reproduced the distortion and whether the launcher affected it are unconfirmed.

In the November 21, 2026 Beam and VITURE hub test, switching the first pair to 3D left the second pair's left eyepiece black until that pair was also switched to 3D. Once both pairs were in 3D, both showed the full 3D picture and played DisplayPort (DP) audio.

## One-pair trial with the installed firmware

For the optional DP-first MCU, use the [one-pair installation and rollback guide](dp-first.md). The Android trial and baseline analysis below apply to the original USB-first kit build.

1. Leave the second pair disconnected. Use one familiar stereo clip, the same cable and a comfortable, nonzero volume throughout. On a fresh direct Motorola connection, confirm the display is active and record which speaker crackles. If practical, check a left/right channel-identification clip as well.
2. Hold **Brightness +** on the glasses for about four seconds, release, and let the picture return. Replay the clip. Confirm that sound comes from both glasses speakers and the right channel is clean. Unplug fully and reconnect without pressing a button. Record whether the original symptom returns. [XREAL Air 2 Pro Quick Start Guide](https://resource.xreal.com/www-xreal-com/docs/manual/XREAL_Air2Pro_Quick_Start_Guide.pdf)
3. For a *direct, button-free* trial, record the phone's original **Developer options → Disable USB audio routing** value. If available and originally off, turn it on, fully reconnect and play the clip without pressing a button. If the phone offers media-output selection and lists the external display, try selecting it. A pass requires clean stereo **from the glasses**, a working picture and the same result on three fresh connections. Check any spatial app or host brightness control you rely on. Silence and playback from the handset both fail the test. If the option was already on when the symptom occurred, that value has already failed the direct-connection trial.
4. Restore the original phone setting after the trial if it fails or interferes with other USB headsets. It controls automatic routing for USB audio devices across the phone. Android does not promise that it selects DP audio, and the kit's USB-mode EDID omits DP audio. The setting can therefore leave the phone with no glasses audio output. [Android Developer options](https://developer.android.com/studio/debug/dev-options#media)
5. If direct button-free playback fails, test the reported clean **VITURE** connection across three reconnects on the same pair, without a hold. For a direct connection the known workaround is Brightness + after plugging in. To regain USB control, unplug and reconnect directly to the ASUS. On Windows check Device Manager for the Air 2 Pro composite interfaces and test playback, local brightness, host control and tracking in the usual app. Endpoint presence alone does not demonstrate working tracking.

**Optional route evidence.** With wireless ADB already paired to the Motorola and only that phone selected, capture the following while the same clip is playing. Repeat with filenames ending in `-manual-dp` and `-android-trial` after steps 2 and 3. The commands only query the phone. Review the active media output and attached USB devices across captures. An attached endpoint alone cannot establish the active playback route. Keep captures local if they include unrelated device or app identifiers. [Android wireless ADB](https://developer.android.com/tools/adb#wireless) · [Android dumpsys](https://developer.android.com/tools/dumpsys)

```powershell
adb shell dumpsys media.audio_policy > policy-direct-usb.txt
adb shell dumpsys usb > devices-direct-usb.txt
```

## What the firmware does

Analysis used the Air 2 family images built from commit `e60edfaa4d66fd6de48e13b762010158ed80c76c`. All code offsets below are hexadecimal **file offsets including the container header**. Addresses beginning with `0x2000` are MCU SRAM. The version strings also appear in official images, so the strings alone cannot identify the bytes installed on either pair.

| Path | Evidence in the verified image |
|---|---|
| Cold initialization | MCU startup at `0x11618` calls bridge setup. The initializer at `0x050EC` calls the E0B8 setter with zero at `0x050F8–0x050FA`. Startup calls the USB audio route, audio peripheral configuration and USB initialization at `0x116AC`, `0x116BC` and `0x116C0`. These call `0x01EA4`, `0x08618` and `0x12648` respectively. |
| USB addressing | The standard-request dispatcher at `0x12AEC` branches on request 5. At `0x12B92–0x12B98` it stores the requested address in `0x20000330`. The status-stage path at `0x121B4–0x121D0` checks request 5 and applies the address to the USB peripheral. The address is assigned by the host and carries no make or model. |
| Automatic choice | The policy in the first `build_mcu.RECORDS` entry starts executing at `0x03140`. It reads E08B through `0x05210`. During the eligible initial wait, `0x0315A–0x03160` tests the address. A nonzero value latches `0xA9` in session byte `0x20000248` and leaves the USB route active. An uninterrupted eligible wait with zero address and no adapter indication increments the counter to `0xA6` (166 polls, about five seconds upstream). If E0B8 is still zero it enters the full transition at `0x03196`. An adapter indication has a separate path at `0x03176`. |
| Four-second key | The unmodified key dispatcher checks the event and inhibit bits, passes `0xF9F` (3999) to its elapsed-time check at `0x0ACCC–0x0ACD0`, then calls the toggle at `0x0ACE0`. The handler at `0x185E8` reads the cached E0B8 value at `0x2000037C`. Its diagnostic string at `0x18700` is “Inc_Key LongPress”. |
| Manual USB → DP | The handler clears E0C2, selects E0B8=1 at `0x18606–0x18608` and toggles the cached E0B6 through `0x1862E`. The DP bridge selector at DP offset `0x01558` advertises the LPCM CTA at `0x01FDC` when E0B8=1 and the no-audio CTA at `0x0205C` otherwise. The switch triggers a display re-enumeration. The MCU mutes/closes SmartPA and changes the audio path via `0x18638–0x18644`, clears USB control bits and the peripheral clock gate at `0x1864E–0x18668`, and clears the USB-active byte at `0x1866A–0x1866E`. A timed follow-up at `0x01C8C–0x01CB0` selects the saved DP gain and calls the SmartPA open routine. |
| Manual DP → USB | The other branch writes E0B8=0 at `0x1869C–0x1869E`, toggles B6 at `0x186B2` and calls the same route, audio and USB initialization routines used at startup. |
| Converter fallback | The automatic path shares the E0B8/B6 setters and DP audio subroutines with the key path. It has additional behavior. The helper at `0x0FD00` changes the session marker to `0xA7`, waits 2000 ms, toggles B6 again, waits 1000 ms and reapplies saved gain. At `0x0FD46–0x0FD4E` it sets bit 3 of `0x20000D92`. This is bit 19 of the key dispatcher's word at `0x20000D90`, which inhibits the manual toggle. The converter fallback is therefore a poor substitute for a user key press on a USB host. |

The button's route selector lives in SRAM and bridge state. The cold initializer explicitly selects USB. Saved speaker gain has a separate restore path. No reviewed nonvolatile route field, compatible write lifecycle or per-host key was identified. Persisting a single DP selection would also carry it to the ASUS.

The stock manual DP path closes the composite device. The host consequently loses USB Audio, IMU (interface 3), HID control (4) and VSYNC (5). Host-based tracking and updates through application HID depend on those interfaces. This does not establish whether an internal sensor stops sampling. A forced startup change could also close the updater's connection during a transfer. For later playback and Smart Connect observations on the immediate build, see the [DP-first hardware results](dp-first.md#immediate-dp-on-a-direct-motorola-or-asus-connection). [Kit HID protocol](../../../docs/hid-protocol.en.md)

The DP bridge already supports both advertisements. Changing only the EDID leaves the MCU audio route unresolved. Skipping USB shutdown or changing clocks would require evidence of simultaneous DP-I2S, USB streaming/idle behavior and control-interface operation. The optional DP-first variant provides USB functions after a manual switch back. The default build retains the original host-dependent policy.

### Brightness + hold tones

Both the two-second and four-second cues play the same embedded WAV. The tone-related instructions and the WAV below are identical in the official `09.1.00.180_20240507` MCU and the immediate DP-first build.

| Stage | MCU file offsets and behavior |
|---|---|
| While held | Event 3 tests elapsed time against 1999 and 3999 ms. At `0x0AC40` and `0x0AC84`, a nonzero cached route byte (`0x2000037C`, 1 for DP) skips the tone request. Both paths still mark the threshold reached. In USB mode each threshold sets `0x20000D41` to 1 and calls the amplifier-open routine at `0x1133C` with the current gain. Release actions are dispatched separately. |
| Sound source | The initializer at `0x09534` points the player at PCM data at `0x1EB6C`, inside the RIFF/WAVE at `0x1EAA0`. The clip contains 5,728 stereo frames of 16-bit PCM at 48 kHz (119.3 ms). |
| Playback | The request can start MCU audio output even when USB playback is idle (`0x01B90`). The transmit DMA handler calls the mixer at `0x0D266`. The mixer at `0x189AC` adds the clip to each channel and clears the request and sample index when the clip ends. |
| DP routing | The stock transition calls `0x01E04`, which clears the MCU audio pin functions, disables its I²S peripheral and changes the GPIO routing. DP playback reaches the speakers through the bridge. Removing the two checks would request a clip on the inactive MCU output. |

An offline Unicorn check exercised the held event at 1999, 2000, 3999 and 4000 ms on both images, with the timer and amplifier calls stubbed. In each image both thresholds advanced in either mode. Only USB mode requested the clip, once at each threshold.

A possible experiment is to interrupt DP sound briefly, isolate the bridge audio output, play the clip on MCU I²S and restore the DP route and saved gain. First establish which device drives the shared clock and data lines during the handoff, whether the amplifier relocks cleanly, and what happens if the user releases the button during the clip. The public AW88266A register map does not document an independent tone generator. Simultaneous mixing with live DP audio and a temporary handoff both remain unverified on hardware. [AW88266A datasheet, digital audio interface and register map](https://atta.szlcsc.com/upload/public/pdf/source/20220923/0F57DA988EE590AA4ADC6573D3E5CBA6.pdf)

## Windows build and offline verification

From the cloned repository root in PowerShell, use Python 3.13. Keep the exact official files at the two input paths below and keep a separate original copy. The image builders use the standard library. The tests additionally use Unicorn to execute the Thumb instructions under test. These commands build and inspect only the **existing published pair**.

```powershell
py -3.13 -m venv .venv
$python = '.\.venv\Scripts\python.exe'
& $python -m pip install hidapi==0.15.0 unicorn==2.1.4
& $python xreal/air2/build_mcu.py --src firmware/air2/09.1.00.180_20240507.bin --out air2-mcu.bin
& $python xreal/air2/build_dp.py --src firmware/air2/1140 --out air2-dp.bin
& $python xreal/air2/build_mcu.py --src firmware/air2/09.1.00.180_20240507.bin --verify air2-mcu.bin
& $python xreal/air2/build_dp.py --src firmware/air2/1140 --verify air2-dp.bin
& $python xreal/mcu_flash.py --image air2-mcu.bin --verify-only
& $python -m unittest discover -s xreal/air2/tests -v
```

Both flasher self-tests passed. For a completely device-free repeat, limit the model hint to each self-test's child process. This avoids the shared HID module's import-time enumeration.

```powershell
cmd /d /c 'set "XREAL_PID=0x0432" && .venv\Scripts\python.exe xreal\mcu_flash.py --self-test'
cmd /d /c 'set "XREAL_PID=0x0432" && .venv\Scripts\python.exe xreal\dp_flash.py --self-test'
```

| File | Verified SHA-256 | Container CRC |
|---|---|---|
| Official MCU input | `C07633E97215346468A18F5306A10F800388A80CCD7DCFE800D468F4AB1BFD49` | `169B7EE4` |
| Built MCU | `950CA9535AFBD02C40D97A167DB06ECFAEEBC35F6ADCCDED81829CC44A8BE4C9` | `CFB740BE` |
| Official DP input | `350BACE369A83823D8EF867AE04AD07CF64D724C10EC7CEECFB83861AC9672F3` | `0AA87FF3` |
| Built DP | `46556947E81DD7EBBD7F26B2541B63E0362804C166C020639DA908D2ABB2F486` | `A86203B1` |

Both output files matched independent reruns byte for byte. MCU verification checked 40 guarded records and 526 changed byte positions. DP verification checked 21 records, 384 changed byte positions, bank tag `BF`, CTA contents and native/scaler/SBS display contracts. Both stock recovery files passed the Air 2 Pro flasher checks. No allowlist or PID gate changed.

Eight baseline automated tests passed. They cover USB startup selection after a previous DP selection, addressed-host latching including last-poll addressing, fallback eligibility and timeout, retry/gain/button lock, key dispatch, toggling both ways on an addressed host, corrupted image rejection, and the existing display and EDID contracts. The tests execute verified firmware instructions with peripheral I/O and selected callees stubbed. They do not simulate a complete boot, measure acoustics, exercise an actual device or establish that a physical replug is always a cold boot.

## Rollback

For this trial, restore the phone's original routing setting and reconnect the glasses. It requires no firmware restoration. To recover USB control after a manual DP switch, reconnect directly to the ASUS over a USB-data-capable cable.

For a separate, explicitly authorized **return to vendor firmware**, connect only the target pair directly to the PC. Verify the two stock hashes above, retain the backup and check that the MCU status command identifies Air 2 Pro APP PID `0x0432`. The kit's documented restore writes the stock MCU **first** and stock DP **second**. It removes the kit's display improvements. If the MCU is already in BOOT (`0x0431`), the flasher accepts only exact stock MCU recovery. If APP cannot start, upstream describes holding the button while connecting USB to enter BOOT. Stop if the expected PID never appears. Following an interrupted transfer, power-cycle before retrying. After a complete vendor restore, check APP PID, MCU version, DP version `1140` and normal display/audio. Version `1109` indicates DP fallback. The exact restore commands and failed-transfer procedure are in the [upstream Windows recovery instructions](../../../README.md#restore-official-xreal-air-family-firmware).
