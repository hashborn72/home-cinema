package space.hashborn.cinema

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.focusable
import androidx.compose.foundation.gestures.scrollBy
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
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
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import androidx.tv.material3.*
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import org.json.JSONObject

private val FilesInk = Color(0xFF0D1520)
private val FilesPanel = Color(0xFF172435)
private val FilesMuted = Color(0xFF9EAFBF)
private val FilesCyan = Color(0xFF79DCEF)

/** Stable file IDs keep list position and D-pad focus while progress is refreshed. */
@Composable
internal fun ReleaseFilesScreen(
    release: JSONObject,
    card: JSONObject?,
    files: List<JSONObject>,
    preparing: Boolean,
    playerBusy: Boolean,
    error: String,
    playerStatus: String,
    onBack: () -> Unit,
    onRetry: () -> Unit,
    onPlay: (JSONObject, Boolean) -> Unit,
    onRefresh: () -> Unit
) {
    key(release.optString("id")) {
        ReleaseFilesContent(release, card, files, preparing, playerBusy, error, playerStatus,
            onBack, onRetry, onPlay, onRefresh)
    }
}

@Composable
private fun ReleaseFilesContent(
    release: JSONObject, card: JSONObject?, files: List<JSONObject>, preparing: Boolean,
    playerBusy: Boolean, error: String, playerStatus: String,
    onBack: () -> Unit, onRetry: () -> Unit, onPlay: (JSONObject, Boolean) -> Unit, onRefresh: () -> Unit
) {
    val listState = rememberLazyListState()
    val requesters = remember { mutableMapOf<String, FocusRequester>() }
    var selected by rememberSaveable { mutableStateOf("") }
    var initialFileFocus by rememberSaveable { mutableStateOf(false) }
    var info by remember { mutableStateOf<Pair<String, String>?>(null) }
    var dialogWasOpen by remember { mutableStateOf(false) }
    var wasBusy by remember { mutableStateOf(playerBusy) }
    val title = card?.let { displayTitle(it) } ?: "Выбор видео"
    val fileIds = files.map { it.getInt("id") }
    val season = episodeRange(release.optString("title")).season
        ?: release.optInt("season").takeIf { it > 0 }
    val context = listOfNotNull(
        season?.let { "Сезон $it" + (card?.let { item -> seasonYearLabel(item, season) } ?: "") },
        release.optional("quality").takeIf { it.isNotBlank() },
        release.optional("source").takeIf { it.isNotBlank() }?.let { providerName(it) }
    ).joinToString(" · ")
    fun focusModifier(name: String) = Modifier
        .focusRequester(requesters.getOrPut(name) { FocusRequester() })
        .onFocusChanged { if (it.isFocused) selected = name }
    fun restoreFocus() {
        val selectedId = selected.substringAfter(':', "").toIntOrNull()
        val selectedFile = files.find { it.optInt("id") == selectedId }
        val controlExists = selectedId == null || (selectedFile != null &&
            (!selected.startsWith("restart:") || releaseFileAction(selectedFile.optLong("position_ms"),
                selectedFile.optBoolean("completed")).resume))
        val target = (if (controlExists) requesters[selected] else null)
            ?: selectedId?.let { requesters["play:$it"] } ?: requesters["back"]
        if (runCatching { target?.requestFocus() }.isFailure) runCatching { requesters["back"]?.requestFocus() }
    }

    LaunchedEffect(Unit) {
        delay(80)
        restoreFocus()
    }
    // Only the initial file load chooses a starting row. Later updates never reset scrolling.
    LaunchedEffect(fileIds, preparing) {
        if (!preparing && files.isNotEmpty() && !initialFileFocus) {
            initialFileFocus = true
            if (selected.isEmpty() || selected == "back") {
                val index = files.indexOfFirst { it.optLong("position_ms") > 0 && !it.optBoolean("completed") }
                    .takeIf { it >= 0 } ?: files.indexOfFirst { !it.optBoolean("sample") }.coerceAtLeast(0)
                listState.scrollToItem(index)
                delay(80)
                runCatching { requesters["play:${files[index].getInt("id")}"]?.requestFocus() }
            }
        }
    }
    LaunchedEffect(playerBusy, playerStatus) {
        if (wasBusy && !playerBusy) {
            delay(80)
            restoreFocus()
        }
        wasBusy = playerBusy
    }
    LaunchedEffect(info) {
        if (info == null && dialogWasOpen) {
            delay(80)
            restoreFocus()
        }
        dialogWasOpen = info != null
    }

    Column(Modifier.fillMaxSize().background(FilesInk).padding(horizontal = 28.dp, vertical = 18.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(12.dp),
            verticalAlignment = Alignment.CenterVertically) {
            FilesControl("← Назад", onBack, focusModifier("back").testTag("release-files-back"))
            Text("ВЫБОР ВИДЕО", color = FilesMuted, fontSize = 11.sp, letterSpacing = 1.6.sp,
                modifier = Modifier.weight(1f))
            FilesControl("Раздача", { info = title to release.optString("title") }, focusModifier("release-info"))
            FilesControl("Обновить", onRefresh, focusModifier("refresh"), enabled = !preparing && !playerBusy)
        }
        Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text(title, color = Color.White, fontSize = 27.sp, fontWeight = FontWeight.SemiBold,
                maxLines = 2, overflow = TextOverflow.Ellipsis, lineHeight = 31.sp)
            Text(listOf(context, if (files.isEmpty()) "" else "Видео: ${files.size}")
                .filter { it.isNotBlank() }.joinToString(" · "), color = FilesMuted, fontSize = 14.sp,
                maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
        if (error.isNotBlank()) {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(12.dp),
                verticalAlignment = Alignment.CenterVertically) {
                Text(error, color = Color(0xFFF1C77B), fontSize = 14.sp, modifier = Modifier.weight(1f),
                    maxLines = 2, overflow = TextOverflow.Ellipsis)
                FilesControl("Повторить", onRetry, focusModifier("retry"), enabled = !preparing && !playerBusy)
            }
        }
        if (preparing || playerBusy) Text(if (preparing) "Подготавливаем видео…" else "Открываем видео…",
            color = FilesCyan, fontSize = 14.sp)
        if (files.isEmpty() && !preparing && error.isBlank())
            Text("В этой раздаче пока нет доступного видео.", color = FilesMuted, fontSize = 16.sp)

        LazyColumn(state = listState, modifier = Modifier.weight(1f).fillMaxWidth(),
            contentPadding = PaddingValues(4.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            itemsIndexed(files, key = { _, file -> file.getInt("id") }) { index, file ->
                val id = file.getInt("id")
                val path = file.optString("path")
                val position = file.optLong("position_ms").coerceAtLeast(0)
                val duration = file.optLong("duration_ms").coerceAtLeast(0)
                val completed = file.optBoolean("completed")
                val sample = file.optBoolean("sample")
                val action = releaseFileAction(position, completed)
                val episode = releaseFileEpisode(path)
                val label = when {
                    sample -> "Фрагмент · " + releaseFileName(path)
                    episode != null -> episode
                    else -> releaseFileName(path)
                }
                val quality = Regex("(?i)\\b(2160|1080|720|480)[pi]\\b").find(path)?.value
                val size = file.optLong("size").takeIf { it > 0 }?.let {
                    if (it >= 1073741824L) "%.1f ГБ".format(it / 1073741824.0)
                    else "${it / 1048576L} МБ"
                }
                val progressText = when {
                    completed -> "Просмотрено"
                    position > 0 -> formatPlaybackPosition(position) +
                        (if (duration > 0) " из ${formatPlaybackPosition(duration)}" else "")
                    duration > 0 -> formatPlaybackPosition(duration)
                    else -> ""
                }
                val subtitle = listOfNotNull(progressText.takeIf { it.isNotBlank() }, quality, size).joinToString(" · ")
                val fraction = releaseFileProgress(position, duration, completed)
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp),
                    verticalAlignment = Alignment.CenterVertically) {
                    Surface(onClick = { if (!playerBusy && !preparing) onPlay(file, action.resume) },
                        modifier = Modifier.weight(1f).heightIn(min = 60.dp).then(focusModifier("play:$id"))
                            .testTag("release-file-$id")
                            .alpha(if (preparing) .6f else 1f),
                        colors = ClickableSurfaceDefaults.colors(containerColor = FilesPanel,
                            focusedContainerColor = Color(0xFF263C50), contentColor = Color.White,
                            focusedContentColor = Color.White),
                        scale = ClickableSurfaceDefaults.scale(focusedScale = 1f),
                        border = ClickableSurfaceDefaults.border(focusedBorder = Border(BorderStroke(2.dp, FilesCyan)))) {
                        Column(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp),
                            verticalArrangement = Arrangement.spacedBy(6.dp)) {
                            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically,
                                horizontalArrangement = Arrangement.spacedBy(14.dp)) {
                                Text(if (completed) "✓" else "▶", color = if (completed) Color(0xFF8DDBB2) else FilesCyan,
                                    fontSize = 19.sp, modifier = Modifier.width(24.dp))
                                Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(3.dp)) {
                                    Text(label, fontSize = 17.sp, maxLines = 1, overflow = TextOverflow.Ellipsis)
                                    if (subtitle.isNotBlank()) Text(subtitle, color = FilesMuted, fontSize = 12.sp,
                                        maxLines = 1, overflow = TextOverflow.Ellipsis)
                                }
                                Text(action.text, color = FilesCyan, fontSize = 14.sp, maxLines = 1)
                            }
                            if (fraction != null) Box(Modifier.fillMaxWidth().height(2.dp)
                                .clip(RoundedCornerShape(2.dp)).background(Color(0xFF2D3D4B))) {
                                Box(Modifier.fillMaxHeight().fillMaxWidth(fraction)
                                    .background(if (completed) Color(0xFF8DDBB2) else FilesCyan))
                            }
                        }
                    }
                    if (action.resume) FilesControl("↺", { onPlay(file, false) },
                        focusModifier("restart:$id").testTag("release-file-restart-$id")
                            .semantics { contentDescription = "$label — с начала" },
                        enabled = !preparing && !playerBusy, square = true)
                    else Spacer(Modifier.width(44.dp))
                    FilesControl("⋯", { info = label to path },
                        focusModifier("info:$id").testTag("release-file-info-$id")
                            .semantics { contentDescription = "$label — имя файла" }, square = true)
                }
            }
        }
        val playerNotice = when {
            playerStatus.contains("не установлен", ignoreCase = true) -> "Видеоплеер не установлен."
            playerStatus.startsWith("Запуск не удался") -> playerStatus
            playerStatus.startsWith("Нет связи") -> "Нет связи с сервером. Сохранённые позиции остаются доступны."
            else -> ""
        }
        if (playerNotice.isNotBlank() && error.isBlank()) Text(playerNotice, color = FilesMuted, fontSize = 12.sp,
            maxLines = 2, overflow = TextOverflow.Ellipsis)
    }
    info?.let { (label, body) -> FileInformationDialog(label, body) { info = null } }
}

@Composable
private fun FilesControl(text: String, onClick: () -> Unit, modifier: Modifier = Modifier,
                         enabled: Boolean = true, square: Boolean = false) {
    Surface(onClick = { if (enabled) onClick() },
        modifier = modifier.heightIn(min = 44.dp).then(if (square) Modifier.width(44.dp) else Modifier.widthIn(min = 44.dp))
            .alpha(if (enabled) 1f else .45f),
        colors = ClickableSurfaceDefaults.colors(containerColor = FilesPanel, focusedContainerColor = Color(0xFF315167),
            contentColor = Color(0xFFD5E2EC), focusedContentColor = Color.White),
        scale = ClickableSurfaceDefaults.scale(focusedScale = 1f),
        border = ClickableSurfaceDefaults.border(focusedBorder = Border(BorderStroke(2.dp, FilesCyan)))) {
        Box(Modifier.then(if (square) Modifier.fillMaxWidth() else Modifier)
            .padding(horizontal = if (square) 0.dp else 14.dp, vertical = 11.dp), contentAlignment = Alignment.Center) {
            Text(text, fontSize = if (square) 20.sp else 14.sp, maxLines = 1)
        }
    }
}

@Composable
private fun FileInformationDialog(title: String, body: String, onClose: () -> Unit) {
    val scroll = rememberScrollState()
    val scope = rememberCoroutineScope()
    val readerFocus = remember { FocusRequester() }
    val closeFocus = remember { FocusRequester() }
    val step = with(LocalDensity.current) { 80.dp.toPx() }
    val maxHeight = (LocalConfiguration.current.screenHeightDp - 72).coerceAtLeast(180).dp
    Dialog(onDismissRequest = onClose, properties = DialogProperties(usePlatformDefaultWidth = false)) {
        Column(Modifier.fillMaxWidth(.82f).heightIn(max = maxHeight).clip(RoundedCornerShape(16.dp))
            .background(FilesInk).padding(24.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(14.dp)) {
                Text(title, color = Color.White, fontSize = 22.sp, modifier = Modifier.weight(1f),
                    maxLines = 2, overflow = TextOverflow.Ellipsis)
                FilesControl("Закрыть", onClose, Modifier.focusRequester(closeFocus))
            }
            Text(body, color = Color(0xFFD0DCE7), fontSize = 17.sp, lineHeight = 25.sp,
                modifier = Modifier.weight(1f, fill = false).fillMaxWidth().focusRequester(readerFocus)
                    .onPreviewKeyEvent { event ->
                        when (event.key) {
                            Key.DirectionUp, Key.DirectionDown -> {
                                if (event.type == KeyEventType.KeyDown) {
                                    if (event.key == Key.DirectionUp && scroll.value == 0) closeFocus.requestFocus()
                                    else scope.launch { scroll.scrollBy(if (event.key == Key.DirectionDown) step else -step) }
                                }
                                true
                            }
                            else -> false
                        }
                    }.focusable().verticalScroll(scroll))
        }
        LaunchedEffect(Unit) { delay(80); readerFocus.requestFocus() }
    }
}
