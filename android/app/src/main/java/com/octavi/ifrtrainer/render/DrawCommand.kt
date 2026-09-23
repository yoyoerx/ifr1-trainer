package com.octavi.ifrtrainer.render

/**
 * The draw-command-list contract described in docs/ANDROID_PORT_PLAN.md
 * §3.6: rather than crossing the Chaquopy/JNI boundary once per drawn
 * primitive at ~30 Hz, the Python side (a future `render_commands.py`,
 * not yet written — see below) builds one list of these per tick; Kotlin
 * crosses the boundary once per frame and replays it against a Compose
 * `Canvas`.
 *
 * UNVERIFIED SCAFFOLD / Phase 0 placeholder: this is the *shape* of the
 * contract only. Nothing produces a real list of these from the actual
 * GNS 530/CDI/HSI layout yet (`render.py`'s pygame-drawing layout logic
 * has not been ported) — [InstrumentCanvas] currently only replays a
 * hardcoded sample list, to prove the Kotlin-side replay mechanism
 * independent of the still-unported layout math.
 *
 * Colors are packed 0xAARRGGBB ints (Compose `Color`'s own representation)
 * rather than named constants, so the Python side never needs to agree on
 * a color-name enum with Kotlin — it just sends the same ints `render.py`
 * already computes today (pygame `Color` is also just an RGBA tuple).
 */
sealed class DrawCommand {
    data class Text(
        val text: String,
        val x: Float,
        val y: Float,
        val sizeSp: Float,
        val colorArgb: Long,
        val fontId: String = "mono",   // "mono" (B612 Mono) | "seven" (DSEG7) - matches render.py's f_sm/f_lg/f_seg naming today
    ) : DrawCommand()

    data class Line(
        val x1: Float, val y1: Float, val x2: Float, val y2: Float,
        val widthPx: Float, val colorArgb: Long,
    ) : DrawCommand()

    data class Rect(
        val x: Float, val y: Float, val w: Float, val h: Float,
        val colorArgb: Long, val filled: Boolean = true,
    ) : DrawCommand()

    data class Circle(
        val cx: Float, val cy: Float, val radius: Float,
        val colorArgb: Long, val filled: Boolean = false,
    ) : DrawCommand()

    data class Polygon(
        val pointsXY: List<Float>,   // flattened [x0,y0, x1,y1, ...]
        val colorArgb: Long, val filled: Boolean = true,
    ) : DrawCommand()
}
