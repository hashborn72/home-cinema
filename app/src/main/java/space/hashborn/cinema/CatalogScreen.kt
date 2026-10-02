package space.hashborn.cinema

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.tv.material3.*
import kotlinx.coroutines.*
import org.json.JSONArray
import org.json.JSONObject
import java.net.URLEncoder
import coil.compose.AsyncImage
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.foundation.BorderStroke
import androidx.compose.ui.Alignment
import androidx.compose.ui.graphics.Brush
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.alpha

private val Ink = Color(0xFF101722)
private val Muted = Color(0xFFA8B5C7)
private fun JSONArray.objects() = (0 until length()).map { getJSONObject(it) }
internal fun JSONObject.optional(name: String) = if (isNull(name)) "" else optString(name)
internal fun displayTitle(card:JSONObject) = card.optJSONObject("metadata")?.optional("title")?.takeIf {it.isNotBlank()} ?: card.optString("provider_title").takeIf{it.isNotBlank()} ?: card.getString("title")
internal fun cardSubtitle(card: JSONObject): String {
    val kind = if (card.optString("media_type") == "tv") "Сериал / ТВ" else "Фильм"
    return listOf(kind,card.optional("year").ifEmpty {card.optJSONObject("metadata")?.optional("year") ?: ""}).filter { it.isNotEmpty() }.joinToString(" · ")
}

@Composable
fun CatalogScreen(request: suspend (String) -> JSONObject,
    post: suspend (String,JSONObject) -> JSONObject,
    onPlay: suspend (String,Int,Boolean) -> Unit,
    playerStatus: String, playerBusy: Boolean, onProbe: () -> Unit) {
    val scope = rememberCoroutineScope()
    var section by remember { mutableStateOf("Главная") }
    var query by remember { mutableStateOf("") }
    var data by remember { mutableStateOf<Pair<String,JSONObject>?>(null) }
    var detail by remember { mutableStateOf<JSONObject?>(null) }
    var error by remember { mutableStateOf("") }
    var opening by remember { mutableStateOf(false) }
    var release by remember { mutableStateOf<JSONObject?>(null) }
    var files by remember { mutableStateOf<JSONObject?>(null) }
    var preparing by remember { mutableStateOf(false) }
    var fileError by remember { mutableStateOf("") }
    var prepareAttempt by remember { mutableIntStateOf(0) }
    var autoResumeFile by remember {mutableStateOf<Int?>(null)}
    var searchingAnwap by remember {mutableStateOf(false)}
    val detailState=remember {DetailState()}
    var flagBusy by remember {mutableStateOf(false)}
    val providerState = remember { ProviderState() }
    var seriesLoading by remember {mutableStateOf(false)}
    var seriesError by remember {mutableStateOf("")}
    var seriesAttempt by remember {mutableIntStateOf(0)}
    var selectedId by remember { mutableStateOf("") }
    var selectedRow by remember { mutableStateOf("") }
    var restoreFocus by remember { mutableStateOf(false) }
    val columnState = rememberLazyListState()
    // Keep actual row scroll positions while the details page is open.
    val rowStates = remember {mutableMapOf<String,androidx.compose.foundation.lazy.LazyListState>()}
    val focusRequesters = remember { mutableMapOf<String,FocusRequester>() }
    val menuFocusRequesters = remember { mutableMapOf<String,FocusRequester>() }

    suspend fun refresh() {
        val requestedSection = section
        val requestedQuery = query
        try {
            val path = when(requestedSection) {
                "Главная" -> "/api/v1/catalog/home"
                "Фильмы" -> "/api/v1/catalog/search?kind=movie"
                "Сериалы" -> "/api/v1/catalog/search?kind=tv"
                "Моё" -> "/api/v1/library"
                else -> "/api/v1/catalog/search?q="+URLEncoder.encode(requestedQuery,"UTF-8")
            }
            val response = request(path)
            if (section == requestedSection && (section != "Поиск" || query == requestedQuery)) {
                data = requestedSection to response; error = ""
            }
        } catch (cancelled: CancellationException) { throw cancelled }
        catch (_: Exception) { if (section == requestedSection) error = "Нет связи с каталогом. Уже загруженные карточки сохранены на экране." }
    }
    LaunchedEffect(section) {
        data = null
        if (section !in listOf("Поиск","Источники","Настройки")) refresh()
        if (section !in listOf("Поиск","Источники","Настройки")) while (isActive) { delay(5000); refresh() }
    }
    LaunchedEffect(section,query) {
        if (section == "Поиск") {
            delay(400); refresh()
            while(isActive) {delay(3000);refresh()}
        }
    }
    LaunchedEffect(detail?.optString("id"),seriesAttempt) {
        val original=detail ?: return@LaunchedEffect
        seriesError=""
        if(original.optString("media_type")!="tv") return@LaunchedEffect
        val cid=original.getString("id")
        seriesLoading=true
        try {
            val path="/api/v1/catalog/items/$cid/expand"
            var state=withTimeout(30000) {
                while(true) {
                    try {return@withTimeout post(path,JSONObject())}
                    catch(e:CancellationException) {throw e}
                    catch(e:Exception) {delay(3000)}
                }
                @Suppress("UNREACHABLE_CODE") JSONObject()
            }
            withTimeout(210000) {
                while(state.optString("status")=="loading" || state.optBoolean("refreshing")) {delay(1500);state=request(path)}
            }
            check(state.optString("status")=="ready") {"Источник не ответил. Доступные раздачи оставлены ниже."}
            val expanded=request("/api/v1/catalog/items/$cid")
            if(detail?.optString("id")==cid) {
                original.optJSONObject("resume_target")?.let {expanded.put("resume_target",it)}
                detail=expanded
                if(state.optBoolean("truncated")) seriesError="Источник ограничил число раздач; список может быть неполным."
            }
        } catch(e:TimeoutCancellationException) {seriesError="Источник отвечает долго; сохранённые серии доступны."}
        catch(e:CancellationException) {throw e}
        catch(e:Exception) {seriesError="Обновление временно недоступно; можно повторить."}
        finally {seriesLoading=false}
    }
    LaunchedEffect(detail,restoreFocus) {
        if (detail == null && restoreFocus) {
            delay(100)
            val payload=data?.second
            val candidates=if(section=="Главная" || section=="Моё") payload?.optJSONArray("shelves")?.objects()?.find {it.optString("id")==selectedRow}?.optJSONArray("results") else payload?.optJSONArray("results")
            val stillVisible=candidates?.objects()?.any {it.optString("id")==selectedId}==true
            val target=if(stillVisible) focusRequesters[selectedRow+":"+selectedId] else null
            val restored=section=="Источники" || (target!=null && runCatching {target.requestFocus()}.isSuccess)
            if(!restored) {
                columnState.scrollToItem(0)
                delay(100)
                runCatching {menuFocusRequesters[section]?.requestFocus()}
            }
            restoreFocus = false
        }
    }
    fun back() { detail = null; scope.launch {if(section=="Моё") refresh(); restoreFocus = true} }
    LaunchedEffect(release,prepareAttempt) {
        val current = release ?: return@LaunchedEffect
        val rid = current.getString("id")
        preparing = true; files = null; fileError = ""
        try {
            post("/api/v1/releases/$rid/prepare",JSONObject())
            withTimeout(120000) {
                while (true) {
                    val result = request("/api/v1/releases/$rid/files")
                    files = result
                    when(result.getString("status")) {
                        "ready" -> break
                        "error" -> throw IllegalStateException("Не удалось получить файлы. Раздача или источник недоступны.")
                    }
                    delay(2000)
                }
            }
        } catch (_: TimeoutCancellationException) { fileError = "Подготовка заняла слишком долго. Можно повторить." }
        catch (cancelled: CancellationException) { throw cancelled }
        catch (e: Exception) { fileError = e.message ?: "Ошибка подготовки" }
        finally { preparing = false }
    }
    LaunchedEffect(playerBusy,playerStatus) {
        val current = release
        if (!playerBusy && !preparing && current != null && files?.optString("status")=="ready") {
            try { files=request("/api/v1/releases/"+current.getString("id")+"/files") }
            catch(cancelled: CancellationException) {throw cancelled}
            catch(_:Exception) { /* Keep the last displayed positions while offline. */ }
        }
    }
    LaunchedEffect(files,preparing) {
        val fid=autoResumeFile
        val current=release
        if(!preparing && current!=null && files?.optString("status")=="ready" && fid!=null) {
            autoResumeFile=null
            try {onPlay(current.getString("id"),fid,true)}
            catch(e:Exception) {fileError=e.message ?: "Не удалось продолжить"}
        }
    }
    BackHandler(enabled = detail != null || section != "Главная") {
        if (release != null) {release = null;autoResumeFile=null} else if (detail != null) back() else section = "Главная"
    }
    fun open(card: JSONObject, row: String) {
        if (opening) return
        selectedId = card.getString("id"); selectedRow = row
        detailState.season=null;detailState.selected=""
        scope.launch {
            opening = true
            try { detail = request("/api/v1/catalog/items/"+card.getString("id")).apply {card.optJSONObject("resume_target")?.let {put("resume_target",it)}}; detailState.scroll.scrollToItem(0); error = "" }
            catch (_: Exception) { error = "Не удалось открыть карточку. Повтори попытку." }
            finally { opening = false }
        }
    }
    fun flag(name:String) {
        if(flagBusy) return
        val current=detail ?: return
        flagBusy=true
        scope.launch {
            try {
                val value=!(current.optJSONObject("library")?.optBoolean(name) ?: false)
                val flags=post("/api/v1/library/"+current.getString("id"),JSONObject().put(name,value))
                if(detail?.optString("id")==current.getString("id")) detail=JSONObject(detail.toString()).put("library",flags)
                error=""
            } catch (cancelled:CancellationException) {throw cancelled}
            catch (_:Exception) {error="Не удалось сохранить отметку. Повтори попытку."}
            finally {flagBusy=false}
        }
    }
    // LazyColumn content may be evaluated between section and request-state updates.
    // Capture a consistent pair; never interpret a home payload as search results.
    val visibleSection = section
    val visibleData = data?.takeIf { it.first == visibleSection }?.second
    MaterialTheme(colorScheme=darkColorScheme()) {
        if (release != null) {
            val current = release!!
            ReleaseFilesScreen(current,detail,files?.optJSONArray("files")?.objects() ?: emptyList(),
                preparing,playerBusy,fileError,playerStatus,
                onBack={release=null;autoResumeFile=null},
                onRetry={prepareAttempt++},
                onPlay={file,resume -> scope.launch {
                    try {onPlay(current.getString("id"),file.getInt("id"),resume);fileError=""}
                    catch(cancelled:CancellationException) {throw cancelled}
                    catch(e:Exception) {fileError=e.message ?: "Ошибка запуска"}
                }},
                onRefresh={scope.launch {
                    try {files=request("/api/v1/releases/"+current.getString("id")+"/files");fileError=""}
                    catch(cancelled:CancellationException) {throw cancelled}
                    catch(_:Exception) {fileError="Нет связи с сервером"}
                }})
        } else if (detail != null) {
            val item=detail!!
            DetailScreen(item,detailState,seriesLoading,seriesError.ifEmpty{error},flagBusy,
                onBack={back()},onFavorite={flag("favorite")},
                onRelease={autoResumeFile=null;release=it},
                onResume={
                    item.optJSONObject("resume_target")?.let {target ->
                        item.getJSONArray("releases").objects().find {it.getString("id")==target.getString("release_id")}?.let {
                            autoResumeFile=target.getInt("file_id");release=it
                        }
                    }
                },onRetry={seriesAttempt++})
        } else if(section=="Настройки") {
            CinemaSettingsScreen(onBack={section="Главная"},onProbe=onProbe)
        } else if(section=="Источники") {
            ProviderScreen(providerState,request,post,onBack={section="Главная"},onOpen={open(it,"Источники")})
        } else {
            LazyColumn(Modifier.fillMaxSize().background(Ink).padding(horizontal=28.dp,vertical=12.dp),state=columnState,verticalArrangement=Arrangement.spacedBy(6.dp)) {
                item {
                    Row(horizontalArrangement=Arrangement.spacedBy(14.dp)) {
                        listOf("Главная","Фильмы","Сериалы","Поиск","Моё","Источники","Настройки").forEach { name ->
                            CompactButton(if(section==name) "• $name" else name,{section=name},Modifier.focusRequester(menuFocusRequesters.getOrPut(name){FocusRequester()}))
                        }
                    }
                }
                if (section=="Поиск") item {
                    Row(horizontalArrangement=Arrangement.spacedBy(16.dp)) {
                        BasicTextField(value=query,onValueChange={query=it.take(200)},singleLine=true,
                            textStyle=TextStyle(color=Color.White,fontSize=20.sp),
                            modifier=Modifier.width(500.dp).background(Color(0xFF263244)).padding(16.dp),
                            decorationBox={ inner -> if(query.isEmpty()) Text("Название в локальном каталоге…",color=Muted); inner() })
                        Button(onClick={scope.launch { refresh() }}) { Text("Найти") }
                        Button(enabled=!searchingAnwap && query.trim().length>=2,onClick={scope.launch {
                            val term=query.trim().take(120)
                            searchingAnwap=true
                            try {
                                post("/api/v1/catalog/anwap-search",JSONObject().put("q",term))
                                withTimeout(150000) {
                                    while(true) {
                                        val result=request("/api/v1/catalog/anwap-search?q="+URLEncoder.encode(term,"UTF-8"))
                                        if(result.optString("status")=="ready") break
                                        if(result.optString("status")=="error") throw IllegalStateException("Anwap временно недоступен")
                                        delay(2000)
                                    }
                                }
                                refresh()
                            } catch(e:Exception) {error="Поиск Anwap не завершён. Локальный каталог доступен."}
                            finally {searchingAnwap=false}
                        }}) {Text(if(searchingAnwap) "Ищем…" else "В Anwap")}
                    }
                }
                if(error.isNotEmpty()) item { Text(error,color=Color(0xFFFBBF24)); Button(onClick={scope.launch {refresh()}}) {Text("Повторить")} }
                if(opening) item { Text("Открываем карточку…",color=Muted) }
                if(visibleData==null) item { Text("Загружаем каталог…",color=Muted) }
                else if(visibleSection=="Главная" || visibleSection=="Моё") {
                    val shelves=visibleData.optJSONArray("shelves")?.objects() ?: emptyList()
                    shelves.forEachIndexed { index,shelf ->
                        val row=shelf.getString("id")
                        item(key=row+"-heading") {
                            Row(horizontalArrangement=Arrangement.spacedBy(16.dp),verticalAlignment=androidx.compose.ui.Alignment.CenterVertically) {
                                Text(shelf.getString("title"),fontSize=20.sp,color=Color.White)
                                if(visibleSection=="Главная") CompactButton("Все →",{providerState.select(row);section="Источники"})
                            }
                            val description = (shelf.opt("description") as? String)?.takeIf { it.isNotBlank() }
                            val note = when {
                                visibleSection=="Моё" -> "Сохранено на вашем сервере"
                                shelf.optBoolean("stale") -> "Сохранённые данные · источник временно недоступен или обновляется"
                                shelf.optBoolean("warming") -> "Загружаем источник…"
                                !shelf.isNull("error") -> "Источник временно недоступен. Остальные витрины работают."
                                description != null -> description
                                row=="rutor" -> "По сидам среди последних 100 раздач · только видео"
                                row=="anwap" -> "Прямое видео · доступное разрешение указано при выборе"
                                else -> "Последние доступные раздачи · одинаковые качества сгруппированы"
                            }
                            if(row=="rutor" || shelf.optBoolean("stale") || shelf.optBoolean("warming")) Text(note,color=Muted,fontSize=12.sp)
                        }
                        val cards=shelf.getJSONArray("results").objects()
                        item(key=row+"-cards") {
                            if(cards.isEmpty()) Text("Пока нет карточек",color=Muted)
                            LazyRow(state=rowStates.getOrPut(row){androidx.compose.foundation.lazy.LazyListState()},horizontalArrangement=Arrangement.spacedBy(12.dp),contentPadding=PaddingValues(vertical=6.dp)) {
                                items(cards,key={it.getString("id")}) { card ->
                                    val key=row+":"+card.getString("id")
                                    CinemaCard(card,focusRequesters.getOrPut(key){FocusRequester()},landscape=false) {open(card,row)}
                                }
                            }
                        }
                    }
                } else {
                    val cards=visibleData.optJSONArray("results")?.objects() ?: emptyList()
                    item { Text("Найдено: "+visibleData.optInt("total")+" · каталог уже полученных раздач",color=Muted) }
                    items(cards.chunked(5)) { chunk ->
                        Row(horizontalArrangement=Arrangement.spacedBy(12.dp)) {
                            chunk.forEach { card ->
                                val key=section+":"+card.getString("id")
                                CinemaCard(card,focusRequesters.getOrPut(key){FocusRequester()}) {open(card,section)}
                            }
                        }
                    }
                }
            }
        }
    }
}

@Composable
internal fun CinemaCard(card:JSONObject,requester:FocusRequester,landscape:Boolean=false,onClick:()->Unit) {
    val width=((LocalConfiguration.current.screenWidthDp-56-48)/5f).dp
    Surface(onClick=onClick,modifier=Modifier.width(width).focusRequester(requester),
        colors=ClickableSurfaceDefaults.colors(containerColor=Color.Transparent,focusedContainerColor=Color.Transparent,
            contentColor=Color.White,focusedContentColor=Color.White),
        scale=ClickableSurfaceDefaults.scale(focusedScale=1f),
        border=ClickableSurfaceDefaults.border(focusedBorder=Border(BorderStroke(2.dp,Color(0xFF78DCF4))))) {
        Column {
            val meta=card.optJSONObject("metadata")
            val poster=if(landscape) meta?.optional("episode_still")?.takeIf{it.isNotEmpty()} ?: meta?.optional("backdrop") else meta?.optional("poster")
            CinemaPoster(poster.orEmpty(),Modifier.fillMaxWidth().aspectRatio(if(landscape) 16f/9f else 2f/3f).clip(RoundedCornerShape(6.dp)),displayTitle(card),landscape)
            // Keep both caption lines and the metadata inside the rounded focus border.
            Column(Modifier.fillMaxWidth().padding(start=6.dp,end=6.dp,top=7.dp,bottom=10.dp),
                verticalArrangement=Arrangement.spacedBy(4.dp)) {
                Text(displayTitle(card),fontSize=17.sp,minLines=2,maxLines=2,overflow=TextOverflow.Ellipsis,lineHeight=20.sp)
                val year=card.optional("year").ifEmpty {card.optJSONObject("metadata")?.optional("year") ?: ""}
                val sources=card.optJSONArray("sources")?.let {a -> (0 until a.length()).joinToString(" · "){providerName(a.getString(it))}}.orEmpty()
                Text(listOf(year,sources).filter{it.isNotBlank()}.joinToString(" · "),fontSize=12.sp,lineHeight=17.sp,
                    color=Muted,maxLines=1,overflow=TextOverflow.Ellipsis)
            }
        }
    }
}

@Composable
internal fun CinemaPoster(url:String,modifier:Modifier,title:String="",landscape:Boolean=false) {
    var state by remember(url) {mutableIntStateOf(0)}
    Box(modifier.background(Brush.linearGradient(listOf(Color(0xFF263D51),Color(0xFF121E2C))))) {
        if(state!=1) Column(Modifier.fillMaxSize().padding(12.dp),verticalArrangement=Arrangement.SpaceBetween) {
            Text("HOME CINEMA",color=Muted,fontSize=9.sp,letterSpacing=1.sp)
            Text(title,color=Color(0xFFEEF5FB),fontSize=19.sp,lineHeight=23.sp,maxLines=4,overflow=TextOverflow.Ellipsis)
            Text(if(state==2) "Изображение недоступно" else "Постер уточняется",color=Muted,fontSize=11.sp)
        }
        if(url.isNotBlank()) AsyncImage(model=url,contentDescription=null,
            modifier=Modifier.fillMaxSize().alpha(if(state==1) 1f else 0f),contentScale=ContentScale.Fit,
            onSuccess={result ->
                val image=result.result.drawable
                state=if(artworkFits(image.intrinsicWidth,image.intrinsicHeight,landscape)) 1 else 2
            },onError={state=2})
    }
}
