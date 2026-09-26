package com.octavi.ifrtrainer

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.octavi.ifrtrainer.bridge.BrainBridge
import com.octavi.ifrtrainer.input.UsbHidTestScreen
import com.octavi.ifrtrainer.nav.ChartsScreen
import com.octavi.ifrtrainer.nav.NavDataScreen
import com.octavi.ifrtrainer.world.Phase1LoopScreen

private enum class Screen { SELF_TEST, USB_TEST, PHASE1_LOOP, NAV_DATA, CHARTS }

/**
 * Entry point. Hosts five verification screens, not the real trainer UI
 * yet (that's §3.6's draw-command-list rendering, mostly done - see
 * `android/README.md`):
 *  1. [Phase0SelfTestScreen] — Chaquopy starts and the brain modules
 *     (navmath/navdata/gpsnav/sim_model/androidbridge) import and tick
 *     on-device — [BrainBridge.selfTest].
 *  2. [UsbHidTestScreen] — §3.1's raw-HID verification (buttons/knobs
 *     against the real IFR-1, confirmed 2026-09-24).
 *  3. [Phase1LoopScreen] — §6 Phase 1's core loop: real IFR-1 events
 *     routed through `main.route_event` into a real `World`, ticked in the
 *     background and surviving Android lifecycle events.
 *  4. [NavDataScreen] — §3.5's on-device FAA nav data download, and the
 *     switch from the synthetic demo database to a real one.
 *  5. [ChartsScreen] — the §3.4/§3.5 approach-plate PDF fetch + "click to
 *     load" hand-off to the system PDF viewer.
 */
class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            MaterialTheme {
                Surface(modifier = Modifier.fillMaxSize()) {
                    var screen by remember { mutableStateOf(Screen.SELF_TEST) }
                    when (screen) {
                        Screen.SELF_TEST -> Phase0SelfTestScreen(
                            onOpenUsbTest = { screen = Screen.USB_TEST },
                            onOpenPhase1Loop = { screen = Screen.PHASE1_LOOP },
                            onOpenNavData = { screen = Screen.NAV_DATA },
                            onOpenCharts = { screen = Screen.CHARTS },
                        )
                        Screen.USB_TEST -> Column(modifier = Modifier.fillMaxSize()) {
                            Button(onClick = { screen = Screen.SELF_TEST }, modifier = Modifier.padding(8.dp)) {
                                Text("Back to self-test")
                            }
                            UsbHidTestScreen()
                        }
                        Screen.PHASE1_LOOP -> Column(modifier = Modifier.fillMaxSize()) {
                            Button(onClick = { screen = Screen.SELF_TEST }, modifier = Modifier.padding(8.dp)) {
                                Text("Back to self-test")
                            }
                            Phase1LoopScreen()
                        }
                        Screen.NAV_DATA -> Column(modifier = Modifier.fillMaxSize()) {
                            Button(onClick = { screen = Screen.SELF_TEST }, modifier = Modifier.padding(8.dp)) {
                                Text("Back to self-test")
                            }
                            NavDataScreen()
                        }
                        Screen.CHARTS -> Column(modifier = Modifier.fillMaxSize()) {
                            Button(onClick = { screen = Screen.SELF_TEST }, modifier = Modifier.padding(8.dp)) {
                                Text("Back to self-test")
                            }
                            ChartsScreen()
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun Phase0SelfTestScreen(
    onOpenUsbTest: () -> Unit,
    onOpenPhase1Loop: () -> Unit,
    onOpenNavData: () -> Unit,
    onOpenCharts: () -> Unit,
) {
    val context = androidx.compose.ui.platform.LocalContext.current
    var result by remember { mutableStateOf("running self-test...") }

    androidx.compose.runtime.LaunchedEffect(Unit) {
        result = try {
            BrainBridge.selfTest(context)
        } catch (t: Throwable) {
            "FAIL: ${t::class.simpleName}: ${t.message}"
        }
    }

    Column(
        modifier = Modifier.fillMaxSize().padding(24.dp),
        verticalArrangement = Arrangement.Center,
    ) {
        Text("octavi-ifr-trainer — Android Phase 0", style = MaterialTheme.typography.titleLarge)
        Text(result, style = MaterialTheme.typography.bodyMedium)
        Button(onClick = onOpenUsbTest, modifier = Modifier.padding(top = 16.dp)) {
            Text("IFR-1 raw-HID test")
        }
        Button(onClick = onOpenPhase1Loop, modifier = Modifier.padding(top = 8.dp)) {
            Text("Phase 1 core loop")
        }
        Button(onClick = onOpenNavData, modifier = Modifier.padding(top = 8.dp)) {
            Text("Nav data (§3.5)")
        }
        Button(onClick = onOpenCharts, modifier = Modifier.padding(top = 8.dp)) {
            Text("Approach plates (§3.4/§3.5)")
        }
    }
}
