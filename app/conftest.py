# app/conftest.py
import pytest
from db import connect_to_db
from mongoengine import disconnect


@pytest.fixture(scope='session', autouse=True)
def handle_db_lifecycle():
    # Connect once before the very first test starts
    connect_to_db(alias='default')
    yield
    # Disconnect once after the very last test finishes
    disconnect(alias='default')
