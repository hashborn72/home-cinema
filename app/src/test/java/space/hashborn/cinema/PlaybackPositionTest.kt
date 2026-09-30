package space.hashborn.cinema

import org.junit.Assert.assertEquals
import org.junit.Test

class PlaybackPositionTest {
    @Test fun shortProgressIsVisible() {assertEquals("00:08",formatPlaybackPosition(8812))}
    @Test fun minutesAndSeconds() {assertEquals("02:41",formatPlaybackPosition(161760))}
    @Test fun longMovie() {assertEquals("1:01:01",formatPlaybackPosition(3661000))}
    @Test fun negativeDoesNotWrap() {assertEquals("00:00",formatPlaybackPosition(-1))}
}
