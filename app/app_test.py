from app import team_ref_to_id, team_menu_logic, send_fb_messenger_response, send_fb_utility_message, send_telegram_message, notify_coach2
import argparse
import datetime
from db import get_coaches_to_notify
from exceptions import SessionError
from flask import Flask, session
from freezegun import freeze_time
import i18n
import logging
from models import Notification
from mongoengine import QuerySet
import os
import pytest
import secrets
from shared import configure_local_logger, path_handler, date_std_to_da
from trace_logging import TraceLogger

app = Flask(__name__)
app.secret_key = secrets.token_hex(16)  # app.secret_key required for flashing and sessions. os.urandom(24) could also be used
logger: TraceLogger = logging.getLogger(__name__)  # type: ignore[assignment]

ADMIN = os.environ.get('ADMIN')

_fake_paired_teams = [
    {
        'name': 'X25Team 11 & 12',
        'teams': [
            {'team_name': 'X25Team 11 - Trampolin - Begynder/Øvet fra skolestart til 8 år', 'ref': 'X25Team 11', 'id': 'abcde7'},
            {'team_name': 'X25Team 12 - Trampolin - Begynder/Øvet 8 år+', 'ref': 'X25Team 12', 'id': 'abcde9'},
        ],
    },
]


def test_team_ref_to_id(monkeypatch):
    # use the "app.PAIRED_TEAMS" because team_ref_to_id is imported from app.py and app.py imports PAIRED_TEAMS
    monkeypatch.setattr('app.PAIRED_TEAMS', _fake_paired_teams)

    # Set up a test session with mock teams
    with app.test_request_context():
        session['teams'] = [
            {'team_name': 'Team 11 - Trampolin - Begynder/Øvet fra skolestart til 8 år', 'id': 'abcdef6', 'ref': 'Team 11'},
            {'team_name': 'Team 12 - Trampolin - Begynder/Øvet 8 år+', 'id': 'abcdef8', 'ref': 'Team 12'},
            {'team_name': 'Team 13 - Trampolin - Mini 3 år til skolestart', 'id': 'abcdef0', 'ref': 'Team 13'},
            {'team_name': 'Team 14 - Trampolin - Mini 3 år til skolestart', 'id': 'abcdef2', 'ref': 'Team 14'},
            {'team_name': 'X25Team 11 - Trampolin - Begynder/Øvet fra skolestart til 8 år', 'id': 'abcde7', 'ref': 'X25Team 11'},
            {'team_name': 'X25Team 12 - Trampolin - Begynder/Øvet 8 år+', 'id': 'abcde9', 'ref': 'X25Team 12'},
            {'team_name': 'XTeam 13 - Trampolin - Mini 3 år til skolestart', 'id': 'abcde8', 'ref': 'X25Team 13'},
            {'team_name': 'XTeam 14 - Trampolin - Mini 3 år til skolestart', 'id': 'abcde1', 'ref': 'X25Team 14'},
        ]

        assert team_ref_to_id('X25Team 11') == 'abcde7'
        assert team_ref_to_id('X25Team 12') == 'abcde9'
        assert team_ref_to_id('X25Team 11 & 12') == 'abcde7,abcde9'
        assert team_ref_to_id('Team 11') == 'abcdef6'
        assert team_ref_to_id('Team 12') == 'abcdef8'
        with pytest.raises(SessionError):
            team_ref_to_id('Hold 13')
        with pytest.raises(SessionError):
            team_ref_to_id('Hold 14')


def test_team_menu_logic(monkeypatch):
    # use freezetime so there won't be an error if the test crosses midnight
    frozen = datetime.datetime.now().strftime('%Y-%m-%d')
    with freeze_time(frozen):
        # reference "shared.PAIRED_TEAMS" because team_menu_logic calls get_pairing, which is imported from shared, therefore uses the PAIRED_TEAMS defined in shared
        monkeypatch.setattr('shared.PAIRED_TEAMS', _fake_paired_teams)

        with app.test_request_context():
            session['teams'] = [
                {'team_name': 'Team 11 - Trampolin - Begynder/Øvet fra skolestart til 8 år', 'id': 'abcdef6', 'ref': 'Team 11'},
                {'team_name': 'Team 12 - Trampolin - Begynder/Øvet 8 år+', 'id': 'abcdef8', 'ref': 'Team 12'},
                {'team_name': 'Team 13 - Trampolin - Mini I år til skolestart', 'id': 'abcdef0', 'ref': 'Team 13'},
                {'team_name': 'Team 14 - Trampolin - Mini II år til skolestart', 'id': 'abcdef2', 'ref': 'Team 14'},
                {'team_name': 'X25Team 11 - Trampolin - Begynder/Øvet fra skolestart til 8 år', 'id': 'abcde7', 'ref': 'X25Team 11'},
                {'team_name': 'X25Team 12 - Trampolin - Begynder/Øvet 8 år+', 'id': 'abcde9', 'ref': 'X25Team 12'},
                {'team_name': 'XTeam 13 - Trampolin - Mini I år til skolestart', 'id': 'abcde8', 'ref': 'X25Team 13'},
                {'team_name': 'XTeam 14 - Trampolin - Mini II år til skolestart', 'id': 'abcde1', 'ref': 'X25Team 14'},
            ]

            team_names, date = team_menu_logic()
            assert team_names == ['Team 11', 'Team 12', 'Team 13', 'Team 14', 'X25Team 11 & 12', 'X25Team 13', 'X25Team 14']
            assert date == frozen


@pytest.mark.skipif(os.environ.get('RUN_LIVE_TESTS') != '1', reason='Set RUN_LIVE_TESTS=1 to run this live smoke test locally')
def test_send_fb_messenger_utility_message_signout_live():
    # there is a mock version of this test below

    ran_test = False
    coaches: QuerySet | None = get_coaches_to_notify('1040490')
    if coaches:
        team_ref = 'Test team'
        date = '2026-01-01'
        da_date = date_std_to_da(date)
        if ADMIN:
            for coach in coaches:
                if coach.username == ADMIN and coach.service == 'facebook':
                    ran_test = True
                    i18n.load_path.append(str(path_handler('i18n_path')))
                    i18n.set('file_format', 'json')
                    i18n.set('skip_locale_root_data', True)
                    i18n.set('fallback', 'en')

                    # test English
                    comment = 'other: test comment (send_fb_utility_message)'
                    assert (
                        send_fb_utility_message(
                            psid=coach.sid,
                            action='signout',
                            name='Test User',
                            event_type_l10n=i18n.t('app.practice', locale='en'),
                            team_or_event=team_ref,
                            date=da_date,
                            comment=comment,
                            lingua='en',
                        )
                        is True
                    )

                    # test Danish
                    comment = 'andet: test kommentar (send_fb_utility_message)'
                    assert (
                        send_fb_utility_message(
                            psid=coach.sid,
                            action='signout',
                            name='Test User',
                            event_type_l10n=i18n.t('app.practice', locale='da'),
                            team_or_event=team_ref,
                            date=da_date,
                            comment=comment,
                            lingua='da',
                        )
                        is True
                    )

    if not ran_test:
        pytest.skip('no coaches to notify for team, test skipped')


@pytest.mark.skipif(os.environ.get('RUN_LIVE_TESTS') != '1', reason='Set RUN_LIVE_TESTS=1 to run this live smoke test locally')
def test_send_fb_messenger_utility_message_signup_live():
    ran_test = False
    coaches: QuerySet | None = get_coaches_to_notify('1040490')
    if coaches:
        team_ref = 'Test team'
        date = '2026-01-01'
        da_date = date_std_to_da(date)
        if ADMIN:
            for coach in coaches:
                if coach.username == ADMIN and coach.service == 'facebook':
                    ran_test = True
                    i18n.load_path.append(str(path_handler('i18n_path')))
                    i18n.set('file_format', 'json')
                    i18n.set('skip_locale_root_data', True)
                    i18n.set('fallback', 'en')

                    # test English
                    assert (
                        send_fb_utility_message(
                            psid=coach.sid,
                            action='signup',
                            name='Test User',
                            event_type_l10n=i18n.t('app.practice', locale='en'),
                            team_or_event=team_ref,
                            date=da_date,
                            comment='',
                            lingua='en',
                        )
                        is True
                    )

                    # test Danish
                    assert (
                        send_fb_utility_message(
                            psid=coach.sid,
                            action='signup',
                            name='Test User',
                            event_type_l10n=i18n.t('app.practice', locale='da'),
                            team_or_event=team_ref,
                            date=da_date,
                            comment='',
                            lingua='da',
                        )
                        is True
                    )

    if not ran_test:
        pytest.skip('no coaches to notify for team, test skipped')


@pytest.mark.skipif(os.environ.get('RUN_LIVE_TESTS') != '1', reason='Set RUN_LIVE_TESTS=1 to run this live smoke test locally')
def test_notify_coach_v2_live():
    ran_test = False
    coaches: QuerySet | None = get_coaches_to_notify('1044963')
    if coaches:
        name = 'App Testuser'
        team_ref = 'TestTeam 1'
        date = '2026-01-01'
        da_date = date_std_to_da(date)
        i18n.load_path.append(str(path_handler('i18n_path')))
        i18n.set('file_format', 'json')
        i18n.set('skip_locale_root_data', True)
        i18n.set('fallback', 'en')
        if ADMIN:
            for coach in coaches:
                if coach.username == ADMIN:
                    comment = i18n.t('app.other', locale=coach.lingua) + ': test comment (notify_coach2)'
                    ran_test = True
                    event_type_l10n = i18n.t('app.practice', locale=coach.lingua)
                    assert (
                        notify_coach2(
                            coach,
                            notification_text=i18n.t(
                                'app.signout_notification',
                                name=name,
                                event_type=event_type_l10n,
                                team_or_event=team_ref,
                                date=date,
                                comment=comment,
                                locale=coach.lingua,
                            ),
                            action='signout',
                            name=name,
                            event_type_l10n=event_type_l10n,
                            team_or_event=team_ref,
                            date=da_date,
                            comment=comment,
                        )
                        == 1
                    )

                    assert (
                        notify_coach2(
                            coach,
                            notification_text=i18n.t(
                                'app.signup_notification',
                                name=name,
                                event_type=event_type_l10n,
                                team_or_event=team_ref,
                                date=da_date,
                                locale=coach.lingua,
                            ),
                            action='signup',
                            name=name,
                            event_type_l10n=event_type_l10n,
                            team_or_event=team_ref,
                            date=da_date,
                    )
                    == 1
                )

    if not ran_test:
        pytest.skip('no coaches to notify for team, test skipped')


def make_mock_fb_notification(username='the_best_coach', sid='fake-psid-123', club_id='662', team_ids=None, lingua='da'):
    return Notification(
        username=username,
        sid=sid,
        club_id=club_id,
        team_ids=team_ids or ['team007'],
        lingua=lingua,
    )


def test_send_fb_messenger_response_calls_api_correctly(mocker):
    mock_post = mocker.patch('requests.post')  # or whatever your send call uses
    mock_post.return_value.status_code = 200
    mock_post.return_value.json.return_value = {'recipient_id': '123', 'message_id': 'mid.456'}

    coach = make_mock_fb_notification(sid='fake-psid-123', lingua='da')  # fixture, not live DB
    event_type = i18n.t('app.practice', locale=coach.lingua)

    send_fb_messenger_response(
        coach.sid,
        i18n.t(
            'app.signout_notification',
            name='Test User',
            event_type=event_type,
            team='TestHold 999',
            date='27-09-2026',
            comment='Skole',
            locale=coach.lingua,
        ),
    )

    mock_post.assert_called_once()
    called_url, called_kwargs = mock_post.call_args
    assert 'fake-psid-123' in called_kwargs['json']['recipient']['id']
    # assert message text, template shape, etc.


def test_send_fb_messenger_utility_message_calls_api_correctly(mocker):
    mock_post = mocker.patch('requests.post')  # or whatever your send call uses
    mock_post.return_value.status_code = 200
    mock_post.return_value.json.return_value = {'recipient_id': '123', 'message_id': 'mid.456'}

    coach = make_mock_fb_notification(sid='fake-psid-123', lingua='da')  # fixture, not live DB

    assert (
        send_fb_utility_message(
            psid=coach.sid,
            action='practice',
            name='Test User',
            event_type_l10n=i18n.t('app.practice', locale=coach.lingua),
            team_or_event='TestHold 999',
            date='27-09-2026',
            comment='Skole',
            lingua=coach.lingua,
        )
        is True
    )

    mock_post.assert_called_once()
    called_url, called_kwargs = mock_post.call_args
    assert 'fake-psid-123' in called_kwargs['json']['recipient']['id']
    # assert message text, template shape, etc.


@pytest.mark.skipif(os.environ.get('RUN_LIVE_TESTS') != '1', reason='Set RUN_LIVE_TESTS=1 to run this live smoke test locally')
def test_send_telegram_message_live():
    # TODO there is a mock version of this test below
    from db import get_coaches_to_notify

    ran_test = False
    coaches: QuerySet | None = get_coaches_to_notify('1044963')
    if coaches:
        team_ref = 'X25Team 11'
        comment = 'other - test comment'
        date = '2026-01-01'
        da_date = date_std_to_da(date)
        if ADMIN:
            for coach in coaches:
                if coach.username == ADMIN and coach.service == 'telegram':
                    ran_test = True
                    i18n.load_path.append(str(path_handler('i18n_path')))
                    i18n.set('file_format', 'json')
                    i18n.set('skip_locale_root_data', True)
                    i18n.set('fallback', 'en')

                    # test English
                    event_type = i18n.t('app.practice', locale='en')
                    comment = 'other: test comment (send_telegram_message)'
                    r = send_telegram_message(
                        coach.sid,
                        i18n.t(
                            'app.signout_notification',
                            name='Test User',
                            event_type=event_type,
                            team_or_event=team_ref,
                            date=da_date,
                            comment=comment,
                            locale='en',
                        ),
                    )
                    assert r is True

                    # test Danish
                    event_type = i18n.t('app.practice', locale='da')
                    comment = 'andet: test kommentar (send_telegram_message)'
                    r = send_telegram_message(
                        coach.sid,
                        i18n.t(
                            'app.signout_notification',
                            name='Test User',
                            event_type=event_type,
                            team_or_event=team_ref,
                            date=da_date,
                            comment=comment,
                            locale='da',
                        ),
                    )
                    assert r is True
                    break
    if not ran_test:
        pytest.skip('no coaches to notify for team, test skipped')


def test_get_practices():
    pass


def test_get_trampoline_events():
    # TODO implement this test
    pass


def test_all_practice_days_in_season():
    # all_practice_days_in_season('Hold 38')
    pass


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--debug', action='store_true', help='enable debug logging')
    parser.add_argument('--trace', action='store_true', help='enable trace logging')
    args = parser.parse_args()
    configure_local_logger(debug=args.debug, trace=args.trace)
