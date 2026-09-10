#!/usr/local/bin/python

# import logging
from datetime import date
from freezegun import freeze_time
from shared import current_season, get_paired_name, get_paired_team_ids, get_paired_name_from_team_id, PairedTeam, TeamData, PAIRED_TEAMS


@freeze_time('2026-09-01')
def test_current_season_on_september_first():
    assert current_season() == '26/27'


@freeze_time('2026-08-31')
def test_current_season_day_before_september():
    assert current_season() == '25/26'


@freeze_time('2026-12-31')
def test_current_season_end_of_year():
    assert current_season() == '26/27'


@freeze_time('2027-01-01')
def test_current_season_start_of_year():
    assert current_season() == '26/27'


@freeze_time('2026-01-01')
def test_current_season_new_year_before_september():
    assert current_season() == '25/26'


def test_current_season():
    this_year_short: str = str(date.today().year)[2:]
    expected: str = (
        f'{this_year_short}/{(int(this_year_short) + 1)}' if date.today().month >= 9 else f'{int(this_year_short) - 1}/{this_year_short}'
    )  # Adjust this based on the current date when you run the test
    assert current_season() == expected


def test_get_paired_name():
    paired_teams: list[PairedTeam] = [{'name': 'Team 1 & 2', 'teams': [{'ref': 'Team 1', 'id': '123456'}, {'ref': 'Team 2', 'id': '123457'}]}]
    team: TeamData = {'ref': 'Team 1', 'id': '123456'}
    assert get_paired_name(team, paired_teams) == 'Team 1 & 2'


def test_get_paired_name_none():
    paired_teams: list[PairedTeam] = [{'name': 'Team 1 & 2', 'teams': [{'ref': 'Team 1', 'id': '123456'}, {'ref': 'Team 2', 'id': '123457'}]}]
    team: TeamData = {'ref': 'Team 3', 'id': '1234568'}
    assert get_paired_name(team, paired_teams) is None


def test_paired_teams_data_is_well_formed():
    seen_ids: set[str] = set()
    for entry in PAIRED_TEAMS:
        assert entry.get('name'), f'Entry missing name: {entry}'
        teams: list[TeamData] = entry.get('teams', [])
        assert teams, f'Entry has no teams: {entry}'
        for team in teams:
            assert team.get('ref'), f'Team missing ref: {team}'
            assert team.get('id'), f'Team missing id: {team}'
            assert team.get('team_name'), f'Team missing name: {team}'
            assert team['id'] not in seen_ids, f'Duplicate id: {team["id"]}'
            seen_ids.add(team['id'])


def test_get_paired_team_ids():
    paired_teams: list[PairedTeam] = [{'name': 'Team 1 & 2', 'teams': [{'ref': 'Team 1', 'id': '123456'}, {'ref': 'Team 2', 'id': '123457'}]}]
    assert get_paired_team_ids(paired_teams[0]['name'], paired_teams) == '123456,123457'
    assert get_paired_team_ids('Team A & B', paired_teams) is None


def test_get_paired_name_from_team_id():
    paired_teams: list[PairedTeam] = [{'name': 'Team 1 & 2', 'teams': [{'ref': 'Team 1', 'id': '123456'}, {'ref': 'Team 2', 'id': '123457'}]}]
    team_id: str = '123456'
    assert get_paired_name_from_team_id(team_id, paired_teams) == 'Team 1 & 2'
    team_id = '123457'
    assert get_paired_name_from_team_id(team_id, paired_teams) == 'Team 1 & 2'
    team_id = '999999'
    assert get_paired_name_from_team_id(team_id, paired_teams) is None
    team_id = '999999'
    assert get_paired_name_from_team_id(team_id) is None
