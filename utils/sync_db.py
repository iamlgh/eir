import argparse
from dotenv import load_dotenv
import os
import re
import sys
from typing import Any, Optional
from pymongo import MongoClient, errors
from pymongo.collection import Collection
from pymongo.cursor import Cursor
from pymongo.results import DeleteResult


load_dotenv()  # This loads variables from .env into os.environ


def safe_uri(uri: str) -> str:
    return re.sub(r'://[^@]+@', '://<user>:<password>@', uri)


def sync_collections(collection_name: str, debug: bool = False) -> None:
    # Fetch environment variables
    prod_uri: Optional[str] = os.environ.get('MONGO_URI_PROD')
    test_uri: Optional[str] = os.environ.get('MONGO_URI_TEST')
    db_name: Optional[str] = os.environ.get('DB_NAME')

    missing_env: list[str] = []
    if not prod_uri:
        missing_env.append('MONGO_URI_PROD')
    if not test_uri:
        missing_env.append('MONGO_URI_TEST')
    if not db_name:
        missing_env.append('DB_NAME')

    if missing_env:
        print(f'Error: Missing required environment variable(s): {", ".join(missing_env)}', file=sys.stderr)
        sys.exit(1)

    assert prod_uri and test_uri and db_name  # narrows types to str for mypy

    if prod_uri == test_uri:
        print(f'Error: MONGO_URI_PROD and MONGO_URI_TEST are the same: {safe_uri(prod_uri)}', file=sys.stderr)
        sys.exit(1)

    batch_size: int = 500

    prod_client: Optional[MongoClient] = None
    test_client: Optional[MongoClient] = None
    try:
        # Initialize Clients and target the DB explicitly
        print(f"Connecting to production and test clusters (Database: '{db_name}')...")
        prod_client = MongoClient(prod_uri)
        test_client = MongoClient(test_uri)

        prod_collection: Collection[dict[str, Any]] = prod_client[db_name][collection_name]
        test_collection: Collection[dict[str, Any]] = test_client[db_name][collection_name]

        # Read from Production
        total_docs: int = prod_collection.count_documents({})
        print(f"Found {total_docs} document(s) in production '{db_name}.{collection_name}'.")

        if total_docs == 0:
            print('No documents to copy.')
            return

        # Clear the test collection first. Upserting by _id can fail with a
        # duplicate-key error on a *different* unique index (e.g. team_id)
        # if the test collection's _ids have ever drifted from production's
        # (for example if test documents were seeded/edited independently).
        # Since this is a one-way prod -> test mirror, wiping and reinserting
        # sidesteps that entirely instead of trying to reconcile mismatched _ids.
        deleted: DeleteResult = test_collection.delete_many({})
        if deleted.deleted_count:
            print(f"Cleared {deleted.deleted_count} existing document(s) from test '{db_name}.{collection_name}'.")

        cursor: Cursor[dict[str, Any]] = prod_collection.find()

        # Write to Test in batches
        batch: list[dict[str, Any]] = []
        copied_count: int = 0

        for doc in cursor:
            batch.append(doc)
            if len(batch) >= batch_size:
                test_collection.insert_many(batch, ordered=False)
                copied_count += len(batch)
                print(f'Copied {copied_count}/{total_docs} documents...')
                batch.clear()

        # Write remaining documents in final batch
        if batch:
            test_collection.insert_many(batch, ordered=False)
            copied_count += len(batch)

        print(f'Successfully synced {copied_count} document(s) to test database!')
        if debug:
            for doc in test_collection.find():
                print(doc)  # doc is a dict that holds JSON object

    except errors.PyMongoError as e:
        print(f'MongoDB Error: {e}', file=sys.stderr)
        sys.exit(1)
    finally:
        if prod_client is not None:
            prod_client.close()
        if test_client is not None:
            test_client.close()


def confirm_destructive_sync(collection_names: list[str], test_uri: str) -> None:
    names: str = ', '.join(collection_names)
    print(f'WARNING: this will DELETE all existing test data in: {names}')
    print(f'from test database {safe_uri(test_uri)}')
    print('and replace it with a copy of production. This cannot be undone.')
    answer: str = input('Type Y to proceed: ').strip()
    if answer != 'Y':
        print('Aborted: no changes made.')
        sys.exit(0)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--notifications', action='store_true', help='notifications')
    parser.add_argument('--pending-links', action='store_true', help='pending links')
    parser.add_argument('--signouts', action='store_true', help='signouts')
    parser.add_argument('--teams', action='store_true', help='teams')
    parser.add_argument('--debug', action='store_true', help='enable debug print')
    parser.add_argument('--yes', action='store_true', help='skip the confirmation prompt (for scripted/automated runs)')
    args = parser.parse_args()

    selected: list[str] = []
    if args.notifications:
        selected.append('fb_notifications')
    if args.pending_links:
        selected.append('pending_links')
    if args.signouts:
        selected.append('signouts')
    if args.teams:
        selected.append('teams')

    if not selected:
        print('No collections selected. Use --notifications, --pending-links, --signouts, and/or --teams.')
        sys.exit(0)

    if not args.yes:
        test_uri: Optional[str] = os.environ.get('MONGO_URI_TEST')
        if not test_uri:
            print('Error: Missing required environment variable(s): MONGO_URI_TEST', file=sys.stderr)
            sys.exit(1)
        confirm_destructive_sync(selected, test_uri)

    for name in selected:
        sync_collections(name, debug=args.debug)
