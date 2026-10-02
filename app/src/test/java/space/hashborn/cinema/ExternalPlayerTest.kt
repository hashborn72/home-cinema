package space.hashborn.cinema

import android.content.Intent
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [28])
class ExternalPlayerTest {
    private val url = "http://192.168.1.144:8093/torrent-play/test"

    @Test fun vlcReceivesOriginalStreamAndLongResumePosition() {
        val intent = ExternalPlayer.VLC.intent(url, "Episode", 42170L)
        assertEquals(url, intent.data.toString())
        assertEquals("video/*", intent.type)
        assertEquals("org.videolan.vlc.gui.video.VideoPlayerActivity", intent.component?.className)
        assertEquals(42170L, intent.getLongExtra("position", -1))
        assertFalse(intent.getBooleanExtra("from_start", true))
        assertEquals("Episode", intent.getStringExtra("title"))
    }

    @Test fun vlcStartFromBeginningOverridesItsOwnHistory() {
        assertTrue(ExternalPlayer.VLC.intent(url, "Episode", 0).getBooleanExtra("from_start", false))
    }

    @Test fun justPlayerKeepsItsExistingContract() {
        val intent = ExternalPlayer.JUST.intent(url, "Episode", 42170L)
        assertEquals("com.brouken.player", intent.`package`)
        assertEquals(42170, intent.getIntExtra("position", -1))
        assertTrue(intent.getBooleanExtra("return_result", false))
        val result = ExternalPlayer.JUST.result(Intent().putExtra("position", 42170).putExtra("duration", 600000).putExtra("end_by", "user"))
        assertEquals(PlaybackResult(42170L, 600000L, "user"), result)
    }

    @Test fun vlcResultPreservesLongTimesWithoutClaimingCompletion() {
        val result = ExternalPlayer.VLC.result(Intent().putExtra("extra_position", 3000000000L).putExtra("extra_duration", 4000000000L))
        assertEquals(PlaybackResult(3000000000L, 4000000000L, null), result)
    }

    @Test fun missingOrMalformedResultsDoNotInventProgress() {
        assertNull(ExternalPlayer.VLC.result(null).positionMs)
        assertNull(ExternalPlayer.VLC.result(Intent().putExtra("extra_position", "42")).positionMs)
        assertNull(ExternalPlayer.VLC.result(Intent().putExtra("extra_position", -1L)).positionMs)
        assertNull(ExternalPlayer.VLC.result(Intent().putExtra("extra_position", 101L).putExtra("extra_duration", 100L)).positionMs)
        assertNull(ExternalPlayer.VLC.result(Intent().putExtra("position", 42)).positionMs)
    }

    @Test fun savedChoiceIsRestoredAndExistingUsersKeepJustPlayer() {
        assertEquals(ExternalPlayer.VLC, ExternalPlayer.fromPreference("org.videolan.vlc"))
        assertEquals(ExternalPlayer.JUST, ExternalPlayer.fromPreference(null))
        assertEquals(ExternalPlayer.JUST, ExternalPlayer.fromPreference("unknown"))
    }
}
