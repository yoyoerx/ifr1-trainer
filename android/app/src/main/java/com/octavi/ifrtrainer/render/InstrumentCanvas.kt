package com.octavi.ifrtrainer.render

import android.graphics.Paint
import android.graphics.Typeface
import androidx.compose.foundation.Canvas
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.scale
import androidx.compose.ui.graphics.nativeCanvas

/**
 * Replays a [DrawCommand] list against a Compose [Canvas] — the Kotlin-side
 * half of the §3.6 draw-command-list contract.
 *
 * Text (2026-09-24): Compose's `Canvas` `DrawScope` has no native text
 * primitive, so this drops to `nativeCanvas.drawText` with a plain
 * `android.graphics.Paint` - the documented way to draw text inside a
 * DrawScope. `fontId == "seven"` (render.py's DSEG7 LCD readouts) uses
 * fake-bold monospace as a stand-in; bundling the real DSEG7 font file is a
 * separate, later cosmetic task, not solved here.
 *
 * [sourceWidth]/[sourceHeight] is the coordinate-space size the Python side
 * rendered the commands in (e.g. `render_commands.hsi_commands`'s/
 * `ap_panel_commands`'s `w`/`h` args) - the actual on-screen `Canvas` is
 * measured in real pixels, which vary by device density, so this scales
 * the whole replay to fit whatever size Compose actually gives the
 * `Canvas` rather than drawing at a fixed pixel offset that would only be
 * correct on one specific device. Per-axis (not a single uniform factor)
 * so non-square instruments (the AP panel strip, unlike the square HSI)
 * scale correctly too - the caller should size its `Modifier` box with the
 * same aspect ratio as `sourceWidth`:`sourceHeight` to avoid stretching.
 */
@Composable
fun InstrumentCanvas(
    commands: List<DrawCommand>,
    modifier: Modifier = Modifier,
    sourceWidth: Float = 1f,
    sourceHeight: Float = 1f,
) {
    val monoPaint = remember { Paint(Paint.ANTI_ALIAS_FLAG).apply { typeface = Typeface.MONOSPACE } }
    val sevenPaint = remember {
        Paint(Paint.ANTI_ALIAS_FLAG).apply { typeface = Typeface.MONOSPACE; isFakeBoldText = true }
    }
    Canvas(modifier = modifier) {
        val factorX = if (sourceWidth > 0f) size.width / sourceWidth else 1f
        val factorY = if (sourceHeight > 0f) size.height / sourceHeight else 1f
        scale(scaleX = factorX, scaleY = factorY, pivot = Offset.Zero) {
        commands.forEach { cmd ->
            when (cmd) {
                is DrawCommand.Line -> drawLine(
                    color = Color(cmd.colorArgb),
                    start = Offset(cmd.x1, cmd.y1),
                    end = Offset(cmd.x2, cmd.y2),
                    strokeWidth = cmd.widthPx,
                )
                is DrawCommand.Rect -> drawRect(
                    color = Color(cmd.colorArgb),
                    topLeft = Offset(cmd.x, cmd.y),
                    size = androidx.compose.ui.geometry.Size(cmd.w, cmd.h),
                    style = if (cmd.filled) androidx.compose.ui.graphics.drawscope.Fill else Stroke(width = 2f),
                )
                is DrawCommand.Circle -> drawCircle(
                    color = Color(cmd.colorArgb),
                    radius = cmd.radius,
                    center = Offset(cmd.cx, cmd.cy),
                    style = if (cmd.filled) androidx.compose.ui.graphics.drawscope.Fill else Stroke(width = 2f),
                )
                is DrawCommand.Polygon -> {
                    val path = androidx.compose.ui.graphics.Path()
                    val pts = cmd.pointsXY
                    if (pts.size >= 2) {
                        path.moveTo(pts[0], pts[1])
                        var i = 2
                        while (i + 1 < pts.size) {
                            path.lineTo(pts[i], pts[i + 1])
                            i += 2
                        }
                        path.close()
                    }
                    drawPath(
                        path = path,
                        color = Color(cmd.colorArgb),
                        style = if (cmd.filled) androidx.compose.ui.graphics.drawscope.Fill else Stroke(width = 2f),
                    )
                }
                is DrawCommand.Text -> {
                    val paint = if (cmd.fontId == "seven") sevenPaint else monoPaint
                    paint.color = cmd.colorArgb.toInt()
                    paint.textSize = cmd.sizeSp
                    paint.textAlign = when (cmd.align) {
                        1 -> Paint.Align.CENTER
                        2 -> Paint.Align.RIGHT
                        else -> Paint.Align.LEFT
                    }
                    // cmd.y is the text's TOP (matching render.py's pygame
                    // convention, r._t/r.lcd blit with topleft=(x,y)) -
                    // android.graphics.Paint.drawText positions by BASELINE,
                    // so this converts once, here, rather than asking every
                    // render_commands.py function to fudge offsets against
                    // Android's convention (a real on-device bug, found
                    // 2026-09-24: the AP panel's tightly-packed 17px-pitch
                    // info box overlapped badly under ad hoc offsets that
                    // happened to look fine on the more loosely-spaced HSI).
                    drawContext.canvas.nativeCanvas.drawText(cmd.text, cmd.x, cmd.y - paint.ascent(), paint)
                }
            }
        }
        }
    }
}
