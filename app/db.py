#!/usr/local/bin/python
# -*- coding: UTF-8 -*-

import argparse
from datetime import datetime
from exceptions import DatabaseError
import inspect
import logging
from models import Signout, Notification, PendingLink, Team
from mongoengine import QuerySet, connect
from mongoengine.connection import ConnectionFailure, get_connection
from mongoengine.errors import NotUniqueError, ValidationError
import os
import re
from pymongo.errors import ServerSelectionTimeoutError
from pymongo.server_api import ServerApi
from shared import configure_local_logger, DB_NAME
from trace_logging import setup_trace_level, TraceLogger


setup_trace_level()
logger: TraceLogger = logging.getLogger(__name__)  # type: ignore[assignment]


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
        except ConnectionFailure:
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
        logger.info(f'Verified connect setup to MongoDB at {re.sub(r"://[^@]+@", "://<user>:<password>", base_uri)}, database: {db_name}')
    except ConnectionFailure as e:
        logger.error(f'Error with connect setup to MongoDB: {e}')
        raise DatabaseError('Error with connection setup to MongoDB')
    logger.info(f'[connect_to_db] connect() returned successfully, alias={alias}')
    return alias


def date_from_str(date_str: str) -> datetime:
    return datetime.strptime(date_str, '%Y-%m-%d')


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

    try:
        docs: QuerySet[Signout] = Signout.objects(**filters)  # ** "unpacks" the dict
        # Process the results
        for doc in docs:
            logger.debug(f'Found signout: {doc.member_id} signed out on {doc.date} for reason: {doc.reason}')
        results: list[Signout] = list(docs)
        logger.info(f'Query returned {len(results)} documents')  # Log the number of documents found
        return results
    except ServerSelectionTimeoutError as e:
        # connection to the db is down
        logger.error(f'DB connection timed out: {e}')
        raise DatabaseError(f'DB connection timed out: {e}')
    except ConnectionFailure as e:
        # connection to the db failed
        logger.error(f'DB connection failed: {e}')
        raise DatabaseError(f'DB connection failed: {e}')
    except Exception as e:
        logger.error(f'DB query failed due to unknown error: {type(e).__module__}.{type(e).__name__}: {e}')
        raise DatabaseError(f'DB query failed due to unknown error: {type(e).__module__}.{type(e).__name__}: {e}')
    finally:
        # This runs even if the code above crashes/returns
        # disconnect(alias=unique_alias)
        logger.debug('Exiting get_signout_list_from_db')


def get_signout_by_id(signout_id):
    filters: dict = {'id': signout_id}
    try:
        docs: QuerySet[Signout] = Signout.objects(**filters)  # ** "unpacks" the dict
        # Process the results
        for doc in docs:
            logger.debug(f'Found signout: {doc.member_id} signed out on {doc.date} for reason: {doc.reason}')
        results: list[Signout] = list(docs)
        logger.info(f'Query returned {len(results)} documents')  # Log the number of documents found
        return results
    except ServerSelectionTimeoutError as e:
        # connection to the db is down
        logger.error(f'DB connection timed out: {e}')
        raise DatabaseError(f'DB connection timed out: {e}')
    except ConnectionFailure as e:
        # connection to the db failed
        logger.error(f'DB connection failed: {e}')
        raise DatabaseError(f'DB connection failed: {e}')
    except Exception as e:
        logger.error(f'DB query failed due to unknown error: {type(e).__module__}.{type(e).__name__}: {e}')
        raise DatabaseError(f'DB query failed due to unknown error: {type(e).__module__}.{type(e).__name__}: {e}')
    finally:
        # This runs even if the code above crashes/returns
        # disconnect(alias=unique_alias)
        logger.debug('Exiting get_signout_list_from_db')


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
    try:
        new_signout: Signout = Signout(
            date=date, member_id=member_id, club_id=club_id, dept_id=dept_id, team_id=team_id, updated_by=updated_by, status=status, reason=reason
        ).save()
        logger.info(f'{member_id} signed out of club={club_id} dept={dept_id} team={team_id} on {date} with ID: {new_signout.id}')
        return new_signout
    except Exception as e:
        logger.error(f'Error creating signout document in MongoDB: {type(e).__module__}.{type(e).__name__}: {e}')
        return None


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
    try:
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
    except Exception as e:
        logger.error(f'Error updating document in MongoDB: {type(e).__module__}.{type(e).__name__}: {e}')
    return None


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

    # if type(date) is str:
    #    date = date_from_str(date)
    try:
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
            raise Exception(f'Found {len(matching_signouts)} signouts for signout_id={signout_id}, expected 1')
    except Exception as e:
        logger.error(f'Error updating document in MongoDB: {type(e).__module__}.{type(e).__name__}: {e}')
        raise


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

    try:
        # with switch_db(Signout, unique_alias) as UniqueSignout:
        docs: QuerySet = Signout.objects(**filters)
        return list(docs)
    except ServerSelectionTimeoutError as e:
        # connection to the db is down
        logger.error(f'DB connection timed out: {e}')
        raise DatabaseError(f'DB connection timed out: {e}')
    except ConnectionFailure as e:
        # connection to the db failed
        logger.error(f'DB connection failed: {e}')
        raise DatabaseError(f'DB connection failed: {e}')
    except Exception as e:
        logger.error(f'DB query failed: {type(e).__module__}.{type(e).__name__}: {e}')  # Log it using logging
        return []
    finally:
        # This runs even if the code above crashes/returns
        # disconnect(alias=unique_alias)
        logger.debug('Exiting get_member_signouts_from_db')


def setup_notification(
    sid: str, username: str, club_id: str, service: str, lingua: str | None = None, team_ids: list[str] = []
) -> Notification | None:
    """
    You must be connected to the db
    """
    try:
        new_notification: Notification = Notification(sid=sid, username=username, club_id=club_id, team_ids=team_ids, service=service)
        if lingua is not None:
            new_notification.lingua = lingua
        new_notification.save()
        logger.info(f'Notification added for {username} with ID: {new_notification.id}')
        return new_notification
    except ServerSelectionTimeoutError as e:
        # connection to the db is down
        logger.error(f'DB connection timed out: {e}')
        raise DatabaseError(f'DB connection timed out: {e}')
    except ConnectionFailure as e:
        # connection to the db failed
        logger.error(f'DB connection failed: {e}')
        raise DatabaseError(f'DB connection failed: {e}')
    except Exception as e:
        logger.error(f'Error creating notification document in MongoDB: {type(e).__module__}.{type(e).__name__}: {e}')
        return None


def add_fb_notification(psid: str, username: str, club_id: str, team_ids: list[str] = []) -> Notification | None:
    """
    You must be connected to the db
    """
    return setup_notification(psid, username, club_id, 'facebook', team_ids=team_ids)


def get_notifications(username: str, club_id: str, service: str) -> list[Notification]:
    """
    Lookup notifications by using the coach username and club_id and service
    NB: You must be connected to the db
    """
    filters: dict = {'username': username, 'club_id': club_id}
    if service:
        filters['service'] = service
    try:
        docs: QuerySet = Notification.objects(**filters)
        return list(docs)
    except ServerSelectionTimeoutError as e:
        # connection to the db is down
        logger.error(f'DB connection timed out: {e}')
        raise DatabaseError(f'DB connection timed out: {e}')
    except ConnectionFailure as e:
        # connection to the db failed
        logger.error(f'DB connection failed: {e}')
        raise DatabaseError(f'DB connection failed: {e}')
    except Exception as e:
        logger.error(f'Error getting notification documents from MongoDB: {type(e).__module__}.{type(e).__name__}: {e}')
        return []


def get_fb_notifications(username: str, club_id: str) -> list[Notification]:
    """
    Lookup FB notifications by using the coach username and club_id
    NB: You must be connected to the db
    """
    return get_notifications(username, club_id, 'facebook')


def get_notifications_by_sid(sid: str, service: str | None = None) -> list[Notification]:
    """
    Lookup notifications by sid, there should just be one
    NB: You must be connected to the db
    """
    filters: dict = {'sid': sid}
    if service:
        filters['service'] = service
    try:
        docs: QuerySet = Notification.objects(**filters)
        return list(docs)
    except ServerSelectionTimeoutError as e:
        # connection to the db is down
        logger.error(f'DB connection timed out: {e}')
        raise DatabaseError(f'DB connection timed out: {e}')
    except ConnectionFailure as e:
        # connection to the db failed
        logger.error(f'DB connection failed: {e}')
        raise DatabaseError(f'DB connection failed: {e}')
    except Exception as e:
        logger.error(f'Error getting notification documents from MongoDB: {type(e).__module__}.{type(e).__name__}: {e}')
        return []


def get_fb_notifications_by_psid(psid: str) -> list[Notification]:
    """
    Lookup FB notifications by psid
    NB: You must be connected to the db
    """
    return get_notifications_by_sid(psid, 'facebook')


def get_coaches_to_notify(team_id: str) -> QuerySet | None:
    """
    Lookup notifications (sid and service) for notifications by team_id
    NB: You must be connected to the db
    """
    filters: dict = {'team_ids': team_id}
    try:
        docs: QuerySet = Notification.objects(**filters)
        return docs
    except ServerSelectionTimeoutError as e:
        # connection to the db is down
        logger.error(f'DB connection timed out: {e}')
        raise DatabaseError(f'DB connection timed out: {e}')
    except ConnectionFailure as e:
        # connection to the db failed
        logger.error(f'DB connection failed: {e}')
        raise DatabaseError(f'DB connection failed: {e}')
    except Exception as e:
        logger.error(f'Error getting coaches to notify documents from MongoDB: {type(e).__module__}.{type(e).__name__}: {e}')
        return None


def update_teams_for_notifications(username: str, club_id: str, service: str, team_ids: list[str]) -> dict | None:
    """
    Updates Notification documents with new team IDs
    NB: You must be connected to the db
    """
    try:
        # Update documents with new team_ids
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
    except ServerSelectionTimeoutError as e:
        # connection to the db is down
        logger.error(f'DB connection timed out: {e}')
        raise DatabaseError(f'DB connection timed out: {e}')
    except ConnectionFailure as e:
        # connection to the db failed
        logger.error(f'DB connection failed: {e}')
        raise DatabaseError(f'DB connection failed: {e}')
    except Exception as e:
        logger.error(f'Error updating documents in MongoDB: {type(e).__module__}.{type(e).__name__}: {e}')
        raise


def update_teams_for_fb_notifications(username: str, club_id: str, team_ids: list[str]) -> dict | None:
    """
    Updates Notification documents with new team IDs
    NB: You must be connected to the db
    """
    try:
        # Update documents with new team_ids
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
    except ServerSelectionTimeoutError as e:
        # connection to the db is down
        logger.error(f'DB connection timed out: {e}')
        raise DatabaseError(f'DB connection timed out: {e}')
    except ConnectionFailure as e:
        # connection to the db failed
        logger.error(f'DB connection failed: {e}')
        raise DatabaseError(f'DB connection failed: {e}')
    except Exception as e:
        logger.error(f'Error updating documents in MongoDB: {type(e).__module__}.{type(e).__name__}: {e}')
        raise


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


def save_pending_link(sid: str, code: str, name: str, service: str, lingua: str | None = None) -> str:
    # if PendingLink(sid=sid).objects.count() >= 3:
    # query the class directly using the objects manager
    if PendingLink.objects(sid=sid, service=service).count() >= 3:
        return 'app.try_again_later'
    try:
        PendingLink(sid=sid, code=code, name=name, service=service, lingua=lingua).save()
        return code
    except ServerSelectionTimeoutError as e:
        # connection to the db is down
        logger.error(f'DB connection timed out: {e}')
        raise DatabaseError(f'DB connection timed out: {e}')
    except ConnectionFailure as e:
        # connection to the db failed
        logger.error(f'DB connection failed: {e}')
        raise DatabaseError(f'DB connection failed: {e}')
    except Exception as e:
        logger.error(f'Error creating pending link document in MongoDB: {type(e).__module__}.{type(e).__name__}: {e}')
        return 'app.pending_link_failed'


def add_team_info_to_db(
    team_info: dict,
) -> Team | None:
    """
    You must be connected to the db
    """
    try:
        team_info.pop('active', None)
        new_team: Team = Team(**team_info)
        new_team.save()
        logger.info(f'Team added for {team_info.get("team_id")} with ID: {new_team.id}')
        return new_team
    except ServerSelectionTimeoutError as e:
        # connection to the db is down
        logger.error(f'DB connection timed out: {e}')
        raise DatabaseError(f'DB connection timed out: {e}')
    except ConnectionFailure as e:
        # connection to the db failed
        logger.error(f'DB connection failed: {e}')
        raise DatabaseError(f'DB connection failed: {e}')
    except NotUniqueError as e:
        logger.error(f'Team {team_info.get("team_id")} already exists: {e}')
        raise DatabaseError(f'Team already exists: {e}')
    except ValidationError as e:
        logger.error(f'Invalid team_info for {team_info.get("team_id")}: {e}')
        raise DatabaseError(f'Invalid team information: {e}')
    except Exception as e:
        logger.error(f'Error creating team document in MongoDB: {type(e).__module__}.{type(e).__name__}: {e}')
        raise DatabaseError(f'Unknown DB error occurred: {e}')


def get_team_info_from_db(**kwargs):
    teams: QuerySet[Team] = Team.objects(**kwargs)
    logger.debug(f'found {len(teams)} teams')
    if len(teams) == 1:
        logger.debug(f'Found team: {teams[0].team_id} with name: {teams[0].name}')
        return teams[0]
    return None


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--debug', action='store_true')
    parser.add_argument('--trace', action='store_true')

    # If REQUEST_METHOD exists, we are likely in a CGI environment
    if os.environ.get('REQUEST_METHOD'):
        args = parser.parse_args([])  # Pass an empty list to ignore CLI input
    else:
        args = parser.parse_args()

    configure_local_logger(debug=args.debug, trace=args.trace)
