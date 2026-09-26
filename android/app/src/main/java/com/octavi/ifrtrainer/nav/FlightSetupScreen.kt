package com.octavi.ifrtrainer.nav

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import com.octavi.ifrtrainer.bridge.BrainBridge
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * Pre-flight setup: type a flight plan, wind, and winds-aloft profile
 * before entering the Phase 1 loop - the Android equivalent of desktop's
 * `python main.py --plan "KBOS PVD KJFK" --wind 300/25 --winds-aloft
 * "3000:280/20/-05 ..."` launch flags, which Android has no command line
 * to pass. "Start flight" creates a session ([BrainBridge.startWorld] or
 * [BrainBridge.startRealWorld], depending on the real-nav-data toggle)
 * then applies the typed fields via [BrainBridge.configureFlight]
 * ([androidbridge.demo_session.configure_flight] - `TrainerSession
 * .load_flight_plan` plus `SimModel.set_wind`/`set_winds_aloft`, applied
 * after construction rather than threaded through `Config`/`World`'s own
 * startup path, so a bad ident is reported back here instead of only ever
 * printed to a desktop stdout nobody's watching on a phone).
 *
 * A real flight plan only resolves against a real nav database - the
 * synthetic demo one (ALFA/BRAVO/CHAR) has no real airport idents to
 * match, so this only makes practical sense with "use real nav data"
 * switched on and [BrainBridge.fetchNavDataKind] already run at least
 * once (`nav/NavDataScreen.kt`). Wind/winds-aloft work either way.
 */
@Composable
fun FlightSetupScreen(onStart: () -> Unit) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var useRealData by remember { mutableStateOf(true) }
    var plan by remember { mutableStateOf("") }
    var wind by remember { mutableStateOf("") }
    var windsAloft by remember { mutableStateOf("") }
    var navStatus by remember { mutableStateOf("checking nav data...") }
    var busy by remember { mutableStateOf(false) }
    var result by remember { mutableStateOf<String?>(null) }
    var started by remember { mutableStateOf(false) }

    LaunchedEffect(Unit) {
        navStatus = withContext(Dispatchers.IO) { BrainBridge.navDataStatus(context) }
    }

    fun start() {
        if (busy) return
        busy = true
        result = null
        scope.launch {
            val startResult = withContext(Dispatchers.IO) {
                if (useRealData) BrainBridge.startRealWorld(context) else { BrainBridge.startWorld(context); "ok" }
            }
            if (startResult != "ok") {
                result = "could not start: $startResult"
                busy = false
                return@launch
            }
            result = withContext(Dispatchers.IO) { BrainBridge.configureFlight(context, plan, wind, windsAloft) }
            started = true
            busy = false
        }
    }

    Column(
        modifier = Modifier.fillMaxWidth().padding(24.dp).verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Text("Flight setup", style = MaterialTheme.typography.titleLarge)
        Text(navStatus, style = MaterialTheme.typography.bodyMedium)
        Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(top = 8.dp)) {
            Switch(checked = useRealData, onCheckedChange = { useRealData = it })
            Text(
                if (useRealData) "Use real nav data" else "Use synthetic demo data (no real idents)",
                modifier = Modifier.padding(start = 8.dp),
                style = MaterialTheme.typography.bodyMedium,
            )
        }
        OutlinedTextField(
            value = plan,
            onValueChange = { plan = it.uppercase() },
            label = { Text("Flight plan (e.g. KBOS PVD KJFK)") },
            modifier = Modifier.fillMaxWidth().padding(top = 8.dp),
            singleLine = true,
        )
        OutlinedTextField(
            value = wind,
            onValueChange = { wind = it },
            label = { Text("Wind (DIR/SPD, e.g. 300/25)") },
            modifier = Modifier.fillMaxWidth().padding(top = 8.dp),
            singleLine = true,
        )
        OutlinedTextField(
            value = windsAloft,
            onValueChange = { windsAloft = it },
            label = { Text("Winds aloft (ALT:DIR/SPD[/TEMPC] ..., optional, overrides wind)") },
            modifier = Modifier.fillMaxWidth().padding(top = 8.dp),
            singleLine = true,
        )
        result?.let { Text(it, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.padding(top = 8.dp)) }
        Button(onClick = { start() }, enabled = !busy, modifier = Modifier.padding(top = 8.dp)) {
            Text(if (busy) "Starting..." else "Start flight")
        }
        if (started) {
            Button(onClick = onStart, modifier = Modifier.padding(top = 8.dp)) {
                Text("Go to Phase 1 core loop")
            }
        }
    }
}
