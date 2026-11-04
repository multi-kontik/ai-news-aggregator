"""The reader the daily digest is curated for.

Edit `USER_PROFILE` to change what the editor agent prioritizes. Everything
here is passed to the model verbatim, so write it the way you'd brief a
human editor: concrete topics beat vague ones ("agent tool-calling
reliability" ranks better than "AI").
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class UserProfile(BaseModel):
    name: str = Field(description="What the daily email calls the reader, e.g. in its greeting.")
    background: str = Field(description="Who the reader is and what they work on.")
    interests: list[str] = Field(description="Topics the reader most wants to hear about, most important first.")
    less_interested_in: list[str] = Field(
        default_factory=list,
        description="Topics the reader would rather skip, so they're ranked down rather than excluded outright.",
    )
    preferences: list[str] = Field(
        default_factory=list,
        description="How the reader likes their news, e.g. technical depth over press-release framing.",
    )

    def to_prompt(self) -> str:
        """Render the profile as the text block the editor agent reads."""

        def bullets(items: list[str]) -> str:
            return "\n".join(f"- {item}" for item in items) or "- (none)"

        return (
            f"Name: {self.name}\n\n"
            f"Background:\n{self.background}\n\n"
            f"Most interested in (most important first):\n{bullets(self.interests)}\n\n"
            f"Less interested in:\n{bullets(self.less_interested_in)}\n\n"
            f"Preferences:\n{bullets(self.preferences)}"
        )


USER_PROFILE = UserProfile(
    name="Aaron",
    background=(
        "Software engineer building LLM-powered applications in Python. "
        "Works hands-on with the OpenAI and Anthropic APIs, structured outputs, agents, "
        "and data pipelines, and is currently building an AI news aggregator. Follows AI "
        "closely to stay current on what is technically possible and what is worth building with."
    ),
    interests=[
        "New model releases and capability jumps from frontier labs (OpenAI, Anthropic, Google DeepMind, Meta)",
        "LLM agents: tool use, multi-agent orchestration, agent frameworks and SDKs",
        "Developer-facing API and platform changes: new endpoints, structured outputs, pricing, rate limits",
        "Practical engineering for LLM apps: prompting, evals, RAG, context management, cost and latency",
        "AI coding tools and developer workflows",
        "Interpretability, alignment and AI safety research with concrete technical findings",
        "Benchmarks and evaluations, especially when they reveal real strengths or weaknesses",
    ],
    less_interested_in=[
        "Regional expansions, new office openings and country-level partnerships",
        "Enterprise customer case studies and sales-oriented success stories",
        "Corporate PR, funding and executive announcements with no technical substance",
        "Advertising and consumer marketing features",
        "Policy and legislation news, unless it directly changes how developers can build or ship",
    ],
    preferences=[
        "Technical depth and concrete details over high-level announcements",
        "Things that are usable today or change what a developer can build",
        "Primary sources and research over commentary",
    ],
)
