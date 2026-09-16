#!/usr/local/bin/python
# -*- coding: UTF-8 -*-

import argparse
from datetime import datetime
from exceptions import DatabaseError
import functools
import inspect
import logging
from models import Signout, Notification, PendingLink, Team
from mongoengine import QuerySet, connect
from mongoengine.connection import ConnectionFailure as MongoEngineConnectionFailure, get_connection
from mongoengine.errors import NotUniqueError, ValidationError
import os
import re
from pymongo.errors import ConnectionFailure as PyMongoConnectionFailure, ServerSelectionTimeoutError
from pymongo.server_api import ServerApi
from shared import configure_local_logger, DB_NAME
from trace_logging import setup_trace_level, TraceLogger


setup_trace_level()
logger: TraceLogger = logging.getLogger(__name__)  # type: ignore[assignment]


def handle_db_errors(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except ServerSelectionTimeoutError as e:
            # connection to the db is down
            logger.error(f'DB connection timed out: {e}')
            raise DatabaseError(f'DB connection timed out: {e}')
        except PyMongoConnectionFailure as e:
            # catches all pymongo connection errors
            logger.error(f'DB connection failed: {e}')
            raise DatabaseError(f'DB connection failed: {e}')
        except MongoEngineConnectionFailure as e:
            # catches alias/registration issues
            logger.error(f'DB connection error: {e}')
            raise DatabaseError(f'DB connection error: {e}')
        except NotUniqueError as e:
            logger.error(f'Not unique error in {func.__name__}: {e}')
            raise DatabaseError(f'Already exists: {e}')
        except ValidationError as e:
            logger.error(f'Validation error in {func.__name__}: {e}')
            raise DatabaseError(f'Invalid data: {e}')
        except (DatabaseError, ValueError):
            raise  # don't rewrap already-handled errors, or non-DB errors
        except Exception as e:
            logger.error(f'Error in {func.__name__}: {type(e).__module__}.{type(e).__name__}: {e}')
            raise DatabaseError(f'Unknown DB error occurred: {e}')
        finally:
            # This runs even if the code above crashes/returns
            logger.debug(f'Exiting {func.__name__}')

    return wrapper


def connect_to_db(alias: str = 'default') -> str:
    caller = inspect.stack()[1]
    logger.debug(f'connect_to_db called from {caller.filename}:{caller.lineno} in {caller.function}, pid={os.getpid()}')
    db_name = DB_NAME
    base_uri = os.environ.get('MONGO_URI', 'mongodb://db:27017/')
    try:
        try:
            get_connection(alias)
            logger.info(f'Reusing existing MongoDB connection for alias: {alias}')
            return alias
        except PyMongoConnectionFailure:
            pass
        except MongoEngineConnectionFailure:
            pass
        if 'mongodb+srv' in base_uri:
            connect(
                db=db_name,
                host=base_uri,
                uuidRepresentation='standard',
                alias=alias,
                server_api=ServerApi('1'),
                serverSelectionTimeoutMS=10000,  # Give it 10s (instead of 5s) to locate the Primary
                connectTimeoutMS=10000,  # 10s to establish the initial connection
                heartbeatFrequencyMS=60000,  # ping every 60s instead of 10s
                retryWrites=True,  # Automatically retry a write if a connection drops
                maxPoolSize=10,  # smaller pool
                minPoolSize=0,  # don't keep connections open when idle
            )
        else:
            connect(db=db_name, host=base_uri, uuidRepresentation='standard', alias=alias)
        logger.info(f'Verified connect setup to MongoDB at {re.sub(r"://[^@]+@", "://<user>:<password>@", base_uri)}, database: {db_name}')
    except PyMongoConnectionFailure as e:
        logger.error(f'DB connection failed: {e}')
        raise DatabaseError(f'DB connection failed: {e}')
    except MongoEngineConnectionFailure as e:
        logger.error(f'DB connection failed: {e}')
        raise DatabaseError(f'DB connection failed: {e}')
    logger.info(f'[connect_to_db] connect() returned successfully, alias={alias}')
    return alias


def date_from_str(date_str: str) -> datetime:
    return datetime.strptime(date_str, '%Y-%m-%d')


@handle_db_errors
def get_signout_list_from_db(
    date: str | datetime, club_id: str, dept_id: str, team_id: str | None = None, member_id: str | None = None, status: bool = True
) -> list[Signout]:
    if type(date) is str:
        try:
            date = date_from_str(date)
        except ValueError as e:
            raise ValueError(e)
            # raise ValueError(f"{date} doesn't match expected format of YYYY-MM-DD.")
    filters: dict = {'club_id': club_id, 'dept_id': dept_id, 'date': date, 'status': status}

    # add team_id if it was provided
    if team_id:
        filters['team_id'] = team_id
    if member_id:
        filters['member_id'] = member_id
    logger.debug(f'Filters: {filters}')  # Log the filters being used for the query

    docs: QuerySet[Signout] = Signout.objects(**filters)  # ** "unpacks" the dict
    # Process the results
    for doc in docs:
        logger.debug(f'Found signout: {doc.member_id} signed out on {doc.date} for reason: {doc.reason}')
    results: list[Signout] = list(docs)
    logger.info(f'Query returned {len(results)} documents')  # Log the number of documents found
    return results


@handle_db_errors
def get_signout_by_id(signout_id: str) -> list[Signout]:
    filters: dict[str, str] = {'id': signout_id}
    docs: QuerySet[Signout] = Signout.objects(**filters)  # ** "unpacks" the dict
    # Process the results
    for doc in docs:
        logger.debug(f'Found signout: {doc.member_id} signed out on {doc.date} for reason: {doc.reason}')
    results: list[Signout] = list(docs)
    logger.info(f'Query returned {len(results)} documents')  # Log the number of documents found
    return results


@handle_db_errors
def add_signout_to_db(
    # name: str,
    date: str | datetime,
    member_id: str,
    club_id: str,
    dept_id: str,
    team_id: str,
    updated_by: str = 'test',
    status: bool = True,
    reason: str = 'other',
    additional_reason='',
) -> Signout | None:
    """
    You must be connected to the db
    """
    if additional_reason:
        reason = f'{reason}: {additional_reason}'

    if type(date) is str:
        date = date_from_str(date)
    new_signout: Signout = Signout(
        date=date, member_id=member_id, club_id=club_id, dept_id=dept_id, team_id=team_id, updated_by=updated_by, status=status, reason=reason
    ).save()
    logger.info(f'{member_id} signed out of club={club_id} dept={dept_id} team={team_id} on {date} with ID: {new_signout.id}')
    return new_signout


@handle_db_errors
def update_signout_in_db(
    date: str | datetime,
    member_id: str,
    club_id: str,
    dept_id: str,
    team_id: str,
    updated_by: str = 'test',
    status: bool = False,
    reason: str = '',
    additional_reason: str = '',
) -> Signout | None:
    """
    You must be connected to the db
    """
    if additional_reason:
        reason = f'{reason}: {additional_reason}'

    if type(date) is str:
        date = date_from_str(date)
    matching_signouts: list[Signout] = get_signout_list_from_db(date, club_id, dept_id, team_id, member_id=member_id, status=True)
    logger.debug(f'Matching signouts: {matching_signouts}')
    if len(matching_signouts) == 1:
        logger.debug(f'Signout to edit {matching_signouts[0]}')
        matching_signouts[0].update(set__status=status, set__reason=reason, set__updated_by=updated_by)
        if status is False:
            logger.info(f'{member_id} signed back in club={club_id} dept={dept_id} team={team_id} on {date}')
        else:
            logger.info(
                f'{updated_by} updated signout club={club_id} dept={dept_id} team={team_id} member_id={member_id} on {date} with status={status} reason={reason}'
            )
        return matching_signouts[0].reload()
    return None


@handle_db_errors
def update_signout_by_id(
    signout_id: str = '',
    updated_by: str = 'test',
    status: bool = False,  # default is setting signed-out to False (signed-in)
    reason: str = '',
    additional_reason: str = '',
) -> Signout | None:
    """
    You must be connected to the db
    """
    if reason and additional_reason:
        reason = f'{reason}: {additional_reason}'

    matching_signouts: list[Signout] = get_signout_by_id(signout_id)
    logger.debug(f'Matching signouts: {matching_signouts}')
    if len(matching_signouts) == 1:
        logger.debug(f'Signout to edit {matching_signouts[0].to_mongo().to_dict()}')
        if status is True and not reason:
            reason = matching_signouts[0].reason
        matching_signouts[0].update(set__status=status, set__reason=reason, set__updated_by=updated_by)
        if status is False:
            logger.info(f'signout {signout_id} changed to signed in by {updated_by}')
        else:
            logger.info(f'{updated_by} updated signout {signout_id} to status={status} reason={reason}')
        return matching_signouts[0].reload()
    else:
        print(f'Found {len(matching_signouts)} signouts for signout_id={signout_id}, expected 1')
        logger.error(f'Found {len(matching_signouts)} signouts for signout_id={signout_id}, expected 1')
        return None


@handle_db_errors
def get_member_signouts_from_db(
    member_id: str,
    date: str | datetime,
    club_id: str,
    dept_id: str | None = None,
    team_id: str | None = None,
    history: bool = False,
    status: bool = True,
) -> list[Signout]:
    date_filter: str = 'date__gte'
    logger.debug(f'History: {history}')
    if history is True:
        date_filter = 'date'
    if type(date) is str:
        date = date_from_str(date)
    filters: dict = {'club_id': club_id, 'member_id': member_id, date_filter: date, 'status': status}

    # add team_id if it was provided
    if team_id:
        filters['team_id'] = team_id
    if dept_id:
        filters['dept_id'] = dept_id
    logger.debug(f'Filters: {filters}')  # Log the filters being used for the query

    docs: QuerySet = Signout.objects(**filters)
    return list(docs)


@handle_db_errors
def setup_notification(
    sid: str, username: str, club_id: str, service: str, lingua: str | None = None, team_ids: list[str] = []
) -> Notification | None:
    """
    You must be connected to the db
    """
    new_notification: Notification = Notification(sid=sid, username=username, club_id=club_id, team_ids=team_ids, service=service)
    if lingua is not None:
        new_notification.lingua = lingua
    new_notification.save()
    logger.info(f'Notification added for {username} with ID: {new_notification.id}')
    return new_notification


def add_fb_notification(psid: str, username: str, club_id: str, team_ids: list[str] = []) -> Notification | None:
    """
    You must be connected to the db
    """
    return setup_notification(psid, username, club_id, 'facebook', team_ids=team_ids)


@handle_db_errors
def get_notifications(username: str, club_id: str, service: str) -> list[Notification]:
    """
    Lookup notifications by using the coach username and club_id and service
    NB: You must be connected to the db
    """
    filters: dict = {'username': username, 'club_id': club_id}
    if service:
        filters['service'] = service
    docs: QuerySet = Notification.objects(**filters)
    return list(docs)


def get_fb_notifications(username: str, club_id: str) -> list[Notification]:
    """
    Lookup FB notifications by using the coach username and club_id
    NB: You must be connected to the db
    """
    return get_notifications(username, club_id, 'facebook')


@handle_db_errors
def get_notifications_by_sid(sid: str, service: str | None = None) -> list[Notification]:
    """
    Lookup notifications by sid, there should just be one
    NB: You must be connected to the db
    """
    filters: dict = {'sid': sid}
    if service:
        filters['service'] = service
    docs: QuerySet = Notification.objects(**filters)
    return list(docs)


def get_fb_notifications_by_psid(psid: str) -> list[Notification]:
    """
    Lookup FB notifications by psid
    NB: You must be connected to the db
    """
    return get_notifications_by_sid(psid, 'facebook')


@handle_db_errors
def get_coaches_to_notify(team_id: str) -> QuerySet:
    """
    Lookup notifications (sid and service) for notifications by team_id
    NB: You must be connected to the db
    """
    filters: dict = {'team_ids': team_id}
    docs: QuerySet = Notification.objects(**filters)
    return docs


@handle_db_errors
def update_teams_for_notifications(username: str, club_id: str, service: str, team_ids: list[str]) -> dict:
    """
    Updates Notification documents with new team IDs
    NB: You must be connected to the db
    """
    user_notification_count: int = Notification.objects(username=username, club_id=club_id, service=service).count()
    if user_notification_count == 1:
        user_notifications: Notification = Notification.objects(username=username, club_id=club_id, service=service).first()
        old: set = set(user_notifications.team_ids)
        logger.info(f'Found old team IDs: {old}')
        user_notifications.update(set__team_ids=team_ids)
        user_notifications.reload()
        logger.info(f'New team IDs: {team_ids}')
        new: set = set(team_ids)
        unchanged = old.intersection(new)
        removed = old - new  # or old.difference(new)
        added = new - old  # or new.difference(old)
        logger.info(f'Unchanged team  IDs: {list(unchanged)}')
        logger.info(f'Removed team IDs: {list(removed)}')
        logger.info(f'Added team IDs: {list(added)}')
        logger.info(f'Updated {user_notifications} {service.title()} notification entries')
        return user_notifications.to_mongo().to_dict()
    else:
        raise Exception(
            f'Found {user_notification_count} {service.title()} notification entries for username={username} club_id={club_id} service={service}, expected 1'
        )


@handle_db_errors
def update_teams_for_fb_notifications(username: str, club_id: str, team_ids: list[str]) -> dict:
    """
    Updates Notification documents with new team IDs
    NB: You must be connected to the db
    """
    user_notification_count: int = Notification.objects(username=username, club_id=club_id, service='facebook').count()
    if user_notification_count == 1:
        user_notifications: Notification = Notification.objects(username=username, club_id=club_id, service='facebook').first()
        old: set = set(user_notifications.team_ids)
        logger.info(f'Found old team IDs: {old}')
        user_notifications.update(set__team_ids=team_ids)
        user_notifications.reload()
        logger.info(f'New team IDs: {team_ids}')
        new: set = set(team_ids)
        unchanged = old.intersection(new)
        removed = old - new  # or old.difference(new)
        added = new - old  # or new.difference(old)
        logger.info(f'Unchanged team  IDs: {list(unchanged)}')
        logger.info(f'Removed team IDs: {list(removed)}')
        logger.info(f'Added team IDs: {list(added)}')
        logger.info(f'Updated {user_notifications} FB notification entries')
        return user_notifications.to_mongo().to_dict()
    else:
        raise Exception(f'Found {user_notification_count} FB notification entries for username={username} club_id={club_id}, expected 1')


@handle_db_errors
def delete_fb_messenger_link_for_coach(
    username: str,
    club_id: str,
) -> int:
    """
    You must be connected to the db
    """
    deleted_count: int = Notification.objects(username=username, club_id=club_id, service='facebook').delete()
    if deleted_count != 1:
        logger.info(f'Deleted {deleted_count} FB notification entries for "{username}"')
    else:
        logger.info(f'Deleted 1 FB notification entry for "{username}"')
    return deleted_count


@handle_db_errors
def delete_link_for_coach(username: str, club_id: str, service: str) -> int:
    """
    You must be connected to the db
    """
    deleted_count: int = Notification.objects(username=username, club_id=club_id, service=service).delete()
    if deleted_count != 1:
        logger.info(f'Deleted {deleted_count} {service} notification entries for "{username}"')
    else:
        logger.info(f'Deleted 1 {service} notification entry for "{username}"')
    return deleted_count


@handle_db_errors
def save_pending_link(sid: str, code: str, name: str, service: str, lingua: str | None = None) -> str:
    # query the class directly using the objects manager
    if PendingLink.objects(sid=sid, service=service).count() >= 3:
        return 'app.try_again_later'
    PendingLink(sid=sid, code=code, name=name, service=service, lingua=lingua).save()
    return code


@handle_db_errors
def add_team_info_to_db(team_info: dict) -> Team:
    """
    You must be connected to the db
    """
    team_info.pop('active', None)
    new_team: Team = Team(**team_info)
    new_team.save()
    logger.info(f'Team added for {team_info.get("team_id")} with ID: {new_team.id}')
    return new_team


@handle_db_errors
def get_team_info_from_db(**kwargs) -> Team | None:
    teams: QuerySet[Team] = Team.objects(**kwargs)
    logger.debug(f'found {len(teams)} teams')
    if len(teams) == 1:
        logger.debug(f'Found team: {teams[0].team_id} with name: {teams[0].name}')
        return teams[0]
    return None


@handle_db_errors
def add_pairing_to_db(pairing: str, **kwargs) -> int:
    teams: QuerySet[Team] = Team.objects(**kwargs)
    logger.debug(f'Found {len(teams)} teams')
    teams.update(set__pairing=pairing)
    return len(teams)


@handle_db_errors
def get_pairings_from_db() -> QuerySet[Team]:
    teams: QuerySet[Team] = Team.objects(pairing__exists=True).only('pairing', 'ref')
    logger.debug(f'Found {len(teams)} teams')
    return teams


if __name__ == '__main__':
    from dotenv import load_dotenv

    load_dotenv()  # This loads variables from .env into os.environ
    parser = argparse.ArgumentParser()
    parser.add_argument('--debug', action='store_true')
    parser.add_argument('--trace', action='store_true')

    # If REQUEST_METHOD exists, we are likely in a CGI environment
    if os.environ.get('REQUEST_METHOD'):
        args = parser.parse_args([])  # Pass an empty list to ignore CLI input
    else:
        args = parser.parse_args()

    configure_local_logger(debug=args.debug, trace=args.trace)
