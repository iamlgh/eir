#!/usr/local/bin/python
# -*- coding: UTF-8 -*-

import argparse
from datetime import datetime, UTC, date, timedelta, timezone
from db import (
    get_signout_list_from_db,
    add_signout_to_db,
    get_member_signouts_from_db,
    connect_to_db,
    get_signout_by_id,
    update_signout_by_id,
    get_fb_notifications,
    get_fb_notifications_by_psid,
    add_fb_notification,
    update_teams_for_fb_notifications,
    save_pending_link,
    get_coaches_to_notify,
    delete_fb_messenger_link_for_coach,
)
from dotenv import load_dotenv
from exceptions import DatabaseError
from freezegun import freeze_time
import logging
from models import (
    Signout,
    Notification,
    PendingLink,
    Team,
    # Trampolinist,
)
from mongoengine import disconnect, QuerySet
import os
from pymongo.errors import ServerSelectionTimeoutError  # , ConnectionFailure
import pytest
from shared import configure_local_logger
from trace_logging import TraceLogger
from unittest.mock import MagicMock, patch


load_dotenv()  # This loads variables from .env into os.environ
logger: TraceLogger = logging.getLogger(__name__)  # type: ignore[assignment]
# logger.addHandler(logging.NullHandler())


# --- reuse your factory ---
def make_mock_signout(**overrides):
    mock = MagicMock()
    mock.club_id = 'clb'
    mock.dept_id = 'dept'
    mock.team_id = 'team06'
    mock.member_id = 'mem007'
    mock.date = datetime(2024, 10, 1, tzinfo=UTC)
    mock.status = True
    mock.reason = 'sick'
    mock.updated_at = datetime(2024, 10, 1, 9, 0, 0, tzinfo=UTC)
    mock.updated_by = 'coach_99'
    for key, value in overrides.items():
        setattr(mock, key, value)
    return mock


@patch('db.Signout.objects')  # mock the query
def test_get_signout_list_returns_docs(mock_objects):
    # arrange
    fake_docs = [
        make_mock_signout(member_id='mem007', reason='sick'),
        make_mock_signout(member_id='mem008', reason='vacation'),
    ]
    mock_objects.return_value = fake_docs

    # act
    result = get_signout_list_from_db(datetime(2024, 10, 1), 'clb', 'dept', 'team06')

    # assert
    assert result == fake_docs
    assert len(result) == 2
    mock_objects.assert_called_once_with(club_id='clb', dept_id='dept', team_id='team06', date=datetime(2024, 10, 1), status=True)


@patch('db.Signout.objects')
def test_get_signout_list_db_query_fails(mock_objects):
    # mock passing an invalid club id to trigger the exception
    mock_objects.side_effect = Exception('Query blew up')
    with pytest.raises(DatabaseError) as exc_info:
        get_signout_list_from_db(datetime(2024, 10, 1), 123, 'dept')

    # Verify the error message inside the exception
    assert 'Query blew up' in str(exc_info.value)


@patch('db.Signout.objects')
def test_get_signout_list_connect_times_out(mock_objects):
    # return an exception
    mock_objects.side_effect = ServerSelectionTimeoutError("Can't reach MongoDB")

    # Tell pytest to expect the exception as a DatabaseError
    with pytest.raises(DatabaseError) as exc_info:
        get_signout_list_from_db('2026-10-01', '123', 'dept')

    # Verify the error message inside the exception
    assert "Can't reach MongoDB" in str(exc_info.value)


def test_get_signout_list_connect_fails():
    # intentionally pull the plug right before the call
    disconnect(alias='default')

    # it should raise a DatabaseError, because there is no DB connection
    try:
        with pytest.raises(DatabaseError):
            get_signout_list_from_db('2026-04-16', '123', 'dept')
    finally:
        # 3. Plug it back in so the next tests don't break
        connect_to_db(alias='default')


@patch('db.Signout.objects')
def test_get_signout_list_empty(mock_objects):
    mock_objects.return_value = []
    result = get_signout_list_from_db(datetime(2024, 10, 1), '123', 'dept')
    assert result == []


def test_get_signout_list_valueerror():
    with pytest.raises(ValueError):
        get_signout_list_from_db('04-29-2026', 'clb', 'dept')


def test_add_signout_to_db():
    all = Signout.objects(club_id='clb')
    if all:
        all.delete()
    updated_by = 'test'
    result = add_signout_to_db('2026-04-16', 'mem005', 'clb', 'dept', 'team04', updated_by, True, 'sick', 'har hovedpine')
    assert type(result) is Signout
    member5 = Signout.objects(date=datetime(2026, 4, 16), member_id='mem005', club_id='clb', dept_id='dept', team_id='team04', status=True)
    assert len(member5) == 1

    result = add_signout_to_db('2026-04-15', 'mem1001', 'clb', 'dept', 'team07', updated_by, True, 'other', 'har ingen kørekort')
    assert type(result) is Signout
    member1 = Signout.objects(date=datetime(2026, 4, 15), member_id='mem1001', club_id='clb', dept_id='dept', team_id='team07', status=True)
    assert len(member1) == 1

    all_15_4 = Signout.objects(date=datetime(2026, 4, 15), club_id='clb', dept_id='dept', team_id='team07', status=True)
    assert len(all_15_4) == 1

    dates = Signout.objects.scalar('date').order_by('date')
    unique_dates = list(set(dates))
    assert len(unique_dates) >= 2

    result = add_signout_to_db('2026-04-29', 'mem1009', 'clb', 'dept', 'team07', updated_by, True, 'school', '')
    assert type(result) is Signout
    member9 = Signout.objects(date=datetime(2026, 4, 29), member_id='mem1009', club_id='clb', dept_id='dept', team_id='team07', status=True)
    assert len(member9) == 1

    result = add_signout_to_db('2026-04-29', 'mem1004', 'clb', 'dept', 'team09', updated_by, True, 'sick', '')
    assert type(result) is Signout
    member4 = Signout.objects(date=datetime(2026, 4, 29), member_id='mem1004', club_id='clb', dept_id='dept', team_id='team09', status=True)
    assert len(member4) == 1

    all_29_4 = Signout.objects(date=datetime(2026, 4, 29), club_id='clb', dept_id='dept', status=True)
    assert len(all_29_4) == 2


def test_get_signout_list_from_db():
    """NB: This is dependent on data added by test_add_signout_to_db"""
    results = get_signout_list_from_db('2026-04-07', 'clb', 'dept')
    assert len(results) == 0  # To check if empty
    assert results == []
    results = get_signout_list_from_db('2026-04-15', 'clb', 'dept')
    assert len(results) == 1
    for r in results:
        assert r.status is True
        assert r.reason == 'other: har ingen kørekort'
        assert 'mem' in r.member_id
    results = get_signout_list_from_db('2026-04-15', 'clb', 'dept', 'team07')
    assert len(results) == 1
    results = get_signout_list_from_db('2026-04-15', 'clb', 'dept', 'team09')
    assert len(results) == 0
    results = get_signout_list_from_db('2026-04-16', 'clb', 'dept')


def add_years(d, years):
    """Return a date that's `years` years after the date (or datetime)
    object `d`. Return the same calendar date (month and day) in the
    destination year, if it exists, otherwise use the following day
    (thus changing February 29 to March 1).

    """
    try:
        return d.replace(year=d.year + years)
    except ValueError:
        return d + (date(d.year + years, 1, 1) - date(d.year, 1, 1))


def test_get_member_signouts_from_db():
    """NB: This is dependent on data added by test_add_signout_to_db"""
    results = get_member_signouts_from_db('mem1001', '2026-04-15', 'clb', 'dept')  # member_id, date, club_id, dept_id
    assert isinstance(results, list)
    # assert len(results) == 1
    for r in results:
        assert 'mem' in r.member_id
        logger.debug(f'Date: {r.date}')
        assert r.date == datetime(2026, 4, 15, 0, 0)
    results = get_member_signouts_from_db('mem1001', add_years(datetime.now(), 1), 'clb', 'dept')  # member_id, date, club_id, dept_id
    assert len(results) == 0


def test_get_fb_notifications():
    result = get_fb_notifications('nonuser', 'clb')
    assert isinstance(result, list)
    assert len(result) == 0


@pytest.mark.skipif(
    not os.environ.get('RUN_LIVE_TESTS'),
    reason='Set RUN_LIVE_TESTS=1 to run this live demo readiness test locally'
    if 'mongodb.net' in os.environ.get('MONGO_URI', '')
    else 'Set RUN_LIVE_TESTS=1 to run this live smoke test, set MONGO_URI to the production db to run this as a demo readiness test',
)
def test_get_signout_by_id():
    doc_id = '6a3506a32cd6a8099b7401cf'
    if 'mongodb.net' in os.environ.get('MONGO_URI', ''):
        doc_id = '6a3385aade832f9e3a3f6ccf'
    logger.debug(f'Querying {doc_id}')
    result = get_signout_by_id(doc_id)
    for so in result:
        assert isinstance(so, Signout)
        so.date = so.date.strftime('%Y-%m-%d')
        logger.debug(f'Found signout: {so.member_id} signed out on {so.date} for reason: {so.reason}')
    assert len(result) == 1


@pytest.mark.skipif(
    not os.environ.get('RUN_LIVE_TESTS'),
    reason='Set RUN_LIVE_TESTS=1 to run this live demo readiness test locally'
    if 'mongodb.net' in os.environ.get('MONGO_URI', '')
    else 'Set RUN_LIVE_TESTS=1 to run this live smoke test, set MONGO_URI to the production db to run this as a demo readiness test',
)
def test_update_signout_by_id_sign_in():
    doc_id = '6a3506a32cd6a8099b7401cf'
    if 'mongodb.net' in os.environ.get('MONGO_URI', ''):
        doc_id = '6a3385aade832f9e3a3f6ccf'
    logger.debug(f'querying {doc_id}')
    result = update_signout_by_id(doc_id)
    assert isinstance(result, Signout)
    logger.info(f'Sign-in: {result}')


@pytest.mark.skipif(
    not os.environ.get('RUN_LIVE_TESTS'),
    reason='Set RUN_LIVE_TESTS=1 to run this live demo readiness test locally'
    if 'mongodb.net' in os.environ.get('MONGO_URI', '')
    else 'Set RUN_LIVE_TESTS=1 to run this live smoke test, set MONGO_URI to the production db to run this as a demo readiness test',
)
def test_update_signout_by_id_sign_out_live():
    doc_id = '6a3506a32cd6a8099b7401cf'
    if 'mongodb.net' in os.environ.get('MONGO_URI', ''):
        doc_id = '6a3385aade832f9e3a3f6ccf'
    logger.debug(f'Querying {doc_id}')
    result = update_signout_by_id(doc_id, 'test', True, 'other: dovenskab')
    assert isinstance(result, Signout)
    logger.debug(f'Sign-out: {result}')
    print(f'Sign-out: {result}')


def test_add_fb_notifications_to_db():
    Notification.objects(sid='1', service='facebook', username='papaya', club_id='clb').delete()
    test_user_notification = add_fb_notification('1', 'papaya', 'clb', ['team07', 'team09'])
    assert isinstance(test_user_notification, Notification)
    assert test_user_notification.team_ids == ['team07', 'team09']
    assert test_user_notification.delete() is None


def test_get_coaches_to_notify():
    # Add a test notification to the database
    Notification.objects(sid='1', service='facebook', username='papaya', club_id='clb').delete()
    test_user_notification = add_fb_notification('1', 'papaya', 'clb', ['team07', 'team09'])
    assert isinstance(test_user_notification, Notification)
    assert test_user_notification.team_ids == ['team07', 'team09']

    # Call the function to get coaches to notify
    coaches: QuerySet | None = get_coaches_to_notify('team07')
    assert isinstance(coaches, QuerySet)
    success = False
    assert len(coaches) > 0
    for coach in coaches:
        if coach.username == 'papaya' and coach.sid == '1':
            success = True
            break
    assert success is True

    # Clean up after the test
    assert test_user_notification.delete() is None


@pytest.mark.skipif(not os.environ.get('RUN_LIVE_TESTS'), reason='Set RUN_LIVE_TESTS=1 to run this live smoke test locally')
def test_get_coaches_to_notify_tg_live():
    # Call the function to get coaches to notify
    coaches: QuerySet | None = get_coaches_to_notify('1044963')
    assert isinstance(coaches, QuerySet)
    success = False
    assert len(coaches) > 0
    if os.environ.get('ADMIN'):
        for coach in coaches:
            if coach.username == os.environ.get('ADMIN') and coach.service == 'telegram' and coach.sid:
                success = True
                break
        assert success is True
    else:
        pytest.skip('no ADMIN to notify, test skipped')


def test_get_coaches_to_notify_no_coaches():
    # Ensure there are no notifications for a specific team
    Notification.objects(team_ids__contains='team09').delete()
    coaches: QuerySet | None = get_coaches_to_notify('team09')
    assert (coaches.count() if coaches else 0) == 0
    assert coaches.count() == 0  # type: ignore[union-attr]


def test_save_pending_link_fb():
    PendingLink.objects(sid='2').delete()
    code = save_pending_link('2', '345676', 'Tesla Testersen', 'facebook')
    assert code == '345676'
    assert PendingLink.objects(sid='2', service='facebook').delete() == 1


def test_save_pending_link_telegram():
    PendingLink.objects(sid='3').delete()
    code = save_pending_link('3', '345677', 'Tesla Testersen', 'telegram')
    assert code == '345677'
    assert PendingLink.objects(sid='3', service='telegram').delete() == 1


def test_save_pending_link_telegram_en():
    PendingLink.objects(sid='3').delete()
    code = save_pending_link('3', '345678', 'Tesla Testersen', 'telegram', 'en')
    assert code == '345678'
    assert PendingLink.objects(sid='3', service='telegram').delete() == 1


def test_get_fb_notifications_by_psid():
    # Add a test notification to the database
    Notification.objects(sid='1', service='facebook', username='papaya', club_id='clb').delete()
    test_user_notification = add_fb_notification('1', 'papaya', 'clb', ['team07', 'team09'])
    assert isinstance(test_user_notification, Notification)
    assert test_user_notification.team_ids == ['team07', 'team09']
    # Call the function to get notifications by PSID
    notifications: list[Notification] = get_fb_notifications_by_psid('1')
    assert isinstance(notifications, list)
    success = False
    assert len(notifications) > 0
    for notification in notifications:
        if notification.username == 'papaya' and notification.sid == '1':
            success = True
            break
    assert success is True
    # Clean up after the test
    assert test_user_notification.delete() is None


def test_update_teams_for_fb_notifications():
    username: str = 'papaya_test_user'  # everything for this username can be deleted, it is only used for test
    Notification.objects(username=username).delete()
    club_id: str = 'clb'
    old_team_ids: list[str] = ['team06', 'team08', 'team00', 'team02', 'team07', 'team09', 'team01']
    test_notification = add_fb_notification('1', username, club_id, old_team_ids)
    new_team_ids = ['team03', 'team05', 'team00', 'team02', 'team07', 'team09', 'team01', 'team04']
    updated_doc: dict = update_teams_for_fb_notifications(username, club_id, new_team_ids)
    assert isinstance(updated_doc, dict)
    assert updated_doc['team_ids'] == new_team_ids
    assert (test_notification.delete() if test_notification else None) is None


def test_update_teams_raises_when_no_match():
    with pytest.raises(Exception, match='expected 1'):
        update_teams_for_fb_notifications('nonexistent', 'clb', [])


def test_delete_fb_messenger_link_for_coach():
    username: str = 'papaya_test_user'  # everything for this username can be deleted, it is only used for test
    club_id: str = 'clb'
    team_ids: list[str] = ['team06', 'team08', 'team00', 'team02', 'team07', 'team09', 'team01']
    add_fb_notification('1', username, club_id, team_ids)
    record_count = delete_fb_messenger_link_for_coach(username=username, club_id=club_id)
    assert record_count >= 1


def test_get_team_info_from_db():
    ref = 'Hold 64'
    teams: QuerySet[Team] = Team.objects(ref=ref)
    for team_info in teams:
        assert isinstance(team_info, Team)
        if team_info.ref == ref and team_info.schedule == [{'day': 6, 'time': '11:00-12:00', 'place': 'GIC hal 4'}]:
            team_info.schedule = [{'day': 6, 'time': '11:00-12:00', 'place': 'GIC hal 4', 'weeks': 'odd'}]
            team_info.save()
        assert team_info.schedule == [{'day': 6, 'time': '11:00-12:00', 'place': 'GIC hal 4', 'weeks': 'odd'}]
    ref = 'Hold 65'
    teams = Team.objects(ref=ref)
    for team_info in teams:
        assert isinstance(team_info, Team)
        assert team_info.ref == 'Hold 65'
        if team_info.ref == ref and team_info.schedule == [{'day': 6, 'time': '12:00-13:00', 'place': 'GIC hal 4'}]:
            team_info.schedule = [{'day': 6, 'time': '12:00-13:00', 'place': 'GIC hal 4', 'weeks': 'odd'}]
            team_info.save()
        assert team_info.schedule == [{'day': 6, 'time': '12:00-13:00', 'place': 'GIC hal 4', 'weeks': 'odd'}]


def test_save_pending_link_blocks_after_limit():
    sid, service = 'test-sid-1', 'telegram'
    PendingLink.objects(sid=sid, service=service).delete()  # clean slate

    for i in range(3):
        code = save_pending_link(sid, f'{i}00000', f'name{i}', service)
        assert code == f'{i}00000'

    assert PendingLink.objects(sid=sid, service=service).count() == 3

    result = save_pending_link(sid, '999999', 'blocked-name', service)
    assert result == 'app.try_again_later'

    # confirm the 4th attempt didn't persist
    assert PendingLink.objects(sid=sid, service=service).count() == 3

    PendingLink.objects(sid=sid, service=service).delete()  # cleanup


def test_save_pending_link_allows_again_after_oldest_expires():
    sid, service = 'test-sid-2', 'telegram'
    PendingLink.objects(sid=sid, service=service).delete()

    frozen_start = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    with freeze_time(frozen_start - timedelta(minutes=11)):
        save_pending_link(sid, '111111', 'oldest', service)

    with freeze_time(frozen_start):
        save_pending_link(sid, '222222', 'second', service)
        save_pending_link(sid, '333333', 'third', service)

        # simulate what MongoDB's TTL reaper would eventually do
        expired_cutoff = frozen_start - timedelta(seconds=600)
        PendingLink.objects(sid=sid, service=service, created_at__lte=expired_cutoff).delete()

        assert PendingLink.objects(sid=sid, service=service).count() == 2

        code = save_pending_link(sid, '444444', 'fourth', service)
        assert code == '444444'
        assert PendingLink.objects(sid=sid, service=service).count() == 3

    PendingLink.objects(sid=sid, service=service).delete()  # cleanup


def test_save_pending_link_limit_is_scoped_per_sid_and_service():
    PendingLink.objects(sid='test-sid-3').delete()
    PendingLink.objects(sid='test-sid-4').delete()

    for i in range(3):
        save_pending_link('test-sid-3', f'{i}11111', 'name', 'telegram')

    result = save_pending_link('test-sid-3', '999999', 'name', 'telegram')
    assert result == 'app.try_again_later'

    code = save_pending_link('test-sid-4', '555555', 'name', 'telegram')
    assert code == '555555'

    code = save_pending_link('test-sid-3', '666666', 'name', 'facebook')
    assert code == '666666'

    PendingLink.objects(sid='test-sid-3').delete()
    PendingLink.objects(sid='test-sid-4').delete()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--debug', action='store_true', help='enable debug logging')
    parser.add_argument('--trace', action='store_true', help='enable trace logging')
    args = parser.parse_args()
    configure_local_logger(debug=args.debug, trace=args.trace)
