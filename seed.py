from __future__ import annotations

import asyncio

from scripts.seed import seed


if __name__ == "__main__":
    asyncio.run(seed())
