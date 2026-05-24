import json
import logging
import urllib.request
import urllib.error
from typing import Dict

from ai.openai_config import (
    get_openai_api_key,
    validate_openai_key,
    get_default_model,
)

logger = logging.getLogger("aegisfx.ai")

OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"

FALLBACK_RESPONSE = {
    "regime": "UNKNOWN",
    "summary": "AI unavailable",
    "confidence": 0,
    "pair_analysis": {},
}

SYSTEM_PROMPT = (
    "You are a senior FX market analyst providing read-only analysis of "
    "currency pair data. You have NO execution authority, NO ability to "
    "place trades, and NO ability to override risk controls. Your role is "
    "to interpret market data and provide a structured analysis. "
    "You MUST respond with valid JSON only, matching this schema exactly:\n"
    "{\n"
    '  "regime": "Trending" | "Ranging" | "Volatile" | "Risk-Off" | "Risk-On",\n'
    '  "summary": "<one-sentence overall market read>",\n'
    '  "confidence": <integer 0-100>,\n'
    '  "pair_analysis": {\n'
    '    "<PAIR>": "<brief analysis for that pair>"\n'
    "  }\n"
    "}\n"
    "Keep summaries terse. Explain reasoning briefly inside each field. "
    "Do not output anything outside the JSON object."
)


class MarketAnalysisService:
    """
    Read-only OpenAI market analysis service.
    Provides FX market interpretation but has no execution authority.
    """

    def __init__(self, model: str = None, timeout: int = 15):
        self._model = model or get_default_model()
        self._timeout = timeout

    def analyze_market_context(self, market_data: Dict) -> Dict:
        """
        Send market data to OpenAI and return structured analysis.
        On any failure, returns deterministic fallback.
        """

        logger.info({
            "event": "ai_analysis_started",
            "model": self._model,
            "pairs": list(market_data.keys()),
        })

        if not validate_openai_key():
            logger.error({
                "event": "ai_analysis_failed",
                "reason": "API key missing or invalid",
            })
            return dict(FALLBACK_RESPONSE)

        try:
            api_key = get_openai_api_key()
        except RuntimeError as e:
            logger.error({
                "event": "ai_analysis_failed",
                "reason": str(e),
            })
            return dict(FALLBACK_RESPONSE)

        user_prompt = (
            "Analyze the following currency pair market data and respond "
            "with the JSON schema described in the system prompt.\n\n"
            f"{json.dumps(market_data, indent=2)}"
        )

        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.2,
        }

        try:
            req = urllib.request.Request(
                OPENAI_API_URL,
                data=json.dumps(payload).encode("utf-8"),
                method="POST",
            )
            req.add_header("Authorization", f"Bearer {api_key}")
            req.add_header("Content-Type", "application/json")

            with urllib.request.urlopen(req, timeout=self._timeout) as response:
                data = json.loads(response.read().decode("utf-8"))

        except urllib.error.HTTPError as e:
            logger.error({
                "event": "ai_analysis_failed",
                "reason": f"HTTP {e.code}",
            })
            return dict(FALLBACK_RESPONSE)
        except urllib.error.URLError as e:
            logger.error({
                "event": "ai_analysis_failed",
                "reason": f"Connection error: {str(e.reason)}",
            })
            return dict(FALLBACK_RESPONSE)
        except Exception as e:
            logger.error({
                "event": "ai_analysis_failed",
                "reason": f"Unexpected error: {str(e)}",
            })
            return dict(FALLBACK_RESPONSE)

        try:
            content = data["choices"][0]["message"]["content"]
            parsed = json.loads(content)
        except (KeyError, IndexError, ValueError, TypeError) as e:
            logger.error({
                "event": "ai_analysis_failed",
                "reason": f"Malformed response: {str(e)}",
            })
            return dict(FALLBACK_RESPONSE)

        # Sanitize — enforce schema shape
        result = {
            "regime": str(parsed.get("regime", "UNKNOWN")),
            "summary": str(parsed.get("summary", "")),
            "confidence": int(parsed.get("confidence", 0)) if isinstance(parsed.get("confidence"), (int, float)) else 0,
            "pair_analysis": parsed.get("pair_analysis", {}) if isinstance(parsed.get("pair_analysis"), dict) else {},
        }

        logger.info({
            "event": "ai_analysis_completed",
            "regime": result["regime"],
            "confidence": result["confidence"],
        })

        return result
