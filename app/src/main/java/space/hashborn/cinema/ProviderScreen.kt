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
    LaunchedEffect(state.source,state.query,state.offset,state.attempt) {
        val key="${state.source}:${state.query}:${state.offset}:${state.attempt}"
        val restored=state.loadedKey==key
        if(!restored) {state.data=null;state.selected="";state.scroll.scrollToItem(0)}
        else {delay(120);runCatching {state.focus[state.selected]?.requestFocus()}}
        busy=!restored;error=""
        val path="/api/v1/providers/"+state.source
        val getPath=path+"?q="+URLEncoder.encode(state.query,"UTF-8")+"&offset="+state.offset
        try {
            var result=withTimeout(30000) {
                while(true) {
                    try {return@withTimeout post(path,JSONObject().put("q",state.query).put("offset",state.offset))}
                    catch(e:CancellationException) {throw e}
                    catch(e:Exception) {delay(3000)}
                }
                @Suppress("UNREACHABLE_CODE") JSONObject()
            }
            withTimeout(210000) {
                while(result.optString("status")=="loading") {delay(1500);result=request(getPath)}
            }
            check(result.optString("status")=="ready")
            state.data=result;state.loadedKey=key;busy=false
            // Refresh cached metadata/posters without resetting focus or jumping back to the top.
            while(isActive) {delay(15000);val fresh=request(getPath);if(fresh.optString("status")=="ready") state.data=fresh}
        } catch(e:TimeoutCancellationException) {error="Источник отвечает долго. Сохранённые карточки остаются доступны."}
        catch(e:CancellationException) {throw e}
        catch(e:Exception) {error="Источник временно недоступен"}
        finally {busy=false}
    }
    LazyColumn(Modifier.fillMaxSize().background(Color(0xFF101722)).padding(horizontal=28.dp,vertical=16.dp),
        state=state.scroll,verticalArrangement=Arrangement.spacedBy(10.dp)) {
        item {
            Row(horizontalArrangement=Arrangement.spacedBy(10.dp)) {
                CompactButton("←",onBack)
                listOf("lostfilm","rutor","exkinoray","anwap").forEach {source ->
                    CompactButton((if(state.source==source) "✓ " else "")+providerName(source),{state.select(source)})
                }
            }
        }
        item {
            Row(horizontalArrangement=Arrangement.spacedBy(12.dp)) {
                BasicTextField(state.input,{state.input=it.take(120)},singleLine=true,textStyle=TextStyle(color=Color.White,fontSize=18.sp),
                    modifier=Modifier.weight(1f).height(36.dp).background(Color(0xFF29374B)).padding(horizontal=12.dp,vertical=7.dp),
                    decorationBox={inner -> if(state.input.isEmpty()) Text("Русское или оригинальное название…",color=Color.Gray,fontSize=16.sp);inner()})
                CompactButton("Найти",{state.query=state.input.trim();state.offset=0;state.pages.clear();state.attempt++},enabled=!busy)
                CompactButton("Все",{state.input="";state.query="";state.offset=0;state.pages.clear();state.attempt++},enabled=!busy)
            }
        }
        if(busy) item {Text("Готовим каталог в фоне…",color=Color(0xFF5EEAD4),fontSize=13.sp)}
        if(error.isNotEmpty()) item {Row(horizontalArrangement=Arrangement.spacedBy(12.dp)) {
            Text(error,color=Color(0xFFFBBF24),fontSize=13.sp);CompactButton("Повторить",{state.attempt++})
        }}
        val payload=state.data
        if(payload!=null) {
            val array=payload.optJSONArray("results")
            val cards=if(array==null) emptyList() else (0 until array.length()).map {array.getJSONObject(it)}
            if(payload.optString("notice").isNotBlank()) item {Text(payload.optString("notice"),color=Color(0xFFFBBF24),fontSize=13.sp)}
            if(cards.isEmpty()) item {Text("Ничего не найдено. Попробуй другое название.",color=Color.White)}
            items(cards.chunked(5),key={it.first().getString("id")}) {chunk ->
                Row(horizontalArrangement=Arrangement.spacedBy(12.dp),modifier=Modifier.padding(vertical=3.dp)) {
                    chunk.forEach {card -> val id=card.getString("id");CinemaCard(card,state.focus.getOrPut(id){FocusRequester()}) {state.selected=id;onOpen(card)}}
                }
            }
            item {Row(horizontalArrangement=Arrangement.spacedBy(16.dp)) {
                if(state.pages.isNotEmpty()) CompactButton("← Предыдущая",{state.offset=state.pages.removeAt(state.pages.lastIndex)})
                if(payload.optBoolean("has_more")) CompactButton("Следующая →",{state.pages.add(state.offset);state.offset=payload.getInt("next_offset")})
            }}
        }
    }
}
