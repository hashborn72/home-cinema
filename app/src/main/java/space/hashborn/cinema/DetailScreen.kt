package space.hashborn.cinema

import androidx.compose.foundation.background
import androidx.compose.foundation.focusable
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.gestures.scrollBy
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.key.*
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalDensity
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
}

@Composable
internal fun DetailScreen(card:JSONObject,state:DetailState,loading:Boolean,error:String,flagBusy:Boolean,
                          onBack:()->Unit,onFavorite:()->Unit,onRelease:(JSONObject)->Unit,onResume:()->Unit,onRetry:()->Unit) {
    val releases=card.getJSONArray("releases").let {a -> (0 until a.length()).map {a.getJSONObject(it)}}
    val series=card.optString("media_type")=="tv"
    val seasons=releases.mapNotNull{episodeRange(it.getString("title")).season}.distinct().sorted()
    val metadata=card.optJSONObject("metadata")
    val description=metadata?.optString("description")?.takeUnless{it=="null" || it.isBlank()}
        ?: "Описание пока не найдено. Видео доступно ниже."
    var showDescription by remember(card.optString("id")) {mutableStateOf(false)}
    var descriptionTruncated by remember(description) {mutableStateOf(false)}
    var titleTruncated by remember(displayTitle(card)) {mutableStateOf(false)}
    val descriptionFocus=remember {FocusRequester()}
    val requesters=remember {mutableMapOf<String,FocusRequester>()}
    val backFocus=remember {FocusRequester()}
    LaunchedEffect(Unit) {delay(120);runCatching {(requesters[state.selected] ?: backFocus).requestFocus()}}
    LazyColumn(Modifier.fillMaxSize().background(Color(0xFF101722)).padding(horizontal=28.dp,vertical=18.dp),
        state=state.scroll,verticalArrangement=Arrangement.spacedBy(10.dp)) {
        item(key="header") {
            LazyRow(horizontalArrangement=Arrangement.spacedBy(10.dp),verticalAlignment=Alignment.CenterVertically,contentPadding=PaddingValues(3.dp)) {
                item {CompactButton("←",onBack,Modifier.focusRequester(backFocus).semantics {contentDescription="Назад"})}
                if(series) {
                    item {CompactButton(if(state.season==null) "✓ Все сезоны" else "Все сезоны",{state.season=null})}
                    items(seasons) {season -> CompactButton((if(state.season==season) "✓ " else "")+"Сезон $season"+seasonYearLabel(card,season),{state.season=season})}
                }
                item {CompactButton(if(card.optJSONObject("library")?.optBoolean("favorite")==true) "♥" else "♡",onFavorite,
                    Modifier.semantics {contentDescription="Нравится"},enabled=!flagBusy)}
                if(card.has("resume_target")) item {CompactButton("Продолжить",onResume)}
            }
        }
        item(key="description") {
            Row(horizontalArrangement=Arrangement.spacedBy(18.dp)) {
                if(metadata?.optString("poster")?.startsWith("http")==true)
                    CinemaPoster(metadata.getString("poster"),Modifier.width(84.dp).height(122.dp))
                Column(Modifier.weight(1f),verticalArrangement=Arrangement.spacedBy(6.dp)) {
                    Text(displayTitle(card),fontSize=27.sp,color=Color.White,maxLines=1,overflow=TextOverflow.Ellipsis,
                        onTextLayout={titleTruncated=it.hasVisualOverflow})
                    val rating=metadata?.optString("rating")?.takeUnless {it=="null" || it.isBlank()}
                    Text(cardSubtitle(card)+(rating?.let {" · ★ $it"} ?: ""),color=Color(0xFFA8B5C7),fontSize=14.sp)
                    Text(description,fontSize=16.sp,color=Color(0xFFA8B5C7),maxLines=3,overflow=TextOverflow.Ellipsis,
                        onTextLayout={descriptionTruncated=it.hasVisualOverflow})
                    if(descriptionTruncated || titleTruncated) CompactButton("Описание целиком",{showDescription=true},Modifier.focusRequester(descriptionFocus))
                }
            }
        }
        if(loading || error.isNotEmpty()) item(key="status") {
            Row(horizontalArrangement=Arrangement.spacedBy(12.dp)) {
                Text(if(error.isNotEmpty()) error else "Обновляется в фоне · сохранённые серии уже доступны",color=Color(0xFFA8B5C7),fontSize=13.sp)
                if(error.isNotEmpty() && !loading) CompactButton("Повторить",onRetry)
            }
        }
        if(releases.isEmpty()) item {Text("Список готовится в фоне…",color=Color(0xFFA8B5C7))}
        if(series) {
            val groups=releases.filter {state.season==null || episodeRange(it.getString("title")).season==state.season}
                .groupBy {episodeRange(it.getString("title"))}.toList()
                .sortedWith(compareBy({it.first.season ?: Int.MAX_VALUE},{it.first.first ?: Int.MAX_VALUE},{it.first.last ?: Int.MAX_VALUE}))
            items(groups,key={it.first.toString()}) { (episode,rows) ->
                Row(verticalAlignment=Alignment.CenterVertically,horizontalArrangement=Arrangement.spacedBy(12.dp),modifier=Modifier.heightIn(min=42.dp)) {
                    Text(episode.compactLabel()+seasonYearLabel(card,episode.season),color=Color.White,fontSize=16.sp,modifier=Modifier.width(156.dp),maxLines=2)
                    LazyRow(horizontalArrangement=Arrangement.spacedBy(10.dp),contentPadding=PaddingValues(4.dp)) {
                        items(rows.sortedByDescending{qualityRank(it.optString("quality"))},key={it.getString("id")}) {release ->
                            val id=release.getString("id")
                            val size=if(release.isNull("size")) "" else " · %.1f ГБ".format(release.optDouble("size")/1073741824.0)
                            val quality=release.optString("quality").takeUnless{it=="null" || it.isBlank()} ?: if(release.optString("kind")=="direct") "Видео" else "WEB"
                            val source=if(card.optJSONArray("sources")?.length()!=1 || release.optString("source")!="lostfilm") " · "+providerName(release.optString("source")) else ""
                            CompactButton(quality+size+source,{state.selected=id;onRelease(release)},Modifier.focusRequester(requesters.getOrPut(id){FocusRequester()}))
                        }
                    }
                }
            }
        } else {
            items(releases,key={it.getString("id")}) {release ->
                val id=release.getString("id")
                Surface(onClick={state.selected=id;onRelease(release)},modifier=Modifier.fillMaxWidth().focusRequester(requesters.getOrPut(id){FocusRequester()}),
                    scale=ClickableSurfaceDefaults.scale(focusedScale=1.01f)) {
                    Column(Modifier.padding(horizontal=12.dp,vertical=8.dp),verticalArrangement=Arrangement.spacedBy(3.dp)) {
                        Text(release.getString("title"),fontSize=16.sp,maxLines=1,overflow=TextOverflow.Ellipsis)
                        val size=if(release.isNull("size")) "" else " · %.1f ГБ".format(release.optDouble("size")/1073741824.0)
                        Text(providerName(release.optString("source"))+size+(if(release.isNull("seeders")) "" else " · сиды ${release.optInt("seeders")}"),fontSize=13.sp)
                    }
                }
            }
        }
    }
    if(showDescription) FullDescription(displayTitle(card),description) {showDescription=false}
    LaunchedEffect(showDescription) {
        if(!showDescription && (descriptionTruncated || titleTruncated)) {
            delay(50)
            runCatching {descriptionFocus.requestFocus()}
        }
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
        Column(Modifier.fillMaxWidth(0.9f).height(height).background(Color(0xFF101722)).padding(20.dp),
            verticalArrangement=Arrangement.spacedBy(12.dp)) {
            Row(horizontalArrangement=Arrangement.spacedBy(16.dp),verticalAlignment=Alignment.CenterVertically) {
                CompactButton("← Закрыть",onClose,Modifier.focusRequester(closeFocus))
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
