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

/**
 * Phase 0 entry point. Proves two things end to end, nothing more yet:
 *  1. Chaquopy starts and the brain modules (navmath/navdata/gpsnav/
 *     sim_model/androidbridge) import and tick on-device — [BrainBridge].
 *  2. A Compose screen can host the [com.octavi.ifrtrainer.render
 *     .InstrumentCanvas] draw-command replay mechanism.
 *
 * Everything else (§3.1 USB input, §3.6 real layout-driven draw commands,
 * §3.3 landscape instrument composition) is later Phase 1 work per
 * docs/ANDROID_PORT_PLAN.md — this screen is a self-test, not the app.
 */
class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            MaterialTheme {
                Surface(modifier = Modifier.fillMaxSize()) {
                    var showUsbTest by remember { mutableStateOf(false) }
                    if (showUsbTest) {
                        Column(modifier = Modifier.fillMaxSize()) {
                            Button(onClick = { showUsbTest = false }, modifier = Modifier.padding(8.dp)) {
                                Text("Back to self-test")
                            }
                            UsbHidTestScreen()
                        }
                    } else {
                        Phase0SelfTestScreen(onOpenUsbTest = { showUsbTest = true })
                    }
                }
            }
        }
    }
}

@Composable
private fun Phase0SelfTestScreen(onOpenUsbTest: () -> Unit) {
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
    }
}
