package com.zerodrivex.zdxnode

import org.json.JSONArray
import org.json.JSONObject

/** Compact, sorted, ASCII-escaped JSON matching the Python protocol signer.
 * Wire numbers are normalized through JSON before signing (as Python sees them).
 */
object CanonicalJson {
    fun encode(value: JSONObject): String = encodeValue(JSONObject(value.toString()))

    private fun quote(value: String): String = buildString {
        append('"')
        for (c in value) when (c) {
            '"' -> append("\\\"")
            '\\' -> append("\\\\")
            '\b' -> append("\\b")
            '\u000c' -> append("\\f")
            '\n' -> append("\\n")
            '\r' -> append("\\r")
            '\t' -> append("\\t")
            else -> if (c.code < 32 || c.code > 126) append("\\u%04x".format(c.code)) else append(c)
        }
        append('"')
    }

    private fun encodeValue(value: Any?): String = when (value) {
        null, JSONObject.NULL -> "null"
        is JSONObject -> value.keys().asSequence().toList().sorted().joinToString(separator = ",", prefix = "{", postfix = "}") { key -> quote(key) + ":" + encodeValue(value.get(key)) }
        is JSONArray -> (0 until value.length()).joinToString(separator = ",", prefix = "[", postfix = "]") { encodeValue(value.get(it)) }
        is String -> quote(value)
        is Boolean -> value.toString()
        is Number -> JSONObject.numberToString(value)
        else -> error("unsupported canonical JSON value")
    }
}
