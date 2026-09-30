package space.hashborn.cinema

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListState
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.tv.material3.*
import kotlinx.coroutines.*
import org.json.JSONObject
import java.net.URLEncoder

internal fun providerName(source:String)=mapOf("lostfilm" to "LostFilm","rutor" to "RuTor","exkinoray" to "ExKinoRay","anwap" to "Anwap")[source] ?: source
internal class ProviderState {
    var source by mutableStateOf("lostfilm")
    var input by mutableStateOf("")
    var query by mutableStateOf("")
    var offset by mutableIntStateOf(0)
    var attempt by mutableIntStateOf(0)
    var data by mutableStateOf<JSONObject?>(null)
    var loadedKey=""
    var selected=""
    val scroll=LazyListState()
    val focus=mutableMapOf<String,FocusRequester>()
    val pages=mutableListOf<Int>()
    fun select(value:String) {if(source!=value) {source=value;input="";query="";offset=0;data=null;loadedKey="";selected="";pages.clear()}}
}

@Composable
internal fun ProviderScreen(state:ProviderState,request:suspend(String)->JSONObject,post:suspend(String,JSONObject)->JSONObject,
                            onBack:()->Unit,onOpen:(JSONObject)->Unit) {
    var busy by remember {mutableStateOf(false)}
    var error by remember {mutableStateOf("")}
    val scope=rememberCoroutineScope()
    LaunchedEffect(state.source,state.query,state.offset,state.attempt) {
        val key="${state.source}:${state.query}:${state.offset}:${state.attempt}"
        if(state.loadedKey==key) {
            delay(150);runCatching{state.focus[state.selected]?.requestFocus()}
            return@LaunchedEffect
        }
        busy=true;error="";state.data=null;state.selected="";state.scroll.scrollToItem(0)
        try {
            val path="/api/v1/providers/"+state.source
            var result=post(path,JSONObject().put("q",state.query).put("offset",state.offset))
            withTimeout(210000) {
                while(result.optString("status")=="loading") {
                    delay(1500)
                    result=request(path+"?q="+URLEncoder.encode(state.query,"UTF-8")+"&offset="+state.offset)
                }
            }
            check(result.optString("status")=="ready") {"Источник временно недоступен"}
            state.data=result;state.loadedKey=key
        } catch(e:TimeoutCancellationException) {error="Источник отвечает долго. Нажми «Повторить»."}
        catch(e:CancellationException) {throw e}
        catch(e:Exception) {error="Не удалось загрузить источник. Повтори запрос через несколько секунд."}
        finally {busy=false}
    }
    LazyColumn(Modifier.fillMaxSize().background(Color(0xFF101722)).padding(40.dp),state=state.scroll,verticalArrangement=Arrangement.spacedBy(18.dp)) {
        item {Button(onClick=onBack) {Text("← На главную")}}
        item {Text("Источники · "+providerName(state.source),color=Color.White,fontSize=28.sp)}
        item {Row(horizontalArrangement=Arrangement.spacedBy(12.dp)) {
            listOf("lostfilm","rutor","exkinoray","anwap").forEach {source -> Button(onClick={state.select(source)}) {Text((if(state.source==source) "✓ " else "")+providerName(source))}}
        }}
        item {
            Text("Поиск у поставщика, включая названия вне главной. Вводи русское или оригинальное название.",color=Color(0xFFA8B5C7))
            Row(horizontalArrangement=Arrangement.spacedBy(16.dp)) {
                BasicTextField(state.input,{state.input=it.take(120)},singleLine=true,textStyle=TextStyle(color=Color.White,fontSize=20.sp),
                    modifier=Modifier.width(430.dp).background(Color(0xFF29374B)).padding(14.dp),
                    decorationBox={inner -> if(state.input.isEmpty()) Text("Название…",color=Color.Gray);inner()})
                Button(onClick={state.query=state.input.trim();state.offset=0;state.pages.clear();state.attempt++},enabled=!busy) {Text("Найти")}
                Button(onClick={state.input="";state.query="";state.offset=0;state.pages.clear();state.attempt++},enabled=!busy) {Text("Каталог")}
            }
        }
        if(busy) item {Text("Загружаем поставщика… Запрос может занять несколько минут.",color=Color(0xFF5EEAD4))}
        if(error.isNotEmpty()) item {Text(error,color=Color(0xFFFBBF24));Button(onClick={state.attempt++}) {Text("Повторить")}}
        val payload=state.data
        if(payload!=null) {
            val array=payload.optJSONArray("results")
            val cards=if(array==null) emptyList() else (0 until array.length()).map {array.getJSONObject(it)}
            item {Text("На этой странице: ${cards.size}. "+(if(state.source=="lostfilm") "Каталог сериалов LostFilm." else "Видео, доступное через поставщика; это не полный архив сайта."),color=Color(0xFFA8B5C7))}
            if(payload.optString("notice").isNotBlank()) item {Text(payload.optString("notice"),color=Color(0xFFFBBF24))}
            if(cards.isEmpty()) item {Text("Ничего не найдено. Попробуй другое название.",color=Color.White)}
            items(cards.chunked(4)) {chunk -> Row(horizontalArrangement=Arrangement.spacedBy(12.dp)) {
                chunk.forEach {card -> val id=card.getString("id");CinemaCard(card,state.focus.getOrPut(id){FocusRequester()}) {state.selected=id;onOpen(card)}}
            }}
            item {Row(horizontalArrangement=Arrangement.spacedBy(16.dp)) {
                if(state.pages.isNotEmpty()) Button(onClick={state.offset=state.pages.removeAt(state.pages.lastIndex)}) {Text("← Предыдущая страница")}
                if(payload.optBoolean("has_more")) Button(onClick={state.pages.add(state.offset);state.offset=payload.getInt("next_offset")}) {Text("Следующая страница →")}
            }}
        }
    }
}
