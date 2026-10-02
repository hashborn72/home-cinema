package space.hashborn.cinema

import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.tv.material3.Text

@Composable
internal fun CinemaSettingsScreen(onBack:()->Unit,onProbe:()->Unit) {
    val backFocus=remember {FocusRequester()}
    var showCredits by remember {mutableStateOf(false)}
    LaunchedEffect(Unit) {backFocus.requestFocus()}
    LazyColumn(Modifier.fillMaxSize().background(Color(0xFF101722)).padding(28.dp),
        verticalArrangement=Arrangement.spacedBy(16.dp)) {
        item {CompactButton("← Назад",onBack,Modifier.focusRequester(backFocus))}
        item {Text("Настройки",fontSize=26.sp,color=Color.White)}
        item {CompactButton("Выбор плеера и проверка подключения",onProbe)}
        item {CompactButton(if(showCredits) "Источники данных ↑" else "Источники данных ↓",{showCredits=!showCredits})}
        if(showCredits) item {
            Column(verticalArrangement=Arrangement.spacedBy(8.dp)) {
                Text("Home Cinema · ${BuildConfig.VERSION_NAME}",color=Color(0xFFA8B5C7),fontSize=14.sp)
                Image(painterResource(R.drawable.tmdb_logo),contentDescription="TMDB",modifier=Modifier.width(137.dp).height(32.dp))
                Text("This product uses the TMDB API but is not endorsed or certified by TMDB.",color=Color(0xFFA8B5C7),fontSize=13.sp)
                Text("TMDB — описания и изображения. Anwap и TVmaze указаны в карточках. Воспроизведение — Just Player или VLC.",color=Color(0xFFA8B5C7),fontSize=13.sp)
            }
        }
    }
}
