package com.octavi.ifrtrainer.nav

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import com.octavi.ifrtrainer.bridge.BrainBridge
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

// Mirrors androidbridge.nav_update.KINDS exactly - kept in sync by hand,
// same cross-language duplication this whole bridge already accepts
// elsewhere (see render_commands.py's module docstring on its color
// constants). cifp/nasr are what navdata.load() actually needs; artcc is
// cheap (~170KB) so it's included too. "airspace" is deliberately left
// out - see nav_update.py's KINDS docstring: it's a ~150MB one-time
// download for Nearest-Airspace/moving-map-overlay data, not core
// navigation, and shouldn't be silently bundled into the default fetch.
private val NAV_DATA_KINDS = listOf("cifp", "nasr", "artcc")

/**
 * §3.5 on-device nav data acquisition: fetches the current FAA AIRAC cycle
 * (`androidbridge/nav_update.py`, stdlib `urllib` - real network I/O,
 * confirmed against the live FAA servers from desktop CPython before this
 * screen existed) into the app's private storage
 * ([BrainBridge.navDataRoot]), one product "kind" per step so this screen
 * can show real per-step progress rather than one opaque blocking call.
 * "Use real nav data" then swaps [BrainBridge]'s active session from the
 * synthetic demo database to a real one built from what's cached -
 * [com.octavi.ifrtrainer.world.Phase1LoopScreen] doesn't need to know
 * which kind of session it's driving, so this can be done independently,
 * any time after at least the "cifp" kind has been fetched.
 */
@Composable
fun NavDataScreen() {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var status by remember { mutableStateOf("checking...") }
    var progress by remember { mutableStateOf<String?>(null) }
    var downloading by remember { mutableStateOf(false) }
    var switchResult by remember { mutableStateOf<String?>(null) }

    suspend fun refreshStatus() {
        status = withContext(Dispatchers.IO) { BrainBridge.navDataStatus(context) }
    }

    LaunchedEffect(Unit) { refreshStatus() }

    fun download() {
        if (downloading) return
        downloading = true
        switchResult = null
        scope.launch {
            for ((i, kind) in NAV_DATA_KINDS.withIndex()) {
                progress = "fetching $kind (${i + 1}/${NAV_DATA_KINDS.size})..."
                val result = withContext(Dispatchers.IO) { BrainBridge.fetchNavDataKind(context, kind) }
                if (result != "ok") {
                    progress = "$kind failed: $result"
                    downloading = false
                    return@launch
                }
            }
            progress = null
            refreshStatus()
            downloading = false
        }
    }

    Column(modifier = Modifier.padding(24.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text("§3.5 nav data", style = MaterialTheme.typography.titleLarge)
        Text(status, style = MaterialTheme.typography.bodyMedium)
        progress?.let { Text(it, style = MaterialTheme.typography.bodyMedium) }
        Button(onClick = { download() }, enabled = !downloading) {
            Text(if (downloading) "Downloading..." else "Download / refresh nav data")
        }
        Button(
            onClick = {
                scope.launch {
                    switchResult = withContext(Dispatchers.IO) { BrainBridge.startRealWorld(context) }
                }
            },
            enabled = !downloading,
        ) {
            Text("Use real nav data in Phase 1 loop")
        }
        switchResult?.let { Text(it, style = MaterialTheme.typography.bodyMedium) }
    }
}
