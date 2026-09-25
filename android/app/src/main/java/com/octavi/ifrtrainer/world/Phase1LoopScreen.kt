package com.octavi.ifrtrainer.world

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import com.octavi.ifrtrainer.bridge.BrainBridge
import com.octavi.ifrtrainer.input.ApKnobs
import com.octavi.ifrtrainer.input.ApPanelOverlay
import com.octavi.ifrtrainer.input.FmsKnobs
import com.octavi.ifrtrainer.input.GnsBezelOverlay
import com.octavi.ifrtrainer.input.Ifr1Mode
import com.octavi.ifrtrainer.input.RadioControlPanel
import com.octavi.ifrtrainer.input.UsbHidInput
import com.octavi.ifrtrainer.render.DrawCommand
import com.octavi.ifrtrainer.render.InstrumentCanvas
import com.octavi.ifrtrainer.render.parseDrawCommands

private const val HSI_SIZE_DP = 260f
// 400dp was too narrow: render_commands.ap_panel_commands's 6-button mode
// row (HDG/NAV/APR/REV/ALT/VS) plus its info box needs ~520dp before the
// last key stops overflowing into the info box - a real bug found
// on-device (2026-09-24).
private const val AP_PANEL_W_DP = 520f
private const val AP_PANEL_H_DP = 90f
// Wide/short like a real GNS unit screen - the DIS/GS/ETE/XTK rows and the
// two bezel-key rows need real width, and render_commands.gns_commands
// reserves its own bottom slice of h for those rows (see its docstring).
private const val GNS_W_DP = 520f
private const val GNS_H_DP = 300f
// Square-ish, like the desktop layout's map pane - wide enough to show a
// few flight-plan legs and nearby fixes without feeling cramped.
private const val MAP_W_DP = 520f
private const val MAP_H_DP = 360f

/**
 * Phase 1 core-loop + real-instrument screen (docs/ANDROID_PORT_PLAN.md
 * §6/§3.6/§3.2): real IFR-1 events -> [BrainBridge.dispatchEvent] ->
 * `main.route_event` -> a real `World`, ticked ~30 Hz in the background by
 * [TrainerLoop] and surviving Android lifecycle events. The HSI head, AP
 * panel, GNS screen (all ten text pages + all 8 modal dialogs - see
 * `render_commands.gns_commands`'s docstring), and the moving map are real
 * instrument graphics now ([BrainBridge.renderHsi]/
 * [BrainBridge.renderApPanel]/[BrainBridge.renderGns]/
 * [BrainBridge.renderMap] + [InstrumentCanvas]).
 *
 * §3.2 touch controls (v2, 2026-09-25) sit directly on the instrument each
 * one belongs to, not in one generic panel: [GnsBezelOverlay] + [FmsKnobs]
 * for the GNS's own buttons/right knob, [ApPanelOverlay] + [ApKnobs] for
 * the AP panel's own mode keys/ALT-VS knob, and [RadioControlPanel] as the
 * only remaining generic surface - COM/NAV/XPDR have no dedicated
 * instrument rendered on Android yet, so there's nothing to anchor their
 * controls to. Every one of these feeds the exact same
 * [BrainBridge.dispatchEvent] call real IFR-1 events use - a second,
 * independent event source for development/testing without the hardware
 * attached, not a separate code path through the brain.
 */
@Composable
fun Phase1LoopScreen() {
    val context = LocalContext.current
    val lifecycleOwner = LocalLifecycleOwner.current
    var line by remember { mutableStateOf("starting...") }
    var hsiCommands by remember { mutableStateOf<List<DrawCommand>>(emptyList()) }
    var apCommands by remember { mutableStateOf<List<DrawCommand>>(emptyList()) }
    var gnsCommands by remember { mutableStateOf<List<DrawCommand>>(emptyList()) }
    var mapCommands by remember { mutableStateOf<List<DrawCommand>>(emptyList()) }
    var usbStatus by remember { mutableStateOf(UsbHidInput.Status.STOPPED) }
    var radioMode by remember { mutableStateOf(Ifr1Mode.COM1) }
    val loop = remember { TrainerLoop(context) }
    val usbInput = remember { UsbHidInput(context) }

    fun dispatch(
        mode: Ifr1Mode,
        pressed: List<String>,
        outer: Int,
        inner: Int,
        modeChanged: Boolean,
        longPress: List<String>,
    ) {
        BrainBridge.dispatchEvent(mode.name, pressed, emptyList(), outer, inner, modeChanged, longPress)
    }

    DisposableEffect(lifecycleOwner) {
        loop.listener = TrainerLoop.Listener { text ->
            line = text
            hsiCommands = parseDrawCommands(BrainBridge.renderHsi(0f, 0f, HSI_SIZE_DP, HSI_SIZE_DP))
            apCommands = parseDrawCommands(
                BrainBridge.renderApPanel(0f, 0f, AP_PANEL_W_DP, AP_PANEL_H_DP),
            )
            gnsCommands = parseDrawCommands(BrainBridge.renderGns(0f, 0f, GNS_W_DP, GNS_H_DP))
            mapCommands = parseDrawCommands(BrainBridge.renderMap(0f, 0f, MAP_W_DP, MAP_H_DP))
        }
        lifecycleOwner.lifecycle.addObserver(loop)

        usbInput.statusListener = UsbHidInput.StatusListener { s -> usbStatus = s }
        usbInput.listener = UsbHidInput.Listener { event ->
            BrainBridge.dispatchEvent(
                event.mode.name,
                event.pressed,
                event.released,
                event.outer,
                event.inner,
                event.modeChanged,
                event.longPress,
            )
        }
        usbInput.start()

        onDispose {
            lifecycleOwner.lifecycle.removeObserver(loop)
            usbInput.stop()
        }
    }

    // Landscape phones are wide and short - a single vertical Column of
    // HSI + AP panel + debug text overflowed the visible screen height with
    // no way to scroll (real bug found on-device, 2026-09-24). This is a
    // debug/verification layout, not §3.3's real screen composition, so the
    // fix is just "fit it and let the leftover text scroll," not a real
    // design pass: HSI fixed on the left, everything else in a scrollable
    // column on the right.
    Row(modifier = Modifier.fillMaxSize().padding(12.dp)) {
        Box(modifier = Modifier.width(HSI_SIZE_DP.dp).height(HSI_SIZE_DP.dp)) {
            InstrumentCanvas(
                commands = hsiCommands,
                modifier = Modifier.fillMaxSize(),
                sourceWidth = HSI_SIZE_DP,
                sourceHeight = HSI_SIZE_DP,
            )
        }
        Column(
            modifier = Modifier.fillMaxHeight().padding(start = 12.dp).verticalScroll(rememberScrollState()),
        ) {
            Text("Phase 1 core loop", style = MaterialTheme.typography.titleMedium)
            Text(
                "IFR-1: $usbStatus",
                style = MaterialTheme.typography.bodyMedium,
                color = when (usbStatus) {
                    UsbHidInput.Status.CONNECTED -> Color(0xFF2E7D32)
                    UsbHidInput.Status.CLAIM_FAILED, UsbHidInput.Status.PERMISSION_DENIED -> Color(0xFFC62828)
                    else -> Color.Unspecified
                },
            )
            // Knobs sit BELOW their panel, not beside it - a real bug found
            // on real-device feedback (2026-09-25): AP_PANEL_W_DP/GNS_W_DP
            // (520dp) plus a knob pair beside it ran past the right edge of
            // the phone screen (this Column only scrolls vertically), so
            // the second knob was mostly or entirely off-screen and
            // effectively untappable - not the knob-overlap bug it first
            // looked like.
            Column(modifier = Modifier.padding(top = 8.dp)) {
                Box(modifier = Modifier.width(AP_PANEL_W_DP.dp).height(AP_PANEL_H_DP.dp)) {
                    InstrumentCanvas(
                        commands = apCommands,
                        modifier = Modifier.fillMaxSize(),
                        sourceWidth = AP_PANEL_W_DP,
                        sourceHeight = AP_PANEL_H_DP,
                    )
                    ApPanelOverlay(
                        widthDp = AP_PANEL_W_DP,
                        heightDp = AP_PANEL_H_DP,
                        onButton = { name -> dispatch(Ifr1Mode.AP, listOf(name), 0, 0, true, emptyList()) },
                    )
                }
                ApKnobs(
                    modifier = Modifier.padding(top = 8.dp),
                    onAltSel = { d -> dispatch(Ifr1Mode.AP, emptyList(), d, 0, true, emptyList()) },
                    onVs = { d -> BrainBridge.adjustVs(d) },
                    onIas = { d -> BrainBridge.adjustIasTarget(d) },
                )
            }
            Column(modifier = Modifier.padding(top = 8.dp)) {
                Box(modifier = Modifier.width(GNS_W_DP.dp).height(GNS_H_DP.dp)) {
                    InstrumentCanvas(
                        commands = gnsCommands,
                        modifier = Modifier.fillMaxSize(),
                        sourceWidth = GNS_W_DP,
                        sourceHeight = GNS_H_DP,
                    )
                    GnsBezelOverlay(
                        widthDp = GNS_W_DP,
                        heightDp = GNS_H_DP,
                        onButton = { name -> dispatch(Ifr1Mode.FMS1, listOf(name), 0, 0, true, emptyList()) },
                        onRangeIn = { BrainBridge.adjustMapRange(1f / 1.5f) },
                        onRangeOut = { BrainBridge.adjustMapRange(1.5f) },
                    )
                }
                FmsKnobs(
                    modifier = Modifier.padding(top = 8.dp),
                    onEvent = { pressed, outer, inner ->
                        dispatch(Ifr1Mode.FMS1, pressed, outer, inner, true, emptyList())
                    },
                )
            }
            Box(modifier = Modifier.width(MAP_W_DP.dp).height(MAP_H_DP.dp).padding(top = 8.dp)) {
                InstrumentCanvas(
                    commands = mapCommands,
                    modifier = Modifier.fillMaxSize(),
                    sourceWidth = MAP_W_DP,
                    sourceHeight = MAP_H_DP,
                )
            }
            RadioControlPanel(
                mode = radioMode,
                modifier = Modifier.padding(top = 12.dp),
                onModeChange = { radioMode = it },
                onEvent = { mode, pressed, outer, inner, modeChanged, longPress ->
                    dispatch(mode, pressed, outer, inner, modeChanged, longPress)
                },
            )
            Text(
                line,
                style = MaterialTheme.typography.bodySmall,
                fontFamily = FontFamily.Monospace,
                modifier = Modifier.padding(top = 12.dp),
            )
        }
    }
}
