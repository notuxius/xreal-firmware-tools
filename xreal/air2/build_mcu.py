#!/usr/bin/env python3
"""Build the XREAL Air 2 / Air 2 Pro MCU image from official firmware.

Relative to the official image, this build automatically performs the official
full DP-audio transition when USB data is absent, including the audio EDID/HPD
re-enumeration and saved-volume restore. It also keeps mode changes consistent
across native RGB and true 720p YCbCr input. Native 1080p selects the four-lane
RGB panel table, 120 Hz selects its matching timing table, and 720p selects the
official scaler/YUV table. Mode 1 clears the mode-9-only state before returning
to normal 2D. Initial setup and mode 1 reuse the official full-register apply
and OLED close/open lifecycle. On the tested powered HDMI converter, each exact
720p disconnect edge toggles live B6 once; reconnect is left to the converter's
delayed retrain.

The tool is offline and standard-library only.  It accepts one known official
Air 2-family MCU container, applies guarded records and pins the complete output.  It
never downloads firmware or touches hardware.
"""

from __future__ import annotations

import argparse
import hashlib
import struct
from pathlib import Path


POLY = 0xF4ACFB13
HDR_LEN = 0x40

SIZE = 151_160
STOCK_SHA256 = "C07633E97215346468A18F5306A10F800388A80CCD7DCFE800D468F4AB1BFD49"
STOCK_CRC = 0x169B7EE4
OUTPUT_SHA256 = "950CA9535AFBD02C40D97A167DB06ECFAEEBC35F6ADCCDED81829CC44A8BE4C9"
OUTPUT_CRC = 0xCFB740BE
EXPECTED_DIFFS = 526
DP_FIRST_SHA256 = "39819E2083911E59AEA844EE484DB52DE8B5B0C9A49BD7EC317DF80F7BD521E3"
DP_FIRST_CRC = 0x29CB1C30
IMMEDIATE_DP_SHA256 = "DCF383BE0684FB0EBC6126CA1AD031594DB4D77F9B16BE6D5C248EA69F5769ED"
IMMEDIATE_DP_CRC = 0xCEE99416
AUDIO_TRACE_SHA256 = "EDBEE02A2EE78CCBA432CFBF23ACB9DB6AE8A6A2A1BA3904DA701E0A97828518"
AUDIO_IDLE_TRACE_SHA256 = "9A7AB98387AE707DC5073C85100222736F1BB7BD50AECDEAE6698700E68C445D"
AUDIO_TIMED_TRACE_SHA256 = "57024EF0A763A21C15E1512D142C06F8CDCF0C7D611ECBEEE51A6135A0E1A40C"
AUDIO_CLOCK_MUTE_SHA256 = "E066A851855F1B8D645EF4C2A304C08EEEED24F2B4939FD91456B6E36D9B3182"
AUDIO_EDGE_TRACE_SHA256 = "3AC0F42784683D234EE4AFE723C44A81BDDE2B9AB12CE24D11A32F4D8F817BF1"
AUDIO_BRIDGE_TRACE_SHA256 = "E6AC383940C92D87D7F9C26D967FC3B45ECC9EE093215AEBBB80E806B84CDD38"
USB_TO_DP_ON_HOLD_SHA256 = "EC5D6D7859FDD9E1B16CCFA46DBFFEE03FBC8501E2F079167219975E8F70EE69"
USB_TO_DP_STABLE_HOLD_SHA256 = "4FEFFE2C771F5EDAED83BDF166BB2878E897DBEE1AE1896EEBBAB0B74C587ABC"
AUDIO_ROUTE_BUTTONS_SHA256 = "77ABB1AF8C1D329A1D6DABD3A92AEBD7952839896BA5682FED1DEC5E9D880FBF"
AUDIO_ROUTE_BUTTONS_V2_SHA256 = "E07A5BA17E3A3565B3ACC2457164F2D0B0265736FD5613854BC79F0B2EF2C404"

EXPECT_LENGTH = 0x24E70
EXPECT_PROJECT = 0x0900
EXPECT_IMAGE_TYPE = 1
EXPECT_VERSION = b"09.1.00.180_20240507"

Record = tuple[str, int, bytes, bytes]

FULL_TRANSITION_HOOK_OFFSET = 0x03196
FULL_TRANSITION_HOOK = bytes.fromhex("07 F0 17 FF")  # BL official OLED close
FULL_TRANSITION_HELPER_OFFSET = 0x0FD00
# Wait 2000 ms after the stock 1000 ms delay, then issue the second B6.
FULL_TRANSITION_HELPER = bytes.fromhex(
    "10 B5 0C 4C 20 78 A6 28 13 D1 A7 20 20 70 40 F2 D0 70 "
    "06 F0 92 FE 08 49 C8 79 01 21 48 40 F5 F7 A9 FA 4F F4 "
    "7A 70 06 F0 88 FE 04 48 C0 88 00 F0 07 F8 10 BD 48 02 "
    "00 20 7A 03 00 20 4C 0E 00 20 10 B5 01 F0 C1 FA 08 20 "
    "02 49 0A 78 02 43 0A 70 10 BD 00 BF 92 0D 00 20"
)


def fail(message: str) -> None:
    raise SystemExit("ERROR: " + message)


def hx(text: str) -> bytes:
    return bytes.fromhex(text)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def crc_container(data: bytes) -> int:
    """CRC-32 / poly 0xF4ACFB13 / MSB-first / init 0 / xorout 0."""
    c = 0
    for b in data:
        c ^= b << 24
        for _ in range(8):
            c = ((c << 1) ^ POLY) & 0xFFFFFFFF if c & 0x80000000 else (c << 1) & 0xFFFFFFFF
    return c


def head(data: bytes) -> dict:
    stored_crc, length, project, image_type = struct.unpack_from("<4I", data, 0)
    return dict(
        stored_crc=stored_crc,
        length=length,
        project=project,
        image_type=image_type,
        version=data[0x10:0x24],
    )


def fix_container_crc(data: bytearray) -> int:
    crc = crc_container(bytes(data[8:]))
    struct.pack_into("<I", data, 0, crc)
    return crc


def validate_container(data: bytes, what: str) -> None:
    if len(data) != SIZE:
        fail(f"{what}: expected {SIZE} bytes, got {len(data)}")
    h = head(data)
    if h["length"] != EXPECT_LENGTH or h["length"] + 8 != len(data):
        fail(f"{what}: declared length does not match the Air 2 MCU container")
    if h["project"] != EXPECT_PROJECT or h["image_type"] != EXPECT_IMAGE_TYPE:
        fail(f"{what}: project/image type mismatch (0x{h['project']:04X} / {h['image_type']})")
    if h["version"] != EXPECT_VERSION:
        fail(f"{what}: version mismatch ({h['version']!r})")
    if crc_container(data[8:]) != h["stored_crc"]:
        fail(f"{what}: container CRC-32 does not match its header")
    initial_sp, reset = struct.unpack_from("<II", data, HDR_LEN)
    if not 0x20000000 <= initial_sp < 0x20040000:
        fail(f"{what}: initial stack pointer 0x{initial_sp:08X} is not in SRAM")
    if not 0x0000F000 <= (reset & ~1) < 0x00040000:
        fail(f"{what}: reset vector 0x{reset:08X} is outside the application range")


RECORDS: tuple[Record, ...] = (
    ("no-USB official full DP-audio transition policy", 0x0313E,
     hx("002038b53448007800b938bd02f061f800909df800000a21b1eb101f03d000202d490870f1e79df8000000f0010050b10020284908709df80000c0f3400010b101202549087024480078002840d001f00bfb0446012c1fd007f017ff02e00a2013f04cfc1e4800680528f8d300201b49086001201b490870002001f0e6fe012001f001fb1849c979012901d0012100e00021084602f04ff802f039fcfef713fe0a2013f02bfc02f044fc0f481049097830f811000f49c8804ff47a7013f01efc0c49c8880ef09bf801200a49087000bf00bf9ae7"),
     hx("002038b5344d2c78a62c1fd202f061f80346d80718d518090a2815d1e81de130006878b9980707d401342c70a62c0dd301f01afb30b90fe0012027490870a820287004e0a92000e00020287038bd01f00bfb0446012c1fd007f017ff02e00a2013f04cfc1e4800680528f8d300201b49086001201b490870002001f0e6fe012001f001fb1849c979012901d0012100e00021084602f04ff802f039fcfef713fe0a2013f02bfc02f044fc0f481049097830f811000f49c8804ff47a7013f01efc0c49c8880ef09bf801200a4908700cf078fdbbe7")),
    ("full-transition saved-gain, second-B6 and manual-lock helper", 0x0FCFC,
     hx("2de9f84304460d464ff001080027002000900220faf7dafc0646012269464ff4ea60f6f78ff89df80000aa2806d023202070012028804ff0000813e09df80000aa280fd10222314640f25170f6f77af837883a46214640f25370f6f773f82f804ff000083046faf75dfc4046bde8f8832de9f84304460d464ff001080027002000900220faf7a2fc06460122694640f27c70f6f757f89df80000aa2806d023202070012028804ff0000813e09df80000aa280fd10222314640f27d70f6f742f837883a46214640f27f70f6f73bf82f804ff000083046faf725fc4046bde8f883"),
     hx("c6e700bf10b50c4c2078a62813d1a720207040f2d07006f092fe0849c87901214840f5f7a9fa4ff47a7006f088fe0448c08800f007f810bd480200207a0300204c0e002010b501f0c1fa082002490a7802430a7010bd00bf920d00204c0e0020920d00204c0d00206c0d002000bf00bf8ee700bf0d490878a62813d1a72008701ce00b49086001200a4908700a490a78082002430a70d63901200870fff7c7fff3f71dbafbf712f9f3f7f9b94802002088040020b60c0020920d0020022048700020dee700bf00bf00bf00bf00bf00bf00bf00bf00bf00bf00bf00bf00bf00bf")),
    ("mode-1 BE-before-BA wrapper hook", 0x0537E,
     hx("0020fff762fe"), hx("0af01ffd00bf")),
    ("accept native/scaler class changes", 0x04CFC,
     hx("02"), hx("03")),
    ("adaptive 72 Hz uses the native profile policy", 0x04E38,
     hx("01"), hx("00")),
    ("adaptive 72 Hz keeps mode-1 E0C0", 0x04E42,
     hx("01"), hx("00")),
    ("adaptive 90 Hz uses the native profile policy", 0x04E68,
     hx("01"), hx("00")),
    ("adaptive 90 Hz keeps mode-1 E0C0", 0x04E72,
     hx("01"), hx("00")),
    ("adaptive 120 Hz uses the native profile policy", 0x04EC8,
     hx("01"), hx("00")),
    ("adaptive 120 Hz keeps mode-1 E0C0", 0x04ED2,
     hx("01"), hx("00")),
    ("true-720p class selects logical mode 1", 0x04DEE,
     hx("012805d1"), hx("0228ead0")),
    ("mode 3 selects E0C0 RGB", 0x053D8,
     hx("0020"), hx("0220")),
    ("mode 5 selects E0C0 RGB", 0x05436,
     hx("0120"), hx("0220")),
    ("mode 4 selects E0C0 RGB", 0x0549A,
     hx("0120"), hx("0220")),
    ("mode 8 selects E0C0 RGB", 0x05504,
     hx("0020"), hx("0220")),
    ("mode 10 selects E0C0 RGB", 0x05574,
     hx("0120"), hx("0220")),
    ("mode 9 selects E0C0 RGB", 0x055DE,
     hx("0120"), hx("0220")),
    ("adaptive RGB/scaler OLED group policy", 0x04D72,
     hx("0846c07a022803d100200ff05efac9e0"),
     hx("c87a022804d1c87840080ff05efac9e0")),
    ("mode 1 compares OLED RGB group 0", 0x053B4,
     hx("0128"), hx("0028")),
    ("mode 1 selects OLED RGB group 0", 0x053B8,
     hx("0120"), hx("0020")),
    ("mode 3 compares OLED RGB group 0", 0x05414,
     hx("0128"), hx("0028")),
    ("mode 3 selects OLED RGB group 0", 0x05418,
     hx("0120"), hx("0020")),
    ("mode 5 compares OLED RGB group 0", 0x05476,
     hx("0228"), hx("0028")),
    ("mode 5 selects OLED RGB group 0", 0x0547A,
     hx("0220"), hx("0020")),
    ("mode 4 compares OLED RGB group 0", 0x054D6,
     hx("0228"), hx("0028")),
    ("mode 4 selects OLED RGB group 0", 0x054DA,
     hx("0220"), hx("0020")),
    ("mode 8 compares OLED RGB group 0", 0x05540,
     hx("0128"), hx("0028")),
    ("mode 8 selects OLED RGB group 0", 0x05544,
     hx("0120"), hx("0020")),
    ("mode 10 compares OLED RGB group 0", 0x055B0,
     hx("0228"), hx("0028")),
    ("mode 10 selects OLED RGB group 0", 0x055B4,
     hx("0220"), hx("0020")),
    ("mode 9 compares OLED RGB group 0", 0x0561A,
     hx("0228"), hx("0028")),
    ("mode 9 selects OLED RGB group 0", 0x0561E,
     hx("0220"), hx("0020")),
    ("OLED group-3 uses the four-lane link table", 0x1A65E,
     hx("d905"), hx("5803")),
    ("resolution-aware OLED group selector hook", 0x1423E,
     hx("0446042c"), hx("09f0e1fc")),
    ("initial-DP full-state apply before first OLED open", 0x0512E,
     hx("012000f0a0f8"), hx("0af04ffe00bf")),
    ("mode-1 full-state apply before first OLED reopen", 0x0539C,
     hx("c249c9790129"), hx("0af018fd0de0")),
)

GROUP_HELPER_OFFSET = 0x1DC04
GROUP_HELPER = hx(
    "04460e480e498d4202d10124c47013e0c17802290dd000290ed14179032904d0"
    "012c08d0022c06d006e0002c04d1032402e0012400e00024042c7047"
    "7a03002088040020"
)

HOTPLUG_HOOK_OFFSET = 0x04C24
HOTPLUG_HOOK = hx("19f046b8")
HOTPLUG_ACTION_OFFSET = 0x1DCB4
HOTPLUG_ACTION = hx(
    "f0b585b000f002f8e6f7b4bf"
    "f0b581b0144fe7f716fb022820d18820e7f7b8fa0446052c1ad0012c1ad1"
    "8920e7f7b0fa05469020e7f7acfa0646022d10d1042e0ed1387800280bd1"
    "01203870b620e7f79ffa01214840e7f7b3fa01e00020387001b0f0bd"
    "3a12002000bf"
)

# Applied after RECORDS because the audio-policy record creates the guarded NOP
# cave used by the mode-1 wrapper. The adjacent helper enters the official full-
# state core with an explicit state reload.
POST_RECORDS: tuple[Record, ...] = (
    ("mode-1 BE-before-BA wrapper", 0x0FDC0,
     hx("00bf" * 14),
     hx("10b50020f5f730fe0020f5f73df910bd"
        "70b5014df4f763f888040020")),
    ("resolution-aware OLED group selector", GROUP_HELPER_OFFSET,
     bytes(len(GROUP_HELPER)), GROUP_HELPER),
    ("periodic gate to powered-HDMI disconnect-edge recovery", HOTPLUG_HOOK_OFFSET,
     hx("f0b585b0"), HOTPLUG_HOOK),
    ("powered-HDMI disconnect-edge live-B6 toggle", HOTPLUG_ACTION_OFFSET,
     bytes(len(HOTPLUG_ACTION)), HOTPLUG_ACTION),
)

ALL_RECORDS = RECORDS + POST_RECORDS
RECORD_BYTES = sum(len(old) for _label, _offset, old, _new in ALL_RECORDS)

# Thumb code in the published image's empty diagnostic slot. File offsets are
# sufficient for the relative branches. r5 points to the existing session byte.
DP_FIRST_HELPER = hx(
    "d5f8e800 18b9"          # load SET_ADDRESS at [r5+0xe8], branch if addressed
    "e7f7dffa e5f77cba"      # otherwise read E08B and resume original wait at 0314E
    "0134 2c70 a62c 09d3"    # increment original poll counter, return below 166
    "a920 2870"              # mark complete before calling the non-idempotent toggle
    "0c49 8878 20b9"         # return if cached E0B8 is nonzero
    "e6f79dfd 08b9"          # read live E0B8 (047A6), return if nonzero
    "faf7bbfc 38bd"          # stock button toggle (185E8), original policy epilogue
    "084a a923 1370 faf7b5bc"  # manual key: latch session, tail-call 185E8
    "064a a923 1370 f5f7a2bb"  # MCU PREPARE: latch session, tail-call 133CC
    "034a a923 1370 e6f7e7bb"  # DP PREPARE: latch session, tail-call 04460
    "00bf 7a030020 48020020"   # alignment, bridge state and session byte literals
)
DP_FIRST_RECORDS: tuple[Record, ...] = (
    ("addressed-host startup policy", 0x0314A, hx("02f061f8"), hx("1af07dbd")),
    ("unaddressed host uses converter timeout", 0x0315A, hx("e81d"), hx("04e0")),
    ("keep manual key available after converter retry", 0x0FD4E, hx("0a70"), hx("00bf")),
    ("manual choice consumes startup preference", 0x0ACE0, hx("0df082fc"), hx("12f0c8ff")),
    ("MCU PREPARE keeps USB active", 0x0DA5C, hx("05f0b6fc"), hx("10f00ff9")),
    ("DP PREPARE keeps USB active", 0x0DEA6, hx("f6f7dbfa"), hx("0ff0effe")),
    ("one-shot policy and stock handler tails", 0x1DC48, bytes(len(DP_FIRST_HELPER)), DP_FIRST_HELPER),
)


def build_dp_first(baseline: bytes, *, immediate: bool = False) -> bytes:
    """Derive the Pro-only variant from the byte-exact, already verified public build."""
    if sha256(baseline) != OUTPUT_SHA256:
        fail("DP-first requires the exact published MCU output")
    out = bytearray(baseline)
    for label, offset, old, new in DP_FIRST_RECORDS:
        if len(old) != len(new) or out[offset:offset + len(old)] != old:
            fail(f"DP-first guard mismatch at 0x{offset:05X}: {label}")
        out[offset:offset + len(new)] = new
    if fix_container_crc(out) != DP_FIRST_CRC or sha256(out) != DP_FIRST_SHA256:
        fail("DP-first output differs from its pinned CRC or SHA-256")
    if immediate:
        # Addressed-host path only. CMP r4,#1 after the poll increment, formerly #166.
        if out[0x1DC5A:0x1DC5C] != hx("a62c"):
            fail("immediate DP guard mismatch at 0x1DC5A")
        out[0x1DC5A:0x1DC5C] = hx("012c")
        if fix_container_crc(out) != IMMEDIATE_DP_CRC or sha256(out) != IMMEDIATE_DP_SHA256:
            fail("immediate DP output differs from its pinned CRC or SHA-256")
    image = bytes(out)
    validate_container(image, "DP-first MCU")
    return image


# File offsets in the tested Air 2 Pro image. The top 128 bytes of the
# original stack are reserved for the trace in both stack-top declarations.
AUDIO_TRACE_RAM = 0x2001C528
AUDIO_TRACE_CODE = 0x1DD20
AUDIO_TRACE_INIT = AUDIO_TRACE_CODE
AUDIO_TRACE_POLL = 0x1DEB0
AUDIO_TRACE_READ = 0x1DE5E

AUDIO_TRACE_HELPER = hx(
    # Initialize the trace and tail-call the stock startup routine.
    "594800210c2202c0013afcd1564857490160f5f7fbbe"
    # Call the original poll. Sample in DP mode every 250 ms or on a mode edge.
    "2de9f041e5f701fa524c5348817801296ad10579e9f77fff064694f82800a842"
    "04d1e06a301ab0f57a7f5dd384f82850e6622779002d0cbf04f1080804f12008"
    # Record 16-bit milliseconds, I2C success mask, B9, C67C, E2EB, D423, D424.
    "b008a8f8000088f8035000254cf27c6000f04cf888f8040045ea1025"
    "4ef2eb2000f044f888f80500000a45ea40054df2234000f03bf888f80600000a"
    "45ea80054df2244000f032f888f80700000a45eac00588f80250"
    # Restore page E0. Preserve the last 2D sample and first, settled, latest 3D.
    "ff20e021e7f706fe98f8030018b90127002060711ce06079ff2838bf01306071"
    "17f0020f06d1d8e90001c4e90401266347f0020717f0040f0ad1206b301a"
    "b0f5fa5f05d3d8e90001c4e9060147f004072771bde8f081"
    # Checked I2C read with the stock bus helper. Return 0x100 | byte on success.
    "10b584b0c4b2010aff20e7f7d8fd90b90020039003a800900123019332200290"
    "144843212246e9f764ff20b99df80c0040f4807000e0002004b010bd"
    # Reserved op 0x2B with A7 AD returns exactly 40 RAM bytes. Other requests
    # tail-call the stock unsupported-command handler.
    "2b2c10d10278a72a0dd14278ad2a0ad128220a80054911f8013b00f8013b013a"
    "f9d100207047edf770b828c50120415452317a03002000100840"
)
# Preserve the latest MCU SmartPA state for each video mode in header bytes 6–7.
AUDIO_TRACE_AMP_HELPER = hx(
    "08b5fff740ff06488178012906d1017901f00101034a12780348425408bd00bf"
    "7a0300204f0e00202ec50120"
)
AUDIO_TRACE_RECORDS: tuple[Record, ...] = (
    ("reserve trace RAM in vector stack top", 0x40, hx("a8c50120"), hx("28c50120")),
    ("reserve trace RAM in C runtime stack top", 0x2C8, hx("a8c50120"), hx("28c50120")),
    ("initialize trace at application startup", 0x1161A, hx("02f087fa"), hx("0cf081fb")),
    ("sample DP audio in the first display loop", 0x034A4, hx("fff74cfe"), hx("1af004fd")),
    ("sample DP audio in the second display loop", 0x038DC, hx("fff730fc"), hx("1af0e8fa")),
    ("hook reserved-command dispatcher", 0x0DFFC, hx("fcf7b4ff"), hx("0ff02fff")),
    ("capture and read four DP audio samples", AUDIO_TRACE_CODE,
     bytes(len(AUDIO_TRACE_HELPER)), AUDIO_TRACE_HELPER),
    ("sample MCU SmartPA state", AUDIO_TRACE_POLL,
     bytes(len(AUDIO_TRACE_AMP_HELPER)), AUDIO_TRACE_AMP_HELPER),
)


# ATR2 keeps the 40-byte readout and uses sample byte 3 for the MCU amp state.
# The classifier saves a baseline after two seconds of continuous lock, then the
# first unlocked sample and the first unlocked sample at least two seconds later.
# Header bit 4 tracks the lock streak and bit 3 marks the latest sample.
AUDIO_IDLE_TRACE_CODE = 0x1DEE0
AUDIO_IDLE_TRACE_HELPER = hx(
    "2248007888f8030047f0080715f0020f38d098f8050010f0800f14d0"
    "17f0100f03d1666347f010072ee0606b301ab0f5fa5f29d327f0060747f00107"
    "d8e90001c4e9020120e027f0100717f0010f1bd017f0020f07d1266347f00207"
    "d8e90001c4e9040110e017f0040f0dd1206b301ab0f5fa5f08d347f00407"
    "d8e90001c4e9060101e027f01007fff758bf4f0e0020"
)
AUDIO_IDLE_TRACE_RECORDS: tuple[Record, ...] = (
    ("keep the latest raw audio sample in slot four", 0x1DD6A,
     hx("002d0cbf04f1080804f12008"), hx("04f1200800bf00bf00bf00bf")),
    ("classify the sample by audio clock lock", 0x1DDD4, hx("98f80300"), hx("00f084b8")),
    ("identify the idle capture format", 0x1DE8C, b"ATR1", b"ATR2"),
    ("save samples before and after loss of lock", AUDIO_IDLE_TRACE_CODE,
     bytes(len(AUDIO_IDLE_TRACE_HELPER)), AUDIO_IDLE_TRACE_HELPER),
)


# ATR3 uses the same reserved 128 bytes. Header bytes 4–5 hold the next ring
# index and count. Each of 28 rows holds t16 (ms) and one byte per amplifier.
# Bits 0–7 are PLL, switching, chip power-down, amp power-down, stable clock,
# missing clock, hard mute and valid. Both SYSST and SYSCTRL must read cleanly.
AUDIO_TIMED_TRACE_POLL = 0x1DEE0
AUDIO_TIMED_TRACE_READ = 0x1DFC0
AUDIO_TIMED_TRACE_HELPER = hx(
    # Run the original poll and sample once per second in DP audio mode.
    "2de9f041e5f72cf92d48807801282bd12c4ce9f7abfea56f"
    "451bb5f57a6f23d3a067257904eb8507083780083880264e"
    "3078012803d1342000f018f800e00020b8707078012803d1"
    "352000f00ff800e00020f87001351c2d08bf002525716079"
    "1c2838bf01306071bde8f081"
    # Read SYSST and SYSCTRL via the stock 16-bit bus helper. Pass the
    # address directly so the live firmware's amplifier selector is untouched.
    "70b582b004461648214601226b46eaf727fdd8b9bdf800"
    "501148214604226b46eaf71efd90b9bdf8001005f03100c5f3002240ea"
    "420001f0030240ea8200c1f3002240ea821040f0800000e0002002b070bd00bf"
    "7a03002028c50120be03002000000840"
)
AUDIO_TIMED_TRACE_READ_HELPER = hx(
    # A7 AD followed by page 0–3 returns the header and seven rows.
    "2b2c22d10278a72a1fd14278ad2a1cd18278042a19d22423"
    "0b8030b4044615460b49082211f8013b00f8013b013af9d1"
    "a5711c225543491911f8013b00f8013b013af9d1002030bc"
    "7047ecf7adbf00bf28c50120"
)
AUDIO_TIMED_TRACE_RECORDS: tuple[Record, ...] = (
    ("clear all 128 reserved bytes", 0x1DD24, hx("0c22"), hx("2022")),
    ("identify the timed amplifier capture format", 0x1DE8C, b"ATR1", b"ATR3"),
    ("sample both amplifiers in the first display loop", 0x034A4, hx("1af004fd"), hx("1af01cfd")),
    ("sample both amplifiers in the second display loop", 0x038DC, hx("1af0e8fa"), hx("1af000fb")),
    ("read one page of the timed capture", AUDIO_TRACE_READ, hx("2b2c10d1"), hx("00f0afb8")),
    ("sample two hardware status registers per amplifier", AUDIO_TIMED_TRACE_POLL,
     bytes(len(AUDIO_TIMED_TRACE_HELPER)), AUDIO_TIMED_TRACE_HELPER),
    ("return one of four frozen trace pages", AUDIO_TIMED_TRACE_READ,
     bytes(len(AUDIO_TIMED_TRACE_READ_HELPER)), AUDIO_TIMED_TRACE_READ_HELPER),
)


def build_audio_trace(immediate: bytes, *, idle: bool = False, timed: bool = False) -> bytes:
    if sha256(immediate) != IMMEDIATE_DP_SHA256:
        fail("audio trace requires the exact immediate DP-first MCU")
    out = bytearray(immediate)
    records = AUDIO_TRACE_RECORDS
    expected_sha256 = AUDIO_TRACE_SHA256
    if timed:
        records += AUDIO_TIMED_TRACE_RECORDS
        expected_sha256 = AUDIO_TIMED_TRACE_SHA256
    elif idle:
        records += AUDIO_IDLE_TRACE_RECORDS
        expected_sha256 = AUDIO_IDLE_TRACE_SHA256
    for label, offset, old, new in records:
        if len(old) != len(new) or out[offset:offset + len(old)] != old:
            fail(f"audio trace guard mismatch at 0x{offset:05X}: {label}")
        out[offset:offset + len(new)] = new
    fix_container_crc(out)
    if sha256(out) != expected_sha256:
        fail(f"audio trace SHA-256 mismatch: {sha256(out)}")
    validate_container(out, "Air 2 Pro audio trace")
    return bytes(out)


# Poll SYSST separately from the 1 Hz recorder. The last four reserved ATR3
# RAM bytes hold the 16-bit tick and a mute ownership byte for each amplifier.
# shortcut: polling can miss a click at the first unstable edge, use a verified advance signal if it persists.
AUDIO_CLOCK_MUTE_CODE = 0x1E014
AUDIO_CLOCK_MUTE_HELPER = hx(
    # Run the original poll. With the stock amplifier profile open in DP
    # mode, sample each detected device no more than once every 8 ms.
    "70b5e5f793f8454ca078474c01281cd143480078c12818d1e9f70efe2188411a89b22029"
    "13d320803e4e02343078012803d12046342100f00bf87078012806d1601c352100f004f8"
    "01e00020206070bd"
    # Ownership: 0 none, 1 write pending, 2 muted, 3 first good recovery
    # sample. Recovery requires two stable, switching, fault-free reads.
    "70b582b004460d46344801226b46eaf79bfc002847d1bdf8006006f0310011280ed12078"
    "002843d040f23f110e4040f211118e4237d1032820d00320207037e0"
    # Clock unstable: only take ownership when the amp is powered up and
    # HMUTE was clear. Keep the pending bit so a failed write can be retried.
    "2078022817d200f035f8002830d1bdf8003013f0030f23d113f4807f08d10120207043f4"
    "807300f02bf8002820d102e0207800281cd00220207019e0"
    # Clear only HMUTE, and only for an owned amp. Failed reads or writes
    # invalidate recovery confirmation. Stock owns shutdown and route changes.
    "00f01af8002810d1bdf8003013f0030f08d113f4807f05d023f4807300f012f8002802d1"
    "0020207004e02078032801d10120207002b070bd"
    "0948294604226b46eaf744bc064829460422eaf76ebc00bf"
    "7a0300204f0e0020be030020a4c5012000000840"
)


# Log both amps before the mute handler runs. Keep the existing ATR3 layout.
# The 8 ms limiter bounds bus traffic, and the main-loop period sets the
# actual cadence. The one-second heartbeat bounds gaps in the rolling window.
AUDIO_EDGE_TRACE_CODE = 0x1E144
AUDIO_EDGE_TRACE_HELPER = hx(
    "2de9f0412248807801283cd1214ce9f77bfda16f411a202935d3a067800880b280461d4e"
    # Read both present devices through the existing ATR3 status encoder.
    "00273078012803d13420fff7ebfe07467078012804d13520fff7e4fe47ea0027"
    # Compare both status bytes to the newest row before writing to the ring.
    "2579667914f10803002e0dd0691e00d51b2153f82120110cb94205d1b8eb020292b2b2f57a7f0cd3"
    # Append the edge or heartbeat, then run the existing control logic.
    "48ea074243f8252001351c2d08bf002525711c2e3cbf01366671fff723ffbde8f08100bf"
    "7a03002028c50120be030020"
)


# ATR4 uses the same pages as ATR3. The second status byte is the receiver
# snapshot, read before amplifier 0x34 so a change between the two reads
# cannot by itself make an absent stream appear to precede stable clock.
AUDIO_BRIDGE_TRACE_HELPER = hx(
    "10b584b0ff20e021e7f7fafb002814d1"                  # select page E0
    "0020039003a800900123019332200290084843218522"      # one-byte read of E085
    "e9f785fd002807d19df80c00fe2804d3fd2002e0"          # reserve FE and FF for errors
    "fe2000e0ff2004b010bd00100840"                      # page error, read error, return
)
AUDIO_BRIDGE_TRACE_RECORDS: tuple[Record, ...] = (
    ("identify synchronized receiver and amplifier data as ATR4", 0x1DE8C, b"ATR3", b"ATR4"),
    ("sample receiver first and then left amplifier", 0x1E168,
     hx("00273078012803d13420fff7ebfe07467078012804d13520fff7e4fe47ea0027"),
     hx("00f03af807023078012809d13420fff7e9fe074300bf00bf00bf00bf00bf00bf")),
    ("read receiver status and retain I2C failure reasons", 0x1E1E0,
     bytes(len(AUDIO_BRIDGE_TRACE_HELPER)), AUDIO_BRIDGE_TRACE_HELPER),
    ("keep ATR4 bits out of the stock display getter", 0x056C8,
     hx("0446022c"), hx("18f0bafd")),
    ("mask off telemetry only when the receiver marker is present", 0x1E240,
     bytes(18), hx("00f0e004 a02c 01d1 00f00100 0446 022c 7047")),
)


def build_audio_clock_mute(timed_trace: bytes, *, edge_trace: bool = False,
                           bridge_trace: bool = False) -> bytes:
    if sha256(timed_trace) != AUDIO_TIMED_TRACE_SHA256:
        fail("audio clock mute requires the exact timed trace MCU")
    out = bytearray(timed_trace)
    records = (
        ("run clock mute before ATR3 capture", AUDIO_TIMED_TRACE_POLL + 4, hx("e5f72cf9"), hx("00f096f8")),
        ("manage only the SmartPA hard-mute bit", AUDIO_CLOCK_MUTE_CODE,
         bytes(len(AUDIO_CLOCK_MUTE_HELPER)), AUDIO_CLOCK_MUTE_HELPER),
    )
    expected_sha256 = AUDIO_CLOCK_MUTE_SHA256
    if edge_trace or bridge_trace:
        records += (
            ("route ATR3 polling to the audio edge sampler", AUDIO_TIMED_TRACE_POLL,
             hx("2de9f041"), hx("00f030b9")),
            ("record clock, switching and mute transitions", AUDIO_EDGE_TRACE_CODE,
             bytes(len(AUDIO_EDGE_TRACE_HELPER)), AUDIO_EDGE_TRACE_HELPER),
        )
        expected_sha256 = AUDIO_EDGE_TRACE_SHA256
    if bridge_trace:
        records += AUDIO_BRIDGE_TRACE_RECORDS
        expected_sha256 = AUDIO_BRIDGE_TRACE_SHA256
    for label, offset, old, new in records:
        if len(old) != len(new) or out[offset:offset + len(old)] != old:
            fail(f"audio clock mute guard mismatch at 0x{offset:05X}: {label}")
        out[offset:offset + len(new)] = new
    fix_container_crc(out)
    if sha256(out) != expected_sha256:
        fail("audio clock mute or edge trace SHA-256 mismatch")
    validate_container(out, "Air 2 Pro audio clock mute")
    return bytes(out)


# The held-key handler already validated the four-second threshold and its
# inhibit flags. Keep the adapter guard and reserve bit 2 of the key's flags
# to consume the release of a hold that already switched audio.
USB_TO_DP_ON_HOLD_CODE = hx(
    "0848 8078 50b9 0849 0978 0129 08d0"   # cached USB route; adapter guard
    "0749 0878 40f00400 0870 fff702fd"      # mark handled, call 1DC74
    "ecf712bd ecf707bd"                    # stock held-key epilogue or USB cue
    "7a030020 bd0c0020 880d0020"
    "0548 0178 8078 0428 04d1 11f0040f 01d0"  # release with handled bit set
    "ecf736bd ecf707bd 880d0020"           # clear flags or resume stock release
)
USB_TO_DP_ON_HOLD_RECORDS: tuple[Record, ...] = (
    ("switch USB to DP on a confirmed hold", 0x0AC80, hx("5d488078"), hx("13f0e8ba")),
    ("consume the matching release", 0x0ACA6, hx("43488078"), hx("13f0edba")),
    ("held-key and release handlers", 0x1E254, bytes(len(USB_TO_DP_ON_HOLD_CODE)),
     USB_TO_DP_ON_HOLD_CODE),
)
# An interrupt can report the same still-pressed button. Only a new press
# should restart its timer. The stock double-press state sets its own timestamp.
USB_TO_DP_STABLE_HOLD_RECORD: Record = (
    "keep the Brightness + timestamp across shared GPIO interrupts",
    0x07336, hx("08b9"), hx("20b9"),
)
BRIGHTNESS_MINUS_STABLE_HOLD_RECORD: Record = (
    "keep the Brightness - timestamp across shared GPIO interrupts",
    0x0737A, hx("08b9"), hx("20b9"),
)


def build_usb_to_dp_on_hold(bridge_trace: bytes, *, stable: bool = False) -> bytes:
    if sha256(bridge_trace) != AUDIO_BRIDGE_TRACE_SHA256:
        fail("USB-to-DP on hold requires the exact ATR4 MCU")
    out = bytearray(bridge_trace)
    records = USB_TO_DP_ON_HOLD_RECORDS
    expected = USB_TO_DP_ON_HOLD_SHA256
    if stable:
        records += (USB_TO_DP_STABLE_HOLD_RECORD,)
        expected = USB_TO_DP_STABLE_HOLD_SHA256
    for label, offset, old, new in records:
        if len(old) != len(new) or out[offset:offset + len(old)] != old:
            fail(f"USB-to-DP on hold guard mismatch at 0x{offset:05X}: {label}")
        out[offset:offset + len(new)] = new
    fix_container_crc(out)
    if sha256(out) != expected:
        fail("USB-to-DP on hold output differs from its pinned SHA-256")
    validate_container(out, "Air 2 Pro USB-to-DP on hold")
    return bytes(out)


# Bit 1 in each key's cue flags records that its four-second gesture was
# handled. The stock release epilogues clear the flags for the next press.
AUDIO_ROUTE_BUTTONS_CODE = hx(
    "2948 0178 41f00201 0170 0020 00f03af8 ecf751bd "       # 1E254: + hold selects USB
    "2448 0178 8078 0428 01d1 8907 01d4 ecf718bd ecf743bd " # 1E268: consume + release before adapter checks
    "0020 2ae0 "                                           # 1E27E: + release fallback
    "1f4a 1178 11f0020f 0dd1 5068 40f69f71 ebf741f9 48b1 " # 1E282: - hold, stock timer
    "194a 1178 41f00201 1170 0120 00f018f8 ecf748be "        # latch gesture and select DP
    "1448 4068 ecf71bbe "                                   # resume the stock two-second cue
    "124a 1078 10f0020f 1cd1 10b5 5068 40f69f71 ebf727f9 " # 1E2B4: - release fallback
    "bde81040 08b1 0120 01e0 faf73cb9 "                     # shorter hold uses stock action
    "0849 897a 11f0080f 0ad1 0849 0978 0129 06d0 "         # 1E2D8: adapter and no-USB locks
    "0749 8978 4840 0128 01d1 fff7bebc 7047 00bf "          # toggle only from the opposite route
    "880d0020 a00d0020 bd0c0020 7a030020"                   # key +, key -, adapter, cached route
)
AUDIO_ROUTE_BUTTONS_RECORDS: tuple[Record, ...] = (
    USB_TO_DP_STABLE_HOLD_RECORD,
    BRIGHTNESS_MINUS_STABLE_HOLD_RECORD,
    ("select USB during a four-second Brightness + hold", 0x0AC80,
     hx("5d488078"), hx("13f0e8ba")),
    ("clear a handled Brightness + release before the stock adapter checks", 0x0ACA6,
     hx("43488078"), hx("13f0dfba")),
    ("select USB if the Brightness + hold was first recognized on release", 0x0ACE0,
     hx("12f0c8ff"), hx("13f0cdfa")),
    ("select DP during a four-second Brightness - hold", 0x0AEE6,
     hx("1d494868"), hx("13f0ccb9")),
    ("select DP if the Brightness - hold was first recognized on release", 0x0AF32,
     hx("0df00dfb"), hx("13f0bff9")),
    ("directional audio route handlers", 0x1E254, bytes(len(AUDIO_ROUTE_BUTTONS_CODE)),
     AUDIO_ROUTE_BUTTONS_CODE),
)


# The 24 bytes below ATR4's reservation store the last input poll tick,
# the fresh active-low sample (converted to pressed bits and a validity bit),
# and a timestamp and state for each key. Each key goes idle, pressed, handled.
AUDIO_ROUTE_BUTTONS_V2_CODE = hx(
    "5148 0021 0622 02c0 013a fcd1 fff75ebd "               # 1E254: zero the state and chain to ATR4 init
    "70b5 4d4c 0022 6260 e9f71ef8 6568 e9f7ebfc 0646 "     # 1E264: run the original key scan
    "4a48 04f10801 2a46 0023 00f042f8 "                    # check + with USB as the destination
    "4848 04f11001 6a08 0123 00f03bf8 70bd "               # check - with DP as the destination
    "e9f7d9fc 4049 0a68 821a 3f4b 1b78 012b 1fd0 "        # 1E296: pending interrupt takes the normal path
    "502a 1ad2 0b68 0a7b 012a 09d1 8a68 a0eb020c "
    "bcf57a5f 03d3 9a1a b2f57a5f 0cd3 "                   # poll every 20 ms or at the first scan crossing + deadline
    "0a7d 012a 0fd1 0a69 a0eb020c bcf57a5f 09d3 "
    "9a1a b2f57a5f 05d2 "                                  # same deadline for -
    "0860 e8f7f0bf 0860 e8f7eabf e9f760b8 "               # periodic reads leave the IRQ flag alone
    "0028 fbd1 9df81000 00f00300 80f00701 264a 5160 "
    "e8f7ffbf "                                           # 1E2F2: publish only a successful expander read
    "2de99041 0446 0f46 02f00108 3879 042d 22d3 0228 1ad0 "
    "072d 25d0 48b1 3868 301a 43f68061 8842 0ad2 "         # 1E30A: elapsed 16,000 quarter-millisecond ticks
    "b8f1000f 1bd0 1ce0 b8f1000f 19d0 3e60 0120 3871 15e0 " # timestamp first fresh press, clear a short release
    "5fea4800 3871 1846 00f012f8 07e0 "                     # select once at the threshold or a near-edge release
    "b8f1000f 04d1 87f80480 01e0 0228 06d1 "               # keep consuming repeats until a fresh release
    "0120 a070 0020 2070 01e0 0020 3871 bde89081 "          # suppress just the completed four-second gesture
    "0a49 897a 11f0080f 0ad1 0a49 0978 0129 06d0 "         # 1E378: honor the no-USB and adapter locks
    "0949 8978 4840 0128 01d1 fff76ebc 7047 00bf "          # call the stock switch only from the opposite route
    "10c50120 180b0020 880d0020 a00d0020 bd0c0020 7a030020"
)
AUDIO_ROUTE_BUTTONS_V2_RECORDS: tuple[Record, ...] = (
    USB_TO_DP_STABLE_HOLD_RECORD,
    BRIGHTNESS_MINUS_STABLE_HOLD_RECORD,
    ("reserve 24 additional bytes above the vector stack", 0x00040, hx("28c50120"), hx("10c50120")),
    ("reserve the same space above the runtime stack", 0x002C8, hx("28c50120"), hx("10c50120")),
    ("initialize route button state before ATR4", 0x1161A, hx("0cf081fb"), hx("0cf01bfe")),
    ("evaluate the physical hold before the stock key dispatcher", 0x0A9CA, hx("fcf76ffc"), hx("13f04bfc")),
    ("sample the button expander even if its interrupt is missed", 0x072BA, hx("47480078"), hx("16f0ecbf")),
    ("publish a button sample only after a successful read", 0x07300, hx("9df81000"), hx("16f0f7bf")),
    ("retire the original Brightness + release toggle", 0x0ACE0, hx("12f0c8ff"), hx("aff30080")),
    ("physical button timing and directional route selection", 0x1E254, bytes(len(AUDIO_ROUTE_BUTTONS_V2_CODE)),
     AUDIO_ROUTE_BUTTONS_V2_CODE),
)


def build_audio_route_buttons(bridge_trace: bytes, *, v2: bool = False) -> bytes:
    if sha256(bridge_trace) != AUDIO_BRIDGE_TRACE_SHA256:
        fail("audio route buttons require the exact ATR4 MCU")
    out = bytearray(bridge_trace)
    records = AUDIO_ROUTE_BUTTONS_V2_RECORDS if v2 else AUDIO_ROUTE_BUTTONS_RECORDS
    expected = AUDIO_ROUTE_BUTTONS_V2_SHA256 if v2 else AUDIO_ROUTE_BUTTONS_SHA256
    for label, offset, old, new in records:
        if len(old) != len(new) or out[offset:offset + len(old)] != old:
            fail(f"audio route buttons guard mismatch at 0x{offset:05X}: {label}")
        out[offset:offset + len(new)] = new
    fix_container_crc(out)
    if sha256(out) != expected:
        fail("audio route buttons output differs from its pinned SHA-256")
    validate_container(out, "Air 2 Pro audio route buttons")
    return bytes(out)


def build(stock: bytes) -> bytes:
    out = bytearray(stock)
    for label, offset, old, new in RECORDS:
        if len(old) != len(new):
            fail(f"record length mismatch: {label}")
        if out[offset:offset + len(old)] != old:
            fail(f"stock guard mismatch at 0x{offset:05X}: {label}")
        out[offset:offset + len(new)] = new
    for label, offset, old, new in POST_RECORDS:
        if out[offset:offset + len(old)] != old:
            fail(f"derived guard mismatch at 0x{offset:05X}: {label}")
        out[offset:offset + len(new)] = new
    fix_container_crc(out)
    return bytes(out)


def verify_stock(stock: bytes) -> None:
    validate_container(stock, "stock image")
    digest = sha256(stock)
    if digest != STOCK_SHA256:
        fail("this is not the expected official Air 2 MCU image.\n"
             f"       expected SHA-256 {STOCK_SHA256}\n"
             f"       got             {digest}")
    if head(stock)["stored_crc"] != STOCK_CRC:
        fail(f"stock container CRC is 0x{head(stock)['stored_crc']:08X}, expected 0x{STOCK_CRC:08X}")
    for label, offset, old, _new in RECORDS:
        if stock[offset:offset + len(old)] != old:
            fail(f"stock guard mismatch at 0x{offset:05X}: {label}")


def hotplug_edge_step(
    latch: int, e086: int, e088: int, e089: int, e090: int, b6: int
) -> tuple[int, int | None]:
    """Model the production helper's one-toggle-per-disconnect policy."""
    if e086 != 2 or e088 == 5:
        return 0, None
    if (e088, e089, e090) != (1, 2, 4) or latch:
        return latch, None
    return 1, b6 ^ 1


def verify_hotplug_model() -> None:
    if hotplug_edge_step(0, 2, 1, 2, 4, 1) != (1, 0):
        fail("powered-hotplug first disconnect did not toggle B6 1->0")
    if hotplug_edge_step(1, 2, 1, 2, 4, 0) != (1, None):
        fail("powered-hotplug disconnect latch retriggered")
    if hotplug_edge_step(1, 2, 1, 3, 11, 0) != (1, None):
        fail("powered-hotplug transition cleared the disconnect latch")
    if hotplug_edge_step(1, 2, 5, 0, 11, 0) != (0, None):
        fail("powered-hotplug reconnect did not arm the next edge")
    if hotplug_edge_step(0, 2, 1, 2, 4, 0) != (1, 1):
        fail("powered-hotplug second disconnect did not toggle B6 0->1")


def verify_output(stock: bytes, image: bytes) -> None:
    validate_container(image, "built image")
    verify_hotplug_model()
    if image != build(stock) or build(stock) != build(stock):
        fail("build is not deterministic")

    allowed = set(range(4))
    for _label, offset, old, _new in ALL_RECORDS:
        allowed.update(range(offset, offset + len(old)))
    diffs = [i for i, (before, after) in enumerate(zip(stock, image)) if before != after]
    stray = [i for i in diffs if i not in allowed]
    if stray:
        fail("changed bytes outside the record set: " + " ".join(f"0x{i:05X}" for i in stray))
    if len(diffs) != EXPECTED_DIFFS:
        fail(f"changed-byte count is {len(diffs)}, expected {EXPECTED_DIFFS}")

    h = head(image)
    if sha256(image) != OUTPUT_SHA256:
        fail(f"output SHA-256 mismatch: {sha256(image)}")
    if h["stored_crc"] != OUTPUT_CRC:
        fail(f"container CRC mismatch: 0x{h['stored_crc']:08X}")

    # Pin the safety-critical order: clear mode-9-only BE before setBA(0).
    wrapper = image[0x0FDC0:0x0FDD0]
    if wrapper != hx("10b50020f5f730fe0020f5f73df910bd"):
        fail("mode-1 BE-before-BA wrapper mismatch")
    if image[0x0504A:0x0504C] != hx("0446"):
        fail("official E0C0 setter was modified")
    if image[0x0512E:0x05134] != hx("0af04ffe00bf"):
        fail("initial-DP full-state hook mismatch")
    if image[0x1423E:0x14242] != hx("09f0e1fc"):
        fail("OLED group selector hook mismatch")
    if image[0x0502A:0x05046] != hx(
        "10b5e021ff2000f0d6fcc02000f005f90446032c00db0024204610bd"
    ):
        fail("official live C0 getter changed")
    if image[0x04D5A:0x04D6A] != hx("00f0a7f97249887200f062f97049c872"):
        fail("C0 getter no longer follows the E0-bank-selecting BF getter")
    if image[0x0539C:0x053A2] != hx("0af018fd0de0"):
        fail("mode-1 full-state hook mismatch")
    if image[FULL_TRANSITION_HOOK_OFFSET:FULL_TRANSITION_HOOK_OFFSET + len(FULL_TRANSITION_HOOK)] != FULL_TRANSITION_HOOK:
        fail("no-USB path no longer enters the official full DP-audio transition")
    if image[FULL_TRANSITION_HELPER_OFFSET:FULL_TRANSITION_HELPER_OFFSET + len(FULL_TRANSITION_HELPER)] != FULL_TRANSITION_HELPER:
        fail("full-transition saved-gain/manual-lock helper mismatch")
    if image[0x0FDD0:0x0FDDC] != hx("70b5014df4f763f888040020"):
        fail("mode-1 full-state helper mismatch")
    if image[0x05648:0x0568C] != stock[0x05648:0x0568C]:
        fail("mode-11 C0=1 / OLED group-3 path changed")
    if image[GROUP_HELPER_OFFSET:GROUP_HELPER_OFFSET + len(GROUP_HELPER)] != GROUP_HELPER:
        fail("resolution-aware OLED group selector mismatch")
    if image[HOTPLUG_HOOK_OFFSET:HOTPLUG_HOOK_OFFSET + len(HOTPLUG_HOOK)] != HOTPLUG_HOOK:
        fail("powered-hotplug periodic hook mismatch")
    if image[HOTPLUG_ACTION_OFFSET:HOTPLUG_ACTION_OFFSET + len(HOTPLUG_ACTION)] != HOTPLUG_ACTION:
        fail("powered-hotplug disconnect-edge helper mismatch")
    if image[0x03DA8:0x03DAC] != hx("02460120"):
        fail("stock host-message 0x9B handler changed")
    if image[0x1DC48:0x1DCB4] != bytes(108):
        fail("removed powered-hotplug diagnostic cave is not empty")
    for offset in (0x04DA2, 0x04E42, 0x04E72, 0x04ED2, 0x05122, 0x0FDC8):
        if image[offset] != 0:
            fail("mode-1 E0C0 policy mismatch")
    if image[0x04DEE:0x04DF2] != hx("0228ead0"):
        fail("true-720p logical-mode selection mismatch")
    for offset, expected in ((0x1A4E0, hx("5803")), (0x0104A, hx("d904")),
                             (0x1A65E, hx("5803"))):
        if image[offset:offset + 2] != expected:
            fail(f"OLED link-table contract mismatch at 0x{offset:05X}")

    print("=== XREAL Air 2 / Air 2 Pro - target-converter MCU ===")
    print("  stock input     : SHA-256 and container CRC verified")
    print(f"  records applied : {len(ALL_RECORDS)} ({RECORD_BYTES} guarded bytes)")
    print(f"  changed bytes   : {len(diffs)} (records + header CRC)")
    print("  no-USB state    : initial-wait official full DP-audio transition")
    print("  audio EDID      : E0B8/B6/HPD re-enumeration; blackout expected")
    print("  audio route     : DP-I2S + SmartPA, saved gain restored")
    print("  retry           : second B6 after a 2-second fixed delay")
    print("  manual switch   : audio-mode long press locked for the no-USB session")
    print("  mode transition : mode 1 clears BE before changing BA")
    print("  initial display : full-state apply before the first OLED open/reopen")
    print("  native RGB      : group 0; 120 Hz uses group 3 timing")
    print("  true 720p       : official group 1 / YCbCr scaler table")
    print("  HDMI hotplug    : exact disconnect edge toggles live B6 once")
    print("  reconnect       : no MCU action; converter delayed retrain retained")
    print("  protected code  : bytes outside guarded records remain official")
    print(f"  container CRC   : 0x{h['stored_crc']:08X}")
    print(f"  sha256          : {sha256(image)}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build the Air 2-family target-converter MCU image from official firmware.",
        epilog="You supply the official firmware. This tool contains no firmware and touches no hardware.",
    )
    parser.add_argument("--src", type=Path, required=True, metavar="STOCK_MCU")
    parser.add_argument("--out", type=Path, metavar="FILE")
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--verify", type=Path, metavar="FILE")
    audio = parser.add_mutually_exclusive_group()
    audio.add_argument("--dp-first", action="store_true",
                       help="build the Air 2 Pro DP-first variant with a startup grace period")
    audio.add_argument("--dp-first-immediate", action="store_true",
                       help="request DP audio on the first addressed-host poll (Air 2 Pro)")
    audio.add_argument("--audio-trace", action="store_true",
                       help="capture bridge audio status across a 3D switch (Air 2 Pro)")
    audio.add_argument("--audio-idle-trace", action="store_true",
                       help="capture bridge audio status across clock loss (Air 2 Pro)")
    audio.add_argument("--audio-timed-trace", action="store_true",
                       help="capture a rolling log of amplifier hardware state (Air 2 Pro)")
    audio.add_argument("--audio-clock-mute", action="store_true",
                       help="test automatic amplifier mute on unstable I2S, with ATR3 (Air 2 Pro)")
    audio.add_argument("--audio-edge-trace", action="store_true",
                       help="capture amplifier transitions around clock mute (Air 2 Pro)")
    audio.add_argument("--audio-bridge-trace", action="store_true",
                       help="capture DP receiver and amplifier transitions as ATR4 (Air 2 Pro)")
    audio.add_argument("--usb-to-dp-on-hold", action="store_true",
                       help="test USB-to-DP switching before button release, with ATR4 (Air 2 Pro)")
    audio.add_argument("--usb-to-dp-stable-hold", action="store_true",
                       help="test USB-to-DP with the hold timer preserved across interrupts (Air 2 Pro)")
    audio.add_argument("--audio-route-buttons", action="store_true",
                       help="hold Brightness + for USB or Brightness - for DP (Air 2 Pro)")
    audio.add_argument("--audio-route-buttons-v2", action="store_true",
                       help="time both audio holds from polled physical button state (Air 2 Pro)")
    args = parser.parse_args()
    if not (args.out or args.check_only or args.verify):
        parser.error("choose one of --out, --check-only, --verify")

    stock = args.src.read_bytes()
    verify_stock(stock)
    image = build(stock)
    verify_output(stock, image)
    if args.dp_first or args.dp_first_immediate:
        image = build_dp_first(image, immediate=args.dp_first_immediate)
        print("=== Air 2 Pro DP-first MCU ===")
        if args.dp_first_immediate:
            print("  direct USB host: first poll after USB addressing (experimental)")
        print("  startup: DP preferred once per power-up; four-second Brightness + toggles USB/DP")
        print(f"  container CRC: 0x{head(image)['stored_crc']:08X}")
        print(f"  sha256: {sha256(image)}")
    usb_to_dp_on_hold = args.usb_to_dp_on_hold or args.usb_to_dp_stable_hold
    audio_route_buttons = args.audio_route_buttons or args.audio_route_buttons_v2
    bridge_trace = args.audio_bridge_trace or usb_to_dp_on_hold or audio_route_buttons
    if any((args.audio_trace, args.audio_idle_trace, args.audio_timed_trace,
            args.audio_clock_mute, args.audio_edge_trace, bridge_trace)):
        image = build_audio_trace(build_dp_first(image, immediate=True), idle=args.audio_idle_trace,
                                  timed=any((args.audio_timed_trace, args.audio_clock_mute,
                                             args.audio_edge_trace, bridge_trace)))
        if bridge_trace:
            image = build_audio_clock_mute(image, bridge_trace=True)
            if audio_route_buttons:
                image = build_audio_route_buttons(image, v2=args.audio_route_buttons_v2)
                version = " v2" if args.audio_route_buttons_v2 else ""
                print(f"=== Air 2 Pro experimental audio route buttons{version} MCU (ATR4) ===")
                print("  four-second hold: Brightness + selects USB; Brightness - selects DP")
            elif usb_to_dp_on_hold:
                image = build_usb_to_dp_on_hold(image, stable=args.usb_to_dp_stable_hold)
                print("=== Air 2 Pro experimental USB-to-DP on hold MCU (ATR4) ===")
                if args.usb_to_dp_stable_hold:
                    print("  hold timer: preserve the press timestamp across GPIO interrupts")
            else:
                print("=== Air 2 Pro synchronized receiver and amplifier trace MCU (ATR4) ===")
        elif args.audio_edge_trace:
            image = build_audio_clock_mute(image, edge_trace=True)
            print("=== Air 2 Pro experimental amplifier edge trace MCU ===")
        elif args.audio_clock_mute:
            image = build_audio_clock_mute(image)
            print("=== Air 2 Pro experimental I2S clock mute MCU with ATR3 ===")
        else:
            print("=== Air 2 Pro DP audio trace MCU ===")
        print(f"  sha256: {sha256(image)}")

    if args.verify:
        if not args.verify.exists() or args.verify.read_bytes() != image:
            fail(f"{args.verify} differs from the build or is missing")
        print(f"  verify          : {args.verify} [byte-identical]")
    elif args.check_only:
        print("  check-only      : nothing written")
    else:
        if args.out.resolve() == args.src.resolve():
            fail("refusing to overwrite the stock image")
        args.out.write_bytes(image)
        print(f"  wrote           : {args.out}")
    print("  hardware access : none")


if __name__ == "__main__":
    main()
