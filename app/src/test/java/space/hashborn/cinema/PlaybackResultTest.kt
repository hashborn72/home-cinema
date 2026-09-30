package space.hashborn.cinema
import org.junit.Assert.*
import org.junit.Test

class PlaybackResultTest {
    @Test fun unknownPositionIsNotInvented() { assertNull(PlaybackResult.validated(null, null, null).positionMs) }
    @Test fun invalidTimesAreRejected() { assertNull(PlaybackResult.validated(-1, 100, "user").positionMs); assertNull(PlaybackResult.validated(101, 100, "user").positionMs) }
    @Test fun millisecondsArePreserved() { assertEquals(42170L, PlaybackResult.validated(42170, 600000, "user").positionMs) }
    @Test fun completionDoesNotFabricatePosition() { val result = PlaybackResult.validated(null, null, "playback_completion"); assertNull(result.positionMs); assertEquals("playback_completion", result.endBy) }
}
