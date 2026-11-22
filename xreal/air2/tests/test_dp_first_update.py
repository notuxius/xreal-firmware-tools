"""Test the post-transfer APP check with simulated connections, without opening HID."""

import unittest
from unittest.mock import patch

from xreal import mcu_flash


class UpdateTests(unittest.TestCase):
    def setUp(self):
        model = mcu_flash.AIR2_PRO
        self.app = mcu_flash.Connection(model, model.app_pid, "unused", 4, "test-pair")
        self.boot = mcu_flash.Connection(model, model.boot_pid, "unused", 0, "")
        self.baseline = mcu_flash.air2_build.build(model.stock.read_bytes())
        self.image = mcu_flash.air2_build.build_dp_first(self.baseline)
        self.info = mcu_flash.check_image(self.image)

    def test_retry_only_app_check_after_completed_transfer(self):
        self.check_app_retry(self.image, self.info)

    def test_immediate_dp_retry_only_app_check_after_completed_transfer(self):
        image = mcu_flash.air2_build.build_dp_first(self.baseline, immediate=True)
        info = mcu_flash.check_image(image)
        self.assertEqual(info.models, {"air2pro"})
        self.check_app_retry(image, info)

    def test_button_variants_retry_only_app_check_after_completed_transfer(self):
        builder = mcu_flash.air2_build
        immediate = builder.build_dp_first(self.baseline, immediate=True)
        trace = builder.build_audio_trace(immediate, timed=True)
        bridge = builder.build_audio_clock_mute(trace, bridge_trace=True)
        for image in (builder.build_usb_to_dp_on_hold(bridge),
                      builder.build_usb_to_dp_on_hold(bridge, stable=True),
                      builder.build_audio_route_buttons(bridge),
                      builder.build_audio_route_buttons(bridge, v2=True)):
            with self.subTest(sha256=builder.sha256(image)):
                info = mcu_flash.check_image(image)
                self.assertEqual(info.models, {"air2pro"})
                self.check_app_retry(image, info)

    def check_app_retry(self, image, info):
        model = self.app.model
        with (
            patch.object(mcu_flash, "unique_connection", return_value=self.app),
            patch.object(mcu_flash, "_open") as open_device,
            patch.object(mcu_flash, "wait_for", side_effect=[
                self.boot, RuntimeError("APP in DP audio"), self.app,
            ]) as wait,
            patch.object(mcu_flash, "_transfer_from_boot") as transfer,
            patch.object(mcu_flash.time, "sleep"),
        ):
            open_device.return_value.app_version.return_value = model.version
            mcu_flash.flash(image, info, self.app)
            transfer.assert_called_once_with(self.boot, image, info)
            self.assertEqual([args.args[1] for args in wait.call_args_list],
                             [model.boot_pid, model.app_pid, model.app_pid])
            self.assertEqual(open_device.return_value.app_version.call_count, 2)

    def test_reject_variant_on_other_models_and_in_boot(self):
        self.assertEqual(self.info.models, {"air2pro"})
        with self.assertRaisesRegex(RuntimeError, "selected model"):
            other = mcu_flash.Connection(mcu_flash.AIR2, 0x0428, "unused", 4, "")
            mcu_flash.flash(self.image, self.info, other)
        with patch.object(mcu_flash, "unique_connection", return_value=self.boot):
            with self.assertRaisesRegex(RuntimeError, "byte-exact stock"):
                mcu_flash.flash(self.image, self.info, self.boot)
