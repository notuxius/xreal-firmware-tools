"""Build the experimental Air 2 Pro pair for audio switches without display retraining."""

import argparse
from pathlib import Path

from . import build_dp, build_mcu


MCU_SHA256 = "888304EF913A2D3C187F13A9DFACED632A40A2BF5354BAFB430F62FD3675AEB3"
NO_RETRAIN_MCU_SHA256 = "2652B23314D745FB08D21CA9050AF66526E6B5969F8EC224E027E1181E717D89"
DP_SHA256 = "562BEFE8493229E6D57B31A601BF1AEAE4EA19095B7D53B29AFBF0A9AC4CE2F5"
MCU_NAME = "air2-pro-mcu-beam-audio-switch.bin"
NO_RETRAIN_MCU_NAME = "air2-pro-mcu-beam-audio-switch-no-retrain.bin"
DP_NAME = "air2-pro-dp-beam-audio-switch.bin"


def build(mcu: bytes, dp: bytes, *, skip_retrain_both_ways: bool = False) -> tuple[bytes, bytes]:
    if build_mcu.sha256(mcu) != build_mcu.AUDIO_ROUTE_BUTTONS_V2_SHA256:
        build_mcu.fail("expected the exact Air 2 Pro route-buttons v2 MCU image")
    if build_dp.sha256(dp) != build_dp.AUDIO_MUTE_PROBE_SHA256:
        build_dp.fail("expected the exact Air 2 Pro DP audio mute-probe image")

    mcu_image = bytearray(mcu)
    dp_image = bytearray(dp)
    # Keep the USB-to-DP route changes and leave the DP retrain request unset.
    mcu_image[0x1862E:0x18632] = bytes.fromhex("00bf00bf")
    if skip_retrain_both_ways:
        mcu_image[0x186B2:0x186B6] = bytes.fromhex("00bf00bf")
    # Let the non-DP EDID path reach the same LPCM template as the DP path.
    dp_image[0x1560] = 0
    build_mcu.fix_container_crc(mcu_image)
    build_dp.fix_bank0_tag(dp_image)
    build_dp.fix_container_crc(dp_image)
    build_mcu.validate_container(mcu_image, "Beam audio switch MCU")
    build_dp.validate_container(dp_image, "Beam audio switch DP")
    expected_mcu = NO_RETRAIN_MCU_SHA256 if skip_retrain_both_ways else MCU_SHA256
    if build_mcu.sha256(mcu_image) != expected_mcu or build_dp.sha256(dp_image) != DP_SHA256:
        build_mcu.fail("Beam audio switch images differ from the pinned output hashes")
    return bytes(mcu_image), bytes(dp_image)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mcu", required=True, type=Path)
    parser.add_argument("--dp", required=True, type=Path)
    parser.add_argument("--skip-retrain-both-ways", action="store_true", help="also skip retraining when switching to USB")
    parser.add_argument("--out-dir", type=Path, default=Path.cwd())
    args = parser.parse_args()
    mcu_name = NO_RETRAIN_MCU_NAME if args.skip_retrain_both_ways else MCU_NAME
    outputs = (args.out_dir / mcu_name, args.out_dir / DP_NAME)
    if not args.out_dir.is_dir() or any(path.exists() for path in outputs):
        parser.error("use an existing output directory without either candidate image")
    images = build(args.mcu.read_bytes(), args.dp.read_bytes(), skip_retrain_both_ways=args.skip_retrain_both_ways)
    for path, image in zip(outputs, images):
        with path.open("xb") as output:
            output.write(image)
        print(path)


if __name__ == "__main__":
    main()
