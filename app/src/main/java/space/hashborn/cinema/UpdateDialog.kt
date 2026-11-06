package space.hashborn.cinema

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import androidx.tv.material3.Text

@Composable
internal fun UpdateDialog(update: AppUpdate, onUpdate: () -> Unit, onLater: () -> Unit) {
    val focus = remember { FocusRequester() }
    Dialog(onDismissRequest = onLater) {
        Column(Modifier.fillMaxWidth().heightIn(max = 420.dp).background(Color(0xFF101722)).padding(24.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp)) {
            Text("Обновление Home Cinema", fontSize = 22.sp, color = Color.White)
            Text("${BuildConfig.VERSION_NAME} → ${update.versionName} · ${"%.1f".format(update.size / 1048576.0)} МБ",
                color = Color(0xFFA8B5C7), fontSize = 16.sp)
            Text(update.changes, color = Color.White, fontSize = 15.sp,
                modifier = Modifier.weight(1f, fill = false).verticalScroll(rememberScrollState()))
            Text("После загрузки Android попросит подтвердить установку. История и настройки сохранятся.",
                color = Color(0xFFA8B5C7), fontSize = 13.sp)
            Row(horizontalArrangement = Arrangement.spacedBy(16.dp)) {
                CompactButton("Обновить", onUpdate, Modifier.focusRequester(focus))
                CompactButton("Позже", onLater)
            }
        }
        LaunchedEffect(Unit) { kotlinx.coroutines.delay(80); focus.requestFocus() }
    }
}


@Composable
internal fun UpdateNotice(message: String, onClose: () -> Unit) {
    val focus = remember { FocusRequester() }
    Dialog(onDismissRequest = onClose) {
        Column(Modifier.fillMaxWidth().background(Color(0xFF101722)).padding(24.dp),
            verticalArrangement = Arrangement.spacedBy(18.dp)) {
            Text("Обновление Home Cinema", color = Color.White, fontSize = 22.sp)
            Text(message, color = Color.White, fontSize = 16.sp)
            CompactButton("Закрыть", onClose, Modifier.focusRequester(focus))
        }
        LaunchedEffect(Unit) { kotlinx.coroutines.delay(80); focus.requestFocus() }
    }
}
