package com.octavi.ifrtrainer.input

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp

/**
 * §3.1 step 4 test harness: proves (or disproves) two things `adb`/`getevent`
 * alone can't - (a) does [UsbHidInput]'s `claimInterface(force = true)`
 * actually succeed against a driver `usbhid` already holds, on this real
 * device, and (b) does the resulting raw report stream match `ifr1.py`'s
 * `LAYOUT` byte-for-byte. Not part of the shipped app UI - a disposable
 * verification screen, results belong in `docs/ANDROID_PORT_PLAN.md` §3.1
 * once run, not preserved as product code.
 */
@Composable
fun UsbHidTestScreen() {
    val context = LocalContext.current
    var status by remember { mutableStateOf(UsbHidInput.Status.STOPPED) }
    val log = remember { mutableStateListOf<String>() }
    val input = remember { UsbHidInput(context) }

    DisposableEffect(Unit) {
        input.statusListener = UsbHidInput.StatusListener { s -> status = s }
        input.listener = UsbHidInput.Listener { event ->
            val parts = mutableListOf<String>()
            if (event.modeChanged) parts += "mode=${event.mode}"
            if (event.pressed.isNotEmpty()) parts += "down=${event.pressed}"
            if (event.released.isNotEmpty()) parts += "up=${event.released}"
            if (event.longPress.isNotEmpty()) parts += "long=${event.longPress}"
            if (event.outer != 0) parts += "outer=${event.outer}"
            if (event.inner != 0) parts += "inner=${event.inner}"
            if (event.shift) parts += "shift"
            val line = "${System.currentTimeMillis() % 100000} ${parts.joinToString(" ")}  [${event.rawHex}]"
            log.add(0, line)
            while (log.size > 60) log.removeAt(log.size - 1)
        }
        input.start()
        onDispose { input.stop() }
    }

    Column(modifier = Modifier.fillMaxSize().padding(16.dp)) {
        Text("IFR-1 raw-HID test (§3.1 step 4)", style = MaterialTheme.typography.titleMedium)
        Text(
            "status: $status",
            style = MaterialTheme.typography.bodyLarge,
            color = when (status) {
                UsbHidInput.Status.CONNECTED -> Color(0xFF2E7D32)
                UsbHidInput.Status.CLAIM_FAILED, UsbHidInput.Status.PERMISSION_DENIED -> Color(0xFFC62828)
                else -> Color.Unspecified
            },
        )
        Column(Modifier.fillMaxWidth().padding(vertical = 8.dp)) {
            Button(onClick = { input.start() }) { Text("Start / retry") }
        }
        Text("Turn knobs and press buttons - events should appear below.", style = MaterialTheme.typography.bodySmall)
        LazyColumn(
            modifier = Modifier
                .fillMaxSize()
                .padding(top = 8.dp)
                .background(Color(0x11000000)),
        ) {
            items(log) { line ->
                Text(line, style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(2.dp))
            }
        }
    }
}
