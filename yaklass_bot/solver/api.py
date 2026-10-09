"""Любой OpenAI-совместимый сервер (OpenAI, OpenRouter, Ollama, vLLM, LM Studio ...)."""
from __future__ import annotations

import base64
import os
from pathlib import Path
from collections.abc import Sequence

import requests

from .base import Provider, SolveError
from .images import Image


class ApiProvider(Provider):
    last_info = ""
    def __init__(self, conf: dict):
        self.url = conf["base_url"].rstrip("/") + "/chat/completions"
        self.model = conf["model"]
        self.temperature = conf.get("temperature", 0.0)
        self.json_mode = bool(conf.get("json_mode", False))   # response_format=json_object (Ollama, OpenAI)
        self.supports_images = bool(conf.get("vision", True))   # False для текстовых моделей
        env = conf.get("api_key_env", "")
        self.key = os.environ.get(env, "") if env else ""
        key_file = conf.get("api_key_file", "")
        if not self.key and key_file and Path(key_file).expanduser().is_file():
            self.key = Path(key_file).expanduser().read_text(encoding="utf-8").strip()
        if not self.key:
            self.key = conf.get("api_key", "")
        # локальные серверы (Ollama и т.п.) ключа не требуют
        if not self.key and "api.openai.com" in self.url:
            raise SolveError("Не задан ключ API. Запустите `yaklass-bot setup` или задайте "
                             f"переменную окружения {env or 'OPENAI_API_KEY'}.")

    def ask(self, prompt: str, images: Sequence[Image] = ()) -> str:
        headers = {"Authorization": f"Bearer {self.key}"} if self.key else {}
        content: str | list = prompt
        if images:
            content = [{"type": "text", "text": prompt}] + [
                {"type": "image_url", "image_url": {
                    "url": f"data:{im.mime};base64,{base64.b64encode(im.data).decode()}"}}
                for im in images]
        body = {"model": self.model, "temperature": self.temperature,
                "messages": [{"role": "user", "content": content}]}
        if self.json_mode:
            body["response_format"] = {"type": "json_object"}
        r = requests.post(self.url, headers=headers, timeout=180, json=body)
        if r.status_code != 200:
            raise SolveError(f"API {r.status_code}: {r.text[:300]}")
        data = r.json()
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        usage = data.get("usage") or {}
        self.last_info = (f"finish_reason={choice.get('finish_reason')}, prompt_tokens={usage.get('prompt_tokens')}, "
                          f"completion_tokens={usage.get('completion_tokens')}")
        text = msg.get("content") or ""
        if not text.strip() and (msg.get("reasoning_content") or msg.get("reasoning")):
            self.last_info += "; пришли только рассуждения — отключите thinking или увеличьте лимит токенов"
        return text
