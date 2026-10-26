import pytest

from nba_engine.config import SNAPSHOT
from nba_engine.eligibility import offers_from_catalog
from nba_engine.loader import default_dataset
from nba_engine.state import build_states


@pytest.fixture(scope="session")
def ds():
    return default_dataset()


@pytest.fixture(scope="session")
def states(ds):
    return build_states(ds, SNAPSHOT)


@pytest.fixture(scope="session")
def offers(ds):
    return offers_from_catalog(ds.product_catalog)
