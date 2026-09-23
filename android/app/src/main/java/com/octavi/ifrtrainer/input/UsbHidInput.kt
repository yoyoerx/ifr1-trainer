package com.octavi.ifrtrainer.input

/**
 * Placeholder for Octavi IFR-1 input over Android USB — deliberately NOT
 * implemented yet. This is the hard-gate spike from docs/ANDROID_PORT_PLAN.md
 * §3.1: whether the device surfaces as ordinary `InputDevice` key/motion
 * events (ideal — no code needed here beyond a standard `InputManager`
 * listener) or requires raw `UsbManager`/`UsbDeviceConnection` HID reports
 * (mirroring `ifr1.py`'s already-hardware-confirmed byte offsets), or is
 * blocked outright by the kernel `usbhid` driver claiming it, is unknown
 * and must be tested on real Pixel 9 + IFR-1 hardware before this class is
 * written for real — see §3.1's spike steps (`adb shell dumpsys usb`,
 * `adb shell getevent -lt`).
 *
 * `ifr1.py`'s `LAYOUT`/`Event`/`Mode` shapes are the target contract this
 * should eventually reproduce, whichever path the spike confirms.
 */
object UsbHidInput {
    // Intentionally empty. See class doc.
}
