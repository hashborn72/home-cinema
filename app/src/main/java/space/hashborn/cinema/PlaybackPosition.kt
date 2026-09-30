package space.hashborn.cinema

fun formatPlaybackPosition(milliseconds:Long):String {
    val seconds=milliseconds.coerceAtLeast(0)/1000
    return if(seconds>=3600) "%d:%02d:%02d".format(seconds/3600,seconds/60%60,seconds%60)
    else "%02d:%02d".format(seconds/60,seconds%60)
}
