package space.hashborn.cinema

import android.content.Intent
import android.net.Uri

internal enum class ExternalPlayer(val packageName: String, val label: String) {
    JUST("com.brouken.player", "Just Player"),
    VLC("org.videolan.vlc", "VLC");

    fun intent(url: String, title: String, positionMs: Long): Intent = Intent(Intent.ACTION_VIEW).apply {
        setDataAndType(Uri.parse(url), "video/*")
        setPackage(packageName)
        putExtra("title", title)
        if (this@ExternalPlayer == VLC) {
            // Launch the exported player directly so its result returns to Home Cinema.
            setClassName(packageName, "org.videolan.vlc.gui.video.VideoPlayerActivity")
            putExtra("position", positionMs)
            putExtra("from_start", positionMs == 0L)
        } else {
            putExtra("position", positionMs.coerceIn(0L, Int.MAX_VALUE.toLong()).toInt())
            putExtra("return_result", true)
        }
    }

    fun result(data: Intent?): PlaybackResult = when (this) {
        JUST -> PlaybackResult.validated(data.timeExtra("position"), data.timeExtra("duration"), data?.getStringExtra("end_by"))
        // VLC returns no explicit completion reason. Do not invent a watched flag.
        VLC -> PlaybackResult.validated(data.timeExtra("extra_position"), data.timeExtra("extra_duration"), null)
    }

    companion object {
        fun fromPreference(value: String?): ExternalPlayer = entries.firstOrNull { it.packageName == value } ?: JUST
    }
}

@Suppress("DEPRECATION")
private fun Intent?.timeExtra(key: String): Long? = when (val value = this?.extras?.get(key)) {
    is Long -> value
    is Int -> value.toLong()
    else -> null
}
