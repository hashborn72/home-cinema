package space.hashborn.cinema

import org.junit.Assert.*
import org.junit.Test

class ReleaseFilePresentationTest {
    @Test fun primaryActionResumesOnlyAnUnfinishedSavedPosition() {
        assertTrue(releaseFileAction(8_000, false).resume)
        assertFalse(releaseFileAction(8_000, true).resume)
        assertFalse(releaseFileAction(0, false).resume)
        assertFalse(releaseFileAction(-1, false).resume)
    }
    @Test fun unknownDurationNeverCreatesAnInvalidProgressBar() {
        assertNull(releaseFileProgress(8_000, 0, false))
        assertNull(releaseFileProgress(0, 60_000, false))
        assertEquals(.5f, releaseFileProgress(30_000, 60_000, false)!!, .001f)
        assertEquals(1f, releaseFileProgress(90_000, 60_000, false)!!, .001f)
        assertEquals(1f, releaseFileProgress(0, 0, true)!!, .001f)
    }
    @Test fun episodeRowsUseExplicitEpisodeNumbersRatherThanTechnicalPaths() {
        assertEquals("Сезон 2 · Серия 7", releaseFileEpisode("Show/Show.S02E07.1080p.WEB-DL.mkv"))
        assertEquals("Сезон 2 · Серия 7–8", releaseFileEpisode("Show\\Show.S02E07-08.mkv"))
        assertNull(releaseFileEpisode("Movie.2026.1080p.mkv"))
    }
    @Test fun nonEpisodeNamesKeepDistinctFormatsAndCollectionTitles() {
        assertEquals("MP4 720p", releaseFileName("MP4 720p"))
        assertEquals("Фильм 2.1080p", releaseFileName("Collection/Фильм 2.1080p.MKV"))
        assertEquals("Part.1.direct", releaseFileName("Collection\\Part.1.direct"))
        assertEquals("Фильм 3", releaseFileName("Collection\\Фильм 3.mp4"))
    }
}
