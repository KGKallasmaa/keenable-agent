"""Provider registry. Adding a provider = one module with a `Provider` subclass + one `register` call."""

from searchaudit.providers.base import Provider


class Registry:
    def __init__(self) -> None:
        self._providers: dict[str, Provider] = {}

    def register(self, provider: Provider) -> Provider:
        if provider.id in self._providers:
            raise ValueError(f"provider already registered: {provider.id}")
        self._providers[provider.id] = provider
        return provider

    def get(self, provider_id: str) -> Provider:
        try:
            return self._providers[provider_id]
        except KeyError:
            raise KeyError(f"no provider registered as {provider_id!r}") from None

    def ids(self) -> list[str]:
        return list(self._providers)


def default_registry() -> Registry:
    from searchaudit.providers.keenable import KeenableSearchProvider
    from searchaudit.providers.routed import RoutedProvider
    from searchaudit.providers.select import SelectProvider
    from searchaudit.providers.server_tools import ServerToolProvider

    registry = Registry()
    registry.register(ServerToolProvider("exa", "exa", "Exa"))
    registry.register(ServerToolProvider("parallel", "parallel", "Parallel"))
    registry.register(KeenableSearchProvider())
    registry.register(SelectProvider())
    registry.register(RoutedProvider())
    return registry
