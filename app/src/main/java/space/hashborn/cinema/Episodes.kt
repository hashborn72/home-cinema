package space.hashborn.cinema

data class EpisodeRange(val season: Int?, val first: Int?, val last: Int?) {
    fun label(): String = when {
        first != null -> (season?.let { "Сезон $it · " } ?: "") + "Серия $first" + (if(last!=null && last!=first) "–$last" else "")
        season != null -> "Сезон $season целиком / сборник"
        else -> "Сборник / номер серии не определён"
    }
}

fun episodeRange(title: String): EpisodeRange {
    val s=Regex("(?i)(?:^|[^a-z0-9])S(\\d{1,2})(?:[ ._-]*E(\\d{1,3})(?:[-–]E?(\\d{1,3}))?)?").find(title)
        ?: Regex("(?i)(?:^|[^0-9])(\\d{1,2})x(\\d{1,3})(?:[-–](\\d{1,3}))?").find(title)
    if(s!=null) {
        val season=s.groupValues[1].toIntOrNull()
        val first=s.groupValues[2].toIntOrNull()
        val end=s.groupValues[3].toIntOrNull()
        return EpisodeRange(season,first,if(end!=null && first!=null && end>=first && end-first<200) end else first)
    }
    val ruSeason=Regex("(?iu)сезон\\s*[:№]?\\s*(\\d{1,2})").find(title)?.groupValues?.get(1)?.toIntOrNull()
    val ruEpisode=Regex("(?iu)сери[яи]\\s*[:№]?\\s*(\\d{1,3})(?:[-–](\\d{1,3}))?").find(title)
    val first=ruEpisode?.groupValues?.get(1)?.toIntOrNull()
    val end=ruEpisode?.groupValues?.get(2)?.toIntOrNull()
    return EpisodeRange(ruSeason,first,if(end!=null && first!=null && end>=first && end-first<200) end else first)
}
