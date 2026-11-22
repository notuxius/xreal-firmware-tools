"""Build the experimental Air 2 Pro DP-to-USB OLED handoff MCU."""

import argparse
from pathlib import Path

from . import build_beam_audio_switch, build_mcu


SHA256 = "889795FF74705B313DD2D891FDC77DB2744F84EA690B998F4DCCDE94BC801D6D"
NAME = "air2-pro-mcu-beam-cover-handoff.bin"
REVERSE_SHA256 = "FC2DB30439E9F2E0FA13B3B84A2CCA9045C8BD24E45E470BB57BFC36A07C6743"
REVERSE_NAME = "air2-pro-mcu-beam-cover-handoff-both-ways.bin"
USB_RETRAIN_SHA256 = "F7D2B3EEC3B0BC069E45A4F39D0B437AA90F78739298B275421457DD06E62CB6"
USB_RETRAIN_NAME = "air2-pro-mcu-beam-cover-handoff-usb-retrain.bin"

# Two reserved words below the v2 button state hold the start tick and the
# phase (idle, waiting, acknowledged) plus the deferred OLED open request.
HELPERS = (
    ("clear the handoff state then run the v2 initializer", 0x1E3B4,
     "0248002101604160fff74abf08c50120"),
    ("start the timeout before the stock DP-to-USB OLED close", 0x1E3D0,
     "10b5e9f73bfc044c206001206060bde81040ecf755be00bf08c50120"),
    ("defer the normal OLED open", 0x1E3F0,
     "0448017919b97cb50348ecf789be01214171704708c5012008020020"),
    ("defer the alternate OLED open", 0x1E410,
     "0448017919b910b50348e8f7f1b802214171704708c5012008020020"),
    ("replay the deferred OLED open in the main loop after ACK or 3000 ms", 0x1E430,
     "10b5fff717ff0d4c2179a9b1022907d0e9f704fc2168401a42f6e06188420bd3617900206060012904d0022904d1e8f7cdf801e0ecf752fe10bd00bf08c50120"),
    ("handle A7 AD page 4 and return BHA1 plus accepted status", 0x1E480,
     "8278042a01d0fff7a5bd094a137913b102231371012303714223037048234370412383703123c37005230b800020704708c50120"),
)

RECORDS = (
    ("reserve the handoff state in the vector stack", 0x40, "10c50120", "08c50120"),
    ("reserve it in the runtime stack", 0x2C8, "10c50120", "08c50120"),
    ("initialize the handoff before the button scanner", 0x1161A, "0cf01bfe", "0cf0cbfe"),
    ("arm before the current OLED close", 0x18692, "f2f7fdfc", "05f09dfe"),
    ("guard the normal OLED open entry", 0xB10C, "7cb57348", "13f070b9"),
    ("guard the alternate OLED open entry", 0x65FC, "10b50848", "17f008bf"),
    ("service the timeout in the main loop", 0xA9CA, "13f04bfc", "13f031fd"),
    ("handle the cover ACK after the existing command and marker checks", 0x1DFD0, "8278042a", "00f056ba"),
)

# The return to DP uses its own start tick and phase below the forward handoff state.
# BHD1 page 5 reads the phase and tick. Page 6 accepts an ACK for that tick only.
REVERSE_HELPERS = (
    ("clear the DP handoff state and chain to the existing initializer", 0x1E4B8,
     "0248002101604160fff778bf00c50120"),
    ("switch after 50 ms for the USB reply or after the 3000 ms timeout", 0x1E4F8,
     "10b5fff799ff114c2179f1b1e9f7a2fb2268801a217902290cbfc82142f6e061884212d300202071"
     "00f096f868b10949097811f0080f08d107490978012904d0034900200870fff799fb10bd00c501207c030020920d0020bd0c0020"),
    ("serve the BHD1 read and validate the ACK tick", 0x1E560,
     "8278052a03d0062a08d0fff789bf70b504460d46194e337932681ae070b504460d46164e002332683179012911d1"
     "e078217940ea0120617940ea0140a17940ea0160904205d1e9f751fb306002233371012342202070482060704420a0"
     "703120e07023716271120aa271120ae271120a227209202880002070bd00bf00c50120"),
    ("keep the USB selection immediate and arm the DP selection", 0x1E600,
     "01280cd110b500f023f838b1074c207920b9e9f71bfb20600120207110bd04490978012901d1fff725bb704700c501207c030020"),
    # The stock host display-settings command can set B8 to DP without tearing down USB.
    ("treat the enabled USB controller as still needing the full DP transition", 0x1E650,
     "0948017859b101290bd108480178012907d10748016801f09001902901d1012070470020704700bf7c0300205b02002010000c40"),
)

REVERSE_RECORDS = (
    ("reserve the DP handoff state in the vector stack", 0x40, "08c50120", "00c50120"),
    ("reserve the DP handoff state in the runtime stack", 0x2C8, "08c50120", "00c50120"),
    ("initialize both handoff states", 0x1161A, "0cf0cbfe", "0cf04dff"),
    ("choose the route after the existing adapter and no-USB checks", 0x1E38A,
     "094989784840012801d1fff76ebc", "00f039b900bf00bf00bf00bf00bf"),
    ("service the return to DP in the main loop", 0xA9CA, "13f031fd", "13f095fd"),
    ("route the DP handoff pages and retain the USB handoff page", 0x1DFD0, "00f056ba", "00f0c6ba"),
)


def build(image: bytes, *, reverse: bool = False) -> bytes:
    expected_input = SHA256 if reverse else build_beam_audio_switch.NO_RETRAIN_MCU_SHA256
    if build_mcu.sha256(image) != expected_input:
        build_mcu.fail("expected the exact existing Air 2 Pro cover handoff MCU" if reverse
                       else "expected the exact Air 2 Pro no-retrain MCU")
    records, helpers = (REVERSE_RECORDS, REVERSE_HELPERS) if reverse else (RECORDS, HELPERS)
    records += tuple((label, offset, "00" * (len(code) // 2), code)
                     for label, offset, code in helpers)
    return apply_records(image, records, REVERSE_SHA256 if reverse else SHA256)


def restore_usb_retrain(image: bytes) -> bytes:
    if build_mcu.sha256(image) != REVERSE_SHA256:
        build_mcu.fail("expected the exact Air 2 Pro bidirectional cover handoff MCU")
    records = (("restore the stock DP-to-USB retrain request", 0x186B2, "00bf00bf", "ecf7dffd"),)
    return apply_records(image, records, USB_RETRAIN_SHA256)


def apply_records(image: bytes, records: tuple[tuple[str, int, str, str], ...], expected_sha256: str) -> bytes:
    out = bytearray(image)
    for label, offset, expected, code in records:
        old = bytes.fromhex(expected)
        new = bytes.fromhex(code)
        if len(old) != len(new) or out[offset:offset + len(old)] != old:
            build_mcu.fail(f"cover handoff guard mismatch at 0x{offset:05X}: {label}")
        out[offset:offset + len(new)] = new
    build_mcu.fix_container_crc(out)
    build_mcu.validate_container(out, "Beam cover handoff MCU")
    if build_mcu.sha256(out) != expected_sha256:
        build_mcu.fail("cover handoff image differs from its pinned SHA-256")
    return bytes(out)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mcu", required=True, type=Path)
    parser.add_argument("--out", type=Path)
    variant = parser.add_mutually_exclusive_group()
    variant.add_argument("--reverse", action="store_true", help="add USB-to-DP handoff to the current cover MCU")
    variant.add_argument("--restore-usb-retrain", action="store_true",
                         help="restore the DP-to-USB retrain in the bidirectional cover MCU")
    args = parser.parse_args()
    source = args.mcu.read_bytes()
    if args.restore_usb_retrain:
        image = restore_usb_retrain(source)
        name = USB_RETRAIN_NAME
    else:
        image = build(source, reverse=args.reverse)
        name = REVERSE_NAME if args.reverse else NAME
    out = args.out or Path(name)
    with out.open("xb") as target:
        target.write(image)
    print(out)


if __name__ == "__main__":
    main()
