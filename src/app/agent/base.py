"""Shared plumbing for the agents: one OpenAI client and a typed structured-output call."""

from __future__ import annotations

from typing import TypeVar

from openai import OpenAI
from pydantic import BaseModel

from app.config import settings

OutputT = TypeVar("OutputT", bound=BaseModel)


class StructuredAgent:
    """Base for agents that make one Responses API call and parse it into a Pydantic model."""

    def __init__(self) -> None:
        self._client = OpenAI(api_key=settings.openai_api_key)

    def _parse(self, *, instructions: str, input: str, text_format: type[OutputT], what: str) -> OutputT:
        """Call the model and return its parsed output, raising if there isn't any.

        `what` names the output in the error message, e.g. "digest summary for 'https://...'".
        """
        response = self._client.responses.parse(
            model=settings.openai_model,
            instructions=instructions,
            input=input,
            text_format=text_format,
        )
        if response.output_parsed is None:
            raise ValueError(f"Model did not return a parsed {what}")
        return response.output_parsed
