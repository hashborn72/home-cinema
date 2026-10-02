package space.hashborn.cinema

internal data class ReleaseFileAction(val text: String, val resume: Boolean)

internal fun releaseFileAction(positionMs: Long, completed: Boolean): ReleaseFileAction = when {
    completed -> ReleaseFileAction("Смотреть снова", false)
    positionMs > 0 -> ReleaseFileAction("Продолжить", true)
    else -> ReleaseFileAction("Смотреть", false)
}

internal fun releaseFileProgress(positionMs: Long, durationMs: Long, completed: Boolean): Float? = when {
    completed -> 1f
    positionMs > 0 && durationMs > 0 -> (positionMs.toDouble() / durationMs).coerceIn(0.0, 1.0).toFloat()
    else -> null
}

internal fun releaseFileEpisode(path: String): String? {
    val episode = episodeRange(path.substringAfterLast('/').substringAfterLast('\\'))
    val first = episode.first ?: return null
    return (episode.season?.let { "Сезон $it · " } ?: "") + "Серия $first" +
        (if (episode.last != null && episode.last != first) "–${episode.last}" else "")
}

internal fun releaseFileName(path: String): String {
    val name = path.substringAfterLast('/').substringAfterLast('\\').trim()
    val extension = name.substringAfterLast('.', "").lowercase()
    return (if (extension in setOf("mkv", "mp4", "avi", "m4v", "mov", "ts", "m2ts", "webm", "mpg", "mpeg"))
        name.substringBeforeLast('.') else name).ifBlank { "Видео" }
}
