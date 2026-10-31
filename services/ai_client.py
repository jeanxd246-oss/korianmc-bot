"""Cliente de la IA (Gemini) con ejecución fuera del event loop y timeouts.

Reglas del módulo:
- Ninguna llamada a la API bloquea el event loop: van a `asyncio.to_thread`
  con `asyncio.wait_for`.
- Si falta `GEMINI_API_KEY` o la API falla, todos los métodos devuelven
  `None` y la capa superior muestra un aviso elegante: el bot sigue funcionando.
"""

from __future__ import annotations

import asyncio
import json
import re
import warnings
from typing import Any, Optional

import aiohttp

from core.config import settings
from core.logging_cfg import get_logger

log = get_logger("ia")

# google-generativeai avisa que está en mantenimiento; no interesa en cada arranque.
warnings.filterwarnings("ignore", category=FutureWarning)

try:
    import google.generativeai as genai
except Exception:  # pragma: no cover - dependencia opcional ausente
    genai = None  # type: ignore[assignment]

INTENTS = ("PREGUNTA", "QUEJA", "SPAM", "NO_APLICA")

_QUESTION_HINTS = (
    "qué", "que ", "cómo", "como ", "cuándo", "cuando ", "dónde", "donde ",
    "por qué", "porque ", "cuál", "cuanto", "puedo", "podría", "sabes",
    "hay algún", "es verdad", "dudas", "ayuda", "help", "?",
)
_COMPLAINT_HINTS = (
    "malo", "mala", "horrible", "inútil", "inutil", "no funciona", "error",
    "lento", "estafa", "queja", "odio", "pésimo", "pesimo", "basura", "rabia",
)
_SPAM_HINTS = (
    "gratis", "free nitro", "nitro", "regala", "airdrop", "da dinero",
    "visitа", "cliquеa", "http://", "https://tinyurl", "18+", "crypto",
)


def _local_classify(text: str) -> str:
    """Clasificación por palabras clave cuando Gemini no está disponible."""
    lowered = (text or "").lower()
    if len(lowered) < 3:
        return "NO_APLICA"
    if any(hint in lowered for hint in _SPAM_HINTS) or lowered.count("http") > 2:
        return "SPAM"
    if any(hint in lowered for hint in _COMPLAINT_HINTS):
        return "QUEJA"
    if any(hint in lowered for hint in _QUESTION_HINTS):
        return "PREGUNTA"
    return "NO_APLICA"


def _parse_json(text: str) -> Optional[dict[str, Any]]:
    """Extrae un objeto JSON de la respuesta del modelo."""
    if not text:
        return None
    cleaned = re.sub(r"```(?:json)?", "", text).replace("```", "").strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        data = json.loads(cleaned[start : end + 1])
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


class AIClient:
    """Fachada asíncrona sobre Gemini (texto) e Imagen (imágenes)."""

    def __init__(self) -> None:
        self._state: Optional[bool] = None
        self._reason = ""

    # ------------------------------------------------------------------
    # Estado
    # ------------------------------------------------------------------
    def configure(self) -> bool:
        """Prepara el SDK una sola vez. Devuelve si la IA está disponible."""
        if self._state is not None:
            return self._state
        if not settings.gemini_available:
            self._reason = "no hay GEMINI_API_KEY configurada"
            self._state = False
        elif genai is None:
            self._reason = "falta la librería google-generativeai"
            self._state = False
        else:
            try:
                genai.configure(api_key=settings.gemini_api_key)
            except Exception as exc:  # pragma: no cover - red/configuración
                self._reason = f"error al configurar Gemini ({type(exc).__name__})"
                self._state = False
            else:
                self._state = True
                self._reason = ""
        if not self._state:
            log.info("IA desactivada: %s.", self._reason)
        return self._state

    @property
    def available(self) -> bool:
        return self.configure()

    @property
    def reason(self) -> str:
        self.configure()
        return self._reason

    @property
    def unavailable_notice(self) -> str:
        return (
            f"🤖 **KorianMC IA no está disponible ahora mismo** ({self.reason}).\n"
            "El resto del bot funciona con normalidad; pide ayuda al staff mientras tanto."
        )

    # ------------------------------------------------------------------
    # Núcleo
    # ------------------------------------------------------------------
    async def generate(
        self,
        prompt: str,
        system: Optional[str] = None,
        *,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        timeout: Optional[float] = None,
    ) -> Optional[str]:
        if not self.available or genai is None:
            return None

        def _call() -> str:
            kwargs: dict[str, Any] = {}
            if system:
                kwargs["system_instruction"] = system
            model = genai.GenerativeModel(settings.gemini_model, **kwargs)
            response = model.generate_content(
                prompt,
                generation_config={
                    "temperature": temperature,
                    "max_output_tokens": max_tokens,
                },
            )
            return (response.text or "").strip()

        try:
            return await asyncio.wait_for(
                asyncio.to_thread(_call), timeout or settings.ai_timeout
            )
        except asyncio.TimeoutError:
            log.warning("Gemini tardó demasiado (>%.0fs).", timeout or settings.ai_timeout)
        except Exception as exc:
            log.warning("Fallo de Gemini: %s: %s", type(exc).__name__, exc)
        return None

    # ------------------------------------------------------------------
    # Funciones de alto nivel
    # ------------------------------------------------------------------
    async def chat(self, prompt: str, system: Optional[str] = None) -> Optional[str]:
        return await self.generate(prompt, system)

    async def classify(self, text: str) -> str:
        """Devuelve PREGUNTA, QUEJA, SPAM o NO_APLICA."""
        if not self.available:
            return _local_classify(text)
        raw = await self.generate(
            "Clasifica el mensaje de un chat de Discord en SOLO una de estas "
            "etiquetas: PREGUNTA, QUEJA, SPAM, NO_APLICA.\n"
            "PREGUNTA = pide información. QUEJA = expresa descontento. "
            "SPAM = publicidad, ruido o repetición. NO_APLICA = saludo, "
            "conversación casual o mensaje sin intención clara.\n"
            "Responde únicamente con la etiqueta.\n\n"
            f"Mensaje: {text[:800]}",
            system="Eres un clasificador de intenciones de mensajes. Respondes solo con la etiqueta.",
            temperature=0.0,
            max_tokens=16,
            timeout=15,
        )
        if not raw:
            return _local_classify(text)
        label = raw.strip().upper()
        for intent in INTENTS:
            if intent in label:
                return intent
        return _local_classify(text)

    async def summarize(self, text: str, lines: int = 6) -> Optional[str]:
        return await self.generate(
            f"Resume el siguiente texto en español en un máximo de {lines} "
            "líneas, con viñetas claras y sin inventar datos:\n\n" + text[:12000],
            system="Eres un asistente que resume con precisión y brevedad.",
            temperature=0.3,
        )

    async def translate(self, text: str, language: str = "inglés") -> Optional[str]:
        return await self.generate(
            f"Traduce el siguiente texto al {language} y responde SOLO con la "
            "traducción:\n\n" + text[:6000],
            system="Eres un traductor profesional.",
            temperature=0.2,
        )

    async def moderate(self, text: str) -> Optional[dict[str, Any]]:
        """Analiza toxicidad. Devuelve {toxicidad, spam, categoria, detalle}."""
        if not self.available:
            return None
        raw = await self.generate(
            "Evalúa este mensaje de Discord y devuelve SOLO un JSON con esta forma:\n"
            '{"toxicidad": 0.0, "spam": 0.0, '
            '"categoria": "ninguna|insulto|amenaza|discriminacion|acoso|spam|otro", '
            '"detalle": "explicación breve en español"}\n'
            "Los valores van de 0.0 (nada) a 1.0 (muy grave).\n\n"
            f"Mensaje: {text[:1500]}",
            system="Eres un moderador de comunidad. Respondes solo con JSON válido.",
            temperature=0.0,
            max_tokens=400,
            timeout=20,
        )
        data = _parse_json(raw or "")
        if data is None:
            return None
        try:
            toxicity = max(0.0, min(1.0, float(data.get("toxicidad", 0.0))))
            spam = max(0.0, min(1.0, float(data.get("spam", 0.0))))
        except (TypeError, ValueError):
            return None
        return {
            "toxicidad": toxicity,
            "spam": spam,
            "categoria": str(data.get("categoria", "otro"))[:40],
            "detalle": str(data.get("detalle", ""))[:400],
        }

    async def generate_image(self, prompt: str) -> Optional[bytes]:
        """Genera una imagen con Imagen a través de la misma API key de Gemini."""
        if not settings.gemini_available:
            return None
        model = "imagen-3.0-generate-002"
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:predict"
        payload = {
            "instances": [{"prompt": prompt[:800]}],
            "parameters": {"sampleCount": 1, "aspectRatio": "1:1"},
        }
        headers = {"x-goog-api-key": settings.gemini_api_key}
        try:
            timeout = aiohttp.ClientTimeout(total=settings.ai_timeout)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, json=payload, headers=headers) as response:
                    if response.status != 200:
                        body = (await response.text())[:300]
                        log.warning("Imagen no disponible (HTTP %s): %s", response.status, body)
                        return None
                    data = await response.json()
        except asyncio.TimeoutError:
            log.warning("La generación de imagen superó el tiempo límite.")
            return None
        except (aiohttp.ClientError, ValueError) as exc:
            log.warning("Fallo al generar imagen: %s", exc)
            return None

        predictions = data.get("predictions") or []
        if not predictions:
            return None
        encoded = predictions[0].get("bytesBase64Encoded")
        if not encoded:
            return None
        import base64

        try:
            return base64.b64decode(encoded)
        except (ValueError, TypeError):
            return None


ai = AIClient()

__all__ = ["AIClient", "ai", "INTENTS", "_local_classify"]
