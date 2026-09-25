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
// 8-key bezel-label row need real width, and render_commands.gns_commands
// reserves its own bottom slice of h for that key row (see its docstring).
private const val GNS_W_DP = 520f
private const val GNS_H_DP = 300f

/**
 * Phase 1 core-loop + real-instrument screen (docs/ANDROID_PORT_PLAN.md
 * §6/§3.6): real IFR-1 events -> [BrainBridge.dispatchEvent] ->
 * `main.route_event` -> a real `World`, ticked ~30 Hz in the background by
 * [TrainerLoop] and surviving Android lifecycle events. The HSI head, AP
 * panel, and GNS screen (default NAV page only - see
 * `render_commands.gns_commands`'s docstring) are real instrument graphics
 * now ([BrainBridge.renderHsi]/[BrainBridge.renderApPanel]/
 * [BrainBridge.renderGns] + [InstrumentCanvas]); everything else stays the
 * plain-text readout until its own rendering pass.
 */
@Composable
fun Phase1LoopScreen() {
    val context = LocalContext.current
    val lifecycleOwner = LocalLifecycleOwner.current
    var line by remember { mutableStateOf("starting...") }
    var hsiCommands by remember { mutableStateOf<List<DrawCommand>>(emptyList()) }
    var apCommands by remember { mutableStateOf<List<DrawCommand>>(emptyList()) }
    var gnsCommands by remember { mutableStateOf<List<DrawCommand>>(emptyList()) }
    var usbStatus by remember { mutableStateOf(UsbHidInput.Status.STOPPED) }
    val loop = remember { TrainerLoop(context) }
    val usbInput = remember { UsbHidInput(context) }

    DisposableEffect(lifecycleOwner) {
        loop.listener = TrainerLoop.Listener { text ->
            line = text
            hsiCommands = parseDrawCommands(BrainBridge.renderHsi(0f, 0f, HSI_SIZE_DP, HSI_SIZE_DP))
            apCommands = parseDrawCommands(
                BrainBridge.renderApPanel(0f, 0f, AP_PANEL_W_DP, AP_PANEL_H_DP),
            )
            gnsCommands = parseDrawCommands(BrainBridge.renderGns(0f, 0f, GNS_W_DP, GNS_H_DP))
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
            Box(modifier = Modifier.width(AP_PANEL_W_DP.dp).height(AP_PANEL_H_DP.dp).padding(top = 8.dp)) {
                InstrumentCanvas(
                    commands = apCommands,
                    modifier = Modifier.fillMaxSize(),
                    sourceWidth = AP_PANEL_W_DP,
                    sourceHeight = AP_PANEL_H_DP,
                )
            }
            Box(modifier = Modifier.width(GNS_W_DP.dp).height(GNS_H_DP.dp).padding(top = 8.dp)) {
                InstrumentCanvas(
                    commands = gnsCommands,
                    modifier = Modifier.fillMaxSize(),
                    sourceWidth = GNS_W_DP,
                    sourceHeight = GNS_H_DP,
                )
            }
            Text(
                line,
                style = MaterialTheme.typography.bodySmall,
                fontFamily = FontFamily.Monospace,
                modifier = Modifier.padding(top = 12.dp),
            )
        }
    }
}
