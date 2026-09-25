package com.octavi.ifrtrainer.world

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
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

/**
 * Phase 1 core-loop verification screen (docs/ANDROID_PORT_PLAN.md §6):
 * real IFR-1 events -> [BrainBridge.dispatchEvent] -> `main.route_event` ->
 * a real `World`, ticked ~30 Hz in the background by [TrainerLoop] and
 * surviving Android lifecycle events (backgrounding/resuming). Deliberately
 * a plain text readout, not real instrument graphics - that's §3.6,
 * deferred to the next pass; this proves the loop underneath it first.
 */
@Composable
fun Phase1LoopScreen() {
    val context = LocalContext.current
    val lifecycleOwner = LocalLifecycleOwner.current
    var line by remember { mutableStateOf("starting...") }
    var usbStatus by remember { mutableStateOf(UsbHidInput.Status.STOPPED) }
    val loop = remember { TrainerLoop(context) }
    val usbInput = remember { UsbHidInput(context) }

    DisposableEffect(lifecycleOwner) {
        loop.listener = TrainerLoop.Listener { text -> line = text }
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

    Column(modifier = Modifier.fillMaxSize().padding(16.dp)) {
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
        Text(
            line,
            style = MaterialTheme.typography.bodySmall,
            fontFamily = FontFamily.Monospace,
            modifier = Modifier.padding(top = 12.dp),
        )
    }
}
