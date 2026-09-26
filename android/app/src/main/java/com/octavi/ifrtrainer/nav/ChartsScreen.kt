package com.octavi.ifrtrainer.nav

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
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

// androidbridge.charts's error strings - anything else fetchChartPath
// returns is a real local path, ready for BrainBridge.openPdf.
private val CHART_FETCH_ERRORS = setOf("no index", "no charts", "bad index")

private fun isChartFetchError(result: String): Boolean =
    result in CHART_FETCH_ERRORS || result.startsWith("unavailable:") || result.startsWith("error:")

/**
 * §3.4/§3.5's "click to load" approach-plate PDF flow: fetch the current
 * AIRAC cycle's chart index once, look up an airport's charts, tap one to
 * fetch (if needed) and open it in the system's PDF viewer via
 * [BrainBridge.openPdf] - the Android-native equivalent of desktop's
 * `main._open_selected_chart` (every non-`stack` layout's AUX>Charts ENT
 * key), not the `stack` layout's separate inline `pypdfium2`
 * rasterization (still desktop-only). Not wired into the GNS's own
 * AUX>Charts page yet (that page isn't rendered on Android at all - see
 * `render_commands.gns_commands`'s docstring, AUX Weather/Charts stayed
 * out of scope in §3.6 for the same "needs live data this module doesn't
 * have" reason) - this is a standalone verification screen proving the
 * fetch+open pipeline works, same "spike before the real UI" pattern as
 * `nav/NavDataScreen.kt`.
 */
@Composable
fun ChartsScreen() {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var indexStatus by remember { mutableStateOf("chart index not fetched yet") }
    var ident by remember { mutableStateOf("KBOS") }
    var charts by remember { mutableStateOf<List<Pair<String, String>>>(emptyList()) }
    var busy by remember { mutableStateOf(false) }
    var message by remember { mutableStateOf<String?>(null) }

    fun updateIndex() {
        if (busy) return
        busy = true
        message = null
        scope.launch {
            indexStatus = withContext(Dispatchers.IO) { BrainBridge.updateChartIndex(context) }
            busy = false
        }
    }

    fun loadCharts() {
        if (busy) return
        busy = true
        message = null
        scope.launch {
            charts = withContext(Dispatchers.IO) { BrainBridge.listCharts(context, ident) }
            if (charts.isEmpty()) {
                message = "no charts found for \"$ident\" - fetch the index first, " +
                    "or this airport/ident has none in the metafile"
            }
            busy = false
        }
    }

    fun openChart(index: Int) {
        if (busy) return
        busy = true
        message = "fetching ${charts[index].second}..."
        scope.launch {
            val result = withContext(Dispatchers.IO) { BrainBridge.fetchChartPath(context, ident, index) }
            busy = false
            if (isChartFetchError(result)) {
                message = "failed: $result"
            } else {
                message = null
                BrainBridge.openPdf(context, result)
            }
        }
    }

    Column(
        modifier = Modifier.fillMaxSize().padding(24.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Text("§3.4/§3.5 approach plates", style = MaterialTheme.typography.titleLarge)
        Text(indexStatus, style = MaterialTheme.typography.bodyMedium)
        Button(onClick = { updateIndex() }, enabled = !busy) {
            Text(if (busy) "working..." else "Fetch chart index (~16MB, once per cycle)")
        }
        Row(verticalAlignment = Alignment.CenterVertically) {
            OutlinedTextField(
                value = ident,
                onValueChange = { ident = it.uppercase() },
                label = { Text("Airport ident") },
                modifier = Modifier.padding(top = 8.dp),
                singleLine = true,
            )
            Button(onClick = { loadCharts() }, enabled = !busy, modifier = Modifier.padding(start = 8.dp, top = 8.dp)) {
                Text("List charts")
            }
        }
        message?.let { Text(it, style = MaterialTheme.typography.bodyMedium) }
        LazyColumn(modifier = Modifier.fillMaxWidth().weight(1f).padding(top = 8.dp)) {
            items(charts.size) { i ->
                val (code, name) = charts[i]
                Row(
                    modifier = Modifier
                        .padding(vertical = 4.dp)
                        .clickable(enabled = !busy) { openChart(i) },
                ) {
                    Text("$code  ", style = MaterialTheme.typography.bodyMedium)
                    Text(name, style = MaterialTheme.typography.bodyMedium)
                }
            }
        }
    }
}
