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
        val align: Int = 0,            // 0 left, 1 center, 2 right - render.py's r._t(center=)/r.lcd(right=)
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

/**
 * Decodes the newline-delimited, pipe-field string `render_commands.py`'s
 * `encode()` produces (Python side: `androidbridge/session.py
 * .render_hsi()`), one command per line, e.g. `T|x|y|sizeSp|colorArgb
 * |fontId|align|text` (text last, since it's the only variable-length
 * field). Deliberately a string, not a Chaquopy Java-List/PyObject return -
 * a real on-device crash (2026-09-24, docs/ANDROID_PORT_PLAN.md §6) found
 * Chaquopy's List marshaling produces a Python-side object `tuple()`/
 * `list()` can't consume; this sidesteps that class of bug the same way
 * `BrainBridge.tickWorld`/`dispatchEvent` already do.
 */
fun parseDrawCommands(encoded: String): List<DrawCommand> {
    if (encoded.isEmpty()) return emptyList()
    val out = ArrayList<DrawCommand>()
    for (line in encoded.split("\n")) {
        if (line.isEmpty()) continue
        val parts = line.split("|")
        try {
            when (parts[0]) {
                "T" -> out.add(
                    DrawCommand.Text(
                        text = parts.drop(7).joinToString("|"),
                        x = parts[1].toFloat(),
                        y = parts[2].toFloat(),
                        sizeSp = parts[3].toFloat(),
                        colorArgb = parts[4].toLong(),
                        fontId = parts[5],
                        align = parts[6].toInt(),
                    )
                )
                "L" -> out.add(
                    DrawCommand.Line(
                        x1 = parts[1].toFloat(), y1 = parts[2].toFloat(),
                        x2 = parts[3].toFloat(), y2 = parts[4].toFloat(),
                        widthPx = parts[5].toFloat(), colorArgb = parts[6].toLong(),
                    )
                )
                "R" -> out.add(
                    DrawCommand.Rect(
                        x = parts[1].toFloat(), y = parts[2].toFloat(),
                        w = parts[3].toFloat(), h = parts[4].toFloat(),
                        colorArgb = parts[5].toLong(), filled = parts[6] == "1",
                    )
                )
                "C" -> out.add(
                    DrawCommand.Circle(
                        cx = parts[1].toFloat(), cy = parts[2].toFloat(),
                        radius = parts[3].toFloat(), colorArgb = parts[4].toLong(),
                        filled = parts[5] == "1",
                    )
                )
                "P" -> out.add(
                    DrawCommand.Polygon(
                        colorArgb = parts[1].toLong(), filled = parts[2] == "1",
                        pointsXY = parts[3].split(";").filter { it.isNotEmpty() }
                            .flatMap { pt -> pt.split(",").map { it.toFloat() } },
                    )
                )
            }
        } catch (_: Exception) {
            // one malformed line shouldn't blank the whole frame - skip it
        }
    }
    return out
}
