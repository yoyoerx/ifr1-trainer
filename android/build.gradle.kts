// Root build file — declares plugin versions once, applied `false` here and
// turned on per-module (just :app for now). Versions are best-effort as of
// this scaffold's authoring date (2026-09) and UNVERIFIED — this repo has no
// JDK 17 / Android SDK / Gradle available to actually resolve and build
// this project. First Android Studio sync should bump anything its own
// upgrade-assistant flags as stale. See android/README.md.
plugins {
    id("com.android.application") version "8.6.1" apply false
    id("org.jetbrains.kotlin.android") version "2.0.20" apply false
    // the Compose compiler is a separate plugin from kotlin.android as of
    // Kotlin 2.0+, and needs its own version here too - app/build.gradle.kts
    // applies it with no version, which only works when one is declared in
    // this root-level `plugins {}` block. Found via a real Gradle sync
    // (2026-09-24): "Plugin [id: 'org.jetbrains.kotlin.plugin.compose'] was
    // not found ... plugin dependency must include a version number."
    id("org.jetbrains.kotlin.plugin.compose") version "2.0.20" apply false
    id("com.chaquo.python") version "16.0.0" apply false
}
