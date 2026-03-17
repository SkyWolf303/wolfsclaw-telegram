"""Configuration loaded from environment variables."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _bool(val: str | None) -> bool:
    return str(val).lower() in ("1", "true", "yes")


# Telegram
TELEGRAM_BOT_TOKEN: str = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHANNEL_ID: str = os.environ.get("TELEGRAM_CHANNEL_ID", "")

# Sky Forum (Discourse)
SKY_FORUM_API_KEY: str = os.environ.get("SKY_FORUM_API_KEY", "")
SKY_FORUM_BASE_URL: str = "https://forum.skyeco.com"

# Twitter / X
X_BEARER_TOKEN: str = os.environ.get("X_BEARER_TOKEN", "")

# GitHub
GITHUB_TOKEN: str = os.environ.get("GITHUB_TOKEN", "")

# Brave Search
BRAVE_API_KEY: str = os.environ.get("BRAVE_API_KEY", "")

# Database
DB_PATH: str = os.environ.get("DB_PATH", "data/state.db")

# Behaviour
LOG_LEVEL: str = os.environ.get("LOG_LEVEL", "INFO").upper()
SKIP_STARTUP_POLL: bool = _bool(os.environ.get("SKIP_STARTUP_POLL"))
DRY_RUN: bool = _bool(os.environ.get("DRY_RUN"))

# Ensure data directory exists
Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)

# VIP forum authors that trigger immediate alert
VIP_AUTHORS: set[str] = {"rune", "adamfraser", "SoterLabs", "Bonapublica", "BALabs"}

# Twitter accounts to monitor: handle -> user_id
TWITTER_ACCOUNTS: dict[str, str] = {
    "SkyEcosystem": "3190865591",
    "runekek": "385328034",
    "sparkdotfi": "1621178567781679105",
    "grovedotfinance": "1928133821872099331",
    "keel_fi": "1859598965882187776",
    "obexincubator": "1976702738823364609",
    "SkyEcoInsights": "2008988810508656640",
    "hexonaut": "330164723",
    "RiverdotInc": "1749307986449985536",
    "centrifuge": "985863970480275456",
    "Securitize": "963568407118462976",
    "MapleFinance": "1164762427604389890",
    "SteakhouseFi": "1562324374010867713",
    "LlamaRisk": "1494086645628518408",
    "BlockAnalitica": "1616000324879228934",
    "phoenixlabsdev": "1623328814943072257",
    "AjnaFi": "1455674919115796481",
    "SoterLabs": "1990130843533332480",
}

# MakerDAO looked up at runtime
TWITTER_LOOKUP_ACCOUNTS: list[str] = ["MakerDAO"]

TWITTER_SEARCH_QUERIES: list[str] = [
    '"Sky ecosystem" -is:retweet lang:en',
    '"USDS stablecoin" -is:retweet lang:en',
    '"SparkLend" -is:retweet lang:en',
    '"Sky protocol governance" -is:retweet lang:en',
    '"#SkyEcosystem" -is:retweet lang:en',
]

# DefiLlama slugs
DEFILLAMA_SLUGS: list[str] = [
    "sky-lending",
    "sparklend",
    "spark-liquidity-layer",
    "spark-savings",
    "sky-rwa",
]

# Forum categories
FORUM_CATEGORY_ENDPOINTS: dict[str, str] = {
    "Sky Core": "/c/92/l/latest.json",
    "Spark Prime": "/c/84/l/latest.json",
    "Incubating Primes": "/c/99/l/latest.json",
}
FORUM_TAG_ENDPOINTS: dict[str, str] = {
    "Atlas Edit Proposals": "/tag/atlas-edit-weekly-proposal.json",
}
FORUM_SEARCH_ENDPOINTS: dict[str, str] = {
    "MSC Settlement": "/search.json?q=MSC+settlement+order:latest",
}

# GitHub Atlas repo
ATLAS_REPO_OWNER: str = "sky-ecosystem"
ATLAS_REPO_NAME: str = "next-gen-atlas"
ATLAS_FILE_PATH: str = "Sky Atlas/Sky Atlas.md"
