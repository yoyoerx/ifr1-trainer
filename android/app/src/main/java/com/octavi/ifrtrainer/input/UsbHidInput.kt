package com.octavi.ifrtrainer.input

import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.hardware.usb.UsbConstants
import android.hardware.usb.UsbDevice
import android.hardware.usb.UsbDeviceConnection
import android.hardware.usb.UsbEndpoint
import android.hardware.usb.UsbInterface
import android.hardware.usb.UsbManager
import android.os.Build
import android.os.Handler
import android.os.Looper
import androidx.core.content.ContextCompat

/**
 * Raw-HID reader for the Octavi IFR-1 over Android's USB host API - the
 * Kotlin counterpart to `ifr1.py`'s `IFR1` class, reproducing the exact same
 * hardware-confirmed byte layout.
 *
 * WHY RAW HID, NOT `InputDevice`/`KeyEvent`: the §3.1 spike (real Pixel 9 +
 * real IFR-1, docs/ANDROID_PORT_PLAN.md, "Spike result 2026-09-25") found
 * that Android's kernel `usbhid` driver already claims the device and
 * translates every button plus the *outer* knob into standard
 * `BTN_*`/`REL_DIAL` evdev events - the "ideal case," no app code needed for
 * those. But the *inner* knob produces zero evdev events at all: the
 * kernel's default HID-usage table has no entry for whatever usage its
 * field declares, so `usbhid` silently drops it before anything above the
 * kernel ever sees it. There is no `InputDevice`-level way to recover a
 * field the kernel's own translation already discarded.
 *
 * The fix is to not depend on that translation for *anything*: this class
 * claims the HID interface itself with `force = true`, which detaches
 * `usbhid` from it (the Android equivalent of what `hidapi` does implicitly
 * on desktop), and reads the same raw interrupt-IN report `ifr1.py` already
 * parses via `hidapi`. That trades the free button/outer-knob translation
 * for one unified code path matching the desktop protocol exactly - every
 * control decoded the same way, everywhere, instead of buttons coming from
 * one Android API and the inner knob needing a different one.
 *
 * UNVERIFIED: written from the real hardware topology the §3.1 spike's
 * `dumpsys usb` captured (one HID interface, class 3, one interrupt-IN
 * endpoint, 64-byte packets) and `ifr1.py`'s own hardware-confirmed byte
 * offsets, but this specific Kotlin code has not yet been compiled or run -
 * no Android SDK/Gradle in the authoring environment. The first real build
 * + run against the actual IFR-1 is what proves `claimInterface(force =
 * true)` actually succeeds here (same caveat as the rest of `android/` -
 * see `android/README.md`), and is expected to need small fixes.
 */

// -- protocol constants, mirrors ifr1.py's module-level constants exactly -------------------
private const val IFR1_VENDOR_ID = 0x04D8
private const val IFR1_PRODUCT_ID = 0xE6D6
private const val REPORT_ID = 0x0B
private const val READ_SIZE = 64          // ifr1.py READ_SIZE - generous; the real report is ~8 bytes
private const val LONG_PRESS_MS = 600L    // ifr1.py LONG_PRESS_S = 0.6
private const val READ_TIMEOUT_MS = 250   // re-checks `running` regularly rather than blocking forever
private const val ACTION_USB_PERMISSION = "com.octavi.ifrtrainer.USB_PERMISSION"

/** Rotary mode-selector position, byte offset 7 - ifr1.py's `Mode` IntEnum. */
enum class Ifr1Mode(val raw: Int) {
    COM1(0), COM2(1), NAV1(2), NAV2(3), FMS1(4), FMS2(5), AP(6), XPDR(7);

    companion object {
        fun fromRaw(v: Int): Ifr1Mode = entries.firstOrNull { it.raw == (v and 0x0F) } ?: COM1
    }
}

/** Byte offsets into the canonical frame - ifr1.py's `Layout` dataclass, same names/values. */
private object Ifr1Layout {
    const val OUTER = 5
    const val INNER = 6
    const val MODE = 7

    // button name -> (frame byte index, bitmask) - ifr1.py's Layout.buttons, confirmed on real
    // hardware one at a time in the §3.1 spike (the BTN_* evdev codes those bits happened to
    // translate to are irrelevant here - this reads the same underlying bits directly).
    val BUTTONS: Map<String, Pair<Int, Int>> = mapOf(
        "DCT" to (1 to 0x10), "MNU" to (1 to 0x20), "CLR" to (1 to 0x40), "ENT" to (1 to 0x80),
        "SWAP" to (2 to 0x01), "KNOB" to (2 to 0x02), "AP" to (2 to 0x40), "HDG" to (2 to 0x80),
        "NAV" to (3 to 0x01), "APR" to (3 to 0x02), "ALT" to (3 to 0x04), "VS" to (3 to 0x08),
    )

    // bits per frame byte already accounted for above - ifr1.py's Layout.known_mask, used to
    // flag unmapped buttons (byte 4, "further buttons", is entirely unconfirmed on both platforms)
    val KNOWN_MASK: Map<Int, Int> = mapOf(1 to 0xF0, 2 to 0xC3, 3 to 0x0F, 4 to 0x00)
}

/** Decoded snapshot of one input report - ifr1.py's `State`. */
data class Ifr1State(
    val mode: Ifr1Mode = Ifr1Mode.COM1,
    val buttons: Set<String> = emptySet(),
    val outer: Int = 0,                        // signed delta this report
    val inner: Int = 0,                        // signed delta this report
    val shift: Boolean = false,                // SWAP held -> encoders address the shifted axis
    val unknownBits: List<Pair<Int, Int>> = emptyList(),   // (frame index, raw byte)
    val raw: List<Int> = emptyList(),
) {
    companion object {
        fun decode(frame: IntArray): Ifr1State {
            val pressed = Ifr1Layout.BUTTONS.filterValues { (idx, mask) ->
                idx < frame.size && (frame[idx] and mask) != 0
            }.keys
            val unknown = Ifr1Layout.KNOWN_MASK.mapNotNull { (idx, known) ->
                if (idx < frame.size && (frame[idx] and known.inv() and 0xFF) != 0) {
                    idx to frame[idx]
                } else {
                    null
                }
            }
            return Ifr1State(
                mode = Ifr1Mode.fromRaw(frame.getOrElse(Ifr1Layout.MODE) { 0 }),
                buttons = pressed,
                outer = sByte(frame.getOrElse(Ifr1Layout.OUTER) { 0 }),
                inner = sByte(frame.getOrElse(Ifr1Layout.INNER) { 0 }),
                shift = "SWAP" in pressed,
                unknownBits = unknown,
                raw = frame.take(8),
            )
        }
    }
}

/** Edge-triggered change between two consecutive states - ifr1.py's `Event`. */
data class Ifr1Event(
    val mode: Ifr1Mode,
    val modeChanged: Boolean = false,
    val pressed: List<String> = emptyList(),   // buttons that went down this report
    val released: List<String> = emptyList(),  // buttons that came up this report
    val outer: Int = 0,                        // accumulated outer delta
    val inner: Int = 0,                        // accumulated inner delta
    val shift: Boolean = false,
    val longPress: List<String> = emptyList(), // buttons that just crossed LONG_PRESS_MS
) {
    val isEmpty: Boolean
        get() = !(
            modeChanged || pressed.isNotEmpty() || released.isNotEmpty() ||
                outer != 0 || inner != 0 || longPress.isNotEmpty()
            )
}

/** Interpret an unsigned byte as a signed 8-bit value - ifr1.py's `_sbyte`. */
private fun sByte(v: Int): Int {
    val b = v and 0xFF
    return if (b > 127) b - 256 else b
}

/**
 * Pad/normalize a raw read to the canonical 8+ byte frame - ifr1.py's
 * `normalize_frame`. `UsbDeviceConnection.bulkTransfer` hands back exactly
 * what the interrupt-IN transfer received (Android's USB host API doesn't
 * strip the report-id byte the way some desktop hidapi backends do), but
 * this still pads a short/empty read so fixed-offset decoding stays safe.
 */
private fun normalizeFrame(data: ByteArray, length: Int): IntArray {
    if (length <= 0) return IntArray(8)
    val unsigned = IntArray(length) { data[it].toInt() and 0xFF }
    val framed = if (unsigned[0] != REPORT_ID) intArrayOf(REPORT_ID) + unsigned else unsigned
    return if (framed.size < 8) framed + IntArray(8 - framed.size) else framed
}

/**
 * Opens, polls and decodes the Octavi IFR-1 as raw USB HID.
 *
 * Usage:
 * ```
 * val input = UsbHidInput(context)
 * input.listener = UsbHidInput.Listener { event -> ... }
 * input.start()   // finds + claims the device if already attached/permitted
 * // ...
 * input.stop()
 * ```
 *
 * `start()` is also the right thing to call from an Activity that was
 * launched via the manifest's `USB_DEVICE_ATTACHED` intent-filter (see
 * `AndroidManifest.xml` / `res/xml/usb_device_filter.xml`) - permission is
 * already granted in that case, so `start()` goes straight to
 * [openAndClaim]; otherwise it falls back to [requestPermission]'s runtime
 * dialog.
 */
class UsbHidInput(private val context: Context) {

    fun interface Listener {
        /** Called on the main thread for every non-empty [Ifr1Event]. */
        fun onEvent(event: Ifr1Event)
    }

    var listener: Listener? = null

    private val usbManager: UsbManager =
        context.getSystemService(Context.USB_SERVICE) as UsbManager
    private val mainHandler = Handler(Looper.getMainLooper())

    private var connection: UsbDeviceConnection? = null
    private var claimedInterface: UsbInterface? = null
    private var endpointIn: UsbEndpoint? = null
    private var readThread: Thread? = null
    @Volatile private var running = false
    private var permissionReceiverRegistered = false

    private var prev: Ifr1State? = null
    private val pressTimeMs = HashMap<String, Long>()
    private val longFired = HashSet<String>()
    private var ledBitmask = 0

    private val permissionReceiver = object : BroadcastReceiver() {
        override fun onReceive(receiverContext: Context, intent: Intent) {
            if (intent.action != ACTION_USB_PERMISSION) return
            val device = deviceExtra(intent)
            val granted = intent.getBooleanExtra(UsbManager.EXTRA_PERMISSION_GRANTED, false)
            if (granted && device != null) {
                openAndClaim(device)
            }
            unregisterPermissionReceiver()
        }
    }

    /**
     * Finds the IFR-1 (if attached) and either opens it immediately
     * (permission already granted - the common case when launched via the
     * manifest's `USB_DEVICE_ATTACHED` intent-filter) or requests it. A
     * no-op if already running, or if no IFR-1 is currently attached.
     */
    fun start() {
        if (running) return
        val device = findDevice() ?: return
        if (usbManager.hasPermission(device)) {
            openAndClaim(device)
        } else {
            requestPermission(device)
        }
    }

    /** Releases the interface, closes the connection, stops the read thread. */
    fun stop() {
        running = false
        readThread?.interrupt()
        readThread = null
        unregisterPermissionReceiver()
        try {
            claimedInterface?.let { connection?.releaseInterface(it) }
        } catch (_: Exception) {
            // best-effort - the interface may already be gone (device unplugged)
        }
        connection?.close()
        connection = null
        claimedInterface = null
        endpointIn = null
        prev = null
        pressTimeMs.clear()
        longFired.clear()
    }

    /**
     * AP-row LEDs: bit0 AP, bit1 HDG, bit2 NAV, bit3 APR, bit4 ALT, bit5 VS
     * - ifr1.py's `set_leds`. Sent as a HID SET_REPORT control transfer
     * (bmRequestType 0x21, bRequest 0x09), since the §3.1 spike's captured
     * descriptor shows only one endpoint on the HID interface and it's
     * IN-direction - there is no interrupt-OUT endpoint to write to, which
     * is the normal situation for a HID device whose output report is small
     * and infrequent. **Untested** - the spike exercised input only.
     */
    fun setLeds(bitmask: Int) {
        ledBitmask = bitmask and 0xFF
        val conn = connection ?: return
        val ifaceNumber = claimedInterface?.id ?: return
        val payload = byteArrayOf(REPORT_ID.toByte(), ledBitmask.toByte())
        conn.controlTransfer(
            0x21,                             // host-to-device | class | interface
            0x09,                             // HID SET_REPORT
            (0x02 shl 8) or REPORT_ID,         // report type 0x02 (Output) | report id
            ifaceNumber,
            payload,
            payload.size,
            1000,
        )
    }

    // -- device discovery / permission -----------------------------------------------------
    private fun findDevice(): UsbDevice? =
        usbManager.deviceList.values.firstOrNull {
            it.vendorId == IFR1_VENDOR_ID && it.productId == IFR1_PRODUCT_ID
        }

    private fun requestPermission(device: UsbDevice) {
        val flags = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            PendingIntent.FLAG_MUTABLE
        } else {
            0
        }
        val permissionIntent = PendingIntent.getBroadcast(
            context,
            0,
            Intent(ACTION_USB_PERMISSION).setPackage(context.packageName),
            flags,
        )
        if (!permissionReceiverRegistered) {
            ContextCompat.registerReceiver(
                context,
                permissionReceiver,
                IntentFilter(ACTION_USB_PERMISSION),
                ContextCompat.RECEIVER_NOT_EXPORTED,
            )
            permissionReceiverRegistered = true
        }
        usbManager.requestPermission(device, permissionIntent)
    }

    private fun unregisterPermissionReceiver() {
        if (!permissionReceiverRegistered) return
        try {
            context.unregisterReceiver(permissionReceiver)
        } catch (_: IllegalArgumentException) {
            // already unregistered
        }
        permissionReceiverRegistered = false
    }

    @Suppress("DEPRECATION")
    private fun deviceExtra(intent: Intent): UsbDevice? =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            intent.getParcelableExtra(UsbManager.EXTRA_DEVICE, UsbDevice::class.java)
        } else {
            intent.getParcelableExtra(UsbManager.EXTRA_DEVICE)
        }

    // -- open + claim (force = true, detaching the kernel's usbhid driver) -----------------
    private fun openAndClaim(device: UsbDevice) {
        val hidInterface = (0 until device.interfaceCount)
            .map { device.getInterface(it) }
            .firstOrNull { it.interfaceClass == UsbConstants.USB_CLASS_HID }
            ?: return  // no HID interface - nothing to claim (shouldn't happen per the spike)
        val inEndpoint = (0 until hidInterface.endpointCount)
            .map { hidInterface.getEndpoint(it) }
            .firstOrNull {
                it.direction == UsbConstants.USB_DIR_IN &&
                    (
                        it.type == UsbConstants.USB_ENDPOINT_XFER_INT ||
                            it.type == UsbConstants.USB_ENDPOINT_XFER_BULK
                        )
            }
            ?: return  // no readable endpoint on the HID interface

        val conn = usbManager.openDevice(device) ?: return
        // force=true detaches usbhid from this interface - see the file header for why. If this
        // returns false, usbhid held on (or some other claimant did); there is no fallback path
        // today - that would be the "true blocker" case the plan's §3.1 step 4 described.
        if (!conn.claimInterface(hidInterface, true)) {
            conn.close()
            return
        }

        connection = conn
        claimedInterface = hidInterface
        endpointIn = inEndpoint
        running = true
        startReadThread()
    }

    // -- read loop --------------------------------------------------------------------------
    private fun startReadThread() {
        val thread = Thread({ readLoop() }, "Ifr1UsbHidRead")
        thread.isDaemon = true
        readThread = thread
        thread.start()
    }

    private fun readLoop() {
        val conn = connection ?: return
        val endpoint = endpointIn ?: return
        val buffer = ByteArray(READ_SIZE)
        while (running) {
            // A short timeout re-checks `running` regularly instead of blocking forever on
            // stop() - the Kotlin analogue of ifr1.py's non-blocking dev.read() drained every
            // ~30 Hz loop tick; Android's bulkTransfer has no non-blocking "nothing pending"
            // mode, so this dedicated thread blocks in short slices instead.
            val n = conn.bulkTransfer(endpoint, buffer, buffer.size, READ_TIMEOUT_MS)
            val now = System.currentTimeMillis()
            if (n > 0) {
                val frame = normalizeFrame(buffer, n)
                val state = Ifr1State.decode(frame)
                dispatchDiff(state, now)
                prev = state
            }
            dispatchHolds(now)
        }
    }

    // -- diffing (state -> edge-triggered Event) - mirrors ifr1.py's IFR1._diff/_check_holds -
    private fun dispatchDiff(state: Ifr1State, now: Long) {
        val previous = prev
        if (previous == null) {
            for (b in state.buttons) pressTimeMs[b] = now
            emit(Ifr1Event(mode = state.mode, modeChanged = true, shift = state.shift))
            return
        }
        val newlyPressed = state.buttons - previous.buttons
        val newlyReleased = previous.buttons - state.buttons
        for (b in newlyPressed) pressTimeMs[b] = now
        for (b in newlyReleased) {
            pressTimeMs.remove(b)
            longFired.remove(b)
        }
        val event = Ifr1Event(
            mode = state.mode,
            modeChanged = state.mode != previous.mode,
            pressed = newlyPressed.sorted(),
            released = newlyReleased.sorted(),
            outer = state.outer,
            inner = state.inner,
            shift = state.shift,
        )
        if (!event.isEmpty) emit(event)
    }

    /** Buttons still held whose press just crossed [LONG_PRESS_MS] for the first time this
     * hold - ifr1.py's `_check_holds`, checked every loop pass (not just on a new report),
     * since a sustained press with nothing else moving produces no further reports. */
    private fun dispatchHolds(now: Long) {
        val previous = prev ?: return
        val fired = previous.buttons.filter { b ->
            val t0 = pressTimeMs[b]
            t0 != null && b !in longFired && now - t0 >= LONG_PRESS_MS
        }.sorted()
        if (fired.isNotEmpty()) {
            longFired.addAll(fired)
            emit(Ifr1Event(mode = previous.mode, longPress = fired))
        }
    }

    private fun emit(event: Ifr1Event) {
        mainHandler.post { listener?.onEvent(event) }
    }
}
