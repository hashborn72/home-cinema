package space.hashborn.cinema

import org.junit.Assert.*
import org.junit.Test

class ArtworkTest {
    @Test fun portraitAcceptsOnlyPortraitArtwork() {
        assertTrue(artworkFits(500,750))
        assertFalse(artworkFits(1920,1080))
        assertFalse(artworkFits(500,500))
    }
    @Test fun previewAcceptsOnlyWideArtwork() {
        assertTrue(artworkFits(1920,1080,true))
        assertFalse(artworkFits(500,750,true))
        assertFalse(artworkFits(600,100,true))
    }
    @Test fun missingDimensionsFailClosed() {
        assertFalse(artworkFits(0,750))
        assertFalse(artworkFits(500,0))
        assertFalse(artworkFits(-1,-1))
    }
}
