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
import android.content.Intent
import android.net.Uri
import androidx.compose.ui.platform.LocalContext

private val Ink = Color(0xFF101722)
private val Muted = Color(0xFFA8B5C7)
private fun JSONArray.objects() = (0 until length()).map { getJSONObject(it) }
private fun JSONObject.optional(name: String) = if (isNull(name)) "" else optString(name)
private fun cardSubtitle(card: JSONObject): String {
    val kind = if (card.optString("media_type") == "tv") "Сериал / ТВ" else "Фильм"
    return listOf(kind,card.optional("year")).filter { it.isNotEmpty() }.joinToString(" · ")
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
    var confirmWatched by remember {mutableStateOf(false)}
    var flagBusy by remember {mutableStateOf(false)}
    val context = LocalContext.current
    var selectedId by remember { mutableStateOf("") }
    var selectedRow by remember { mutableStateOf("") }
    var restoreFocus by remember { mutableStateOf(false) }
    val columnState = rememberLazyListState()
    // Keep actual row scroll positions while the details page is open.
    val rowStates = remember {mutableMapOf<String,androidx.compose.foundation.lazy.LazyListState>()}
    val focusRequesters = remember { mutableMapOf<String,FocusRequester>() }

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
        if (section != "Поиск") refresh()
        if (section == "Главная") while (isActive) { delay(10000); refresh() }
    }
    LaunchedEffect(section,query) {
        if (section == "Поиск") { delay(400); refresh() }
    }
    LaunchedEffect(detail,restoreFocus) {
        if (detail == null && restoreFocus) {
            delay(100)
            runCatching { focusRequesters[selectedRow+":"+selectedId]?.requestFocus() }
            restoreFocus = false
        }
    }
    fun back() { detail = null; restoreFocus = true; if(section=="Моё") scope.launch {refresh()} }
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
        if(confirmWatched) confirmWatched=false else if (release != null) {release = null;autoResumeFile=null} else if (detail != null) back() else section = "Главная"
    }
    fun open(card: JSONObject, row: String) {
        if (opening) return
        selectedId = card.getString("id"); selectedRow = row
        scope.launch {
            opening = true
            try { detail = request("/api/v1/catalog/items/"+card.getString("id")).apply {card.optJSONObject("resume_target")?.let {put("resume_target",it)}}; confirmWatched=false; error = "" }
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
            LazyColumn(Modifier.fillMaxSize().background(Ink).padding(40.dp),verticalArrangement=Arrangement.spacedBy(18.dp)) {
                item { Button(onClick={release=null}) {Text("← К раздачам")} }
                item { Text(current.getString("title"),fontSize=24.sp,color=Color.White) }
                item { Text("Выбери файл. Позиция сохраняется отдельно для каждого файла и версии раздачи.",color=Muted) }
                if(preparing) item { Text(if(current.optString("kind")=="direct") "Получаем варианты прямого видео…" else "Получаем список файлов через TorrServer…",color=Color(0xFF5EEAD4)) }
                if(fileError.isNotEmpty()) item {
                    Text(fileError,color=Color(0xFFFBBF24))
                    Button(onClick={prepareAttempt++},enabled=!preparing) {Text("Повторить")}
                }
                items(files?.optJSONArray("files")?.objects() ?: emptyList(),key={it.getInt("id")}) { file ->
                    Column(verticalArrangement=Arrangement.spacedBy(8.dp)) {
                        Text(file.getString("path"),color=Color.White,fontSize=18.sp)
                        Text((if(file.optBoolean("sample")) "Образец · " else "") +
                            (if(file.optBoolean("completed")) "Плеер сообщил о завершении" else if(!file.isNull("position_ms")) "Сохранено: ${file.optLong("position_ms")/60000} мин" else "Позиция ещё не сохранена"),color=Muted)
                        Row(horizontalArrangement=Arrangement.spacedBy(18.dp)) {
                            listOf(false to "С начала",true to "Продолжить").forEach { (resume,label) ->
                                Button(enabled=!playerBusy && !preparing,onClick={scope.launch {
                                    try { onPlay(current.getString("id"),file.getInt("id"),resume); fileError="" }
                                    catch(e:Exception) {fileError=e.message ?: "Ошибка запуска"}
                                }}) { Text(label) }
                            }
                        }
                    }
                }
                item { Text(playerStatus,color=Muted,fontSize=13.sp) }
                item { Button(onClick={scope.launch {
                    try {files=request("/api/v1/releases/"+current.getString("id")+"/files")}
                    catch(_:Exception) {fileError="Нет связи с сервером"}
                }},enabled=!preparing) {Text("Обновить позиции")} }
            }
        } else if (detail != null) {
            val item = detail!!
            LazyColumn(Modifier.fillMaxSize().background(Ink).padding(40.dp), verticalArrangement=Arrangement.spacedBy(16.dp)) {
                item { Button(onClick={back()}) { Text("← К каталогу") } }
                item { Text(item.getString("title"),fontSize=32.sp,color=Color.White) }
                item { Text(cardSubtitle(item)+" · "+item.optInt("release_count")+" раздач",color=Muted) }
                item {
                    Row(horizontalArrangement=Arrangement.spacedBy(12.dp)) {
                        listOf("favorite" to "Избранное","watch_later" to "Позже","watched" to "Просмотрено").forEach { (key,label) ->
                            Button(enabled=!flagBusy,onClick={if(key=="watched" && item.optString("media_type")=="tv" && item.optJSONObject("library")?.optBoolean(key)!=true) confirmWatched=true else flag(key)}) {Text((if(item.optJSONObject("library")?.optBoolean(key)==true) "✓ " else "+ ")+label)}
                        }
                    }
                    Text("Отметки можно снять повторным нажатием. «Просмотрено» относится ко всей карточке.",color=Muted,fontSize=12.sp)
                    if(error.isNotEmpty()) Text(error,color=Color(0xFFFBBF24))
                    if(confirmWatched) Row(horizontalArrangement=Arrangement.spacedBy(12.dp)) {
                        Text("Пометить сериал целиком?",color=Muted)
                        Button(onClick={flag("watched");confirmWatched=false}) {Text("Да, целиком")}
                        Button(onClick={confirmWatched=false}) {Text("Отмена")}
                    }
                    item.optJSONObject("resume_target")?.let { target ->
                        Text(target.optString("file_title"),color=Muted,fontSize=13.sp)
                        Button(onClick={
                            val found=item.getJSONArray("releases").objects().find {it.getString("id")==target.getString("release_id")}
                            if(found!=null) {autoResumeFile=target.getInt("file_id");release=found}
                        },enabled=!playerBusy) {Text("Продолжить этот файл")}
                    }
                }
                val metadata=item.optJSONObject("metadata")
                item {
                    Row(horizontalArrangement=Arrangement.spacedBy(24.dp)) {
                        if(metadata?.optional("poster")?.isNotEmpty()==true) AsyncImage(model=metadata.getString("poster"),contentDescription=null,modifier=Modifier.width(145.dp).height(215.dp),contentScale=ContentScale.Crop)
                        Column(Modifier.weight(1f),verticalArrangement=Arrangement.spacedBy(12.dp)) {
                            Text(metadata?.optional("description")?.ifEmpty {null} ?: "Нет уверенного совпадения с базой описаний. Доступные раздачи можно открыть ниже.",color=Muted,fontSize=17.sp)
                            if(metadata!=null) {
                                Text(metadata.optString("provider")+" · рейтинг: "+metadata.optional("rating").ifEmpty{"—"}+" · "+metadata.optString("language")+" · "+metadata.optString("license"),color=Muted,fontSize=12.sp)
                                Button(onClick={runCatching {context.startActivity(Intent(Intent.ACTION_VIEW,Uri.parse(metadata.getString("url"))))}}) {Text("Источник: "+metadata.optString("provider"))}
                            }
                        }
                    }
                }
                item { Text("Доступные раздачи",fontSize=23.sp,color=Color.White) }
                items(item.getJSONArray("releases").objects(),key={it.getString("id")}) { rowRelease ->
                    val size = if(rowRelease.isNull("size")) "Размер неизвестен" else "%.1f ГБ".format(rowRelease.optDouble("size")/1073741824.0)
                    Surface(onClick={autoResumeFile=null;release=rowRelease}, modifier=Modifier.fillMaxWidth()) {
                        Column(Modifier.padding(18.dp),verticalArrangement=Arrangement.spacedBy(6.dp)) {
                            Text(rowRelease.getString("title"),fontSize=18.sp,maxLines=3,overflow=TextOverflow.Ellipsis)
                            Text(if(rowRelease.optString("kind")=="direct") "Anwap · прямое видео · выбрать качество" else rowRelease.getString("source")+" · "+size+" · сиды: "+rowRelease.optional("seeders").ifEmpty{"неизвестно"},fontSize=14.sp)
                        }
                    }
                }
                item { Text("Выбери раздачу → видеофайл → Just Player",color=Muted,fontSize=14.sp) }
            }
        } else {
            LazyColumn(Modifier.fillMaxSize().background(Ink).padding(horizontal=40.dp,vertical=24.dp),state=columnState,verticalArrangement=Arrangement.spacedBy(20.dp)) {
                item { Text("ДОМАШНЯЯ МЕДИАТЕКА",color=Color(0xFF5EEAD4),fontSize=18.sp) }
                item {
                    Row(horizontalArrangement=Arrangement.spacedBy(14.dp)) {
                        listOf("Главная","Фильмы","Сериалы","Поиск","Моё").forEach { name ->
                            Button(onClick={section=name}) { Text(if(section==name) "• $name" else name) }
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
                            Text(shelf.getString("title"),fontSize=25.sp,color=Color.White)
                            val note = when {
                                visibleSection=="Моё" -> "Сохранено на вашем сервере"
                                shelf.optBoolean("stale") -> "Сохранённые данные · источник временно недоступен или обновляется"
                                shelf.optBoolean("warming") -> "Загружаем источник…"
                                !shelf.isNull("error") -> "Источник временно недоступен. Остальные витрины работают."
                                row=="rutor" -> "По сидам среди последних 100 раздач · только видео"
                                row=="anwap" -> "Прямое видео · доступное разрешение указано при выборе"
                                else -> "Последние доступные раздачи · одинаковые качества сгруппированы"
                            }
                            Text(note,color=Muted,fontSize=13.sp)
                        }
                        val cards=shelf.getJSONArray("results").objects()
                        item(key=row+"-cards") {
                            if(cards.isEmpty()) Text("Пока нет карточек",color=Muted)
                            LazyRow(state=rowStates.getOrPut(row){androidx.compose.foundation.lazy.LazyListState()},horizontalArrangement=Arrangement.spacedBy(16.dp),contentPadding=PaddingValues(8.dp)) {
                                items(cards,key={it.getString("id")}) { card ->
                                    val key=row+":"+card.getString("id")
                                    CinemaCard(card,focusRequesters.getOrPut(key){FocusRequester()}) {open(card,row)}
                                }
                            }
                        }
                    }
                } else {
                    val cards=visibleData.optJSONArray("results")?.objects() ?: emptyList()
                    item { Text("Найдено: "+visibleData.optInt("total")+" · каталог уже полученных раздач",color=Muted) }
                    items(cards.chunked(4)) { chunk ->
                        Row(horizontalArrangement=Arrangement.spacedBy(12.dp)) {
                            chunk.forEach { card ->
                                val key=section+":"+card.getString("id")
                                CinemaCard(card,focusRequesters.getOrPut(key){FocusRequester()}) {open(card,section)}
                            }
                        }
                    }
                }
                item { Text("0.4 · Личный каталог · Без TMDB",color=Muted,fontSize=12.sp); Button(onClick=onProbe) {Text("Проверка плеера")} }
            }
        }
    }
}

@Composable
private fun CinemaCard(card:JSONObject,requester:FocusRequester,onClick:()->Unit) {
    Surface(onClick=onClick,modifier=Modifier.width(202.dp).height(310.dp).focusRequester(requester)) {
        Column(Modifier.padding(16.dp),verticalArrangement=Arrangement.spacedBy(10.dp)) {
            val poster=card.optJSONObject("metadata")?.optional("poster")
            if(!poster.isNullOrEmpty()) AsyncImage(model=poster,contentDescription=null,modifier=Modifier.fillMaxWidth().height(145.dp),contentScale=ContentScale.Crop)
            else Box(Modifier.fillMaxWidth().height(145.dp).background(Color(0xFF263244))) {Text("Без постера",modifier=Modifier.padding(16.dp),color=Muted,fontSize=14.sp)}
            Text(cardSubtitle(card),fontSize=13.sp)
            Text(card.getString("title"),fontSize=20.sp,maxLines=3,overflow=TextOverflow.Ellipsis,modifier=Modifier.weight(1f))
            Text("Раздач: "+card.optInt("release_count")+" · сиды: "+card.optional("seeders").ifEmpty{"?"},fontSize=12.sp)
            card.optJSONObject("resume_target")?.let { target -> Text("Позиция: ${target.optLong("position_ms")/60000} мин",fontSize=12.sp) }
        }
    }
}
