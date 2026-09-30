"""autobot: strategy lab + Alpaca paper trading."""
# meta registers the strategy-switching strategies into STRATEGIES on import
from . import meta  # noqa: F401,E402
from . import news_guard as _news_guard  # noqa: E402

_news_guard.register()  # the news-aware wrapper strategy (Phase 5 step 5)
