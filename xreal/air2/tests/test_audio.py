"""Offline Air 2 audio regression tests. See ../docs/motorola-audio.md for setup."""

import struct
import unittest
from pathlib import Path

from unicorn import UC_ARCH_ARM, UC_HOOK_CODE, UC_MODE_THUMB, Uc
from unicorn.arm_const import (
    UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,
    UC_ARM_REG_R3, UC_ARM_REG_R4, UC_ARM_REG_SP,
)

from xreal.air2 import build_beam_audio_switch, build_beam_cover_handoff, build_dp, build_mcu


FIRMWARE = Path(__file__).resolve().parents[3] / "firmware" / "air2"
SESSION = 0x20000248
ADDRESS = 0x20000330
BRIDGE_STATE = 0x2000037A
BRIGHTNESS_PLUS = 0x20000D88
BRIGHTNESS_MINUS = 0x20000DA0
USB_CONTROL = 0x400C0010
USB_ENABLED = 0x2000025B
END = 0x24000


class AudioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stock = (FIRMWARE / "09.1.00.180_20240507.bin").read_bytes()
        build_mcu.verify_stock(cls.stock)
        cls.image = build_mcu.build(cls.stock)
        build_mcu.verify_output(cls.stock, cls.image)
        cls.dp_first = build_mcu.build_dp_first(cls.image)
        cls.immediate = build_mcu.build_dp_first(cls.image, immediate=True)
        trace = build_mcu.build_audio_trace(cls.immediate, timed=True)
        cls.bridge_trace = build_mcu.build_audio_clock_mute(trace, bridge_trace=True)
        cls.usb_to_dp_on_hold = build_mcu.build_usb_to_dp_on_hold(cls.bridge_trace)
        cls.usb_to_dp_stable_hold = build_mcu.build_usb_to_dp_on_hold(cls.bridge_trace, stable=True)
        cls.audio_route_buttons = build_mcu.build_audio_route_buttons(cls.bridge_trace)
        cls.audio_route_buttons_v2 = build_mcu.build_audio_route_buttons(cls.bridge_trace, v2=True)

    def setUp(self):
        # Addresses in the trace are container offsets. All executed calls are PC-relative.
        self.cpu = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
        for addr, size in ((0, 0x25000), (0x20000000, 0x20000),
                           (0x40000000, 0x1000), (0x400C0000, 0x1000)):
            self.cpu.mem_map(addr, size)
        self.cpu.mem_write(0, self.image)
        self.cpu.reg_write(UC_ARM_REG_SP, 0x2001FFF0)
        self.calls = []
        self.status = 0xA1  # E08B eligible initial wait, no Adapter Attention
        self.stubs = {0x5210: lambda: self.status,
                      0x47A6: lambda: self.byte(BRIDGE_STATE + 2)}
        self.cpu.hook_add(UC_HOOK_CODE, self.on_instruction)

    def byte(self, addr):
        return self.cpu.mem_read(addr, 1)[0]

    def on_instruction(self, cpu, addr, size, _):
        if addr in self.stubs:
            self.calls.append((addr, cpu.reg_read(UC_ARM_REG_R0)))
            cpu.reg_write(UC_ARM_REG_R0, self.stubs[addr]())
            cpu.reg_write(UC_ARM_REG_PC, cpu.reg_read(UC_ARM_REG_LR))

    def run_code(self, start, stop=END):
        self.cpu.reg_write(UC_ARM_REG_LR, END | 1)
        self.cpu.emu_start(start | 1, stop, count=500)
        self.assertEqual(self.cpu.reg_read(UC_ARM_REG_PC), stop)

    def test_addressed_host_and_cold_reconnect(self):
        self.cpu.mem_write(ADDRESS, struct.pack("<I", 7))
        self.run_code(0x3140)
        self.assertEqual(self.byte(SESSION), 0xA9)
        self.cpu.mem_write(ADDRESS, bytes(4))
        self.run_code(0x3140)
        self.assertEqual(self.byte(SESSION), 0xA9)  # latched for this power session

        # Start with DP selected, execute the real cold-init defaults up to the next setter.
        self.cpu.mem_write(BRIDGE_STATE + 2, b"\x01")
        self.stubs.update(dict.fromkeys((0x5088, 0x592C, 0x59E0), lambda: 0))
        self.run_code(0x50EC, 0x50FE)
        self.assertEqual(self.byte(BRIDGE_STATE + 2), 0)

    def test_fallback_requires_continuous_eligible_wait(self):
        self.run_code(0x3140)
        self.assertEqual(self.byte(SESSION), 1)
        self.status = 0
        self.run_code(0x3140)
        self.assertEqual(self.byte(SESSION), 0)
        self.status = 0xA1
        for _ in range(165):
            self.run_code(0x3140)
        self.assertEqual(self.byte(SESSION), 165)
        self.run_code(0x3140, 0x3196)  # entry to full no-USB transition
        self.assertEqual(self.byte(SESSION), 166)

    def test_address_on_last_poll_wins(self):
        self.cpu.mem_write(SESSION, bytes([165]))
        self.cpu.mem_write(ADDRESS, struct.pack("<I", 1))
        self.run_code(0x3140)
        self.assertEqual(self.byte(SESSION), 0xA9)
        self.assertNotIn(0x47A6, [addr for addr, arg in self.calls])

    def test_no_usb_retry_restores_gain_and_locks_button(self):
        self.cpu.mem_write(SESSION, b"\xa6")
        self.cpu.mem_write(0x20000E52, struct.pack("<H", 0x180))
        for addr in (0x16A3A, 0x59E0, 0x112C8):
            self.stubs[addr] = lambda: 0
        self.run_code(0xFD00)
        self.assertEqual(self.byte(SESSION), 0xA7)
        self.assertEqual(self.byte(BRIDGE_STATE + 7), 1)
        self.assertEqual(self.byte(0x20000D92) & 8, 8)
        self.assertIn((0x112C8, 0x180), self.calls)
        self.assertEqual([arg for addr, arg in self.calls if addr == 0x16A3A], [2000, 1000])
        self.calls.clear()
        self.run_code(0xFD00)
        self.assertEqual(self.calls, [])

    def test_four_second_button_gate(self):
        self.cpu.mem_write(0x20000D8A, b"\x04")  # key event consumed by the dispatcher
        self.stubs[0x7C4C] = lambda: 4000 * 4  # 4 timer ticks/ms, press started at 0
        self.stubs[0x185E8] = lambda: 0
        self.run_code(0xACA6, 0xAD0A)
        self.assertIn(0x185E8, [addr for addr, arg in self.calls])
        self.calls.clear()
        self.cpu.mem_write(0x20000D92, b"\x08")  # production no-USB session lock
        self.stubs[0x187C8] = lambda: 0  # shorter press has a different action
        self.run_code(0xACA6, 0xAD0A)
        self.assertNotIn(0x185E8, [addr for addr, arg in self.calls])

    def stub_toggle_io(self):
        # Exercise the stock toggle and E0B8/B6 setters. Stub bus I/O, logging,
        # timing, amplifier and peripheral subroutines at their call boundaries.
        for addr in (0x9568, 0xB090, 0x59E0, 0x9EB0, 0x11410, 0x5A4C, 0x1E04,
                     0x5A70, 0x7C4C, 0x1EA4, 0x8618, 0x12648):
            self.stubs[addr] = lambda: 0

    def test_manual_switch_in_both_directions(self):
        self.stub_toggle_io()
        self.cpu.mem_write(USB_CONTROL, struct.pack("<I", 0x90))
        self.cpu.mem_write(USB_ENABLED, b"\x01")
        self.cpu.mem_write(ADDRESS, struct.pack("<I", 7))  # even on an enumerated host
        self.run_code(0x185E8)
        self.assertEqual(self.byte(BRIDGE_STATE + 2), 1)
        self.assertEqual(self.byte(BRIDGE_STATE + 7), 1)
        self.assertEqual(self.byte(USB_CONTROL) & 0x90, 0)
        self.assertEqual(self.byte(USB_ENABLED), 0)
        self.assertIn(0x1E04, [addr for addr, arg in self.calls])

        self.calls.clear()
        self.run_code(0x185E8)
        self.assertEqual(self.byte(BRIDGE_STATE + 2), 0)
        self.assertEqual(self.byte(BRIDGE_STATE + 7), 0)
        usb_init = [addr for addr, arg in self.calls if addr in (0x1EA4, 0x8618, 0x12648)]
        self.assertEqual(usb_init, [0x1EA4, 0x8618, 0x12648])

    def test_beam_audio_switch_can_skip_display_retrain(self):
        dp_stock = (FIRMWARE / "1140").read_bytes()
        dp_baseline = build_dp.build(dp_stock, audio_mute_probe=True)
        self.stub_toggle_io()
        for both_ways, expected_usb_retrain in ((False, 1), (True, 0)):
            with self.subTest(skip_retrain_both_ways=both_ways):
                mcu, dp = build_beam_audio_switch.build(
                    self.audio_route_buttons_v2, dp_baseline, skip_retrain_both_ways=both_ways)
                build_dp.verify_edid(dp_stock, dp)
                self.assertEqual(dp[0x155E:0x1565], bytes.fromhex("b40100 7a1f799c"))

                self.cpu.mem_write(0, mcu)
                self.cpu.ctl_remove_cache(0, len(mcu))
                self.cpu.mem_write(BRIDGE_STATE + 7, b"\x00")
                self.cpu.mem_write(USB_CONTROL, b"\x90")
                self.cpu.mem_write(USB_ENABLED, b"\x01")
                self.calls.clear()
                self.run_code(0x185E8)
                self.assertEqual(self.byte(BRIDGE_STATE + 2), 1)
                self.assertEqual(self.byte(BRIDGE_STATE + 7), 0)
                self.assertEqual(self.byte(USB_CONTROL) & 0x90, 0)
                self.assertEqual(self.byte(USB_ENABLED), 0)
                self.assertIn(0x1E04, [addr for addr, _ in self.calls])

                self.calls.clear()
                self.run_code(0x185E8)
                self.assertEqual(self.byte(BRIDGE_STATE + 2), 0)
                self.assertEqual(self.byte(BRIDGE_STATE + 7), expected_usb_retrain)
                usb_init = [addr for addr, _ in self.calls if addr in (0x1EA4, 0x8618, 0x12648)]
                self.assertEqual(usb_init, [0x1EA4, 0x8618, 0x12648])

    def press_brightness_plus(self):
        self.cpu.mem_write(0x20000D8A, b"\x04")
        self.cpu.mem_write(0x20000D8C, bytes(4))
        self.stubs[0x7C4C] = lambda: 4000 * 4
        self.run_code(0xACA6, 0xAD0A)

    def test_dp_to_usb_cover_handoff_holds_until_ack_or_timeout(self):
        dp = build_dp.build((FIRMWARE / "1140").read_bytes(), audio_mute_probe=True)
        baseline, _ = build_beam_audio_switch.build(
            self.audio_route_buttons_v2, dp, skip_retrain_both_ways=True)
        image = build_beam_cover_handoff.build(baseline)
        self.cpu.mem_write(0, image)
        self.stub_toggle_io()
        ticks = 4000
        self.stubs[0x7C4C] = lambda: ticks
        self.stubs[0x1E264] = lambda: 0
        pending = 0x2001C50C
        self.stubs[0xB090] = lambda: self.assertEqual(self.byte(pending), 1) or 0
        self.cpu.mem_write(BRIDGE_STATE + 2, b"\x01")
        self.run_code(0x185E8)
        self.assertEqual(self.byte(BRIDGE_STATE + 2), 0)
        for entry in (0xB10C, 0x65FC):
            self.run_code(entry)  # Neither open function enters its stock body.
        self.assertEqual(self.byte(pending), 1)

        ticks += 11999
        self.run_code(0x1E430)
        self.assertEqual(self.byte(pending), 1)
        self.send_cover_ack()
        self.assertEqual(self.byte(pending), 2)
        self.stubs[0x65FC] = lambda: self.assertEqual(self.byte(pending), 0) or 0
        self.run_code(0x1E430)
        self.assertEqual(self.byte(pending), 0)
        self.assertEqual(self.calls[-1][0], 0x65FC)
        del self.stubs[0x65FC]
        for entry, continuation in ((0xB10C, 0xB110), (0x65FC, 0x6600)):
            self.run_code(entry, continuation)
            self.cpu.reg_write(UC_ARM_REG_SP, 0x2001FFF0)

        self.cpu.mem_write(BRIDGE_STATE + 2, b"\x01")
        self.run_code(0x185E8)
        self.run_code(0xB10C)
        self.stubs[0xB10C] = lambda: self.assertEqual(self.byte(pending), 0) or 0
        ticks += 12000
        self.run_code(0x1E430)
        self.assertEqual(self.byte(pending), 0)
        self.assertEqual(self.calls[-1][0], 0xB10C)

    def send_cover_ack(self):
        packet = 0x20001800
        length = 0x20001880
        self.cpu.mem_write(packet, b"\xa7\xad\x04")
        self.cpu.reg_write(UC_ARM_REG_R0, packet)
        self.cpu.reg_write(UC_ARM_REG_R1, length)
        self.cpu.reg_write(UC_ARM_REG_R4, 0x2B)
        self.run_code(0x1DFC0)
        self.assertEqual(self.byte(length), 5)
        self.assertEqual(bytes(self.cpu.mem_read(packet, 5)), b"BHA1\x01")

    def test_usb_to_dp_cover_handoff_waits_for_the_current_ack_or_timeout(self):
        dp = build_dp.build((FIRMWARE / "1140").read_bytes(), audio_mute_probe=True)
        baseline, _ = build_beam_audio_switch.build(
            self.audio_route_buttons_v2, dp, skip_retrain_both_ways=True)
        forward = build_beam_cover_handoff.build(baseline)
        image = build_beam_cover_handoff.build(forward, reverse=True)
        self.setup_route_buttons(forward)
        self.cpu.mem_write(USB_CONTROL, b"\x90")
        self.cpu.mem_write(USB_ENABLED, b"\x01")
        self.cpu.reg_write(UC_ARM_REG_R0, 1)
        self.run_code(0x47C4)  # The host settings handler can select DP without dropping USB.
        self.poll_buttons(1000, 1, False)
        self.poll_buttons(5000, 1, False)
        self.assertEqual(self.byte(BRIDGE_STATE + 2), 1)
        self.assertEqual(self.byte(USB_ENABLED), 1)  # The original hold checks just the cached route.

        self.setup_route_buttons(image)
        self.cpu.ctl_remove_cache(0, len(image))
        packet, length = 0x20001800, 0x20001880
        self.stubs[0x13B2C] = lambda: 0
        self.run_code(0x1161A, 0x1161E)
        self.assertEqual(self.cpu.mem_read(0x2001C500, 16), bytes(16))

        def exchange(page, token=0):
            self.cpu.mem_write(packet, struct.pack("<BBBI", 0xA7, 0xAD, page, token))
            self.cpu.reg_write(UC_ARM_REG_R0, packet)
            self.cpu.reg_write(UC_ARM_REG_R1, length)
            self.cpu.reg_write(UC_ARM_REG_R4, 0x2B)
            self.run_code(0x1DFC0)
            self.assertEqual(self.byte(length), 9)
            return struct.unpack("<4sBI", self.cpu.mem_read(packet, 9))

        for start, send_ack, host_selected_dp, stereo_mode in (
            (1000, True, False, 0), (10000, False, True, 0), (20000, False, False, 1),
        ):
            self.cpu.mem_write(BRIDGE_STATE + 2, b"\x00")
            self.cpu.mem_write(USB_CONTROL, b"\x90")
            self.cpu.mem_write(USB_ENABLED, b"\x01")
            if stereo_mode:
                self.stubs.update(dict.fromkeys((0x16A3A, 0xB348, 0x1423C), lambda: 0))
                del self.stubs[0x187C8]
                self.run_code(0x187C8)
            self.assertEqual(self.byte(BRIDGE_STATE + 3), stereo_mode)
            retrain = self.byte(BRIDGE_STATE + 7)
            if host_selected_dp:
                self.cpu.reg_write(UC_ARM_REG_R0, 1)
                self.run_code(0x47C4)
            self.poll_buttons(start, 1, False)
            self.poll_buttons(start + 4000, 1, False)
            token = (start + 4000) * 4
            self.assertEqual(exchange(5), (b"BHD1", 1, token))
            self.assertEqual(self.byte(USB_ENABLED), 1)
            self.assertEqual(self.byte(BRIDGE_STATE + 2), int(host_selected_dp))
            self.assertEqual(exchange(6, token - 1), (b"BHD1", 0, token))
            if send_ack:
                self.assertEqual(exchange(6, token), (b"BHD1", 1, token))
            delay = 50 if send_ack else 3000
            self.poll_buttons(start + 4000 + delay - 1, 3, False)
            self.assertEqual(self.byte(USB_ENABLED), 1)
            self.poll_buttons(start + 4000 + delay, 3, False)
            self.assertEqual(self.byte(BRIDGE_STATE + 2), 1)
            self.assertEqual(self.byte(USB_CONTROL) & 0x90, 0)
            self.assertEqual(self.byte(USB_ENABLED), 0)
            self.assertEqual(self.byte(BRIDGE_STATE + 3), stereo_mode)
            self.assertEqual(self.byte(BRIDGE_STATE + 7), retrain)

    def dispatch_brightness_plus(self, event, elapsed_ms):
        self.cpu.mem_write(BRIGHTNESS_PLUS + 2, bytes([event]))
        self.stubs[0x7C4C] = lambda: elapsed_ms * 4
        self.run_code(0xABF4, 0xAD0A)

    def test_usb_to_dp_switches_during_hold_and_consumes_release(self):
        for image, switches_on_hold in ((self.bridge_trace, False), (self.usb_to_dp_on_hold, True)):
            with self.subTest(switches_on_hold=switches_on_hold):
                self.cpu.mem_write(0, image)
                self.cpu.ctl_remove_cache(0, len(image))
                self.stub_toggle_io()
                self.stubs[0x1133C] = lambda: 0
                self.cpu.mem_write(BRIDGE_STATE + 2, b"\x00")
                self.cpu.mem_write(USB_ENABLED, b"\x01")
                self.cpu.mem_write(USB_CONTROL, struct.pack("<I", 0x90))
                self.dispatch_brightness_plus(3, 2000)  # held-key sample, two-second cue
                self.dispatch_brightness_plus(3, 3999)
                self.assertEqual(self.byte(USB_ENABLED), 1)
                self.dispatch_brightness_plus(3, 4000)
                self.assertEqual(self.byte(USB_ENABLED), 0 if switches_on_hold else 1)
                self.dispatch_brightness_plus(3, 4500)
                self.dispatch_brightness_plus(4, 4600)  # release of the same key hold
                self.assertEqual(self.byte(BRIDGE_STATE + 2), 1)
                self.assertEqual(self.byte(USB_ENABLED), 0)
                self.assertEqual(self.byte(BRIGHTNESS_PLUS), 0)

                self.dispatch_brightness_plus(3, 2000)
                self.dispatch_brightness_plus(3, 4000)
                self.assertEqual(self.byte(BRIDGE_STATE + 2), 1)
                self.calls.clear()
                self.dispatch_brightness_plus(4, 4600)
                self.assertEqual(self.byte(BRIDGE_STATE + 2), 0)
                self.assertIn(0x12648, [addr for addr, _ in self.calls])

    def test_usb_to_dp_hold_keeps_short_press_and_adapter_guard(self):
        self.cpu.mem_write(0, self.usb_to_dp_on_hold)
        self.stub_toggle_io()
        self.stubs[0x1133C] = lambda: 0
        self.stubs[0x187C8] = lambda: 0
        self.cpu.mem_write(USB_ENABLED, b"\x01")
        self.dispatch_brightness_plus(3, 2000)
        self.dispatch_brightness_plus(4, 2500)
        self.assertIn(0x187C8, [addr for addr, _ in self.calls])
        self.assertEqual(self.byte(USB_ENABLED), 1)

        self.cpu.mem_write(0x20000CBD, b"\x01")  # stock adapter inhibit
        self.dispatch_brightness_plus(3, 2000)
        self.dispatch_brightness_plus(3, 4000)
        self.dispatch_brightness_plus(4, 4600)
        self.assertEqual(self.byte(BRIDGE_STATE + 2), 0)
        self.assertEqual(self.byte(USB_ENABLED), 1)

    def sample_buttons(self, time_ms, expander_pins, interrupt, read_error=0):
        def read_expander():
            if self.cpu.reg_read(UC_ARM_REG_R2) == 0:
                sp = self.cpu.reg_read(UC_ARM_REG_SP)
                buffer, = struct.unpack("<I", self.cpu.mem_read(sp, 4))
                self.cpu.mem_write(buffer, bytes([expander_pins]))
                return read_error
            return 0

        self.stubs[0x7C4C] = lambda: time_ms * 4
        self.stubs[0x7D14] = read_expander
        self.cpu.mem_write(0x20000B18, bytes([interrupt]))

    def scan_brightness_plus(self, time_ms, pressed, interrupt):
        self.sample_buttons(time_ms, 2 if pressed else 3, interrupt)
        self.cpu.mem_write(BRIGHTNESS_PLUS + 2, b"\x01")
        self.cpu.reg_write(UC_ARM_REG_R0, BRIGHTNESS_PLUS)
        self.cpu.reg_write(UC_ARM_REG_R1, BRIGHTNESS_PLUS + 24)
        self.run_code(0x72AC)

    def test_usb_to_dp_hold_survives_repeated_shared_gpio_interrupts(self):
        for image, switches in ((self.usb_to_dp_on_hold, False), (self.usb_to_dp_stable_hold, True)):
            with self.subTest(switches=switches):
                self.cpu.mem_write(0, image)
                self.cpu.ctl_remove_cache(0, len(image))
                self.stub_toggle_io()
                self.stubs[0x7F28] = self.stubs[0x1133C] = lambda: 0
                self.cpu.mem_write(BRIGHTNESS_PLUS, struct.pack("<6I", 0, 0, 0, 200, 500, 1000))
                self.cpu.mem_write(USB_ENABLED, b"\x01")
                self.cpu.mem_write(USB_CONTROL, struct.pack("<I", 0x90))
                self.cpu.mem_write(BRIDGE_STATE + 2, b"\x00")

                for elapsed_ms in range(0, 6000, 50):
                    self.scan_brightness_plus(1000 + elapsed_ms, True, elapsed_ms % 250 == 0)
                    self.run_code(0xABF4, 0xAD0A)
                    if elapsed_ms < 4000:
                        self.assertEqual(self.byte(USB_ENABLED), 1)
                    elif elapsed_ms == 4000:
                        self.assertEqual(self.byte(USB_ENABLED), int(not switches))
                self.assertEqual(self.byte(BRIDGE_STATE + 2), int(switches))
                self.assertEqual(self.byte(USB_ENABLED), int(not switches))
                self.scan_brightness_plus(7000, False, True)
                self.run_code(0xABF4, 0xAD0A)
                self.assertEqual(self.byte(BRIDGE_STATE + 2), int(switches))

                self.cpu.mem_write(BRIGHTNESS_PLUS, bytes(12))
                for time_ms, pressed in ((8000, True), (8100, False), (8200, True), (8300, False)):
                    self.scan_brightness_plus(time_ms, pressed, True)
                self.assertEqual(self.byte(BRIGHTNESS_PLUS + 2), 2)  # stock double press

    def setup_route_buttons(self, image=None):
        self.cpu.mem_write(0, image or self.audio_route_buttons)
        self.stub_toggle_io()
        for addr in (0x7568, 0x7F28, 0x1133C, 0x187C8, 0x18550, 0xB3CC):
            self.stubs[addr] = lambda: 0
        self.cpu.mem_write(0x20000CAE, b"\x01")  # enable the normal key dispatcher
        self.cpu.mem_write(0x20000D4A, b"\x01")  # rocker in brightness mode
        self.cpu.mem_write(0x20000E4C, b"\x04")  # brightness between its limits
        for key in (BRIGHTNESS_PLUS, BRIGHTNESS_MINUS):
            self.cpu.mem_write(key, struct.pack("<6I", 0, 0, 0, 200, 500, 1000))

    def poll_buttons(self, time_ms, pins, interrupt=True, read_error=0):
        self.sample_buttons(time_ms, pins, interrupt, read_error)
        self.run_code(0xA9B0)

    def test_route_buttons_select_once_during_hold_even_with_shared_interrupts(self):
        self.setup_route_buttons()
        self.cpu.mem_write(BRIDGE_STATE + 2, b"\x01")
        self.cpu.mem_write(USB_CONTROL, struct.pack("<I", 0x90))
        for start, target, pressed in ((1000, 0, 2), (8000, 0, 2), (15000, 1, 1), (22000, 1, 1)):
            with self.subTest(start=start, target=target):
                previous = self.byte(BRIDGE_STATE + 2)
                self.calls.clear()
                for elapsed in (*range(0, 4000, 50), 3999, 4000, 4250, 4500):
                    self.poll_buttons(start + elapsed, pressed, elapsed % 250 == 0)
                    self.assertEqual(self.byte(BRIDGE_STATE + 2), previous if elapsed < 4000 else target)
                self.poll_buttons(start + 5000, 3)
                self.assertEqual(self.byte(BRIDGE_STATE + 2), target)
                calls = [addr for addr, _ in self.calls]
                self.assertEqual(calls.count(0xB090), int(previous != target))
                self.assertEqual(calls.count(0x12648), int(previous == 1 and target == 0))
                if target == 1:
                    self.assertEqual(self.byte(USB_CONTROL) & 0x90, 0)
                self.assertEqual(self.byte(BRIGHTNESS_PLUS), 0)
                self.assertEqual(self.byte(BRIGHTNESS_MINUS), 0)

    def test_route_buttons_keep_shortcuts_and_switch_on_release_if_hold_poll_was_missed(self):
        self.setup_route_buttons()
        for target, pressed, shortcut in ((0, 2, 0x187C8), (1, 1, 0x18550)):
            with self.subTest(target=target):
                self.cpu.mem_write(BRIDGE_STATE + 2, bytes([1 - target]))
                self.calls.clear()
                self.poll_buttons(1000, pressed)
                self.poll_buttons(1100, 3)
                self.poll_buttons(1350, 3, False)
                self.assertIn(0xB3CC, [addr for addr, _ in self.calls])

                self.calls.clear()
                self.poll_buttons(2000, pressed)
                self.poll_buttons(2600, pressed, False)
                self.poll_buttons(4550, 3)
                self.assertIn(shortcut, [addr for addr, _ in self.calls])
                self.assertNotIn(0xB090, [addr for addr, _ in self.calls])

                self.calls.clear()
                self.poll_buttons(5000, pressed)
                self.poll_buttons(5600, pressed, False)
                self.poll_buttons(9000, 3)
                self.assertEqual(self.byte(BRIDGE_STATE + 2), target)
                self.assertIn(0xB090, [addr for addr, _ in self.calls])
                self.assertNotIn(shortcut, [addr for addr, _ in self.calls])

    def test_route_buttons_respect_adapter_and_no_usb_locks(self):
        self.setup_route_buttons()
        for lock, value in ((0x20000CBD, 1), (BRIGHTNESS_PLUS + 10, 8)):
            self.cpu.mem_write(lock, bytes([value]))
            for start, route, pressed in ((1000, 1, 2), (7000, 0, 1)):
                with self.subTest(lock=hex(lock), route=route):
                    self.cpu.mem_write(BRIDGE_STATE + 2, bytes([route]))
                    self.calls.clear()
                    self.poll_buttons(start, pressed)
                    self.poll_buttons(start + 600, pressed, False)
                    self.poll_buttons(start + 2500, pressed, False)
                    self.poll_buttons(start + 4000, pressed, False)
                    self.poll_buttons(start + 4100, 3)
                    self.assertEqual(self.byte(BRIDGE_STATE + 2), route)
                    self.assertNotIn(0xB090, [addr for addr, _ in self.calls])
                    self.assertEqual(self.byte(BRIGHTNESS_PLUS) & 2, 0)
                    self.assertEqual(self.byte(BRIGHTNESS_MINUS) & 2, 0)
            self.cpu.mem_write(lock, b"\0")

    def test_physical_holds_switch_at_deadline_without_interrupts_or_stock_hold_events(self):
        self.setup_route_buttons(self.audio_route_buttons_v2)
        self.cpu.mem_write(0x20000CAE, b"\x00")
        self.cpu.mem_write(BRIDGE_STATE + 2, b"\x01")
        for start, target, pressed in ((1000, 0, 2), (8000, 1, 1)):
            with self.subTest(target=target):
                self.calls.clear()
                for elapsed in (0, 500, 2000, 3950, 3999):
                    self.poll_buttons(start + elapsed, pressed, False)
                    self.assertEqual(self.byte(BRIDGE_STATE + 2), 1 - target)
                self.poll_buttons(start + 4000, pressed, False)
                self.assertEqual(self.byte(BRIDGE_STATE + 2), target)
                self.poll_buttons(start + 5200, pressed, False)
                self.poll_buttons(start + 5300, 3, False)
                self.assertEqual([address for address, _ in self.calls].count(0xB090), 1)

    def test_physical_holds_respect_short_release_and_invalid_expander_sample(self):
        self.setup_route_buttons(self.audio_route_buttons_v2)
        self.poll_buttons(1000, 1, False)
        self.poll_buttons(1600, 1, False)
        self.poll_buttons(4979, 1, False)
        self.poll_buttons(4999, 3, False)
        self.poll_buttons(5000, 3, False)
        self.assertEqual(self.byte(BRIDGE_STATE + 2), 0)
        self.assertIn(0x18550, [address for address, _ in self.calls])

        self.poll_buttons(6000, 1, False)
        self.poll_buttons(9999, 1, False)
        self.poll_buttons(10000, 1, False, read_error=1)
        self.assertEqual(self.byte(BRIDGE_STATE + 2), 0)
        self.poll_buttons(10020, 1, False)
        self.assertEqual(self.byte(BRIDGE_STATE + 2), 1)

    def test_physical_hold_state_starts_empty_and_preserves_atr4(self):
        self.setup_route_buttons(self.audio_route_buttons_v2)
        start = 0x2001C510
        self.cpu.mem_write(start, b"\xff" * 152)
        self.stubs[0x13B2C] = lambda: 0
        self.run_code(0x1161A, 0x1161E)
        self.assertEqual(self.cpu.mem_read(start, 152), bytes(24) + b"ATR4" + bytes(124))

    def test_physical_hold_also_switches_if_released_at_four_seconds(self):
        self.setup_route_buttons(self.audio_route_buttons_v2)
        self.poll_buttons(1000, 1, False)
        self.poll_buttons(1600, 1, False)
        self.poll_buttons(4999, 1, False)
        self.poll_buttons(5000, 3, False)
        self.assertEqual(self.byte(BRIDGE_STATE + 2), 1)
        self.assertNotIn(0x18550, [address for address, _ in self.calls])

    def test_dp_first_addressed_startup_and_key(self):
        self.cpu.mem_write(0, self.dp_first)
        self.stub_toggle_io()
        self.cpu.mem_write(ADDRESS, struct.pack("<I", 3))
        self.cpu.mem_write(USB_ENABLED, b"\x01")
        self.status = 0  # a direct host need not present converter E08B status
        for _ in range(165):
            self.run_code(0x3140)
        self.assertEqual(self.byte(USB_ENABLED), 1)
        self.run_code(0x3140)
        self.assertEqual(self.byte(USB_ENABLED), 0)
        self.assertEqual(self.byte(BRIDGE_STATE + 2), 1)
        self.assertEqual(self.byte(SESSION), 0xA9)
        self.press_brightness_plus()
        self.assertIn(0x12648, [addr for addr, arg in self.calls])
        self.assertEqual(self.byte(BRIDGE_STATE + 2), 0)
        self.calls.clear()
        for _ in range(170):
            self.run_code(0x3140)
        self.assertEqual(self.calls, [])
        self.press_brightness_plus()
        self.assertEqual(self.byte(BRIDGE_STATE + 2), 1)

    def test_dp_first_unaddressed_and_adapter_fallback_keeps_key(self):
        self.cpu.mem_write(0, self.dp_first)
        self.stub_toggle_io()
        for addr in (0xAFC8, 0x16A3A, 0x112C8, 0x1133C):
            self.stubs[addr] = lambda: 0
        for status in (0xA1, 0xA3):
            with self.subTest(status=status):
                self.status = status
                self.cpu.mem_write(SESSION, b"\xa5")
                self.cpu.mem_write(0x20000488, struct.pack("<I", 5))  # bridge ready
                self.run_code(0x3140)
                self.assertEqual(self.byte(SESSION), 0xA7)
                self.assertEqual(self.byte(BRIDGE_STATE + 2), 1)
                self.assertEqual(self.byte(0x20000CBD), 0)
                self.assertEqual(self.byte(0x20000D92) & 8, 0)
                self.press_brightness_plus()
                self.assertEqual(self.byte(BRIDGE_STATE + 2), 0)
                self.run_code(0x3140)
                self.assertEqual(self.byte(SESSION), 0xA9)

    def test_immediate_after_address_and_manual_usb_return(self):
        self.cpu.mem_write(0, self.immediate)
        self.stub_toggle_io()
        self.cpu.mem_write(USB_ENABLED, b"\x01")
        self.status = 0
        self.run_code(0x3140)
        self.assertEqual(self.byte(SESSION), 0)
        self.assertEqual(self.byte(USB_ENABLED), 1)
        self.cpu.mem_write(ADDRESS, struct.pack("<I", 7))
        self.run_code(0x3140)
        self.assertEqual(self.byte(SESSION), 0xA9)
        self.assertEqual(self.byte(BRIDGE_STATE + 2), 1)
        self.assertEqual(self.byte(USB_ENABLED), 0)
        self.press_brightness_plus()
        self.assertIn(0x12648, [addr for addr, arg in self.calls])
        self.assertEqual(self.byte(BRIDGE_STATE + 2), 0)
        self.calls.clear()
        for _ in range(170):
            self.run_code(0x3140)
        self.assertEqual(self.calls, [])
        self.press_brightness_plus()
        self.assertEqual(self.byte(BRIDGE_STATE + 2), 1)

    def test_immediate_keeps_no_data_fallback(self):
        self.cpu.mem_write(0, self.immediate)
        for _ in range(165):
            self.run_code(0x3140)
        self.assertEqual(self.byte(SESSION), 165)
        self.run_code(0x3140, 0x3196)
        self.assertEqual(self.byte(SESSION), 166)

    def test_immediate_only_changes_first_poll_threshold_and_crc(self):
        self.assertEqual([i for i, (a, b) in enumerate(zip(self.dp_first, self.immediate))
                          if a != b], [0, 1, 2, 3, 0x1DC5A])
        self.assertEqual(self.immediate[0x1DC5A:0x1DC5C], bytes.fromhex("012c"))

    def test_dp_first_early_key_consumes_pending_preference(self):
        self.cpu.mem_write(0, self.dp_first)
        self.stub_toggle_io()
        self.cpu.mem_write(ADDRESS, struct.pack("<I", 7))
        self.press_brightness_plus()
        self.press_brightness_plus()
        self.assertEqual(self.byte(SESSION), 0xA9)
        self.assertEqual(self.byte(BRIDGE_STATE + 2), 0)
        self.calls.clear()
        self.run_code(0x3140)
        self.assertEqual(self.calls, [])

    def test_dp_first_prefers_existing_route_when_cached_or_live_dp(self):
        self.cpu.mem_write(0, self.dp_first)
        self.cpu.mem_write(ADDRESS, struct.pack("<I", 7))
        for cached, live in ((1, 1), (1, 0), (0, 1), (0, 2)):
            with self.subTest(cached=cached, live=live):
                self.cpu.mem_write(SESSION, b"\xa5")
                self.cpu.mem_write(BRIDGE_STATE + 2, bytes([cached]))
                self.stubs[0x47A6] = lambda: live
                self.run_code(0x3140)
                self.assertEqual(self.byte(SESSION), 0xA9)
                self.assertEqual(self.byte(BRIDGE_STATE + 2), cached)

    def test_dp_first_prepare_preserves_usb_and_stock_arguments(self):
        self.cpu.mem_write(0, self.dp_first)
        for op, target in ((62, 0x133CC), (110, 0x4460)):
            with self.subTest(op=op):
                self.cpu.mem_write(SESSION, b"\xa5")
                self.cpu.reg_write(UC_ARM_REG_R0, op)
                self.cpu.reg_write(UC_ARM_REG_R1, 0x20001000)
                self.cpu.reg_write(UC_ARM_REG_R2, 0x20001100)
                self.run_code(0xD84C, target)
                self.assertEqual(self.cpu.reg_read(UC_ARM_REG_R0), 0x20001000)
                self.assertEqual(self.cpu.reg_read(UC_ARM_REG_R1), 0x20001100)
                self.assertEqual(self.byte(SESSION), 0xA9)
                self.run_code(0x3140)
                self.assertEqual(self.calls, [])

    def test_dp_first_changes_only_crc_and_guarded_bytes(self):
        expected = bytearray(self.image)
        for _, offset, old, new in build_mcu.DP_FIRST_RECORDS:
            self.assertEqual(expected[offset:offset + len(old)], old)
            expected[offset:offset + len(old)] = new
        expected[:4] = self.dp_first[:4]
        self.assertEqual(expected, self.dp_first)
        self.assertEqual(sum(a != b for a, b in zip(self.image, self.dp_first)), 101)
        self.assertEqual(sum(a != b for a, b in zip(self.stock, self.dp_first)), 619)
        with self.assertRaisesRegex(SystemExit, "exact published MCU"):
            build_mcu.build_dp_first(self.stock)

    def test_crc_and_patch_guards(self):
        damaged = bytearray(self.stock)
        damaged[0x313E] ^= 1
        with self.assertRaisesRegex(SystemExit, "CRC"):
            build_mcu.verify_stock(damaged)
        build_mcu.fix_container_crc(damaged)
        with self.assertRaisesRegex(SystemExit, "SHA-256"):
            build_mcu.verify_stock(damaged)
        with self.assertRaisesRegex(SystemExit, "stock guard mismatch"):
            build_mcu.build(damaged)

    def test_dp_display_and_audio_edids(self):
        stock = (FIRMWARE / "1140").read_bytes()
        build_dp.verify_stock(stock)
        build_dp.verify_output(stock, build_dp.build(stock))

    def test_dp_audio_relock_keeps_checking_after_initial_window(self):
        stock = (FIRMWARE / "1140").read_bytes()
        baseline = build_dp.build(stock)
        relock = build_dp.build(stock, audio_relock=True)
        build_dp.verify_output(stock, relock, audio_relock=True)
        self.assertEqual(
            {i for i, (old, new) in enumerate(zip(baseline, relock)) if old != new},
            {0, 1, 2, 3, build_dp.TAG_OFF, 0x84CA, 0x84CB, 0x84CC},
        )
        add, immediate, jnc, displacement = relock[0x84CA:0x84CE]
        self.assertEqual((add, jnc), (0x24, 0x50))
        self.assertEqual(relock[0x84CE:0x84D1], bytes.fromhex("12c3d0"))
        self.assertEqual(relock[0x84CE + displacement], 0x22)  # RET
        self.assertEqual([count for count in range(256) if count + immediate > 255],
                         list(range(5, 256)))

    def test_3d_button_toggles_b6_and_keeps_dp_audio(self):
        self.cpu.mem_write(0, self.immediate)
        self.cpu.mem_write(BRIDGE_STATE + 2, b"\x01")
        self.stubs.update(dict.fromkeys(
            (0x9EB0, 0xB090, 0x16A3A, 0xB348, 0x9568, 0x1423C), lambda: 0,
        ))
        writes = []

        def write_bridge():
            writes.append((self.cpu.reg_read(UC_ARM_REG_R0), self.cpu.reg_read(UC_ARM_REG_R1)))
            return 0

        self.stubs[0x59E0] = write_bridge
        for b6, mode in ((1, 1), (0, 0)):
            writes.clear()
            self.run_code(0x187C8)
            self.assertEqual(self.byte(BRIDGE_STATE + 2), 1)
            self.assertEqual(self.byte(BRIDGE_STATE + 3), mode)
            self.assertEqual([value for reg, value in writes if reg == 0xB6], [b6])
            self.assertNotIn((0xB8, 0), writes)

    def test_dp_mode_audio_reset_uses_existing_dp_only_retrain_gate(self):
        stock = (FIRMWARE / "1140").read_bytes()
        relock = build_dp.build(stock, audio_relock=True)
        reset = build_dp.build(stock, mode_audio_reset=True)
        build_dp.verify_output(stock, reset, mode_audio_reset=True)
        self.assertEqual(
            {i for i, (old, new) in enumerate(zip(relock, reset)) if old != new},
            {0, 1, 2, 3, build_dp.TAG_OFF, 0x9189, 0x918A} | set(range(0x91A6, 0x91B4)),
        )
        self.assertEqual(reset[0x8775:0x8778], bytes.fromhex("12913c"))
        self.assertEqual(reset[0x917C:0x918C], bytes.fromhex("12c1fb 90e0b8e0 b40105 7f01 129166 22"))
        helper = build_dp.HDR + int.from_bytes(reset[0x9189:0x918B], "big")
        self.assertEqual(helper, 0x91A6)
        self.assertEqual(reset[helper:helper + 14], bytes.fromhex("12c54b e4 9006ea f0 a3 f0 ff 02c138"))

    def test_dp_clock_hold_changes_only_the_locked_stream_loss_path(self):
        stock = (FIRMWARE / "1140").read_bytes()
        reset = build_dp.build(stock, mode_audio_reset=True)
        hold = build_dp.build(stock, audio_clock_hold=True)
        build_dp.verify_output(stock, hold, audio_clock_hold=True)
        self.assertEqual(
            {i for i, (old, new) in enumerate(zip(reset, hold)) if old != new},
            {0, 1, 2, 3, build_dp.TAG_OFF} | set(range(0x84F5, 0x84F9)) | set(range(0x91B4, 0x91C4)),
        )
        self.assertEqual(hold[0x84EF:0x84F9], bytes.fromhex("12c3d0 ef 7018 029174 00"))
        helper = build_dp.HDR + int.from_bytes(hold[0x84F6:0x84F8], "big")
        self.assertEqual(helper, 0x91B4)
        # DP returns on lock loss. Other routes take the stock shutdown and retry path.
        self.assertEqual(hold[helper:helper + 16], bytes.fromhex("90e0b8 e0 b40101 22 e4 ff 12c138 0284b9"))

    def test_dp_bridge_trace_preserves_stock_detector_and_flag(self):
        stock = (FIRMWARE / "1140").read_bytes()
        image = build_dp.build(stock, audio_bridge_trace=True)
        build_dp.verify_output(stock, image, audio_bridge_trace=True)
        self.assertEqual(image[0xBA5E:0xBA66], bytes.fromhex("129184 129df3 80f8"))
        self.assertEqual(image[0xC30A:0xC30E], stock[0xC30A:0xC30E])
        self.assertEqual(image[0x872C:0x8732], stock[0x872C:0x8732])
        self.assertEqual(image[0x91D8:0x91DE], bytes.fromhex("b40126 12a97d"))
        self.assertEqual(image[0x9201:0x9209], bytes.fromhex("90e085e0 5401 4e f0"))
        self.assertEqual(image[0x9217:0x921A], bytes.fromhex("0248f9"))

    def test_dp_fast_resume_keeps_atr4_and_the_stock_rate_comparison(self):
        stock = (FIRMWARE / "1140").read_bytes()
        trace = build_dp.build(stock, audio_bridge_trace=True)
        image = build_dp.build(stock, audio_fast_resume=True)
        build_dp.verify_output(stock, image, audio_fast_resume=True)
        changed = {i for i, (before, after) in enumerate(zip(trace, image)) if before != after}
        self.assertLessEqual(changed, {0, 1, 2, 3, build_dp.TAG_OFF}
                             | set(range(0x3470, 0x34DC)) | set(range(0xC30A, 0xC30D)))
        self.assertEqual(image[0xC30A:0xC30D], bytes.fromhex("023430"))
        self.assertEqual(image[0x3470:0x3473], bytes.fromhex("12a97d"))
        self.assertEqual(image[0x34D9:0x34DC], bytes.fromhex("02c2cd"))
        self.assertEqual(image[0xC30D:0xC323], stock[0xC30D:0xC323])

    def test_dp_mute_probe_only_replaces_the_fast_resume_sampler(self):
        stock = (FIRMWARE / "1140").read_bytes()
        resume = build_dp.build(stock, audio_fast_resume=True)
        image = build_dp.build(stock, audio_mute_probe=True)
        build_dp.verify_output(stock, image, audio_mute_probe=True)
        changed = {i for i, (before, after) in enumerate(zip(resume, image)) if before != after}
        self.assertLessEqual(changed, {0, 1, 2, 3, build_dp.TAG_OFF} | set(range(0x91C4, 0x921C)))
        first = 0x91DA
        for address, mask in ((0x30, 2), (0x35, 4), (0x36, 8), (0x37, 16)):
            self.assertEqual(image[first:first + 11], bytes((0x90, 0xE7, address, 0xE0, 0x30,
                                                            0xE4, 4, 0xEE, 0x44, mask, 0xFE)))
            first += 11
        self.assertEqual(image[first:first + 8], bytes.fromhex("90e085e0 5401 4e f0"))

    def test_audio_trace_samples_and_retains_usb_readout(self):
        self.cpu.mem_write(0, build_mcu.build_audio_trace(self.immediate))
        ram = build_mcu.AUDIO_TRACE_RAM
        time_ticks, page = 0, 0xE0
        regs = {0xC67C: 0x30, 0xE2EB: 0x80, 0xD423: 3, 0xD424: 0x33}

        def select_page():
            nonlocal page
            self.assertEqual(self.cpu.reg_read(UC_ARM_REG_R0), 0xFF)
            page = self.cpu.reg_read(UC_ARM_REG_R1)
            return 0

        def read_i2c():
            addr = page * 256 + self.cpu.reg_read(UC_ARM_REG_R2)
            ptr = struct.unpack("<I", self.cpu.mem_read(self.cpu.reg_read(UC_ARM_REG_SP), 4))[0]
            if addr not in regs:
                return 1
            self.cpu.mem_write(ptr, bytes([regs[addr]]))
            return 0

        self.stubs.update({0x13B2C: lambda: 0, 0x3140: lambda: 0,
                           0x7C4C: lambda: time_ticks, 0x59E0: select_page, 0x7D14: read_i2c})
        self.run_code(build_mcu.AUDIO_TRACE_INIT)
        self.assertEqual(self.cpu.mem_read(ram, 40), b"ATR1" + bytes(36))
        self.cpu.mem_write(BRIDGE_STATE + 2, b"\x01")
        self.cpu.mem_write(0x20000E4F, b"\xc1")
        time_ticks = 4000
        self.run_code(build_mcu.AUDIO_TRACE_POLL)
        self.assertEqual(page, 0xE0)
        self.assertEqual(self.cpu.mem_read(ram + 8, 8), struct.pack("<H6B", 1000, 15, 0, 48, 128, 3, 51))

        self.cpu.mem_write(BRIDGE_STATE + 4, b"\x01")
        self.cpu.mem_write(0x20000E4F, b"\xc2")
        regs.update({0xC67C: 0, 0xE2EB: 0, 0xD423: 0})
        del regs[0xD424]
        time_ticks = 4100  # A 3D edge bypasses the normal poll interval.
        self.run_code(build_mcu.AUDIO_TRACE_POLL)
        self.assertEqual(self.cpu.mem_read(ram + 16, 8), struct.pack("<H6B", 1025, 7, 1, 0, 0, 0, 0))
        self.assertEqual(self.byte(ram + 4), 3)
        time_ticks += 8000
        self.run_code(build_mcu.AUDIO_TRACE_POLL)
        self.assertEqual(self.byte(ram + 4), 7)
        self.assertEqual(self.cpu.mem_read(ram + 6, 2), b"\xc1\xc2")
        self.assertEqual(self.cpu.mem_read(ram + 24, 8), self.cpu.mem_read(ram + 32, 8))

        self.cpu.mem_write(BRIDGE_STATE + 2, b"\x00")
        retained = self.cpu.mem_read(ram, 40)
        time_ticks += 2000
        self.run_code(build_mcu.AUDIO_TRACE_POLL)
        self.assertEqual(self.cpu.mem_read(ram, 40), retained)

        payload, length = 0x20001000, 0x20001100
        self.cpu.mem_write(payload, b"\xa7\xad" + bytes(39))
        self.cpu.reg_write(UC_ARM_REG_R0, payload)
        self.cpu.reg_write(UC_ARM_REG_R1, length)
        self.cpu.reg_write(UC_ARM_REG_R4, 0x2B)
        self.run_code(build_mcu.AUDIO_TRACE_READ)
        self.assertEqual(self.cpu.mem_read(length, 2), b"\x28\x00")
        self.assertEqual(self.cpu.mem_read(payload, 41), retained + b"\x00")

    def test_audio_idle_trace_preserves_lock_loss_until_usb_readout(self):
        self.cpu.mem_write(0, build_mcu.build_audio_trace(self.immediate, idle=True))
        ram = build_mcu.AUDIO_TRACE_RAM
        time_ticks = 0
        regs = {0xC67C: 0x30, 0xE2EB: 0x80, 0xD423: 3, 0xD424: 0x33}

        def read_bridge():
            value = regs.get(self.cpu.reg_read(UC_ARM_REG_R0))
            return 0 if value is None else 0x100 | value

        self.stubs.update({
            0x13B2C: lambda: 0, 0x3140: lambda: 0, 0x59E0: lambda: 0,
            0x7C4C: lambda: time_ticks, 0x1DE22: read_bridge,
        })
        self.run_code(build_mcu.AUDIO_TRACE_INIT)
        self.assertEqual(self.cpu.mem_read(ram, 4), b"ATR2")
        self.cpu.mem_write(BRIDGE_STATE + 2, b"\x01")
        self.cpu.mem_write(0x20000E4F, b"\xc1")
        time_ticks = 4000
        self.run_code(build_mcu.AUDIO_TRACE_POLL)
        self.assertEqual(self.byte(ram + 4) & 0x0F, 8)  # A brief lock cannot arm the trace.
        time_ticks = 12000
        self.run_code(build_mcu.AUDIO_TRACE_POLL)
        self.assertEqual(self.cpu.mem_read(ram + 8, 8), struct.pack("<H6B", 3000, 15, 0xC1, 48, 128, 3, 51))

        del regs[0xE2EB]
        time_ticks += 1000
        self.run_code(build_mcu.AUDIO_TRACE_POLL)
        self.assertEqual(self.byte(ram + 4) & 0x0F, 9)  # A read error isn't an unlock.
        regs.update({0xE2EB: 0, 0xC67C: 0, 0xD423: 0, 0xD424: 0})
        time_ticks += 1000
        self.run_code(build_mcu.AUDIO_TRACE_POLL)
        self.assertEqual(self.cpu.mem_read(ram + 16, 8), struct.pack("<H6B", 3500, 15, 0xC1, 0, 0, 0, 0))
        self.cpu.mem_write(0x20000E4F, b"\xc2")
        time_ticks += 8000
        self.run_code(build_mcu.AUDIO_TRACE_POLL)
        self.assertEqual(self.cpu.mem_read(ram + 24, 8), struct.pack("<H6B", 5500, 15, 0xC2, 0, 0, 0, 0))

        self.cpu.mem_write(BRIDGE_STATE + 2, b"\x00")
        retained = self.cpu.mem_read(ram, 40)
        self.assertEqual(self.byte(ram + 4), 15)
        time_ticks += 1000
        self.run_code(build_mcu.AUDIO_TRACE_POLL)
        self.assertEqual(self.cpu.mem_read(ram, 40), retained)

    def test_timed_audio_trace_wraps_and_reads_frozen_amplifier_pages(self):
        self.cpu.mem_write(0, build_mcu.build_audio_trace(self.immediate, timed=True))
        ram = build_mcu.AUDIO_TRACE_RAM
        ticks = 0
        selector = 0x20000DD8
        registers = {(0x34, 1): 0x0113, (0x34, 4): 0x0102, (0x35, 1): 0x0020, (0x35, 4): 0x0001}

        def read_amp():
            self.assertEqual(self.cpu.reg_read(UC_ARM_REG_R0), 0x40080000)
            address = self.cpu.reg_read(UC_ARM_REG_R1)
            register = self.cpu.reg_read(UC_ARM_REG_R2)
            self.assertIn(register, (1, 4))
            value = registers.get((address, register))
            if value is None:
                return 1
            output = self.cpu.reg_read(UC_ARM_REG_R3)
            self.cpu.mem_write(output, struct.pack("<H", value))
            return 0

        self.stubs.update({0x13B2C: lambda: 0, 0x3140: lambda: 0, 0x7C4C: lambda: ticks, 0x89AC: read_amp})
        self.run_code(build_mcu.AUDIO_TRACE_INIT)
        self.assertEqual(self.cpu.mem_read(ram, 128), b"ATR3" + bytes(124))
        self.cpu.mem_write(BRIDGE_STATE + 2, b"\x01")
        self.cpu.mem_write(0x200003BE, b"\x01\x01")
        self.cpu.mem_write(selector, b"\x35")
        ticks = 3999
        self.run_code(build_mcu.AUDIO_TIMED_TRACE_POLL)
        self.assertEqual(self.byte(ram + 5), 0)
        for second in range(1, 32):
            ticks = second * 4000
            if second == 31:
                del registers[0x35, 4]
            self.run_code(build_mcu.AUDIO_TIMED_TRACE_POLL)
        self.assertEqual(self.byte(selector), 0x35)
        self.assertEqual(self.cpu.mem_read(ram, 8), b"ATR3\x03\x1c\0\0")
        self.assertEqual(self.cpu.mem_read(ram + 8, 12), struct.pack("<HBBHBBHBB", 29000, 0xDB, 0xA4,
                                                                  30000, 0xDB, 0xA4, 31000, 0xDB, 0))
        self.assertEqual(self.cpu.mem_read(ram + 20, 4), struct.pack("<HBB", 4000, 0xDB, 0xA4))

        self.cpu.mem_write(BRIDGE_STATE + 2, b"\x00")
        frozen = self.cpu.mem_read(ram, 128)
        ticks += 4000
        self.run_code(build_mcu.AUDIO_TIMED_TRACE_POLL)
        self.assertEqual(self.cpu.mem_read(ram, 128), frozen)

        payload, length = 0x20001000, 0x20001100
        for page in range(4):
            self.cpu.mem_write(payload, b"\xa7\xad" + bytes([page]) + bytes(33) + b"\x55")
            self.cpu.reg_write(UC_ARM_REG_R0, payload)
            self.cpu.reg_write(UC_ARM_REG_R1, length)
            self.cpu.reg_write(UC_ARM_REG_R4, 0x2B)
            self.run_code(build_mcu.AUDIO_TRACE_READ)
            self.assertEqual(self.cpu.mem_read(length, 2), b"\x24\x00")
            header = frozen[:6] + bytes([page]) + frozen[7:8]
            start = 8 + page * 28
            self.assertEqual(self.cpu.mem_read(payload, 37), header + frozen[start:start + 28] + b"\x55")

    def test_dp_clock_mute_retries_and_releases_only_its_own_mute(self):
        trace = build_mcu.build_audio_trace(self.immediate, timed=True)
        self.cpu.mem_write(0, build_mcu.build_audio_clock_mute(trace))
        ram = build_mcu.AUDIO_TRACE_RAM
        ticks = 4000
        reads = []
        writes = []
        failed_writes = set()
        registers = {(0x34, 1): 0x111, (0x35, 1): 0x111, (0x34, 4): 0xB248, (0x35, 4): 0xB360}

        def read_amp():
            self.assertEqual(self.cpu.reg_read(UC_ARM_REG_R0), 0x40080000)
            key = (self.cpu.reg_read(UC_ARM_REG_R1), self.cpu.reg_read(UC_ARM_REG_R2))
            reads.append(key)
            if key not in registers:
                return 1
            self.cpu.mem_write(self.cpu.reg_read(UC_ARM_REG_R3), struct.pack("<H", registers[key]))
            return 0

        def write_amp():
            self.assertEqual(self.cpu.reg_read(UC_ARM_REG_R0), 0x40080000)
            address = self.cpu.reg_read(UC_ARM_REG_R1)
            self.assertEqual(self.cpu.reg_read(UC_ARM_REG_R2), 4)
            value = self.cpu.reg_read(UC_ARM_REG_R3)
            writes.append((address, value))
            if address in failed_writes:
                return 1
            registers[address, 4] = value
            return 0

        def poll(elapsed=32):
            nonlocal ticks
            ticks += elapsed
            self.run_code(build_mcu.AUDIO_TIMED_TRACE_POLL)

        self.stubs.update({0x13B2C: lambda: 0, 0x3140: lambda: 0, 0x7C4C: lambda: ticks,
                           0x89AC: read_amp, 0x8A0A: write_amp})
        self.run_code(build_mcu.AUDIO_TRACE_INIT)
        self.cpu.mem_write(BRIDGE_STATE + 2, b"\x01")
        self.cpu.mem_write(0x200003BE, b"\x01\x01")
        self.cpu.mem_write(0x20000E4F, b"\xC1")
        poll()
        self.assertEqual(writes, [])
        read_count = len(reads)
        registers[0x34, 1] = 0x101  # Clock unstable even though the amp is still switching.
        registers[0x35, 1] = 0x021
        poll(31)
        self.assertEqual(len(reads), read_count)
        failed_writes.add(0x34)
        poll(1)
        self.assertEqual(writes, [(0x34, 0xB348)])
        self.assertEqual(registers[0x34, 4], 0xB248)
        failed_writes.clear()
        registers[0x34, 1] = 0x021  # Clock now absent.
        poll(4000)
        self.assertEqual(writes, [(0x34, 0xB348)] * 2)
        self.assertEqual(self.cpu.mem_read(ram + 14, 2), b"\xe1\xe1")  # ATR3 records both mutes.

        registers[0x34, 1] = 0x011  # PLL and clock recovered, switching still off.
        poll()
        poll()
        self.assertEqual(len(writes), 2)
        registers[0x34, 1] = registers[0x35, 1] = 0x111
        poll()
        del registers[0x34, 1]  # A failed read breaks recovery confirmation.
        poll()
        registers[0x34, 1] = 0x111
        poll()
        self.assertEqual(len(writes), 2)
        failed_writes.add(0x34)
        poll()
        self.assertEqual(writes[-1], (0x34, 0xB248))
        self.assertEqual(registers[0x34, 4], 0xB348)
        failed_writes.clear()
        poll()
        self.assertEqual(len(writes), 3)
        poll()
        self.assertEqual(writes[2:], [(0x34, 0xB248)] * 2)
        self.assertEqual(registers[0x34, 4], 0xB248)
        self.assertEqual(registers[0x35, 4], 0xB360)  # Stock mute remains set.

        registers[0x34, 1] = 0x21
        registers[0x35, 4] = 0xB262
        registers[0x35, 1] = 0x21
        poll()
        self.assertEqual(writes[4:], [(0x34, 0xB348)])  # Skip powered-down amps.
        registers[0x35, 4] = 0xB260
        poll()
        self.assertEqual(writes[-1], (0x35, 0xB360))
        registers[0x35, 1] = 0x111
        poll()
        poll()
        self.assertEqual(writes[-1], (0x35, 0xB260))
        self.assertEqual(registers[0x34, 4], 0xB348)  # Left clock is still absent.
        self.cpu.mem_write(BRIDGE_STATE + 2, b"\x00")
        capture = self.cpu.mem_read(ram, 124)
        read_count = len(reads)
        poll()
        self.assertEqual(len(reads), read_count)
        self.assertEqual(len(writes), 7)
        self.assertEqual(self.cpu.mem_read(ram, 128), capture + bytes(4))

    def test_edge_trace_records_before_mute_and_keeps_recent_transitions(self):
        trace = build_mcu.build_audio_trace(self.immediate, timed=True)
        self.cpu.mem_write(0, build_mcu.build_audio_clock_mute(trace, edge_trace=True))
        ram = build_mcu.AUDIO_TRACE_RAM
        ticks = 65520 * 4
        status = {0x34: 0x93, 0x35: 0x93}
        self.stubs.update({
            0x13B2C: lambda: 0,
            0x7C4C: lambda: ticks,
            0x1DF4C: lambda: status[self.cpu.reg_read(UC_ARM_REG_R0)],
            build_mcu.AUDIO_CLOCK_MUTE_CODE: lambda: 0,
        })
        self.run_code(build_mcu.AUDIO_TRACE_INIT)
        self.cpu.mem_write(BRIDGE_STATE + 2, b"\x01")
        self.cpu.mem_write(0x200003BE, b"\x01\x01")
        self.calls.clear()
        self.run_code(build_mcu.AUDIO_TIMED_TRACE_POLL)
        self.assertEqual([address for address, _ in self.calls],
                         [0x7C4C, 0x1DF4C, 0x1DF4C, build_mcu.AUDIO_CLOCK_MUTE_CODE])

        def poll(elapsed=32):
            nonlocal ticks
            ticks += elapsed
            self.run_code(build_mcu.AUDIO_TIMED_TRACE_POLL)

        status[0x34] = 0x83  # No stable clock on the left, still switching.
        poll(31)
        self.assertEqual(self.byte(ram + 5), 1)
        poll(1)
        status[0x34] = status[0x35] = 0xE1
        poll()
        self.assertEqual(self.cpu.mem_read(ram + 8, 12),
                         struct.pack("<HBBHBBHBB", 65520, 0x93, 0x93, 65528, 0x83, 0x93, 0, 0xE1, 0xE1))
        poll(3999)  # Unchanged status does not consume a row before the 1 s heartbeat.
        self.assertEqual(self.byte(ram + 5), 3)
        poll(33)
        self.assertEqual(self.cpu.mem_read(ram + 20, 4), struct.pack("<HBB", 1008, 0xE1, 0xE1))
        for index in range(31):
            status[0x34] = status[0x35] = 0x93 if index % 2 == 0 else 0xE1
            poll()
        self.assertEqual(self.cpu.mem_read(ram, 6), b"ATR3\x07\x1c")
        self.assertEqual(self.cpu.mem_read(ram + 8 + 6 * 4, 4), struct.pack("<HBB", 1256, 0x93, 0x93))

        frozen = self.cpu.mem_read(ram, 124)
        self.cpu.mem_write(BRIDGE_STATE + 2, b"\x00")
        self.calls.clear()
        poll()
        self.assertEqual([address for address, _ in self.calls], [build_mcu.AUDIO_CLOCK_MUTE_CODE])
        self.assertEqual(self.cpu.mem_read(ram, 124), frozen)

    def test_bridge_trace_reads_the_receiver_before_the_amplifier(self):
        trace = build_mcu.build_audio_trace(self.immediate, timed=True)
        self.cpu.mem_write(0, build_mcu.build_audio_clock_mute(trace, bridge_trace=True))
        ram = build_mcu.AUDIO_TRACE_RAM
        ticks, receiver, amp, page_error, read_error = 4000, 0xBE, 0x93, 0, 0

        def select_page():
            self.assertEqual(self.cpu.reg_read(UC_ARM_REG_R0), 0xFF)
            self.assertEqual(self.cpu.reg_read(UC_ARM_REG_R1), 0xE0)
            return page_error

        def read_receiver():
            self.assertEqual(self.cpu.reg_read(UC_ARM_REG_R0), 0x40081000)
            self.assertEqual(self.cpu.reg_read(UC_ARM_REG_R1), 0x43)
            self.assertEqual(self.cpu.reg_read(UC_ARM_REG_R2), 0x85)
            self.assertEqual(self.cpu.reg_read(UC_ARM_REG_R3), 1)
            stack = self.cpu.reg_read(UC_ARM_REG_SP)
            dest, size, timeout = struct.unpack("<III", self.cpu.mem_read(stack, 12))
            self.assertEqual((size, timeout), (1, 50))
            self.cpu.mem_write(dest, bytes([receiver]))
            return read_error

        self.stubs.update({
            0x13B2C: lambda: 0,
            0x7C4C: lambda: ticks,
            0x59E0: select_page,
            0x7D14: read_receiver,
            0x1DF4C: lambda: amp,
            build_mcu.AUDIO_CLOCK_MUTE_CODE: lambda: 0,
        })
        self.run_code(build_mcu.AUDIO_TRACE_INIT)
        self.cpu.mem_write(BRIDGE_STATE + 2, b"\x01")
        self.cpu.mem_write(0x200003BE, b"\x01\x01")
        self.calls.clear()
        self.run_code(build_mcu.AUDIO_TIMED_TRACE_POLL)
        self.assertEqual(self.calls[1:4], [(0x59E0, 0xFF), (0x7D14, 0x40081000), (0x1DF4C, 0x34)])
        for receiver, amp, page_error, read_error in (
            (0xBC, 0x93, 0, 0), (0xB4, 0xA1, 0, 0), (0x02, 0xE1, 0, 0),
            (0xFE, 0x93, 0, 0), (0xBE, 0x93, 0, 1), (0xBE, 0x93, 1, 0),
        ):
            ticks += 32
            self.run_code(build_mcu.AUDIO_TIMED_TRACE_POLL)
        self.assertEqual(self.cpu.mem_read(ram, 6), b"ATR4\x07\x07")
        self.assertEqual(
            list(struct.iter_unpack("<HBB", self.cpu.mem_read(ram + 8, 28))),
            [(1000, 0x93, 0xBE), (1008, 0x93, 0xBC), (1016, 0xA1, 0xB4),
             (1024, 0xE1, 2), (1032, 0x93, 0xFD), (1040, 0x93, 0xFF), (1048, 0x93, 0xFE)],
        )
        self.assertNotIn(0x7D14, [address for address, _ in self.calls[-4:]])

    def test_bridge_trace_display_getter_ignores_marked_telemetry(self):
        trace = build_mcu.build_audio_trace(self.immediate, timed=True)
        self.cpu.mem_write(0, build_mcu.build_audio_clock_mute(trace, bridge_trace=True))
        self.stubs.update({0x59E0: lambda: 0, 0x5244: lambda: status})
        for status, expected in ((0, 0), (1, 1), (2, 2), (0xFF, 2),
                                 (0xA2, 0), (0xA3, 1), (0xBE, 0), (0xBF, 1)):
            self.run_code(0x56B8)
            self.assertEqual(self.cpu.reg_read(UC_ARM_REG_R0), expected)
