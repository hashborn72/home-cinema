package space.hashborn.cinema

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.focusable
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.gestures.scrollBy
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.key.*
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import androidx.tv.material3.*
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import org.json.JSONObject

@Composable
internal fun CompactButton(text:String,onClick:()->Unit,modifier:Modifier=Modifier,enabled:Boolean=true) {
    Button(onClick=onClick,enabled=enabled,modifier=modifier.height(34.dp),contentPadding=PaddingValues(horizontal=12.dp,vertical=4.dp)) {
        Text(text,fontSize=15.sp,maxLines=1)
    }
}

internal class DetailState {
    var season by mutableStateOf<Int?>(null)
    var selected by mutableStateOf("")
    val scroll=LazyListState()
    private var qualityCard=""
    private val qualityRows=mutableMapOf<String,LazyListState>()
    fun qualityScroll(cardId:String,row:String):LazyListState {
        if(qualityCard!=cardId) {qualityRows.clear();qualityCard=cardId}
        return qualityRows.getOrPut(row) {LazyListState()}
    }
}

private val DetailBackground=Color(0xFF101722)
private val DetailPanel=Color(0xFF172333)
private val DetailText=Color(0xFFEDF3FA)
private val DetailMuted=Color(0xFFA8B5C7)
private val DetailCyan=Color(0xFF78DCF4)

/** Fixed-size controls keep remote focus clear without moving neighbouring choices. */
@Composable
private fun DetailSurface(onClick:()->Unit,modifier:Modifier=Modifier,enabled:Boolean=true,
                          selected:Boolean=false,content:@Composable ()->Unit) {
    var focused by remember {mutableStateOf(false)}
    val shape=RoundedCornerShape(8.dp)
    Surface(onClick=onClick,enabled=enabled,
        modifier=modifier.onFocusChanged {focused=it.isFocused}.clip(shape).border(
            BorderStroke(if(focused) 2.dp else 1.dp,
                if(focused) DetailCyan else if(selected) Color(0xFF3D7485) else Color(0xFF2B3A4D)),shape),
        colors=ClickableSurfaceDefaults.colors(
            containerColor=if(selected) Color(0xFF203B4B) else Color(0xFF1B2A3B),
            focusedContainerColor=Color(0xFF254653),contentColor=if(selected) DetailCyan else DetailText,
            focusedContentColor=Color.White),
        scale=ClickableSurfaceDefaults.scale(focusedScale=1f)) {content()}
}

@Composable
private fun DetailControl(text:String,onClick:()->Unit,modifier:Modifier=Modifier,
                          selected:Boolean=false,enabled:Boolean=true,small:Boolean=false) {
    DetailSurface(onClick,modifier.height(if(small) 30.dp else 34.dp),enabled,selected) {
        Box(Modifier.fillMaxHeight().padding(horizontal=12.dp),contentAlignment=Alignment.Center) {
            Text(text,fontSize=if(small) 13.sp else 14.sp,maxLines=1,overflow=TextOverflow.Ellipsis)
        }
    }
}

private fun detailSize(release:JSONObject):String {
    val size=release.optDouble("size").takeIf {it.isFinite() && it>0} ?: return ""
    return if(size>=1073741824.0) "%.1f ГБ".format(size/1073741824.0) else "%.0f МБ".format(size/1048576.0)
}

private fun EpisodeRange.rowLabel():String = when {
    first!=null && last!=null && first!=last -> "Серии $first–$last"
    first!=null -> "Серия $first"
    season!=null -> "Весь сезон"
    else -> "Без номера"
}

@Composable
internal fun DetailScreen(card:JSONObject,state:DetailState,loading:Boolean,error:String,flagBusy:Boolean,
                          onBack:()->Unit,onFavorite:()->Unit,onRelease:(JSONObject)->Unit,onResume:()->Unit,onRetry:()->Unit) {
    val cardId=card.optString("id")
    val releases=card.getJSONArray("releases").let {a -> (0 until a.length()).map {a.getJSONObject(it)}}
    val series=card.optString("media_type")=="tv"
    val seasons=releases.mapNotNull{episodeRange(it.getString("title")).season}.distinct().sorted()
    val metadata=card.optJSONObject("metadata")
    val description=metadata?.optString("description")?.takeUnless{it=="null" || it.isBlank()}
        ?: "Описание пока не найдено. Видео доступно ниже."
    var showDescription by remember(cardId) {mutableStateOf(false)}
    var descriptionWasOpen by remember(cardId) {mutableStateOf(false)}
    var descriptionTruncated by remember(description) {mutableStateOf(false)}
    var titleTruncated by remember(displayTitle(card)) {mutableStateOf(false)}
    val descriptionFocus=remember {FocusRequester()}
    val requesters=remember(cardId) {mutableMapOf<String,FocusRequester>()}
    val backFocus=remember {FocusRequester()}
    LaunchedEffect(Unit) {delay(120);runCatching {(requesters[state.selected] ?: backFocus).requestFocus()}}
    val sources=card.optJSONArray("sources")?.let {a -> (0 until a.length()).map {providerName(a.getString(it))}}.orEmpty()
    val groups=remember(card,state.season) {
        releases.filter {state.season==null || episodeRange(it.getString("title")).season==state.season}
            .groupBy {episodeRange(it.getString("title"))}.toList()
            .sortedWith(compareBy({it.first.season ?: Int.MAX_VALUE},{it.first.first ?: Int.MAX_VALUE},{it.first.last ?: Int.MAX_VALUE}))
    }
    LazyColumn(Modifier.fillMaxSize().background(DetailBackground).padding(horizontal=24.dp,vertical=14.dp),
        state=state.scroll,verticalArrangement=Arrangement.spacedBy(6.dp),contentPadding=PaddingValues(bottom=10.dp)) {
        item(key="header") {
            Row(horizontalArrangement=Arrangement.spacedBy(8.dp),verticalAlignment=Alignment.CenterVertically) {
                DetailControl("← Назад",onBack,Modifier.focusRequester(backFocus).semantics {contentDescription="Назад"})
                Spacer(Modifier.weight(1f))
                val favorite=card.optJSONObject("library")?.optBoolean("favorite")==true
                DetailControl(if(favorite) "♥ В избранном" else "♡ В избранное",onFavorite,
                    Modifier.semantics {contentDescription=if(favorite) "Удалить из избранного" else "Добавить в избранное"},
                    selected=favorite,enabled=!flagBusy)
                if(card.has("resume_target")) DetailControl("▶ Продолжить",onResume,selected=true)
            }
        }
        item(key="description") {
            Row(Modifier.fillMaxWidth().background(DetailPanel,RoundedCornerShape(10.dp)).padding(10.dp),
                horizontalArrangement=Arrangement.spacedBy(14.dp)) {
                if(metadata?.optString("poster")?.startsWith("http")==true)
                    CinemaPoster(metadata.getString("poster"),Modifier.width(54.dp).height(81.dp).clip(RoundedCornerShape(5.dp)))
                Column(Modifier.weight(1f),verticalArrangement=Arrangement.spacedBy(3.dp)) {
                    Row(horizontalArrangement=Arrangement.spacedBy(12.dp),verticalAlignment=Alignment.CenterVertically) {
                        Text(displayTitle(card),fontSize=24.sp,lineHeight=28.sp,color=DetailText,maxLines=1,
                            modifier=Modifier.weight(1f),overflow=TextOverflow.Ellipsis,onTextLayout={titleTruncated=it.hasVisualOverflow})
                        if(descriptionTruncated || titleTruncated)
                            DetailControl("Описание",{showDescription=true},Modifier.focusRequester(descriptionFocus).testTag("detail-description"),small=true)
                    }
                    val rating=metadata?.optString("rating")?.takeUnless {it=="null" || it.isBlank()}
                    Text(listOf(cardSubtitle(card),sources.joinToString(" · "),rating?.let {"★ $it"}.orEmpty())
                        .filter {it.isNotBlank()}.joinToString("  ·  "),color=DetailMuted,fontSize=13.sp,maxLines=1,overflow=TextOverflow.Ellipsis)
                    Text(description,fontSize=14.sp,lineHeight=18.sp,color=DetailMuted,maxLines=2,overflow=TextOverflow.Ellipsis,
                        onTextLayout={descriptionTruncated=it.hasVisualOverflow})
                }
            }
        }
        if(loading || error.isNotEmpty()) item(key="status") {
            Row(horizontalArrangement=Arrangement.spacedBy(12.dp),verticalAlignment=Alignment.CenterVertically) {
                Text(if(error.isNotEmpty()) error else "Обновляем список · сохранённые серии доступны",
                    color=DetailMuted,fontSize=12.sp,modifier=Modifier.weight(1f))
                if(error.isNotEmpty() && !loading) DetailControl("Повторить",onRetry,small=true)
            }
        }
        if(releases.isEmpty()) item {Text("Список готовится в фоне…",color=DetailMuted,fontSize=14.sp)}
        if(series) {
            item(key="season-tabs") {
                LazyRow(state=state.qualityScroll(cardId,"season-tabs"),horizontalArrangement=Arrangement.spacedBy(6.dp),contentPadding=PaddingValues(vertical=2.dp)) {
                    item {DetailControl("Все сезоны",{state.season=null},Modifier.testTag("detail-season-all"),selected=state.season==null)}
                    items(seasons,key={it}) {season ->
                        DetailControl("Сезон $season"+seasonYearLabel(card,season),{state.season=season},
                            Modifier.testTag("detail-season-$season"),selected=state.season==season)
                    }
                }
            }
            groups.groupBy {it.first.season}.forEach { (season,episodes) ->
                item(key="season-heading-${season ?: "unknown"}") {
                    Row(Modifier.fillMaxWidth().padding(top=4.dp,bottom=2.dp),verticalAlignment=Alignment.CenterVertically) {
                        Text(if(season!=null) "Сезон $season"+seasonYearLabel(card,season) else "Сборники / без номера",
                            color=DetailText,fontSize=14.sp)
                        Spacer(Modifier.weight(1f))
                        Text("Качество · размер",color=DetailMuted,fontSize=12.sp)
                    }
                }
                items(episodes,key={"episode-${it.first}"}) { (episode,rows) ->
                    Row(Modifier.fillMaxWidth().background(DetailPanel,RoundedCornerShape(9.dp)).padding(horizontal=10.dp,vertical=3.dp),
                        verticalAlignment=Alignment.CenterVertically,horizontalArrangement=Arrangement.spacedBy(10.dp)) {
                        Text(episode.rowLabel(),color=DetailText,fontSize=15.sp,modifier=Modifier.width(112.dp),maxLines=1,overflow=TextOverflow.Ellipsis)
                        LazyRow(Modifier.weight(1f),state=state.qualityScroll(cardId,episode.toString()),
                            horizontalArrangement=Arrangement.spacedBy(6.dp),contentPadding=PaddingValues(3.dp)) {
                            items(rows.sortedByDescending{qualityRank(it.optString("quality"))},key={it.getString("id")}) {release ->
                                val id=release.getString("id")
                                val quality=release.optString("quality").takeUnless{it=="null" || it.isBlank()}
                                    ?: if(release.optString("kind")=="direct") "Видео" else "WEB"
                                val source=if(sources.size!=1) providerName(release.optString("source")) else ""
                                val label=listOf(quality,detailSize(release),source).filter {it.isNotBlank()}.joinToString(" · ")
                                DetailControl(label,{state.selected=id;onRelease(release)},
                                    Modifier.focusRequester(requesters.getOrPut(id){FocusRequester()}).testTag("detail-release-$id"),selected=state.selected==id)
                            }
                        }
                    }
                }
            }
        } else {
            items(releases,key={it.getString("id")}) {release ->
                val id=release.getString("id")
                DetailSurface(onClick={state.selected=id;onRelease(release)},modifier=Modifier.fillMaxWidth()
                    .focusRequester(requesters.getOrPut(id){FocusRequester()}).testTag("detail-release-$id"),
                    selected=state.selected==id) {
                    Column(Modifier.padding(horizontal=12.dp,vertical=8.dp),verticalArrangement=Arrangement.spacedBy(3.dp)) {
                        Text(release.getString("title"),fontSize=16.sp,maxLines=1,overflow=TextOverflow.Ellipsis)
                        Text(listOf(providerName(release.optString("source")),detailSize(release),
                            if(release.isNull("seeders")) "" else "сиды ${release.optInt("seeders")}")
                            .filter {it.isNotBlank()}.joinToString(" · "),fontSize=13.sp,color=DetailMuted)
                    }
                }
            }
        }
    }
    if(showDescription) FullDescription(displayTitle(card),description) {showDescription=false}
    LaunchedEffect(showDescription) {
        if(!showDescription && descriptionWasOpen && (descriptionTruncated || titleTruncated)) {
            delay(50)
            runCatching {descriptionFocus.requestFocus()}
        }
        descriptionWasOpen=showDescription
    }
}

/** A separate reading surface keeps episode controls compact while making all text reachable on TV. */
@Composable
private fun FullDescription(title:String,description:String,onClose:()->Unit) {
    val scroll=rememberScrollState()
    val scope=rememberCoroutineScope()
    val textFocus=remember {FocusRequester()}
    val closeFocus=remember {FocusRequester()}
    val step=with(LocalDensity.current) {96.dp.toPx()}
    val height=(LocalConfiguration.current.screenHeightDp-48).coerceAtLeast(180).dp
    Dialog(onDismissRequest=onClose,properties=DialogProperties(usePlatformDefaultWidth=false)) {
        Column(Modifier.fillMaxWidth(0.9f).height(height).clip(RoundedCornerShape(12.dp))
            .background(DetailBackground).border(1.dp,Color(0xFF2B3A4D),RoundedCornerShape(12.dp)).padding(20.dp),
            verticalArrangement=Arrangement.spacedBy(12.dp)) {
            Row(horizontalArrangement=Arrangement.spacedBy(16.dp),verticalAlignment=Alignment.CenterVertically) {
                DetailControl("← Закрыть",onClose,Modifier.focusRequester(closeFocus))
                Text("↑ ↓ — читать · Назад — закрыть",fontSize=14.sp,color=Color(0xFFA8B5C7))
            }
            Column(Modifier.weight(1f).fillMaxWidth().focusRequester(textFocus).onPreviewKeyEvent {event ->
                when(event.key) {
                    Key.DirectionUp,Key.DirectionDown -> {
                        if(event.type==KeyEventType.KeyDown) {
                            if(event.key==Key.DirectionUp && scroll.value==0) closeFocus.requestFocus()
                            else scope.launch {scroll.scrollBy(if(event.key==Key.DirectionDown) step else -step)}
                        }
                        true
                    }
                    Key.DirectionLeft -> {
                        if(event.type==KeyEventType.KeyDown) closeFocus.requestFocus()
                        true
                    }
                    else -> false
                }
            }.focusable().verticalScroll(scroll),verticalArrangement=Arrangement.spacedBy(14.dp)) {
                Text(title,fontSize=27.sp,color=Color.White)
                Text(description,fontSize=18.sp,lineHeight=26.sp,color=Color(0xFFD1D9E6))
            }
        }
        LaunchedEffect(Unit) {delay(100);textFocus.requestFocus()}
    }
}

internal fun EpisodeRange.compactLabel():String = when {
    first!=null -> (season?.let{"$it сезон · "} ?: "")+"$first"+(if(last!=null && last!=first) "–$last" else "")+" серия"
    season!=null -> "$season сезон · сборник"
    else -> "Сборники / без номера"
}
internal fun qualityRank(value:String):Int=Regex("(2160|1080|720|480)").find(value)?.value?.toIntOrNull() ?: 0

internal fun seasonYearLabel(card:JSONObject,season:Int?):String {
    if(season==null) return ""
    val year=card.optJSONObject("metadata")?.optJSONObject("season_years")?.optional(season.toString()).orEmpty()
    return if(year.isBlank()) "" else " · $year"
}
