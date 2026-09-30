package space.hashborn.cinema
import org.junit.Assert.*
import org.junit.Test

class EpisodesTest {
    @Test fun lostFilmSingle() {assertEquals(EpisodeRange(1,7,7),episodeRange("Lanterns - S1E7 - rus 1080p"))}
    @Test fun rangePack() {assertEquals(EpisodeRange(1,1,8),episodeRange("Show [S01E01-08 of 08] (2026)"))}
    @Test fun seasonPack() {assertEquals(EpisodeRange(2,null,null),episodeRange("Show S02 1080p"))}
    @Test fun numberedFile() {assertEquals(EpisodeRange(1,2,2),episodeRange("Show.S01E02.1080.x264.mkv"))}
    @Test fun alternativeFormat() {assertEquals(EpisodeRange(3,12,12),episodeRange("Show 3x12 HDTV"))}
    @Test fun unknownIsNotGuessed() {assertEquals(EpisodeRange(null,null,null),episodeRange("Movie 2026 1080p"))}
    @Test fun malformedRangeBounded() {assertEquals(EpisodeRange(1,1,1),episodeRange("Show S01E01-999"))}
    @Test fun russian() {assertEquals(EpisodeRange(1,1,8),episodeRange("Сезон: 1 Серии: 1-8"))}
}
