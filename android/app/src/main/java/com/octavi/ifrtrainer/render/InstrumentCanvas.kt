package com.octavi.ifrtrainer.render

import androidx.compose.foundation.Canvas
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.Stroke

/**
 * Replays a [DrawCommand] list against a Compose [Canvas] — the Kotlin-side
 * half of the §3.6 draw-command-list contract. Phase 0 placeholder: text
 * uses a plain top-left `drawText`-equivalent, not yet positioned/measured
 * to match `render.py`'s actual font metrics (B612 Mono / DSEG7) — that
 * needs the real layout math ported first, tracked in
 * docs/ANDROID_PORT_PLAN.md, not solved here.
 */
@Composable
fun InstrumentCanvas(commands: List<DrawCommand>, modifier: Modifier = Modifier) {
    Canvas(modifier = modifier) {
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
                    // Placeholder only - text inside a Canvas DrawScope needs
                    // a native android.graphics.Paint/drawText call (Compose's
                    // BasicText is a separate composable, not usable inside
                    // drawScope directly). Left as a TODO rather than guessed
                    // at, since getting text metrics right is exactly the
                    // part that needs to match render.py's existing,
                    // FINDINGS.md-validated layout positions.
                }
            }
        }
    }
}
