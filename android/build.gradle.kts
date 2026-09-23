// Root build file — declares plugin versions once, applied `false` here and
// turned on per-module (just :app for now). Versions are best-effort as of
// this scaffold's authoring date (2026-09) and UNVERIFIED — this repo has no
// JDK 17 / Android SDK / Gradle available to actually resolve and build
// this project. First Android Studio sync should bump anything its own
// upgrade-assistant flags as stale. See android/README.md.
plugins {
    id("com.android.application") version "8.6.1" apply false
    id("org.jetbrains.kotlin.android") version "2.0.20" apply false
    id("com.chaquo.python") version "16.0.0" apply false
}
