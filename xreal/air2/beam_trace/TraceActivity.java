package org.xrealdiag.audiotrace;

import android.app.Activity;
import android.app.PendingIntent;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.hardware.usb.UsbConstants;
import android.hardware.usb.UsbDevice;
import android.hardware.usb.UsbDeviceConnection;
import android.hardware.usb.UsbEndpoint;
import android.hardware.usb.UsbInterface;
import android.hardware.usb.UsbManager;
import android.os.Bundle;
import android.util.Log;
import android.widget.ScrollView;
import android.widget.TextView;
import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.util.Arrays;
import java.util.Locale;
import java.util.zip.CRC32;

public final class TraceActivity extends Activity {
    private static final String TAG = "XrealAudioTrace";
    private static final String PERMISSION = "org.xrealdiag.audiotrace.USB_PERMISSION";
    private static final int COMMAND = 0x2B;
    private static final int IN_ENDPOINT = 0x86, OUT_ENDPOINT = 0x07;
    private static final int TIMED_ROWS = 28;
    private static final int TIMED_PAGE_BYTES = 28;
    private UsbManager usb;
    private TextView status;
    private final BroadcastReceiver permission = new BroadcastReceiver() {
        @Override public void onReceive(Context context, Intent intent) {
            if (!PERMISSION.equals(intent.getAction())) return;
            UsbDevice device = intent.getParcelableExtra(UsbManager.EXTRA_DEVICE);
            if (isGlasses(device) && usb.hasPermission(device)) {
                read(device);
            } else {
                show("USB permission was not granted. Reopen the reader to try again.");
            }
        }
    };

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        status = new TextView(this);
        status.setPadding(32, 32, 32, 32);
        status.setTextSize(19);
        ScrollView scroll = new ScrollView(this);
        scroll.addView(status);
        setContentView(scroll);
        usb = (UsbManager) getSystemService(USB_SERVICE);
        registerReceiver(permission, new IntentFilter(PERMISSION));
        for (UsbDevice device : usb.getDeviceList().values()) {
            if (!isGlasses(device)) continue;
            show("Reading the Air 2 Pro trace…");
            if (usb.hasPermission(device)) read(device);
            else usb.requestPermission(device, PendingIntent.getBroadcast(this, 0,
                new Intent(PERMISSION).setPackage(getPackageName()),
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_MUTABLE));
            return;
        }
        show("Air 2 Pro USB control is unavailable. Switch to USB audio without unplugging, then reopen this app.");
    }

    @Override public void onDestroy() {
        unregisterReceiver(permission);
        super.onDestroy();
    }

    private static boolean isGlasses(UsbDevice device) {
        return device != null && device.getVendorId() == 0x3318 && device.getProductId() == 0x0432;
    }

    private void show(String message) {
        for (String line : message.split("\\n")) Log.i(TAG, line);
        runOnUiThread(() -> status.setText(message));
    }

    private void read(UsbDevice device) {
        boolean muteProbe = getIntent().getBooleanExtra("muteProbe", false);
        new Thread(() -> {
            try {
                show(describe(readTrace(device), muteProbe));
            } catch (Exception error) {
                Log.w(TAG, "USB trace read failed", error);
                show("Trace read failed: " + error);
            }
        }, "air2-trace").start();
    }

    private byte[] readTrace(UsbDevice device) throws Exception {
        for (int i = 0; i < device.getInterfaceCount(); i++) {
            UsbInterface control = device.getInterface(i);
            if (control.getId() != 4) continue;
            UsbEndpoint in = null, out = null;
            for (int j = 0; j < control.getEndpointCount(); j++) {
                UsbEndpoint endpoint = control.getEndpoint(j);
                if (endpoint.getType() != UsbConstants.USB_ENDPOINT_XFER_INT) continue;
                if (endpoint.getAddress() == IN_ENDPOINT) in = endpoint;
                if (endpoint.getAddress() == OUT_ENDPOINT) out = endpoint;
            }
            if (in == null || out == null) break;
            UsbDeviceConnection connection = usb.openDevice(device);
            if (connection == null) throw new IOException("could not open the glasses");
            try {
                if (!connection.claimInterface(control, true)) throw new IOException("control interface busy");
                try {
                    byte[] first = readPage(connection, in, out, 0);
                    if (!isTimed(first[3])) return first;
                    if ((first[4] & 255) >= TIMED_ROWS || (first[5] & 255) > TIMED_ROWS) {
                        throw new IOException("invalid trace ring header");
                    }
                    byte[] trace = Arrays.copyOf(first, 8 + TIMED_ROWS * 4);
                    for (int page = 1; page < 4; page++) {
                        byte[] next = readPage(connection, in, out, page);
                        if (next[3] != first[3] || next[4] != first[4] || next[5] != first[5]) {
                            throw new IOException("trace changed during USB readout");
                        }
                        System.arraycopy(next, 8, trace, 8 + page * TIMED_PAGE_BYTES, TIMED_PAGE_BYTES);
                    }
                    return trace;
                } finally {
                    connection.releaseInterface(control);
                }
            } finally {
                connection.close();
            }
        }
        throw new IOException("Air 2 Pro USB control endpoints unavailable");
    }

    private static byte[] readPage(UsbDeviceConnection connection, UsbEndpoint in, UsbEndpoint out, int page)
            throws Exception {
        byte[] packet = new byte[64];
        packet[0] = (byte) 0xFD;
        packet[5] = 20;
        packet[15] = COMMAND;
        packet[22] = (byte) 0xA7;
        packet[23] = (byte) 0xAD;
        packet[24] = (byte) page;
        CRC32 crc = new CRC32();
        crc.update(packet, 5, packet[5]);
        ByteBuffer.wrap(packet).order(ByteOrder.LITTLE_ENDIAN).putInt(1, (int) crc.getValue());
        for (int attempt = 0; attempt < 2; attempt++) {
            // Linux usbfs also accepts interrupt endpoints on the synchronous bulk ioctl.
            if (connection.bulkTransfer(out, packet, packet.length, 1500) != packet.length) {
                throw new IOException("incomplete USB interrupt write");
            }
            byte[] trace = receiveTrace(connection, in, page);
            if (trace != null) return trace;
        }
        throw new IOException("the glasses did not return diagnostic page " + page);
    }

    private static byte[] receiveTrace(UsbDeviceConnection connection, UsbEndpoint endpoint, int page)
            throws Exception {
        for (int attempt = 0; attempt < 4; attempt++) {
            byte[] data = new byte[64];
            int length = connection.bulkTransfer(endpoint, data, data.length, 1500);
            if (length < 0) return null;
            Log.d(TAG, "USB response (" + length + " bytes): " + Arrays.toString(Arrays.copyOf(data, length)));
            if (length < 27 || (data[0] & 255) != 0xFD || data[15] != COMMAND
                || data[6] != 0 || data[16] != 0 || data[22] != 0
                || data[23] != 'A' || data[24] != 'T' || data[25] != 'R') continue;
            int frameLength = data[5] & 255;
            boolean timed = isTimed(data[26]);
            if (timed) {
                if (frameLength != 54 || length < 59 || data[29] != page) continue;
            } else if (page != 0 || frameLength != 58 || length < 63 || (data[26] != '1' && data[26] != '2')) {
                continue;
            }
            CRC32 crc = new CRC32();
            crc.update(data, 5, frameLength);
            if ((int) crc.getValue() == ByteBuffer.wrap(data).order(ByteOrder.LITTLE_ENDIAN).getInt(1)) {
                return Arrays.copyOfRange(data, 23, 5 + frameLength);
            }
        }
        return null;
    }

    private static String describe(byte[] trace, boolean muteProbe) {
        if (isTimed(trace[3])) return describeTimed(trace, muteProbe);
        boolean idle = trace[3] == '2';
        StringBuilder text = new StringBuilder(idle ? "Air 2 Pro audio idle trace" : "Air 2 Pro bridge audio trace");
        String[] labels = idle ? new String[]{"Last locked", "First unlocked", "Later unlocked", "Latest"}
            : new String[]{"Last 2D", "First 3D", "Settled 3D", "Last 3D"};
        int flags = trace[4] & 255;
        for (int i = 0; i < labels.length; i++) {
            text.append("\n").append(labels[i]).append("  ");
            int validFlag = !idle && i == 3 ? 2 : 1 << i;
            if ((flags & validFlag) == 0) {
                text.append("missing");
                continue;
            }
            int offset = 8 + 8 * i;
            int tick = (trace[offset] & 255) | ((trace[offset + 1] & 255) << 8);
            text.append(String.format(Locale.ROOT,
                "t=%d ms  valid=%X  %s=%02X  C67C=%02X  E2EB=%02X  D423=%02X  D424=%02X",
                tick, trace[offset + 2], idle ? "MCU AMP" : "B9", trace[offset + 3], trace[offset + 4], trace[offset + 5],
                trace[offset + 6], trace[offset + 7]));
        }
        if (idle) return text.toString();
        text.append(String.format(Locale.ROOT, "\nLast MCU amp state 2D=%02X  3D=%02X",
            trace[6], trace[7]));
        return text.append("\n3D samples: ").append(trace[5] & 255).toString();
    }

    private static String describeTimed(byte[] trace, boolean muteProbe) {
        int next = trace[4] & 255;
        int count = trace[5] & 255;
        boolean receiver = trace[3] == '4';
        String title = "Air 2 Pro timed amplifier trace (ATR3)";
        if (receiver) {
            title = muteProbe ? "Air 2 Pro receiver bit-4 probe (ATR4)"
                : "Air 2 Pro receiver and amplifier trace (ATR4)";
        }
        StringBuilder text = new StringBuilder(title).append("\nSamples: ").append(count);
        if (count == 0) return text.toString();
        ByteBuffer ring = ByteBuffer.wrap(trace).order(ByteOrder.LITTLE_ENDIAN);
        int latest = (next + TIMED_ROWS - 1) % TIMED_ROWS;
        int latestTick = ring.getShort(8 + latest * 4) & 65535;
        for (int age = count; age > 0; age--) {
            int offset = 8 + ((next + TIMED_ROWS - age) % TIMED_ROWS) * 4;
            int elapsed = (latestTick - (ring.getShort(offset) & 65535)) & 65535;
            text.append(String.format(Locale.ROOT, "\n%d ms   34 %s", -elapsed,
                describeAmp(trace[offset + 2])));
            byte second = trace[offset + 3];
            text.append(receiver ? "   DP " + describeReceiver(second, muteProbe) : "   35 " + describeAmp(second));
        }
        return text.toString();
    }

    private static boolean isTimed(byte version) {
        return version == '3' || version == '4';
    }

    private static String describeReceiver(byte status, boolean muteProbe) {
        if ((status & 255) == 0xFE) return "I2C page select failed";
        if ((status & 255) == 0xFF) return "I2C read failed";
        if ((status & 0xE0) != 0xA0) {
            return String.format(Locale.ROOT, "no receiver marker (%02X)", status & 255);
        }
        if (muteProbe) {
            return String.format(Locale.ROOT, "%02X E730.4=%d E735.4=%d E736.4=%d E737.4=%d",
                status & 255, (status >> 1) & 1, (status >> 2) & 1, (status >> 3) & 1, (status >> 4) & 1);
        }
        if ((status & 0xFE) == 0xA2) return "DP audio route inactive";
        String rate = (status & 4) == 0 ? "invalid" : (status & 2) == 0 ? "absent" : "present";
        return String.format(Locale.ROOT, "%02X RATE=%s PLL=%d I2S_PINS=%d",
            status & 255, rate, (status >> 3) & 1, (status >> 4) & 1);
    }

    private static String describeAmp(byte status) {
        if ((status & 0x80) == 0) return "unavailable";
        return String.format(Locale.ROOT, "%02X PLL=%d CLK=%d LOST=%d SW=%d MUTE=%d AMP_PD=%d PD=%d",
            status & 255, status & 1, (status >> 4) & 1, (status >> 5) & 1,
            (status >> 1) & 1, (status >> 6) & 1, (status >> 3) & 1, (status >> 2) & 1);
    }
}
