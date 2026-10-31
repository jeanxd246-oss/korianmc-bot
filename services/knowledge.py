"""Base de conocimiento del servidor (FAQ + entradas de KorianMC IA).

La misma tabla alimenta los comandos `/faq` y el prompt de la IA.
"""

from __future__ import annotations

from typing import Any, Optional

from core.database import db
from core.logging_cfg import get_logger

log = get_logger("conocimiento")

_STOPWORDS = {
    "el", "la", "los", "las", "un", "una", "de", "del", "que", "y", "o",
    "a", "en", "por", "para", "con", "como", "es", "son", "se", "mi",
    "tu", "su", "me", "te", "le", "lo", "al", "qué", "cómo", "cuándo",
    "donde", "dónde", "cuál", "hay", "puedo", "puede", "hola", "gracias",
    "the", "of", "and", "how", "what", "is",
}


def _words(text: str) -> set[str]:
    return {
        word
        for word in "".join(ch.lower() if ch.isalnum() else " " for ch in text).split()
        if len(word) > 2 and word not in _STOPWORDS
    }


async def entries(guild_id: Any, source: Optional[str] = None, enabled_only: bool = True) -> list[dict[str, Any]]:
    sql = "SELECT * FROM ai_knowledge WHERE guild_id = ?"
    params: list[Any] = [str(guild_id)]
    if source:
        sql += " AND source = ?"
        params.append(source)
    if enabled_only:
        sql += " AND enabled = 1"
    sql += " ORDER BY id ASC"
    return await db.fetchall(sql, params)


async def knowledge_block(guild_id: Any, limit: int = 40) -> str:
    """Devuelve la base de conocimiento lista para inyectar en un prompt."""
    rows = await entries(guild_id)
    lines: list[str] = []
    for row in rows[:limit]:
        question = str(row.get("question", "")).strip()
        answer = str(row.get("answer", "")).strip()
        if not question or not answer:
            continue
        lines.append(f"- P: {question}\n  R: {answer}")
    if not lines:
        return "No hay base de conocimiento configurada para este servidor todavía."
    return "\n".join(lines)


async def search(guild_id: Any, question: str, source: Optional[str] = None) -> Optional[dict[str, Any]]:
    """Busca la entrada más parecida (solapamiento de palabras significativas)."""
    query_words = _words(question)
    if not query_words:
        return None
    best: Optional[dict[str, Any]] = None
    best_score = 0.0
    for row in await entries(guild_id, source):
        haystack = " ".join(
            [
                str(row.get("question", "")),
                str(row.get("keywords", "")),
                str(row.get("answer", "")),
            ]
        )
        words = _words(haystack)
        if not words:
            continue
        overlap = len(query_words & words)
        score = overlap / max(1, len(query_words))
        keyword_only = len(query_words & _words(str(row.get("keywords", ""))))
        score += 0.25 * keyword_only
        if score > best_score:
            best_score = score
            best = row
    if best is None or best_score < 0.5:
        return None
    return best


async def answer(guild_id: Any, question: str) -> Optional[str]:
    """Respuesta directa por reglas, sin tocar la IA."""
    row = await search(guild_id, question, source="faq")
    if row is None:
        return None
    return str(row.get("answer", "")).strip()


async def add_entry(
    guild_id: Any,
    question: str,
    answer_text: str,
    keywords: str = "",
    source: str = "faq",
) -> int:
    cursor = await db.execute(
        "INSERT INTO ai_knowledge (guild_id, question, answer, keywords, source) "
        "VALUES (?, ?, ?, ?, ?)",
        (str(guild_id), question, answer_text, keywords, source),
    )
    return int(cursor.lastrowid or 0)


async def remove_entry(guild_id: Any, entry_id: int) -> bool:
    cursor = await db.execute(
        "DELETE FROM ai_knowledge WHERE guild_id = ? AND id = ?",
        (str(guild_id), entry_id),
    )
    return cursor.rowcount > 0


async def toggle_entry(guild_id: Any, entry_id: int) -> Optional[int]:
    row = await db.fetchone(
        "SELECT id, enabled FROM ai_knowledge WHERE guild_id = ? AND id = ?",
        (str(guild_id), entry_id),
    )
    if row is None:
        return None
    new_value = 0 if int(row["enabled"]) else 1
    await db.execute(
        "UPDATE ai_knowledge SET enabled = ? WHERE guild_id = ? AND id = ?",
        (new_value, str(guild_id), entry_id),
    )
    return new_value


async def support_prompt(guild_id: Any) -> str:
    """Instrucciones de soporte por reglas para `/ia soporte`."""
    rows = await entries(guild_id, "faq")
    if not rows:
        return (
            "No hay reglas de soporte cargadas. Indica al staff que use `/faq add` "
            "para registrar las dudas frecuentes del servidor."
        )
    return "\n".join(
        f"- Si preguntan por '{row.get('question')}' responde: {row.get('answer')}"
        for row in rows
    )


__all__ = [
    "entries",
    "knowledge_block",
    "search",
    "answer",
    "add_entry",
    "remove_entry",
    "toggle_entry",
    "support_prompt",
]
