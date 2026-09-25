package com.octavi.ifrtrainer.input

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.pointer.PointerId
import androidx.compose.ui.input.pointer.changedToUpIgnoreConsumed
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.input.pointer.positionChanged
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlin.math.abs
import kotlin.math.atan2

/**
 * Touch/rotary controls (docs/ANDROID_PORT_PLAN.md §3.2) - a virtual
 * emulation of the IFR-1's twist knobs and the real GNS 530's own bezel
 * buttons, for development/testing without the hardware attached, and as
 * the eventual portrait (§6 Phase 2) control surface.
 *
 * **Design, v2 (2026-09-25, after real-device feedback on v1):** v1 put
 * every button (including the GNS's own CDI/OBS/MSG/FPL/VNAV/PROC keys,
 * relabeled from the physical IFR-1's AP-row names) in one generic panel
 * below all the instrument boxes - functional, but you couldn't see the
 * GNS screen while pressing its own buttons, and the AP-row buttons
 * appeared to belong to the GNS bezel when they're actually a different
 * physical unit's keys (`main.py`'s `_FMS_BEZEL` only *reuses* the AP
 * row's physical buttons for GNS softkeys while the mode dial reads
 * FMS1/FMS2 - it isn't the GNS's own key set). v2 fixes both: touch
 * targets for the GNS's own real bezel buttons sit directly on the GNS
 * `InstrumentCanvas` (see [GnsBezelOverlay], positioned to match
 * `render_commands.gns_commands`'s own two bezel key rows pixel-for-pixel
 * - both must be kept in sync by hand, same category of cross-language
 * duplication as this whole render bridge already accepts), the AP
 * panel's own 5 wired buttons sit on *its* canvas ([ApPanelOverlay]), and
 * only what has no dedicated rendered panel yet (COM/NAV/XPDR - no
 * radio-stack instrument exists on Android yet) keeps a small, clearly
 * separate fallback cluster ([RadioControlPanel]). Nothing is duplicated
 * across two places any more.
 *
 * Every composable here feeds the exact same [Ifr1Event]/[Ifr1Mode] fields
 * and the same `BrainBridge.dispatchEvent` call path real [UsbHidInput]
 * events already use (see `Phase1LoopScreen.kt`'s wiring) - touch is a
 * second, independent event *source*, not a separate code path through
 * the brain. `main.route_event` only ever reads the edge-triggered
 * `pressed`/`long_press` sets (never `released`), so a tap needs to emit
 * only `pressed`.
 */

// One detent per this many degrees of drag rotation. v1 shipped at 18 deg
// and real-device feedback (2026-09-25) was that it was too sensitive to
// dial in a specific number - doubled here. Still a "feels about right"
// rotary-encoder emulation constant, not a value measured off real IFR-1
// hardware (unlike every byte offset elsewhere in this codebase) - tune
// further by feel, not by a spec.
private const val DETENT_DEG = 34f

// Cumulative rotation (degrees) below which a gesture counts as a tap
// (push) rather than a turn, for a knob's dual tap/drag role.
private const val TAP_SLOP_DEG = 6f

private fun angleOf(p: Offset, center: Offset): Float =
    Math.toDegrees(atan2((p.y - center.y).toDouble(), (p.x - center.x).toDouble())).toFloat()

/**
 * A single circular rotary-knob target: drag around the center to turn
 * (emits one [onTick] call, +1 or -1, per [DETENT_DEG] of rotation - the
 * same signed per-report delta convention `ifr1.py`'s `outer`/`inner`
 * fields use, just accumulated client-side instead of read off a HID
 * report). [onTap] (if given) fires for a drag that stays under
 * [TAP_SLOP_DEG] total rotation - a knob's "push" (KNOB/CRSR, or a
 * mode-specific shift latch).
 */
@Composable
fun RotaryKnob(
    label: String,
    diameter: Dp,
    modifier: Modifier = Modifier,
    onTick: (Int) -> Unit,
    onTap: (() -> Unit)? = null,
) {
    Box(
        modifier = modifier
            .size(diameter)
            .background(Color(0xFF181B20), CircleShape)
            .border(1.dp, Color(0xFF3C444C), CircleShape)
            .pointerInput(onTick, onTap) {
                val center = Offset(size.width / 2f, size.height / 2f)
                awaitEachGesture {
                    val down = awaitFirstDown()
                    var lastAngle = angleOf(down.position, center)
                    var accumulatedDeg = 0f
                    var totalRotation = 0f
                    val trackedId: PointerId = down.id
                    while (true) {
                        val event = awaitPointerEvent()
                        val change = event.changes.firstOrNull { it.id == trackedId } ?: break
                        if (change.changedToUpIgnoreConsumed()) {
                            if (totalRotation < TAP_SLOP_DEG) onTap?.invoke()
                            break
                        }
                        if (change.positionChanged()) {
                            change.consume()
                            val angle = angleOf(change.position, center)
                            var delta = angle - lastAngle
                            if (delta > 180f) delta -= 360f
                            if (delta < -180f) delta += 360f
                            lastAngle = angle
                            totalRotation += abs(delta)
                            accumulatedDeg += delta
                            while (accumulatedDeg >= DETENT_DEG) {
                                onTick(1)
                                accumulatedDeg -= DETENT_DEG
                            }
                            while (accumulatedDeg <= -DETENT_DEG) {
                                onTick(-1)
                                accumulatedDeg += DETENT_DEG
                            }
                        }
                    }
                }
            },
        contentAlignment = Alignment.Center,
    ) {
        Text(label, color = Color(0xFF78828A), fontSize = 11.sp, fontFamily = FontFamily.Monospace)
    }
}

/** One bezel-key touch target - a tap emits [onTap], a sustained press past
 * the platform long-press timeout emits [onLongPress] (CLR-hold -> Default
 * NAV, SWAP-hold -> emergency frequency, same as a real held press). */
@Composable
fun BezelButton(
    label: String,
    modifier: Modifier = Modifier,
    onTap: () -> Unit,
    onLongPress: (() -> Unit)? = null,
) {
    Box(
        modifier = modifier
            .background(Color(0xFF181B20), RoundedCornerShape(4.dp))
            .border(1.dp, Color(0xFF3C444C), RoundedCornerShape(4.dp))
            .padding(vertical = 10.dp)
            .pointerInput(onTap, onLongPress) {
                detectTapGestures(onTap = { onTap() }, onLongPress = { onLongPress?.invoke() })
            },
        contentAlignment = Alignment.Center,
    ) {
        Text(label, color = Color(0xFFD2DCE1), fontSize = 12.sp, fontFamily = FontFamily.Monospace)
    }
}

// -- GNS bezel overlay - mirrors render_commands.gns_commands's own bezel
// key-row layout math exactly (key_area=78, two rows of 22dp-tall keys) so
// the invisible touch targets land on top of the visibly-drawn keys. Must
// be kept in sync by hand if that Python layout ever changes - same
// cross-language duplication this render bridge already accepts elsewhere
// (see render_commands.py's own module docstring on its color constants).
private const val GNS_KEY_AREA_DP = 78f
private const val GNS_KEY_H_DP = 22f
private val GNS_ROW1 = listOf("CDI", "OBS", "MSG", "FPL", "VNAV", "PROC")
private val GNS_ROW2 = listOf("RNG", "D>", "MENU", "CLR", "ENT")

// row1 label -> the physical IFR-1 button that produces it. On the real
// hardware these are the AP-row's own buttons (AP/HDG/NAV/APR/ALT/VS);
// `main.py`'s `_FMS_BEZEL` reuses them as GNS softkeys only while the mode
// dial reads FMS1/FMS2 - `route_event`'s `_fms_bezel(w, _FMS_BEZEL[b], ...)`
// call is what this table is the reverse of.
private val GNS_ROW1_PHYSICAL = mapOf(
    "CDI" to "AP", "OBS" to "HDG", "MSG" to "NAV",
    "FPL" to "APR", "VNAV" to "ALT", "PROC" to "VS",
)
// row2 label -> physical name. "D>"/"MENU" are the printed labels for the
// real "DCT"/"MNU" buttons (`ifr1.py`'s `Layout.buttons` keys); CLR/ENT
// are literal. RNG has no entry - see GnsBezelOverlay's onRangeTap.
private val GNS_ROW2_PHYSICAL = mapOf("D>" to "DCT", "MENU" to "MNU", "CLR" to "CLR", "ENT" to "ENT")

/**
 * Invisible touch targets over the GNS `InstrumentCanvas`'s own two bezel
 * key rows (drawn by `render_commands.gns_commands`) - place this in a
 * `Box` stacked on top of that `InstrumentCanvas`, both sized
 * `widthDp`x`heightDp`. [onButton] receives the *physical* IFR-1 button
 * name (already reverse-mapped from the visible GNS-softkey label, see
 * [GNS_ROW1_PHYSICAL]) - the caller just needs to dispatch it with
 * `mode = Ifr1Mode.FMS1`, no further translation.
 */
@Composable
fun GnsBezelOverlay(
    widthDp: Float,
    heightDp: Float,
    modifier: Modifier = Modifier,
    onButton: (physicalName: String) -> Unit,
    onRangeIn: () -> Unit,
    onRangeOut: () -> Unit,
) {
    val screenH = heightDp - GNS_KEY_AREA_DP
    val row1Y = screenH + 4f
    val row2Y = row1Y + 26f
    val kw1 = widthDp / GNS_ROW1.size
    val kw2 = widthDp / GNS_ROW2.size
    Box(modifier = modifier.width(widthDp.dp).height(heightDp.dp)) {
        for ((i, label) in GNS_ROW1.withIndex()) {
            val kx = i * kw1
            Box(
                modifier = Modifier
                    .offset(x = (kx + 4).dp, y = row1Y.dp)
                    .size((kw1 - 8).dp, GNS_KEY_H_DP.dp)
                    .pointerInput(label) {
                        detectTapGestures(onTap = { onButton(GNS_ROW1_PHYSICAL.getValue(label)) })
                    },
            )
        }
        for ((i, label) in GNS_ROW2.withIndex()) {
            val kx = i * kw2
            if (label == "RNG") {
                // a real rocker (IN/OUT), not a single push key - split
                // into two tap targets matching the two "-"/"+" boxes
                // render_commands.gns_commands now draws in this same
                // slot (a real bug found on real-device feedback,
                // 2026-09-25: tap=zoom-out/long-press=zoom-in on one key
                // wasn't discoverable or reliable as a touch gesture).
                val half = (kw2 - 8) / 2f
                Box(
                    modifier = Modifier
                        .offset(x = (kx + 4).dp, y = row2Y.dp)
                        .size((half - 2).dp, GNS_KEY_H_DP.dp)
                        .pointerInput(Unit) { detectTapGestures(onTap = { onRangeIn() }) },
                )
                Box(
                    modifier = Modifier
                        .offset(x = (kx + 4 + half + 2).dp, y = row2Y.dp)
                        .size((half - 2).dp, GNS_KEY_H_DP.dp)
                        .pointerInput(Unit) { detectTapGestures(onTap = { onRangeOut() }) },
                )
            } else {
                Box(
                    modifier = Modifier
                        .offset(x = (kx + 4).dp, y = row2Y.dp)
                        .size((kw2 - 8).dp, GNS_KEY_H_DP.dp)
                        .pointerInput(label) {
                            detectTapGestures(onTap = { onButton(GNS_ROW2_PHYSICAL.getValue(label)) })
                        },
                )
            }
        }
    }
}

// -- AP panel overlay - mirrors render_commands.ap_panel_commands's own
// `key()` loop exactly: box_y = top+4 = 12dp, box_w=40dp, box_h=26dp,
// starting at x=92dp with a 45dp pitch per key, in HDG/NAV/APR/REV/ALT/VS
// order. REV has no `route_event`/`_AP_BTN` mapping via the IFR-1 at all
// (`ifr1.py`'s `Layout.buttons` has no REV entry - the real hardware can't
// reach it either, only the desktop keyboard's F4 can) - not wired here,
// same hardware-parity gap, not a v2 regression.
private val AP_KEY_INDEX = mapOf("HDG" to 0, "NAV" to 1, "APR" to 2, "ALT" to 4, "VS" to 5)

/** Invisible touch targets over the AP panel `InstrumentCanvas`'s own 5
 * wired mode-select keys. [onButton] receives the literal button name
 * (AP/HDG/NAV/APR/ALT/VS are never remapped outside FMS mode - the caller
 * just dispatches with `mode = Ifr1Mode.AP`). */
@Composable
fun ApPanelOverlay(
    widthDp: Float,
    heightDp: Float,
    modifier: Modifier = Modifier,
    onButton: (String) -> Unit,
) {
    Box(modifier = modifier.width(widthDp.dp).height(heightDp.dp)) {
        for ((label, idx) in AP_KEY_INDEX) {
            val bx = 92f + idx * 45f
            Box(
                modifier = Modifier
                    .offset(x = bx.dp, y = 12.dp)
                    .size(40.dp, 26.dp)
                    .pointerInput(label) { detectTapGestures(onTap = { onButton(label) }) },
            )
        }
    }
}

/**
 * The AP mode's three knobs: ALT SEL (`main.py route_event`'s `Mode.AP`
 * branch - outer, 100ft/detent, still dispatched through [onAltSel] /
 * `BrainBridge.dispatchEvent` since it isn't shift-gated), plus VS and IAS
 * as two independent knobs. On real hardware VS/IAS *share* one physical
 * knob (a KNOB push toggles `World.shift_latched` between them,
 * `_SHIFT_FN["AP"]` = "IAS") - real-device feedback (2026-09-25) found
 * that shared-knob-plus-invisible-toggle unusable as a touch gesture, so
 * [onVs]/[onIas] call `BrainBridge.adjustVs`/`adjustIasTarget` directly
 * instead (see [androidbridge.session.TrainerSession.adjust_vs]'s
 * docstring), bypassing the shift latch entirely rather than needing to
 * read and toggle it first. Not overlaid on the AP panel canvas (there's
 * no dedicated key cutout for these there, unlike the GNS's FMS knob) -
 * placed below the panel instead.
 */
@Composable
fun ApKnobs(
    modifier: Modifier = Modifier,
    onAltSel: (Int) -> Unit,
    onVs: (Int) -> Unit,
    onIas: (Int) -> Unit,
) {
    Row(modifier = modifier, horizontalArrangement = Arrangement.spacedBy(16.dp)) {
        RotaryKnob(label = "ALT SEL", diameter = 56.dp, onTick = onAltSel)
        RotaryKnob(label = "VS", diameter = 56.dp, onTick = onVs)
        RotaryKnob(label = "IAS", diameter = 56.dp, onTick = onIas)
    }
}

/** The GNS's own right-side dual FMS knob (large=page group/field,
 * small=page/value; small knob push = CRSR) - `render_commands
 * .gns_commands`'s own hint text ("right knob: turn = page/field  press =
 * CRSR") describes exactly this. Placed beside the GNS canvas, not
 * overlaid on it (the real unit's knob sits in a side bezel column this
 * flat/no-bezel render style doesn't draw - see `GnsBezelOverlay`'s doc
 * comment on the two-rows-instead-of-row+column tradeoff). */
@Composable
fun FmsKnobs(
    modifier: Modifier = Modifier,
    onEvent: (pressed: List<String>, outer: Int, inner: Int) -> Unit,
) {
    Column(modifier = modifier, verticalArrangement = Arrangement.spacedBy(16.dp)) {
        RotaryKnob(
            label = "PAGE",
            diameter = 72.dp,
            onTick = { d -> onEvent(emptyList(), d, 0) },
        )
        RotaryKnob(
            label = "CRSR",
            diameter = 56.dp,
            onTick = { d -> onEvent(emptyList(), 0, d) },
            onTap = { onEvent(listOf("KNOB"), 0, 0) },
        )
    }
}

// -- COM/NAV/XPDR fallback cluster - no dedicated radio-stack instrument
// is rendered on Android yet (desktop's `stack` layout has one; porting it
// is its own future §3.6-style slice), so this is the only touch surface
// for these three modes, clearly separated from the GNS/AP overlays above
// rather than pretending to be part of either unit's own faceplate.
private val RADIO_MODES = listOf(Ifr1Mode.COM1, Ifr1Mode.COM2, Ifr1Mode.NAV1, Ifr1Mode.NAV2, Ifr1Mode.XPDR)

@Composable
fun RadioControlPanel(
    mode: Ifr1Mode,
    modifier: Modifier = Modifier,
    onModeChange: (Ifr1Mode) -> Unit,
    onEvent: (
        mode: Ifr1Mode,
        pressed: List<String>,
        outer: Int,
        inner: Int,
        modeChanged: Boolean,
        longPress: List<String>,
    ) -> Unit,
) {
    Column(modifier = modifier) {
        Text(
            "COM / NAV / XPDR (no dedicated panel yet)",
            color = Color(0xFF78828A),
            fontSize = 12.sp,
            fontFamily = FontFamily.Monospace,
        )
        Row(modifier = Modifier.padding(top = 6.dp), horizontalArrangement = Arrangement.spacedBy(4.dp)) {
            for (m in RADIO_MODES) {
                val selected = m == mode
                Box(
                    modifier = Modifier
                        .background(
                            if (selected) Color(0xFFF0B43C) else Color(0xFF181B20),
                            RoundedCornerShape(4.dp),
                        )
                        .border(1.dp, Color(0xFF3C444C), RoundedCornerShape(4.dp))
                        .padding(horizontal = 8.dp, vertical = 6.dp)
                        .pointerInput(m) {
                            detectTapGestures(
                                onTap = {
                                    onModeChange(m)
                                    onEvent(m, emptyList(), 0, 0, true, emptyList())
                                },
                            )
                        },
                    contentAlignment = Alignment.Center,
                ) {
                    Text(
                        m.name,
                        color = if (selected) Color.Black else Color(0xFFD2DCE1),
                        fontSize = 11.sp,
                        fontFamily = FontFamily.Monospace,
                    )
                }
            }
        }
        Row(modifier = Modifier.padding(top = 10.dp), horizontalArrangement = Arrangement.spacedBy(16.dp)) {
            RotaryKnob(
                label = "TUNE",
                diameter = 72.dp,
                onTick = { d -> onEvent(mode, emptyList(), d, 0, false, emptyList()) },
            )
            RotaryKnob(
                label = "FINE",
                diameter = 56.dp,
                modifier = Modifier.padding(top = 8.dp),
                onTick = { d -> onEvent(mode, emptyList(), 0, d, false, emptyList()) },
                onTap = { onEvent(mode, listOf("KNOB"), 0, 0, false, emptyList()) },
            )
            BezelButton(
                "SWAP",
                modifier = Modifier.width(64.dp).height(40.dp).padding(top = 16.dp),
                onTap = { onEvent(mode, listOf("SWAP"), 0, 0, false, emptyList()) },
                onLongPress = { onEvent(mode, emptyList(), 0, 0, false, listOf("SWAP")) },
            )
        }
    }
}
