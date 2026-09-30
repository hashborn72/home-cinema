package space.hashborn.cinema

data class PlaybackResult(val positionMs: Long?, val durationMs: Long?, val endBy: String?) {
    companion object {
        fun validated(position: Long?, duration: Long?, endBy: String?): PlaybackResult {
            val validDuration = duration?.takeIf { it > 0 }
            val validPosition = position?.takeIf { it >= 0 && (validDuration == null || it <= validDuration) }
            return PlaybackResult(validPosition, validDuration, endBy?.takeIf { it == "user" || it == "playback_completion" })
        }
    }
}
