package com.zerodrivex.zdxnode

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import java.io.ByteArrayOutputStream
import java.io.DataOutputStream
import java.util.zip.CRC32

class PyxelEngineTest {
    private fun chunk(type: String, data: ByteArray): ByteArray {
        val body = type.toByteArray(Charsets.US_ASCII) + data
        return ByteArrayOutputStream().also { bytes ->
            DataOutputStream(bytes).use { out ->
                out.writeInt(data.size); out.write(body)
                out.writeInt(CRC32().apply { update(body) }.value.toInt())
            }
        }.toByteArray()
    }
    private fun png(width: Int = 1, height: Int = 1, extra: ByteArray = byteArrayOf()): ByteArray {
        val header = ByteArrayOutputStream().also { bytes -> DataOutputStream(bytes).use {
            it.writeInt(width); it.writeInt(height); it.write(byteArrayOf(8, 2, 0, 0, 0))
        } }.toByteArray()
        return byteArrayOf(-119, 80, 78, 71, 13, 10, 26, 10) + chunk("IHDR", header) + extra +
            chunk("IDAT", byteArrayOf(1)) + chunk("IEND", byteArrayOf())
    }

    @Test fun desktopGoldenVectorsMatchEveryRegisterAndSharedSlot() {
        val vectors = JSONArray(javaClass.getResource("/pyxel_vectors.json")!!.readText())
        for (i in 0 until vectors.length()) {
            val vector = vectors.getJSONObject(i)
            val raw = vector.getJSONArray("pixels")
            val state = PyxelEngine.execute(IntArray(raw.length()) { raw.getInt(it) }, vector.getInt("width"), vector.getInt("height"), vector.getInt("threads"))
            state.registers.forEachIndexed { index, values ->
                val expected = vector.getJSONObject("registers").getJSONObject("T$index")
                listOf("A", "B", "OUT", "T").forEachIndexed { slot, key -> assertEquals("vector $i T$index $key", expected.getInt(key), values[slot]) }
            }
            state.shared.forEachIndexed { slot, value -> assertEquals(vector.getJSONObject("shared").getInt("M$slot"), value) }
            assertEquals(vector.getInt("clock"), state.clock)
        }
    }

    @Test fun sharedWritesAreVisibleWithinTheSameColumn() {
        val state = PyxelEngine.execute(intArrayOf(10 shl 16 or 42, 41 shl 16, 255 shl 16, 0, 40 shl 16, 255 shl 16), 3, 2, 2)
        assertEquals(42, state.registers[1][0])
    }

    @Test fun pngAdmissionRejectsAnimationMetadataCrcTruncationAndHugeGeometry() {
        assertEquals(PyxelEngine.Geometry(1, 1), PyxelEngine.inspectPng(png()))
        for (bad in listOf(png(2_000_000, 1), png(extra = chunk("acTL", ByteArray(8))),
            png(extra = chunk("eXIf", ByteArray(6))), png().dropLast(1).toByteArray(),
            png().apply { this[30] = (this[30].toInt() xor 1).toByte() }, png() + byteArrayOf(1))) {
            assertThrows(IllegalArgumentException::class.java) { PyxelEngine.inspectPng(bad) }
        }
    }

    @Test fun loopsUnsupportedOpsAndInvalidAddressesFailClosed() {
        assertThrows(IllegalStateException::class.java) { PyxelEngine.execute(intArrayOf(70 shl 16), 1, 1, 1) }
        for (op in listOf(60 shl 16, 99 shl 16, 40 shl 16 or (8 shl 8), 41 shl 16 or (255 shl 8))) {
            assertThrows(IllegalArgumentException::class.java) { PyxelEngine.execute(intArrayOf(op), 1, 1, 1) }
        }
    }

    @Test fun cancellationStopsBeforeExecutionAndParsing() {
        val cancelled = { throw IllegalStateException("cancelled") }
        assertThrows(IllegalStateException::class.java) { PyxelEngine.execute(intArrayOf(0), 1, 1, 1, cancelled) }
        assertThrows(IllegalStateException::class.java) { PyxelEngine.inspectPng(png(), cancelled) }
    }

    @Test fun canonicalJsonMatchesPythonSignatureInput() {
        val input = JSONObject().put("z", JSONArray().put("é😀").put(0.0)).put("a", JSONObject().put("x", "</\n"))
        assertEquals("{\"a\":{\"x\":\"</\\n\"},\"z\":[\"\\u00e9\\ud83d\\ude00\",0]}", CanonicalJson.encode(input))
    }
}
