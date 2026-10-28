"""AI-assisted humanization and paraphrasing for OriginCheck.

Supports Google Gemini and Groq-compatible chat completions APIs.
API keys can be supplied in Streamlit secrets/environment variables or typed
into the UI for the current session. This is editing assistance, not detector evasion.
"""
import os
import requests
import streamlit as st

DEFAULT_GEMINI_MODEL = "gemini-3.8-flash"
DEFAULT_GROQ_MODEL = "openai/gpt-oss-120b"


def _setting(name, default=None):
    value = os.getenv(name)
    if value and value.strip():
        return value.strip()
    try:
        value = st.secrets.get(name, default)
        return value.strip() if isinstance(value, str) else value
    except Exception:
        return default


def humanize_text(text, tone="Natural and conversational", preserve_headings=True,
                  preserve_facts=True, task="humanize", provider="Gemini",
                  api_key=None, model=None):
    """Rewrite or paraphrase text using the selected provider."""
    if not text or not text.strip():
        raise ValueError("Please upload a supported file or paste some content first.")
    if len(text) > 30000:
        raise ValueError("Please keep each request under 30,000 characters.")

    provider = (provider or "Gemini").strip().lower()
    if provider == "groq":
        api_key = api_key or _setting("GROQ_API_KEY")
        model = model or _setting("GROQ_MODEL", DEFAULT_GROQ_MODEL)
    else:
        provider = "gemini"
        api_key = api_key or _setting("GEMINI_API_KEY")
        model = model or _setting("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)
    if not api_key:
        key_name = "GROQ_API_KEY" if provider == "groq" else "GEMINI_API_KEY"
        raise RuntimeError(
            f"No {provider.title()} API key provided. Enter it in the API settings above "
            f"or configure {key_name} in Streamlit Cloud Secrets."
        )

    headings_instruction = (
        "Preserve the existing headings and their order."
        if preserve_headings else
        "You may improve headings where useful, while retaining the same topic and structure."
    )
    facts_instruction = (
        "Preserve facts, figures, names, citations, URLs, quotations, and technical meaning. "
        "Do not invent sources or add unsupported claims."
        if preserve_facts else
        "Keep the central meaning and avoid adding unsupported facts or sources."
    )
    if task == "paraphrase":
        task_instruction = (
            "Paraphrase the supplied content using different wording and sentence structures. "
            "Stay close to the original meaning and level of detail. Do not merely swap synonyms. "
            "Do not add arguments, claims, examples, or sources. Keep direct quotations unchanged and attributed."
        )
    else:
        task_instruction = (
            "Rewrite the supplied content to improve readability, clarity, and flow in the selected writing style."
        )

    prompt = f"""You are a careful editor working on a blog post, article, or research draft.
{task_instruction}

Style: {tone}
{headings_instruction}
{facts_instruction}
Additional rules:
- Keep the author's intended meaning and important details.
- Avoid repetitive phrasing, filler, exaggerated claims, and generic introductions or conclusions.
- Do not add fake personal experiences, fake quotations, or fabricated citations.
- Keep technical terms accurate.
- Return only the revised content, without a preamble or a list of changes.
- This is a writing-assistance task, not an attempt to evade AI-detection systems or disguise plagiarism.

TEXT TO EDIT:
{text}"""

    try:
        if provider == "groq":
            response = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": "You are a careful, accurate editor."},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.65,
                    "max_tokens": 8192,
                },
                timeout=90,
            )
        else:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            response = requests.post(
                url,
                headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"temperature": 0.65, "maxOutputTokens": 8192},
                },
                timeout=90,
            )
    except requests.RequestException as exc:
        raise RuntimeError(f"Could not reach {provider.title()}: {exc}") from exc

    if response.status_code != 200:
        try:
            detail = response.json().get("error", {}).get("message", response.text)
        except Exception:
            detail = response.text
        if response.status_code in (401, 403):
            raise RuntimeError(f"The {provider.title()} API key was rejected or lacks permission for model '{model}'. Check the key, and try a newer model name in the settings. ({detail[:200]})")
        if response.status_code == 404:
            raise RuntimeError(f"Model '{model}' was not found for {provider.title()}. Pick a current model name in the settings. ({detail[:200]})")
        if response.status_code == 429:
            raise RuntimeError(f"The {provider.title()} API rate limit or quota was reached. Check your quota and try again later.")
        raise RuntimeError(f"{provider.title()} API error ({response.status_code}): {detail[:500]}")

    data = response.json()
    if provider == "groq":
        choices = data.get("choices") or []
        result = choices[0].get("message", {}).get("content", "") if choices else ""
    else:
        candidates = data.get("candidates") or []
        parts = candidates[0].get("content", {}).get("parts", []) if candidates else []
        result = "\n".join(p.get("text", "") for p in parts if p.get("text"))
    if not result or not result.strip():
        raise RuntimeError(f"{provider.title()} returned no text. Try a shorter input or another model.")
    return result.strip()
