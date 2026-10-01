package space.hashborn.cinema

/** Also protects an updated client connected to an older backend's untyped images. */
internal fun artworkFits(width:Int,height:Int,landscape:Boolean=false):Boolean {
    if(width<=0 || height<=0) return false
    val ratio=width.toFloat()/height
    return if(landscape) ratio in 1.4f..2.1f else ratio in 0.55f..0.8f
}
