import org.gradle.api.tasks.Copy

// UNVERIFIED SCAFFOLD — see android/README.md. This file has never been
// resolved/built (no JDK 17 / Android SDK / Gradle in the authoring
// environment); treat every version number and the Chaquopy source-set API
// call below as "confirm at first Android Studio sync," not as tested fact.

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose") // Kotlin 2.0+ Compose compiler plugin
    id("com.chaquo.python")
}

android {
    namespace = "com.octavi.ifrtrainer"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.octavi.ifrtrainer"
        minSdk = 26       // Pixel 9 ships far newer; 26 chosen only as a floor, not load-bearing
        targetSdk = 35
        versionCode = 1
        versionName = "0.1.0-android-phase0"

        ndk {
            // Chaquopy needs at least one ABI; arm64 covers the Pixel 9 and
            // real hardware testing generally. Add x86_64 back if emulator
            // testing on an Intel/AMD dev machine is needed.
            abiFilters += listOf("arm64-v8a")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }

    buildFeatures {
        compose = true
    }

    buildTypes {
        release {
            isMinifyEnabled = false // flip on once ProGuard rules are written; premature now
        }
    }
}

chaquopy {
    defaultConfig {
        version = "3.12"
    }
    sourceSets {
        getByName("main") {
            // Populated by stageBrainPython below, NOT src/main/python directly -
            // the actual source of truth stays the repo-root modules (navmath.py,
            // gpsnav.py, etc.) so there is exactly one copy of the validated
            // avionics brain, not a hand-maintained duplicate. See
            // docs/ANDROID_PORT_PLAN.md §1/§3.4/§9.1.
            srcDir(layout.buildDirectory.dir("generated/pythonBrain"))
        }
    }
}

dependencies {
    implementation(platform("androidx.compose:compose-bom:2024.09.00"))
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-graphics")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.activity:activity-compose:1.9.2")
    implementation("androidx.core:core-ktx:1.13.1")
    // TrainerLoop.kt's DefaultLifecycleObserver (§3.7 background-thread sim
    // loop suspending cleanly on onStop) - explicit rather than relying on
    // whatever version activity-compose happens to pull in transitively.
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.6")
}

// -- Stage the existing pure-Python avionics brain into this build --------
//
// Single source of truth: the desktop modules (navmath.py, gpsnav.py,
// instruments.py, autopilot.py, radios.py, windsaloft.py, wmm.py,
// scoring.py, sim_model.py, navdata/, androidbridge/) are never duplicated
// by hand into this Android project. This Gradle task copies exactly that
// curated set from the repo root into a build-generated (gitignored)
// directory, which Chaquopy's source set above then points at. Anything
// desktop-only (main.py, render.py, ifr1.py, config.py, gdl90_out.py,
// foreflight_discovery.py, xplane_feed.py, wx_auto.py, datasrc/, tests/) is
// deliberately left out — datasrc/ in particular needs `requests`-equivalent
// networking Chaquopy would need its own pip entry for, and isn't needed by
// Phase 0 (nav-data acquisition on-device is §3.5, not yet scoped here).
val repoRoot = rootDir.parentFile!!
val brainModules = listOf(
    "navmath.py", "gpsnav.py", "instruments.py", "autopilot.py",
    "radios.py", "windsaloft.py", "wmm.py", "scoring.py", "sim_model.py",
    // Phase 1 (docs/ANDROID_PORT_PLAN.md §6): androidbridge now reuses
    // main.py's World/route_event/Config directly rather than
    // reimplementing them - all confirmed import-clean under Chaquopy
    // (stdlib-only at module scope; pygame/render/hid are only imported
    // lazily inside function bodies main.py's CLI path calls, never
    // triggered by androidbridge). gns530.py/gns430.py are what World
    // actually instantiates as the GNS unit.
    "main.py", "ifr1.py", "config.py", "gns530.py", "gns430.py",
)
val brainPackages = listOf("navdata", "androidbridge")

val stageBrainPython = tasks.register<Copy>("stageBrainPython") {
    val dest = layout.buildDirectory.dir("generated/pythonBrain")
    into(dest)
    brainModules.forEach { name -> from(repoRoot.resolve(name)) }
    brainPackages.forEach { pkg ->
        from(repoRoot.resolve(pkg)) {
            into(pkg)
            exclude("__pycache__/**", "**/__pycache__/**")
        }
    }
    // wmm.py resolves this relative to its own file location (Path(__file__)
    // .parent / "assets" / "wmm" / "WMM2025.COF") - stage it at the matching
    // relative path so that lookup still works unmodified on Android.
    from(repoRoot.resolve("assets/wmm")) { into("assets/wmm") }
}

tasks.named("preBuild") { dependsOn(stageBrainPython) }

// `preBuild` ordering alone isn't enough: Gradle's task-graph validation
// (real error, found via an actual build 2026-09-24) flags
// mergeDebugPythonSources/mergeReleasePythonSources - Chaquopy's own tasks,
// one per build variant, that read stageBrainPython's output directory -
// for using that output without a *declared* dependency, since Gradle can't
// otherwise guarantee ordering (or cache correctness) from a shared
// directory path alone. Match by name instead of a fixed variant list so
// this keeps working if/when a release variant's task appears too.
tasks.matching { it.name.matches(Regex("merge[A-Za-z]*PythonSources")) }
    .configureEach { dependsOn(stageBrainPython) }
