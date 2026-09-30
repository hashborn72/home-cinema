package space.hashborn.cinema

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.tv.material3.*
import kotlinx.coroutines.launch

@Composable
fun ConnectionScreen(currentBackend: String, canCancel: Boolean, onCancel: () -> Unit,
                     onConnect: suspend (String) -> Unit) {
    var backend by remember { mutableStateOf(currentBackend) }
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf("") }
    val scope = rememberCoroutineScope()
    val connectFocus = remember { FocusRequester() }
    LaunchedEffect(Unit) { connectFocus.requestFocus() }
    MaterialTheme {
        Column(Modifier.fillMaxSize().background(Color(0xFF101722)).padding(40.dp),
               verticalArrangement = Arrangement.spacedBy(14.dp)) {
            Text("Подключение телевизора", color=Color.White, fontSize=28.sp)
            Text("Домашняя медиатека · сервер должен быть доступен из сети телевизора",
                 color=Color(0xFFA8B5C7), fontSize=14.sp)
            Text("Сервер", color=Color.White)
            BasicTextField(backend, { if (!busy) backend=it },
                modifier=Modifier.fillMaxWidth().background(Color(0xFF29374B)).padding(12.dp),
                textStyle=TextStyle(color=Color.White,fontSize=19.sp),singleLine=true,
                keyboardOptions=KeyboardOptions(keyboardType=KeyboardType.Uri))
            Row(horizontalArrangement=Arrangement.spacedBy(16.dp)) {
                Button(modifier=Modifier.focusRequester(connectFocus),enabled=!busy && backend.isNotBlank(),onClick={
                    busy=true;error=""
                    scope.launch {
                        try { onConnect(backend) }
                        catch(e:Exception) { error=e.message ?: "Подключение не удалось" }
                        finally { busy=false }
                    }
                }) { Text(if(busy) "Подключаем…" else "Подключить") }
                if(canCancel) Button(enabled=!busy,onClick=onCancel) { Text("Назад") }
            }
            if(error.isNotEmpty()) Text(error,color=Color(0xFFFCA5A5),fontSize=16.sp)
            Text("Доверенная локальная сеть — без кода и ключа. Just Player устанавливается отдельно.\n"+
                 "Подключение по HTTP предназначено только для доверенной домашней сети.",
                 color=Color(0xFFA8B5C7),fontSize=13.sp)
        }
    }
}
