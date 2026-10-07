"""The no-search baseline: the same agent loop, model, prompt and budget, with zero tools.

The model answers from memory. Comparing a search provider against it shows what search added
("search lift"); a prompt set where the baseline scores as well as the providers measures the model,
not search.
"""

from contextlib import AbstractAsyncContextManager

from searchaudit.config import Settings
from searchaudit.providers.base import Provider, ProviderContext, ProviderSession, static_session

NO_TOOLS_NOTE = (
    "In this run you have NO search or fetch tools. Answer from what you already know. Say plainly "
    "which parts you cannot know or verify (for example events after your training data, or very "
    "recent or long-tail facts) instead of guessing, and do not invent source URLs."
)


class NoSearchSession(ProviderSession):
    def instructions(self) -> str:
        return NO_TOOLS_NOTE


class NoSearchProvider(Provider):
    id = "none"
    via = "No tools: the pinned agent model answers from memory (closed-book baseline)"

    def price_note(self, settings: Settings) -> str:
        return "model tokens only"

    def unavailable_reason(self, ctx: ProviderContext) -> str | None:
        return None if ctx.settings.openrouter_api_key else "OPENROUTER_API_KEY is not set"

    def open(self, ctx: ProviderContext) -> AbstractAsyncContextManager[ProviderSession]:
        return static_session(NoSearchSession())
