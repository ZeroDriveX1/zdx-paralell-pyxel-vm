package com.zerodrivex.zdxnode

import java.nio.ByteBuffer
import java.util.zip.CRC32

/** Small, explicitly negotiated subset of the desktop VM; no host capabilities. */
object PyxelEngine {
    const val MAX_PIXELS = 1_048_576
    const val MAX_THREADS = 32
    const val MAX_STEPS = 4096
    const val MAX_ARTIFACT = 8 * 1024 * 1024
    const val FEATURE = "pyxel-static-rgb-v1"
    private val opcodes = setOf(0, 10, 11, 12, 20, 21, 22, 23, 24, 30, 31, 40, 41, 50, 70, 255)

    data class Geometry(val width: Int, val height: Int)
    data class State(val registers: List<IntArray>, val shared: IntArray, val clock: Int, val steps: Int)

    /** Validate the complete container before any native raster decoder sees bytes.
     * Only canonical, non-interlaced RGB8 PNGs with IHDR/IDAT/IEND are accepted.
     * In particular, APNG, profiles, text, EXIF, palettes and unknown chunks fail closed.
     */
    fun inspectPng(data: ByteArray, checkpoint: () -> Unit = {}): Geometry {
        require(data.size in 45..MAX_ARTIFACT) { "PNG artifact size is outside limits" }
        require(data.copyOfRange(0, 8).contentEquals(byteArrayOf(-119, 80, 78, 71, 13, 10, 26, 10))) { "not PNG" }
        var offset = 8
        var geometry: Geometry? = null
        var dataSeen = false
        var ended = false
        var chunks = 0
        while (offset < data.size) {
            checkpoint()
            require(++chunks <= 4096 && data.size - offset >= 12) { "invalid PNG chunk count/length" }
            val length = ByteBuffer.wrap(data, offset, 4).int
            require(length >= 0 && length <= data.size - offset - 12) { "invalid PNG chunk length" }
            val type = String(data, offset + 4, 4, Charsets.US_ASCII)
            val crc = CRC32().apply { update(data, offset + 4, length + 4) }.value
            val expected = ByteBuffer.wrap(data, offset + 8 + length, 4).int.toLong() and 0xffffffffL
            require(crc == expected) { "PNG chunk CRC mismatch" }
            when (type) {
                "IHDR" -> {
                    require(offset == 8 && length == 13 && geometry == null) { "invalid IHDR" }
                    val width = ByteBuffer.wrap(data, offset + 8, 4).int
                    val height = ByteBuffer.wrap(data, offset + 12, 4).int
                    require(width in 1..4096 && height in 1..4096 && width.toLong() * height <= MAX_PIXELS) { "PNG dimensions exceed adapter limits" }
                    require(data.sliceArray(offset + 16..offset + 20).contentEquals(byteArrayOf(8, 2, 0, 0, 0))) { "requires non-interlaced RGB8 PNG" }
                    geometry = Geometry(width, height)
                }
                "IDAT" -> { require(geometry != null && !ended) { "misordered IDAT" }; dataSeen = true }
                "IEND" -> {
                    require(geometry != null && dataSeen && length == 0 && offset + 12 == data.size) { "invalid IEND" }
                    ended = true
                }
                else -> throw IllegalArgumentException("PNG chunk $type is not supported by ${FEATURE}")
            }
            offset += length + 12
        }
        require(ended) { "PNG has no IEND" }
        return requireNotNull(geometry)
    }

    fun execute(pixels: IntArray, width: Int, height: Int, threads: Int, checkpoint: () -> Unit = {}): State {
        require(width > 0 && height > 0 && width.toLong() * height <= MAX_PIXELS && pixels.size == width * height)
        require(threads in 1..MAX_THREADS && threads <= height) { "unsupported thread geometry" }
        // Reject unsupported instructions even in unreachable columns. Never silently
        // treat desktop SAVE_STATE/frame chaining or a custom opcode as a no-op.
        for (y in 0 until threads) for (x in 0 until width) {
            if (x % 256 == 0) checkpoint()
            val p = pixels[y * width + x]
            val r = (p ushr 16) and 255
            require(r in opcodes) { "unsupported opcode $r at $x,$y" }
            if (r == 40 || r == 41) require(((p ushr 8) and 255) < 8) { "invalid shared memory address" }
        }
        val registers = List(threads) { IntArray(4) } // A, B, OUT, T
        val shared = IntArray(8)
        val halted = BooleanArray(threads)
        val skip = BooleanArray(threads)
        var x = 0
        var clock = 0
        var steps = 0
        while (x < width) {
            checkpoint()
            check(steps++ < MAX_STEPS) { "maximum column steps exceeded" }
            clock = x
            var jump: Int? = null
            for (y in 0 until threads) {
                checkpoint()
                if (halted[y]) continue
                if (skip[y]) { skip[y] = false; continue }
                val reg = registers[y]
                reg[3] = x
                val p = pixels[y * width + x]
                val r = (p ushr 16) and 255
                val g = (p ushr 8) and 255
                val b = p and 255
                when (r) {
                    0 -> Unit
                    10 -> reg[0] = b
                    11 -> reg[1] = b
                    12 -> reg[0] = reg[0].inv() and 255
                    20 -> reg[2] = (reg[0] + reg[1]) and 255
                    21 -> reg[0] = (reg[0] - reg[1]) and 255
                    22 -> reg[0] = (reg[0] * reg[1]) and 255
                    23 -> reg[0] = if (reg[1] == 0) 0 else reg[0] / reg[1]
                    24 -> reg[0] = reg[2]
                    30 -> if (reg[0] != b) skip[y] = true
                    31 -> reg[2] = if (reg[0] == reg[1]) 1 else 0
                    40 -> reg[0] = shared[g]
                    41 -> shared[g] = reg[0]
                    50 -> reg[0] = if ((p and 0xffffff) == (pixels[y * width + width - 1 - x] and 0xffffff)) 1 else 0
                    70 -> if (jump == null && g < width) jump = g
                    255 -> halted[y] = true
                }
            }
            x = jump ?: (x + 1)
        }
        return State(registers, shared, clock, steps)
    }
}
