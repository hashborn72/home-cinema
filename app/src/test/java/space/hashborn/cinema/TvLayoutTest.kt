package space.hashborn.cinema

import android.graphics.Bitmap
import android.graphics.Canvas
import android.view.WindowManager
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.runtime.Composable
import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.key.Key
import androidx.compose.ui.semantics.SemanticsActions
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createEmptyComposeRule
import androidx.compose.ui.unit.dp
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.darkColorScheme
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertTrue
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.android.controller.ActivityController
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode
import java.io.File

/** Render the production Compose components at a TV viewport, without a TV or emulator. */
@OptIn(ExperimentalTestApi::class)
@RunWith(RobolectricTestRunner::class)
@Config(sdk=[34],qualifiers="w960dp-h540dp-land-xhdpi")
@GraphicsMode(GraphicsMode.Mode.NATIVE)
class TvLayoutTest {
    @get:Rule val compose=createEmptyComposeRule()
    private var activity:ActivityController<ComponentActivity>?=null

    @After fun close() { activity?.pause()?.stop()?.destroy() }

    private fun render(content:@Composable ()->Unit) {
        val controller=Robolectric.buildActivity(ComponentActivity::class.java)
        activity=controller
        controller.get().window.setFlags(WindowManager.LayoutParams.FLAG_FULLSCREEN,WindowManager.LayoutParams.FLAG_FULLSCREEN)
        controller.setup().windowFocusChanged(true)
        controller.get().setContent { MaterialTheme(colorScheme=darkColorScheme()) {content()} }
        compose.waitForIdle()
    }

    private fun capture(name:String) {
        val folder=File("build/reports/tv-layout").apply {mkdirs()}
        compose.runOnIdle {
            val view=activity!!.get().window.decorView
            val bitmap=Bitmap.createBitmap(view.width,view.height,Bitmap.Config.ARGB_8888)
            view.draw(Canvas(bitmap))
            File(folder,"$name.png").outputStream().use {
                bitmap.compress(Bitmap.CompressFormat.PNG,100,it)
            }
            bitmap.recycle()
        }
    }

    /** The production screens deliberately defer focus until their lazy children exist. */
    private fun settleFocus() {
        compose.waitForIdle()
        compose.mainClock.advanceTimeBy(250)
        compose.waitForIdle()
    }

    private fun SemanticsNodeInteraction.remoteClick():SemanticsNodeInteraction {
        performSemanticsAction(SemanticsActions.RequestFocus) {it()}
        assertIsFocused().performKeyInput {pressKey(Key.DirectionCenter)}
        return this
    }

    private fun seriesFixture()=JSONObject("""{
        "id":"fixture-series","title":"Тестовый сериал","media_type":"tv","year":2024,
        "sources":["lostfilm"],"library":{"favorite":false},
        "metadata":{"title":"Тестовый сериал","description":"Короткое описание для проверки интерфейса.",
            "season_years":{"1":"2024","2":"2025"}},
        "releases":[
            {"id":"s1e1","title":"Test S01E01","source":"lostfilm","quality":"1080p","kind":"torrent","size":1073741824},
            {"id":"s1e2","title":"Test S01E02","source":"lostfilm","quality":"1080p","kind":"torrent","size":1073741824},
            {"id":"s2e1-1080","title":"Test S02E01","source":"lostfilm","quality":"1080p","kind":"torrent","size":1073741824},
            {"id":"s2e1-720","title":"Test S02E01","source":"lostfilm","quality":"720p","kind":"torrent","size":536870912},
            {"id":"s2e2","title":"Test S02E02","source":"lostfilm","quality":"1080p","kind":"torrent","size":1073741824}
        ]
    }""")

    private fun filesFixture()=listOf(
        JSONObject("""{"id":1,"path":"Test.S01E01.1080p.mkv","size":1073741824,"position_ms":0,"duration_ms":2400000,"completed":false,"sample":false}"""),
        JSONObject("""{"id":2,"path":"Season.01/Test.Series.S01E02.1080p.WEB-DL.Multi.Audio.Subtitles.Full.Release.Name.mkv","size":1073741824,"position_ms":600000,"duration_ms":2400000,"completed":false,"sample":false}"""),
        JSONObject("""{"id":3,"path":"Test.S01E03.1080p.mkv","size":1073741824,"position_ms":2400000,"duration_ms":2400000,"completed":true,"sample":false}""")
    )

    private fun filesRelease()=JSONObject("""{
        "id":"fixture-release","title":"Test Series S01 1080p WEB-DL",
        "season":1,"source":"lostfilm","quality":"1080p"
    }""")

    @Test fun focusedCardKeepsYearInsideBorder() {
        val card=JSONObject("""{"id":"card","title":"Очень длинное название сериала на двух строках","year":"2024","sources":["lostfilm"]}""")
        val focus=FocusRequester()
        render {
            Box(Modifier.fillMaxSize().background(Color(0xFF101722)).padding(28.dp)) {
                CinemaCard(card,focus) {}
            }
        }
        compose.runOnIdle {focus.requestFocus()}
        compose.waitForIdle()
        val year=compose.onNodeWithText("2024 · LostFilm",useUnmergedTree=true).assertIsDisplayed().fetchSemanticsNode().boundsInRoot
        val cardBounds=compose.onNode(hasClickAction()).fetchSemanticsNode().boundsInRoot
        assertTrue("Year must have room below its baseline and above the focus border",cardBounds.bottom-year.bottom>=16f)
        capture("card_caption")
    }

    @Test fun seasonFilterSelectsReleaseAndRestoresItsFocusAfterReturning() {
        val card=seriesFixture().apply {
            getJSONObject("metadata").put("description","Подробное описание истории и её героев. ".repeat(25))
        }
        val state=DetailState()
        val visible=mutableStateOf(true)
        var opened=""
        render {
            if(visible.value) DetailScreen(card,state,loading=false,error="",flagBusy=false,
                onBack={},onFavorite={},onRelease={opened=it.getString("id");visible.value=false},
                onResume={},onRetry={})
            else Box(Modifier.fillMaxSize())
        }
        settleFocus()
        compose.onNodeWithTag("detail-release-s1e1").assertIsDisplayed()
        compose.onNodeWithTag("detail-season-2").remoteClick()
        compose.waitForIdle()
        compose.onNodeWithTag("detail-release-s1e1").assertDoesNotExist()
        compose.onNodeWithTag("detail-release-s1e2").assertDoesNotExist()
        compose.onNodeWithTag("detail-release-s2e1-720").assertIsDisplayed().remoteClick()
        compose.runOnIdle {
            assertEquals(2,state.season)
            assertEquals("s2e1-720",opened)
            assertEquals(opened,state.selected)
        }
        compose.onNodeWithTag("detail-release-s2e1-720").assertDoesNotExist()
        // The picker is another screen, while the catalog retains the same DetailState.
        compose.runOnIdle {visible.value=true}
        settleFocus()
        compose.onNodeWithTag("detail-release-s2e1-720").assertIsDisplayed().assertIsFocused()
        compose.onNodeWithTag("detail-release-s1e1").assertDoesNotExist()
        compose.onNodeWithTag("detail-season-all").remoteClick()
        compose.waitForIdle()
        compose.onNodeWithTag("detail-release-s1e1").assertIsDisplayed()
        compose.runOnIdle {assertEquals(null,state.season)}
    }

    @Test fun fileActionsSendCorrectResumeIntentForProgressCompletedAndNewFiles() {
        val files=filesFixture()
        val plays=mutableListOf<Pair<Int,Boolean>>()
        render {
            ReleaseFilesScreen(filesRelease(),seriesFixture(),files,preparing=false,playerBusy=false,
                error="",playerStatus="",onBack={},onRetry={},
                onPlay={file,resume -> plays += file.getInt("id") to resume},onRefresh={})
        }
        settleFocus()
        compose.onNodeWithTag("release-file-2").assertIsFocused().assertIsDisplayed().remoteClick()
        compose.onNodeWithTag("release-file-restart-2").assertIsDisplayed().remoteClick()
        compose.onNodeWithTag("release-file-3").performScrollTo().assertIsDisplayed()
        compose.onNodeWithText("Смотреть снова").assertIsDisplayed()
        compose.onNodeWithTag("release-file-restart-3").assertDoesNotExist()
        compose.onNodeWithTag("release-file-3").remoteClick()
        compose.onNode(SemanticsMatcher.keyIsDefined(SemanticsActions.ScrollToIndex)).performScrollToIndex(0)
        compose.onNodeWithTag("release-file-1").assertIsDisplayed().remoteClick()
        compose.runOnIdle {
            assertEquals(listOf(2 to true,2 to false,3 to false,1 to false),plays)
        }
    }

    @Test fun fullFileInformationClosesBackToItsOriginalFocusControl() {
        val files=filesFixture()
        val fullName=files[1].getString("path")
        render {
            ReleaseFilesScreen(filesRelease(),seriesFixture(),files,preparing=false,playerBusy=false,
                error="",playerStatus="",onBack={},onRetry={},onPlay={_,_ ->},onRefresh={})
        }
        settleFocus()
        compose.onNodeWithTag("release-file-info-2")
            .performSemanticsAction(SemanticsActions.RequestFocus) {it()}
        compose.onNodeWithTag("release-file-info-2").assertIsFocused().remoteClick()
        settleFocus()
        compose.onNodeWithText(fullName,useUnmergedTree=true).assertIsDisplayed()
        compose.onNodeWithText("Закрыть").remoteClick()
        settleFocus()
        compose.onNodeWithText(fullName,useUnmergedTree=true).assertDoesNotExist()
        compose.onNodeWithTag("release-file-info-2").assertIsDisplayed().assertIsFocused()
    }
}
