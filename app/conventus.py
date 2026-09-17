#!/usr/local/bin/python
# -*- coding: UTF-8 -*-
"""
Contains functions for logging into Conventus and handling basic Conventus functions that don't require specific profile setup
"""

import argparse
from bs4 import BeautifulSoup, ResultSet, Tag  # https://pypi.org/project/beautifulsoup4/
from datetime import datetime, timedelta
from db import get_signout_list_from_db
import csv
from exceptions import ParsingError, SiteError, DatabaseError, SessionError
from flask.wrappers import Request
import i18n
import io
import logging
import models
import os
import re
import requests  # https://pypi.org/project/requests/
from requests.structures import CaseInsensitiveDict
from shared import (
    date_std_to_da,
    date_da_to_std,
    path_handler,
    configure_local_logger,
    localize_reason,
    TeamData,
    LoginResult,
    REQUEST_TIMEOUT,
    CONVENTUS_CLUB_ID,
    CONVENTUS_DEPT_NAME,
    CONVENTUS_DEPT_ID,
    CONVENTUS_DEPT_REPORT_ID,
)
from trace_logging import setup_trace_level, TraceLogger
from typing import Any
from urllib.parse import urlencode
from werkzeug.datastructures import Headers
from zoneinfo import ZoneInfo


# global
# script config
HEADERS: CaseInsensitiveDict = CaseInsensitiveDict(
    {
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
        'Accept-Language': 'da,en-US;q=0.9,en;q=0.8',
        'Cache-Control': 'max-age=0',
        'Connection': 'keep-alive',
        'Content-Type': 'application/x-www-form-urlencoded',
        'Host': 'py-tra',
        'Origin': 'https://www.conventus.dk',
        'Referer': 'https://www.conventus.dk/',
        'Sec-Ch-Ua': '"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"',
        'Sec-Ch-Ua-Mobile': '?0',
        'Sec-Ch-Ua-Platform': '"Windows"',
        'Sec-Fetch-Site': 'same-origin',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-User': '?1',
        'Sec-Fetch-Dest': 'document',
        'Upgrade-Insecure-Requests': '1',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36',
    }
)


# logger
logger: TraceLogger = logging.getLogger(__name__)  # type: ignore[assignment]
setup_trace_level()


def log_redirect(response) -> str | None:
    if 300 <= response.status_code < 400:
        redirect_url = response.headers.get('Location', 'unknown')
        logger.debug(f'Location: {redirect_url}')
        return redirect_url
    return None


def get_home_page(cookies: requests.cookies.RequestsCookieJar, headers: CaseInsensitiveDict = HEADERS) -> bytes:
    response: requests.Response = run_login_loggedin_get(cookies, headers)
    logger.debug(f'Login home response code: {response.status_code}')
    ##logger.trace(response.content)
    if not response.ok:
        logger.error('Reading home page failed')
        raise SessionError('Reading home page failed')
    # log_redirect('Login complete', response)
    redirect: str | None = log_redirect(response)
    if redirect:
        logger.error(f'Unexpected redirect when reading home page: {redirect}')
        raise SessionError(f'Unexpected redirect when reading home page: {redirect}')
    else:
        return response.content


def coach_login(username: str, password: str, clubname: str, headers: CaseInsensitiveDict = HEADERS) -> LoginResult:
    """Authenticate a coach and return login state plus cookies and home page on success."""
    logger.info('Logging into Conventus as coach')
    login_success: bool = False
    hdrs: CaseInsensitiveDict = header_passthru(headers)
    session: requests.Response = requests.post(
        'https://www.conventus.dk/login/checkuser.php?form=true',
        data={'brugernavn': username, 'password': password},
        headers=hdrs,
        allow_redirects=False,
        timeout=REQUEST_TIMEOUT,
    )
    redirect: str | None = log_redirect(session)

    if session.ok and redirect and 'before_login/husk_mig.php' in redirect:
        logger.trace('session established and redirected to remember me step')
        logger.trace(session.cookies)
        response: requests.Response = requests.get(
            'https://www.conventus.dk/before_login/husk_mig.php',
            headers=hdrs,
            cookies=session.cookies,
            allow_redirects=False,
            timeout=REQUEST_TIMEOUT,
        )
        logger.trace(response.content)
        redirect = log_redirect(response)
    else:
        logger.error(f'Session start response code: {session.status_code}')
        return {'login_success': login_success}

    logger.debug(f'Remember me response code: {response.status_code}')
    if response.ok and not redirect:
        logger.trace('remember me skipped by script and redirected to choose club')
        response = requests.get(
            'https://www.conventus.dk/before_login/choose_forening.php',
            headers=hdrs,
            cookies=session.cookies,
            allow_redirects=False,
            timeout=REQUEST_TIMEOUT,
        )
        # logger.trace(response.content)
        logger.trace('choose association redirect:')
        redirect = log_redirect(response)
    else:
        logger.error('Remember me failed')
        return {'login_success': login_success}

    if response.ok:
        if not redirect:
            soup: BeautifulSoup = BeautifulSoup(response.content or '', features='html.parser')
            logger.trace('choose association:' + str(soup.prettify()))
            if os.environ.get('FLASK_ENV', '') == 'trace':
                if not (path_handler('data_path') / 'choose_association.html').exists():
                    with open(path_handler('data_path') / 'choose_association.html', 'w', encoding='utf-8') as f:
                        f.write(str(soup.prettify()))

        response = run_login_loggedin_get(session.cookies, hdrs)
        logger.debug(f'Final login step response code: {response.status_code}')
        # logger.trace(response.content)
        if not response.ok:
            logger.error('Login failed at final step')
            return {'login_success': login_success}
        redirect = log_redirect(response)
        if redirect:
            logger.error(f'Unexpected redirect after login: {redirect}')
            return {'login_success': login_success}
    else:
        logger.debug(f'Choose club response code: {response.status_code}')
        return {'login_success': login_success}

    # check for club name e.g. "Greve Gymnastik & Trampolin" in title
    # <title>Greve Gymnastik og Trampolin (GreveGym) | Conventus</title>
    soup = BeautifulSoup(response.content, features='html.parser')
    if os.environ.get('FLASK_ENV', '') == 'trace':
        if not (path_handler('data_path') / 'conventus_coach_home.html').exists():
            with open(path_handler('data_path') / 'conventus_coach_home.html', 'w', encoding='utf-8') as f:
                f.write(str(soup.prettify()))
    if soup.title and soup.title.string:
        title: str = soup.title.string.strip()
        logger.debug(f'Title: {title}')
        if title.startswith(clubname) or clubname in title:
            logger.debug(f'Logged in to the selected club: {clubname}')
            login_success = True
        else:
            logger.error(f'Logged in, but unexpected club in title: {title}')
            return {'login_success': login_success}
    else:
        logger.error('Logged in, but no title text')
        return {'login_success': login_success}

    return {'cookies': session.cookies, 'home_page': response.content, 'login_success': login_success}


def make_team_dict(team_id: str, full_team_name: str, ref: str | None = None) -> TeamData:
    # TODO make this configurable by dept admin
    if full_team_name == 'Hold 32 - Begynder/Øvet Trampolin - Skolestart':
        full_team_name = 'Hold 32 - Trampolin - Begynder/Øvet fra Skolestart'
    elif full_team_name == 'Hold 37 - Betalingsdelt Trampolin - Aspirantholdet (Lukket Hold)':
        full_team_name = 'Hold 37 B - Trampolin - Aspirantholdet (Lukket Hold)'
    elif full_team_name == 'Hold 38 -Betalingsdelt Trampolin - Konkurrence/Elite (Lukket hold)':
        full_team_name = 'Hold 38 B - Trampolin - Konkurrence/Elite (Lukket hold)'
    if ref is None:
        ref = full_team_name.split(' - ')[0]
    return {'team_name': re.sub(r' *\([^)]+\)', '', full_team_name), 'id': team_id, 'ref': ref}


def get_team_tds(content: str) -> ResultSet[Tag]:  # this should be update to ResultSet[Tag] (ResultSet and Tag need to be imported from bs4)
    # look for <td class="uos">
    soup: BeautifulSoup = BeautifulSoup(content, features='html.parser')
    team_tds: ResultSet[Tag] = soup.find_all('td', class_='uos')
    return team_tds


def parse_team_td(td) -> tuple[str, str | None]:
    full_team_name: str = td.get_text().strip() if td.get_text() else ''
    team_id: str | None = td.get('onclick').split(',')[0].lstrip('roa2field(').strip("'") if td.get('onclick') else None
    return full_team_name, team_id


def extract_teams(team_tds: list[Any]) -> list[TeamData]:
    teams: list[TeamData] = []
    for td in team_tds:
        full_team_name, team_id = parse_team_td(td)
        if team_id and full_team_name:
            teams.append(make_team_dict(team_id, full_team_name))  # TeamData
    return teams


def check_for_added_and_removed_members_by_teams(
    teams: list[TeamData],
    headers=HEADERS,
    cookies=None,
    group_results=False,
    send_notifications=0,
) -> str:
    """Fetch recent membership changes for each team and render them as HTML.

    A positive ``send_notifications`` value replaces the default seven-day lookback.
    """
    updates = ''
    messages = ''
    for team in teams:
        # team_name, team_id = parse_team_td(td)
        team_id = team.get('id')
        content1 = None
        if cookies and team_id:
            logger.debug('')
            hdrs: CaseInsensitiveDict = header_passthru(headers)
            response = requests.get(
                f'https://www.conventus.dk/login/popup.php?page=adressebog/medlemmer/logbog_gruppe.php&idv1={team_id}',
                headers=hdrs,
                cookies=cookies,
                allow_redirects=False,
                timeout=REQUEST_TIMEOUT,
            )
            if response.content:
                # team = make_team_dict(team_id, team_name)
                content1 = response.content
        elif team_id is None:
            logger.error(f'No team_id found in team: {team}')
            # TODO handle error
        if content1:
            soup = BeautifulSoup(content1, features='html.parser')
            if os.environ.get('FLASK_ENV', '') == 'trace':
                filename = f'log_{team.get("ref").lower().replace(" ", "_").replace("x", "X")}.html'
                if not (path_handler('data_path') / filename).exists():
                    with open(path_handler('data_path') / filename, 'wb') as f:
                        f.write(content1)
            log_entries: list[Tag] = soup.find_all('td', class_='row')
            if log_entries:
                log_entries.pop(0)  # Skip header row
            team_name = team.get('team_name', 'Unknown Team')
            logger.debug(f'Processing {team_name}:')
            team_updates: str = ''
            for entry in log_entries:
                # parse sub-table
                columns: list[Tag] = entry.find_all('td')
                if columns and len(columns) >= 6:
                    name = action = date = None
                    # need columns 3, 4, and 5 for the member name, action, and date
                    # Medlemmet er blevet afmeldt holdet
                    # Medlemmet er blevet tilmeldt holdet
                    name = columns[3].get_text(strip=True)
                    name = name.lower().title()
                    action = columns[4].get_text(strip=True).split('holdet')[0] + 'holdet'
                    logger.debug(f'Action: {action}')
                    if action.startswith('Medlemmet er blevet tilmeldt holdet') or action.startswith('Medlemmet er blevet afmeldt holdet'):
                        action = re.sub(r'^Medlemmet er blevet ', '', action)
                        # convert date format from "dd-mm-yy HH:MM" to "yyyy-mm-dd"
                        original_date = columns[5].get_text(strip=True)
                        if original_date:
                            date = datetime.strptime(original_date, '%d-%m-%y %H:%M').strftime('%Y-%m-%d %H:%M')
                            # compare date to current date and stop processing if older than 7 days
                            days: int = 7
                            if send_notifications > 0:
                                days = send_notifications
                            logger.debug(
                                f'Comparing {date} ({original_date}) against {(datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M")}'
                            )
                            if date > (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d %H:%M'):
                                action = action.replace('afmeldt ', 'frameldt ')
                                logger.debug(f'{original_date}: {name} - {action} ({team_name})')
                                if group_results:
                                    team_updates += f'{original_date}: {name} - {action}<br>'
                                else:
                                    team_updates += f'{original_date}: {name} - {action} ({team_name})<br>'
                            else:
                                logger.debug(f'Done processing {team_name}')
                                break
                elif columns and 1 < len(columns) < 6 and 'Tom' in columns[1].get_text(strip=True):
                    logger.debug(f'No log entries for {team_name}')
                    # messages += f'<p>No log entries for {team_name}</p>'
                else:
                    logger.warning(f'Unexpected log entry format, less than 6 columns: {entry}')
            if team_updates:
                if group_results:
                    updates += f'<h5>{team_name}:</h5><p>{team_updates}</p>'
                else:
                    updates += f'{team_updates}'
                logger.trace(f'Updates for {team_name}: {team_updates}')
                if send_notifications > 0:
                    pass
            # print('entry exited')
    logger.trace(f'Updates: {updates}')
    if updates:
        if not group_results:
            updates = f'<p>{updates}</p>'
        return f'<p>{i18n.t("conventus.member_updates_summary", count=days)}</p>{updates}'
    elif messages:
        return messages
    return ''


def complete_member_login(cookies: dict, member_id: str, headers: CaseInsensitiveDict = HEADERS) -> tuple[bool, requests.Response | None]:
    """After logging in as member, this is used to log into a specific member profile or switch the member profile"""
    response: requests.Response = requests.get(
        'https://www.conventus.dk/before_login/choose_profil_action.php',
        params={'medlem': member_id},
        headers=headers,
        cookies=cookies,
        timeout=REQUEST_TIMEOUT,
    )
    redirect = log_redirect(response)
    if response.ok and not redirect:
        # logged in
        logger.info(f'Logged in to profile with member_id {member_id}')
        logger.debug(response.content)
        return True, response
    return False, None


def get_member_profiles(content, profile: dict[str, str], team_profiles: list[dict[str, str | list[TeamData]]]):
    """Populate an eligible profile's supported teams and append the expanded profile to ``team_profiles``.
    Only to team members in the CONVENTUS_DEPT_NAME dept are eligible teams"""
    logger.debug(profile)
    soup = BeautifulSoup(content, features='html.parser')
    if os.environ.get('FLASK_ENV', '') == 'trace':
        if not (path_handler('data_path') / 'overview.html').exists():
            with open(path_handler('data_path') / 'overview.html', 'wb') as f:
                f.write(content)
    team_tables = soup.find_all('table', class_='uos')

    # find CONVENTUS_DEPT_NAME (Trampolingymnastik), then any teams where the person is a member (not a coach, waiting list, etc.) and add those teams to the profile['teams'] list
    teams: list[TeamData] = []
    for table in team_tables:
        tds = table.find_all('td')

        for i, td in enumerate(tds):
            if CONVENTUS_DEPT_NAME in td.get_text():  # if text in this td contains CONVENTUS_DEPT_NAME
                teams_table = tds[i + 1]  # the next td should have a table with the teams in it
                # print(teams_table)
                teams_tds = teams_table.find_all('td')
                # print(teams_tds)
                for team_td in teams_tds:
                    logger.debug(team_td)
                    # regex match for (<anything>)
                    if team_td.find('span', class_='light') and re.match(r'\(.+\)', team_td.find('span', class_='light').get_text()):
                        logger.debug('Member profile is a non-member: ' + team_td.find('span', class_='light').get_text())
                        continue  # skip this team if, it is marked as a non-member (coach, waiting list, etc.)
                    else:
                        logger.debug('Member profile is a member')
                        team_name_full = team_td.get_text().strip()
                        logger.debug(team_name_full)
                        team_id = None
                        try:
                            team_id = team_td.get('onclick').split('&idv1=')[1].rstrip("';")
                        except (AttributeError, IndexError):
                            if team_td.get('onclick'):
                                logger.warning(f'Failed to extract team_id from onclick attribute: {team_td}')
                            else:
                                logger.debug(f'No onclick attribute found for team td: {team_td}')
                        if team_name_full and team_id:
                            logger.debug('Member profile is a team member')
                            teams.append(make_team_dict(team_id, team_name_full))
    if teams:
        extended_profile: dict[str, str | list[TeamData]] = {}  # profile with teams
        for key, value in profile.items():
            extended_profile[key] = value
        extended_profile['teams'] = teams
        team_profiles.append(extended_profile)


def parse_member_profile(content: str) -> dict:
    """this is used for getting the club member from logged_in.php content"""
    soup = BeautifulSoup(content, features='html.parser')
    roottable = soup.find('table', id='roottable')
    logger.trace(f'Member profile roottable: {roottable}')
    logger.trace(roottable.find('table', class_='bt'))
    tables = roottable.find_all('table', class_='bt')
    member_table = tables[2]
    logger.trace(f'Member profile table: {member_table}')
    member_table_tds = member_table.find_all('td')
    logger.trace(f'Member profile table cells: {member_table_tds}')
    member_id = ''
    name = ''
    for i, td in enumerate(member_table_tds):
        # find the td where the text = Id: and get text of next td
        if td.get_text() == 'Id:':
            id_td = member_table_tds[i + 1]  # the next td should have a table with the teams in it
            member_id = id_td.get_text().strip()
        # find the td where the text = Navn: and get next td text
        if td.get_text() == 'Navn:':
            name_td = member_table_tds[i + 1]  # the next td should have a table with the teams in it
            name = name_td.get_text().strip()
        if member_id and name:
            break
    return {'name': name, 'member_id': member_id}


def header_passthru(headers: Headers | CaseInsensitiveDict, club_id: str = '', sessid: str = '') -> CaseInsensitiveDict:
    hdrs = CaseInsensitiveDict(headers)
    hdrs.pop('Host', None)
    hdrs.pop('Content-Length', None)
    hdrs.pop('Accept-Encoding', None)
    hdrs.pop('Cookie', None)
    if sessid:
        hdrs['Cookie'] = f'PHPSESSID={sessid}'
    if club_id:
        hdrs['Origin'] = 'https://www.conventus.dk'
        hdrs['Referer'] = f'https://www.conventus.dk/medlemslogin/checkuser.php?forening={club_id}'
    else:
        hdrs.pop('Origin', None)
        hdrs.pop('Referer', None)
    # del hdrs["Content-Length"]
    logger.trace(f'Headers: {hdrs}')
    return hdrs


def coach_login_check(cookies, headers: CaseInsensitiveDict = HEADERS) -> bool:
    hdrs: CaseInsensitiveDict = header_passthru(headers, club_id=CONVENTUS_CLUB_ID)
    logger.debug('Testing coach login is still active by accessing the news subscription page')
    response: requests.Response = requests.get(
        'https://www.conventus.dk/login/loggedin.php?page=profil/nyheder.php',
        headers=hdrs,
        cookies=cookies,
        allow_redirects=False,
        timeout=REQUEST_TIMEOUT,
    )
    logger.trace(response.content)
    redirect = log_redirect(response)
    logger.debug(f'News subscription page response code: {response.status_code}')
    if response.ok and not redirect:
        return True
    return False


def member_login(
    password: str,
    club_id: str,
    login_type: str,
    headers: CaseInsensitiveDict = HEADERS,
    email: str = '',
    phone_country: str = '',
    phone_no: str = '',
    sessid: str = '',
):
    """Authenticate a member and discover profiles belonging to supported teams.

    Return the session cookies, selectable profiles, and whether authentication succeeded.
    """
    logger.info('Logging into Conventus as member')
    logger.trace(f'Login type: {login_type}')
    login_data = {}
    if login_type == 'email' and email:
        logger.trace(f'Received email: {email}')
        login_data = {'log_ind_med': login_type, 'email': email, 'password': password}
    elif login_type == 'mobil' and phone_country and phone_no:
        logger.trace(f'Received phone_no: {phone_no} ({phone_country})')
        login_data = {'log_ind_med': login_type, 'mobil_land': phone_country, 'mobil': phone_no, 'email': '', 'password': password}
        logger.trace(f'Login data: {login_data}')
    else:
        logger.error('Email or phone number must be provided for member login')
        # TODO raise Error
        return None, None, False

    login_success = False
    hdrs: CaseInsensitiveDict = header_passthru(headers, club_id, sessid)

    session = requests.post(
        'https://www.conventus.dk/medlemslogin/checkuser.php',
        params={'forening': club_id},
        data=login_data,
        headers=hdrs,
        allow_redirects=False,
        timeout=REQUEST_TIMEOUT,
    )
    logger.debug(session.content)
    # logger.debug(session.cookies)
    redirect = log_redirect(session)

    if not session.ok:
        logger.error(f'Member login request failed with status code {session.status_code}')
        return None, [], login_success
        # TODO raise Error
    if not redirect:
        logger.error(f'Member login did not return a redirect (status {session.status_code})')
        return None, [], login_success
        # TODO raise Error
    if session.ok and '&msg=1' in redirect:
        logger.error('Member login failed due to invalid credentials')
        return None, [], login_success
        # TODO raise Error
    if session.ok and '&msg=Brugernavnet+var+ugyldigt' in redirect:
        logger.error('Member login failed due to missing credentials')
        return None, [], login_success
        # TODO raise Error
    selectable_profiles = []
    if session.ok and 'before_login/choose_profil.php' in redirect:
        response = requests.get(
            'https://www.conventus.dk/before_login/choose_profil.php',
            headers=hdrs,
            cookies=session.cookies,
            timeout=REQUEST_TIMEOUT,
        )

        logger.debug(response.status_code)
        # save content to file, if it doesn' exist
        if os.environ.get('FLASK_ENV', '') == 'trace':
            if not (path_handler('data_path') / 'member_profile_options.html').exists():
                with open(path_handler('data_path') / 'member_profile_options.html', 'wb') as f:
                    f.write(response.content)
        if response.status_code == 200:
            logger.debug('Multiple member profiles')
            # response is a profile select page, scrape profile options
            member_profiles: list[dict[str, str]] = member_get_profile_options(response.content)
            if not member_profiles:
                logger.error(f'Failed to get member profile info from {redirect}')
                return None, [], login_success

            # completes the login for each person and checks if any have a team belonging to CONVENTUS_DEPT_NAME
            for profile in member_profiles:
                member_id: str = profile.get('member_id', '')
                if member_id:
                    logger.debug(f'Log in to profile {profile.get("name")} with member_id {member_id}')
                    # login_success, response = complete_member_login_sxs(session, member_id)
                    profile_login_success, response = complete_member_login(session.cookies, member_id, headers=hdrs)
                    logger.debug(response)
                    if not profile_login_success:
                        logger.error(f'Failed to log in to profile {profile.get("name")} with member_id {member_id}')
                        return None, [], login_success

                    # logged in to profile, so now we need to see if the member is a member of any teams that are configured to use the helper app
                    response = requests.get(
                        'https://www.conventus.dk/medlemslogin/popup.php',
                        params={'page': 'profil/mine_hold_oversigt.php'},
                        headers=hdrs,
                        cookies=session.cookies,
                        timeout=REQUEST_TIMEOUT,
                    )
                    if response.ok:
                        # see if the member is a member of any teams that are configured to use the helper app
                        logger.debug(
                            f'Checking profile {profile.get("name")} with member_id {member_id} to see if selectable (member of a supported team, not coach/staff)'
                        )
                        logger.debug(f'Before: selectable profiles -> {selectable_profiles}')
                        get_member_profiles(response.content, profile, selectable_profiles)
                        logger.debug(f'After: selectable profiles -> {selectable_profiles}')
                    else:
                        # this is just a warning, there could be multiple profiles in a family, but they may not all be active members of a team
                        logger.debug('Not a member of a team (inactive)')
                        logger.warning(f'Failed to read team info from "profil/mine_hold_oversigt.php" for {profile.get("name")}')
                        continue
                else:
                    # every profile should have a member id, so this could be a change to the page, and the scraper needs to be fixed
                    logger.error(f'Something is wrong with this profile: {profile}')
                    login_success = False
                    break

            login_success = True
            num_selectable_profiles = len(selectable_profiles)
            if num_selectable_profiles > 0:
                logger.debug(f'Found {num_selectable_profiles} selectable profiles')
            else:
                # return only that login was successful (selectable profiles and session cookie are not returned)
                logger.warning('No selectable profiles found for the login')
                return None, [], login_success
        elif response.ok:
            # Response is a redirect to the user page, not a profile select page; complete login and display menu for the user
            logger.debug('Just one member profile')
            response = requests.get(
                'https://www.conventus.dk/medlemslogin/loggedin.php',
                headers=hdrs,
                cookies=session.cookies,
                timeout=REQUEST_TIMEOUT,
            )
            redirect = log_redirect(response)

            profile = {}
            if response.ok:  # and 'medlemslogin/loggedin.php?page=profil/vis_mig.php' in redirect:
                logger.trace(response)
                profile = parse_member_profile(response.content)
            else:
                logger.error('Profile page retrieval error')
                raise SiteError('Profile page retrieval error')

            # logged in to profile
            response = requests.get(
                'https://www.conventus.dk/medlemslogin/popup.php',
                params={'page': 'profil/mine_hold_oversigt.php'},
                headers=hdrs,
                cookies=session.cookies,
                timeout=REQUEST_TIMEOUT,
            )
            if response.ok:
                log_redirect(response)
                login_success = True
                get_member_profiles(response.content, profile, selectable_profiles)
                logger.debug(f'{session.cookies}, {selectable_profiles}, {login_success}')
            else:
                logger.error('Team page retrieval error')
                raise SiteError('Team page retrieval error')

        else:
            logger.error('Choose profile request failed')
            return None, [], login_success
    elif session.ok and 'medlemslogin/loggedin.php' in redirect:
        # already logged in to profile
        logger.debug('Already logged in to profile')
        logger.debug(session.content)
        profile = {}
        try:
            # 1. goto Profile page and get "Navn" and "Id" using parse_member_profile
            # https://www.conventus.dk/medlemslogin/loggedin.php?page=profil/vis_mig.php
            params = urlencode({'page': 'profil/vis_mig.php'}, safe='/')
            url = f'https://www.conventus.dk/medlemslogin/loggedin.php?{params}'
            response = requests.get(
                url,
                headers=hdrs,
                cookies=session.cookies,
                timeout=REQUEST_TIMEOUT,
            )
            if response.ok:
                log_redirect(response)
                profile = parse_member_profile(response.content)
            else:
                logger.error('Profile page retrieval error')
                raise SiteError('Profile page retrieval error')

            # 2. goto Hold page and get teams
            response = requests.get(
                'https://www.conventus.dk/medlemslogin/popup.php',
                params={'page': 'profil/mine_hold_oversigt.php'},
                headers=hdrs,
                cookies=session.cookies,
                timeout=REQUEST_TIMEOUT,
            )
            if response.ok:
                log_redirect(response)
                login_success = True
                get_member_profiles(response.content, profile, selectable_profiles)
                logger.debug(f'{session.cookies}, {selectable_profiles}, {login_success}')
            else:
                logger.error('Team page retrieval error')
                raise SiteError('Team page retrieval error')
        except Exception as e:
            logger.exception(f'Error after logged in to profile: {e}')
    else:
        logger.error('Unexpected redirect')
        # TODO send message to ADMIN
    logger.debug('Returning from conventus.member_login function')
    return session.cookies, selectable_profiles, login_success


def download_csv(cookies, dept_or_team_id=CONVENTUS_DEPT_REPORT_ID, dept_or_team_name=CONVENTUS_DEPT_NAME, csv_file='') -> str:
    logger.debug(f'Downloading the {dept_or_team_name} csv')

    response = requests.post(
        'https://www.conventus.dk/login/popup_fil.php?page=adressebog/rapport/eksport.php',
        headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36',
            'Content-Type': 'application/x-www-form-urlencoded',
            'Cache-Control': 'max-age=0',
        },
        cookies=cookies,
        data={
            'aopdelt': 'on',
            'gopdelt': 'on',
            'gruppe_afdeling': dept_or_team_id,
            'felter_valgte': 'id;navn;birth;email;mobil',
            'ekstra_felter_valgte': '',
            'relation_medlemmer': 'on',
            'type_person': 'on',
            'type_medlem': 'on',
            'type_virksomhed': 'on',
            'koen_mand': 'on',
            'koen_kvinde': 'on',
            'alderBasis': '2026-01-01',  # TODO need to find out what this does and when this changes
            'indmeldBasis': '2026-01-01',  # TODO need to find out what this does and when this changes
            'slettet': 'nej',
            'vis_personer': 'on',
        },
        timeout=REQUEST_TIMEOUT,
    )
    if not response.ok:
        logger.error(f'Failed to download "{dept_or_team_name}" report: {response.status_code}')
        raise SiteError(f'Failed to download "{dept_or_team_name}" report: {response.status_code}')
    if response.ok and csv_file:
        logger.debug(f'Write the {dept_or_team_name} csv to {csv_file}')
        try:
            with open(csv_file, 'wb') as f:
                try:
                    f.write(response.content)
                except (IOError, OSError) as e:
                    logger.error(f'Error writing to file: {e}')
        except (FileNotFoundError, PermissionError, OSError) as e:
            logger.error(f'Error creating/opening file: {e}')
        logger.info(f'The csv file, {csv_file}, was saved successfully')
    csv_text = response.content.decode('utf-8-sig')  # remove BOM if present
    logger.debug(f'dept_or_team_id: {dept_or_team_id}')
    logger.debug(f'resulting csv text: {csv_text}')
    return csv_text


def get_members_from_csv(csv_text):
    logger.trace(csv_text)
    f = io.StringIO(csv_text)
    reader = csv.DictReader(f, delimiter=';')
    return list(reader)


def get_team_checkin_lists(team_id, cookies, headers=HEADERS) -> requests.Response:
    """Fetch a team's check-in-list page, raising ``SiteError`` if unavailable."""
    # can't use normal request "params" because the / will be converted to %2f, and while not necessary to url encode the team_id, still better to be consistent
    params: str = urlencode({'page': 'adressebog/afkrydsningslister/gruppe.php', 'gruppe': team_id}, safe='/')
    url: str = f'https://www.conventus.dk/login/loggedin.php?{params}'
    hdrs: CaseInsensitiveDict = header_passthru(headers)
    response: requests.Response = requests.get(
        url,
        headers=hdrs,
        cookies=cookies,
        allow_redirects=False,
        timeout=REQUEST_TIMEOUT,
    )
    logger.trace(cookies)
    if not response.ok or log_redirect(response):
        logger.error(f'Failed to fetch check-in list page for team {team_id}: {response.status_code}')
        # logger.traceback()
        raise SiteError(f'Failed to fetch check-in list page for team {team_id}: {response.status_code}')

    logger.debug(f'Fetched check-in list page for team {team_id} successfully')
    if response.content:
        if os.environ.get('FLASK_ENV', '') == 'trace':
            if not (path_handler('data_path') / f'team_checkin_lists_{team_id}.html').exists():
                with open(path_handler('data_path') / f'team_checkin_lists_{team_id}.html', 'wb') as f:
                    f.write(response.content)
    return response


def find_team_checkin_list_id_from_content(team_id, date, content) -> str:
    """
    expected structure of the check-in list page content (for each list, there should be a div with class "row body simple", containing a div with
    class "col-xs-9" with the date of the list, and a button with an onclick attribute containing the list id)
    """
    web_date = date_std_to_da(date)
    logger.debug(f'Date: {date}, Conventus date: {web_date}')
    soup = BeautifulSoup(content, features='html.parser')  # html is not decoded here
    if os.environ.get('FLASK_ENV', '') == 'trace':
        if not (path_handler('data_path') / f'checkin_lists_{team_id}.html').exists():
            with open(path_handler('data_path') / f'checkin_lists_{team_id}.html', 'w', encoding='utf-8') as f:
                f.write(str(soup.prettify()))
    all_lists = soup.find_all('div', class_='row body simple')

    if len(all_lists):
        if not all_lists[0].find('div', class_='col-xs-9'):
            logger.error(
                'Check-in list page structure seems to have changed, expected div with class "col-xs-9" inside div with class "row body simple"'
            )
            raise ParsingError(
                'Check-in list page structure seems to have changed, expected div with class "col-xs-9" inside div with class "row body simple"'
            )
        for list in all_lists:
            if list.find('div', class_='col-xs-9') and list.find('div', class_='col-xs-9').string:
                list_date = list.find('div', class_='col-xs-9').string.strip()
                list_date_std = date_da_to_std(list_date)
                logger.debug(f'Found check-in list for team {team_id} on date {list_date_std}')
                if list_date and list_date_std == date:
                    list_id = list.find('button', onclick=True)['onclick'].split('vaelg_liste(', 1)[1].rstrip(')')
                    logger.debug(f'Found check-in list for team {team_id} on date {list_date}: {list_id}')
                    return list_id
                if list_date_std < date:
                    logger.debug(f'Check-in list for team {team_id} on {web_date} not found, found earlier date: {list_date}')
                    break
            else:
                """
                expected content:
                    <div class="row body simple">
                        <div class="col-xs-9 col-xs-offset-3">
                            <em>Ingen</em>
                        </div>
                    </div>
                """
                logger.debug(list.find('div', class_='col-xs-9'))
    elif soup.find('div', class_='row head simple'):
        logger.debug(f'Check-in list page structure seems to be intact, but no lists found for team {team_id}')
    else:
        logger.error('Check-in list page structure seems to have changed')
        raise ParsingError('Check-in list page structure seems to have changed')
    return ''  # return empty string to indicate list for requested date not found, but page structure seems intact


def add_checkin_list(cookies, team_id, date, headers=HEADERS):
    """Create a dated team check-in list and return its redirected list identifier.

    Raise ``SiteError`` when creation fails or the redirect contains no identifier.
    """
    web_date = date
    logger.debug(f'{date} converted to {web_date}')
    params: str = urlencode({'page': 'adressebog/afkrydsningslister/add_action.php', 'gruppe': team_id}, safe='/')
    url: str = f'https://www.conventus.dk/login/loggedin.php?{params}'
    hdrs: CaseInsensitiveDict = header_passthru(headers)
    hdrs['Content-Type'] = 'application/x-www-form-urlencoded'
    response: requests.Response = requests.post(
        url,
        data={'dato': web_date},
        headers=hdrs,
        cookies=cookies,
        allow_redirects=False,
        timeout=REQUEST_TIMEOUT,
    )
    redirect: str | None = log_redirect(response)
    if not response.ok or not redirect:
        logger.error(f'Failed to create check-in list for team {team_id} on {date}: {response.status_code}')
        raise SiteError(f'Failed to create check-in list for team {team_id} on {date}: {response.status_code}')
    else:
        # get list-id from redirect
        logger.info(redirect)
        list_id = redirect.split('liste=')[1]
        if list_id:
            return list_id
        raise SiteError(f'Failed to get check-in list ID from redirect: {redirect}')


def get_checkin_list(cookies, team_id: str, date: str, list_id: str, headers: Headers | CaseInsensitiveDict = HEADERS):
    """Fetch check-in-list content, returning ``None`` for a failed or redirected request."""
    params: str = urlencode({'page': 'adressebog/afkrydsningslister/liste.php', 'liste': list_id}, safe='/')
    url: str = f'https://www.conventus.dk/login/loggedin.php?{params}'
    hdrs: CaseInsensitiveDict = header_passthru(headers)
    response: requests.Response = requests.get(
        url,
        headers=hdrs,
        cookies=cookies,
        allow_redirects=False,
        timeout=REQUEST_TIMEOUT,
    )
    # logger.trace(response.content)
    if not response.ok or log_redirect(response):
        logger.error(f'Failed to fetch check-in list for team {team_id} on {date}: {response.status_code}')
        return None

    logger.debug(f'Fetched check-in list {list_id} for team {team_id} on {date} successfully')
    if os.environ.get('FLASK_ENV', '') == 'trace':
        if not (path_handler('data_path') / f'checkin_list_{team_id}_{date}_{list_id}.html').exists():
            with open(path_handler('data_path') / f'checkin_list_{team_id}_{date}_{list_id}.html', 'wb') as f:
                f.write(response.content)
    return response.content


def passthru_check_action_fake(request: Request, phpsessid: str):
    # Get data from JS. Flask picks it up in request.form
    list_id = request.form.get('afkrydsningsliste')
    logger.trace(request.form)

    # Forward to the real server using the same Content-Type
    real_headers: CaseInsensitiveDict = CaseInsensitiveDict(request.headers)
    #'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
    real_headers['Cookie'] = f'PHPSESSID={phpsessid}'
    real_headers['Origin'] = 'https://www.conventus.dk'
    real_headers['Referer'] = f'https://www.conventus.dk/login/loggedin.php?page=adressebog/afkrydsningslister/liste.php&liste={list_id}'
    real_headers['Host'] = 'www.conventus.dk'
    logger.trace(real_headers)

    # requests.post defaults to x-www-form-urlencoded when you use the 'data' param
    return 'success'


def passthru_check_action(request: Request, phpsessid: str):
    logger.trace(request.headers)
    logger.trace(request.form)

    # Get data from JS. Flask picks it up in request.form
    list_id = request.form.get('afkrydsningsliste')

    # Forward to the real server using the same headers except: update origin and referer, remove host (if it exists)
    real_url: str = 'https://www.conventus.dk/login/popup_nohtml.php?page=adressebog/afkrydsningslister/tjek_action.php'
    real_headers: CaseInsensitiveDict = CaseInsensitiveDict(request.headers)
    real_headers['Cookie'] = f'PHPSESSID={phpsessid}'
    real_headers['Origin'] = 'https://www.conventus.dk'
    real_headers['Referer'] = f'https://www.conventus.dk/login/loggedin.php?page=adressebog/afkrydsningslister/liste.php&liste={list_id}'
    real_headers['Host'] = 'www.conventus.dk'
    logger.trace(real_headers)

    # requests.post defaults to x-www-form-urlencoded when you use the 'data' param
    real_response: requests.Response = requests.post(real_url, headers=real_headers, data=request.form, timeout=REQUEST_TIMEOUT)

    # respond back to our JavaScript
    return real_response.text


def run_login_loggedin_get(cookies, headers: CaseInsensitiveDict = HEADERS, allow_redirects=False) -> requests.Response:
    response = requests.get(
        'https://www.conventus.dk/login/loggedin.php?page=adressebog/medlemmer/start.php',
        headers=header_passthru(headers),
        cookies=cookies,
        allow_redirects=allow_redirects,
        timeout=REQUEST_TIMEOUT,
    )
    return response


def member_get_profile_options(content) -> list[dict[str, str]]:
    """If there are several profiles connected to a login, extract names and member IDs from the Conventus profile-selection page."""
    soup = BeautifulSoup(content, features='html.parser')
    member_table = soup.find('table', class_='bt')
    if member_table is None:
        logger.error('No member profile table found in content')
        return []
    logger.trace(f'Member profile options table: {member_table}')
    member_table_tds = member_table.find_all('td')
    if member_table_tds is None:
        logger.error('No member profile options table cells found in content')
        return []
    logger.trace(f'Member profile options table cells: {member_table_tds}')
    members: list[dict[str, str]] = []
    for td in member_table_tds:
        onclick = td.get('onclick')  # get the onclick attribute, e.g. "location.href='choose_profil_action.php?medlem=123456';"
        if onclick and 'medlem' in onclick:
            # print(onclick)
            # get the "medlem" parameter and remove "';" from the end
            member_id = onclick.split('?')[1].rstrip("';")
            # print(member_id)
            # remove "medlem=" from the beginning
            member_id = member_id.split('=')[1]
            # print(member_id)
            name = td.get_text(strip=True)
            # print(name)
            logger.debug(f'Found member profile option: name="{name}", member_id="{member_id}"')
            members.append({'name': name, 'member_id': member_id})
    # print(members)
    logger.debug(f'Member profile options: {members}')
    return members


def find_member_checkins(team_id, date, content, docs: list[models.Signout] | None = None) -> list[dict]:
    """Combine members parsed from a check-in page with their sign-out records.
    From the content provided, find the members in the check-in list for the team and date, as well as query the DB for signouts
        and return a list of dicts with member_id, member_name, and status (checked-in or signed-out + reason)

    a database failure is represented by an initial ``{'db_available': False}`` item. Raise
    ``ParsingError`` when the expected member markup is absent or malformed.

    expected content:
        <div class="row body simple">
            <div class="col-xs-1">
                <input type="checkbox" class="pull-right" onclick="medlem_clicked([member_id], 2);" name="medlem_[member_id]_2" id="medlem_[member_id]_2" checked />
            </div>
            <div class="col-xs-11 arrow" onclick="$('#medlem_[member_id]_2').click();">[member_name]</div>
        </div>
    """
    soup = BeautifulSoup(content, features='html.parser')
    # logger.trace(soup.prettify())
    all_members = soup.find_all('div', class_='row body simple')
    if len(all_members):
        logger.debug(f'Members in check-in list for team {team_id} on {date}: {len(all_members)}')
        # select member_id and status from 'checkin' db where club_id = CONVENTUS_CLUB_ID and team_id = team_id and date = date
        # then for each member in the check-in list, find the member in the db and update their check-in status for the date of the list
        # parse the check-in list content and return list of members with their check-in status
        member_checkin_data = []
        if docs is None:
            logger.debug(f'{date}, {CONVENTUS_CLUB_ID}, {CONVENTUS_DEPT_ID}, {team_id}')
            # this will get the number from the end of the dept id, e.g. "a_1234" -> "1234", and if there is no underscore, it will just return the value
            dept = CONVENTUS_DEPT_ID.split('_')[-1]
            try:
                docs = get_signout_list_from_db(date, CONVENTUS_CLUB_ID, dept, team_id)
            except DatabaseError as e:
                # allow the db to fail, but alert that db is not available for reading signouts
                logger.exception(f'Error fetching signout list from db: {e}')
                member_checkin_data.append({'db_available': False})
                docs = []
        logger.debug(f'Signouts for {team_id} on {date}: {len(docs)} ')
        # logger.trace(f'Signouts for {team_id} on {date}: {docs}') #Signouts for 984967 on 2026-04-15: [<Signout: Signout object>]
        for member in all_members:
            status: str = ''
            if member.find('input', type='checkbox'):
                member_id = member.find('input', type='checkbox')['onclick'].split('medlem_clicked(', 1)[1].split(',')[0]
                member_name = None
                if member.find('div', class_='col-xs-11') and member.find('div', class_='col-xs-11').string:
                    member_name = member.find('div', class_='col-xs-11').string.strip()
                if member_name is None:
                    logger.error(
                        'Check-in list page structure seems to have changed, expected div with class "col-xs-11" inside div with class "row body simple"'
                    )
                    raise ParsingError(
                        'Check-in list page structure seems to have changed, expected div with class "col-xs-11" inside div with class "row body simple"'
                    )
                member_name_norm = member_name.lower().title()  # normalize name for db lookup
            elif member.find('em') and member.find('em').get_text().strip() == 'Ingen':
                # no members on this team
                logger.info('Page shows there are no members in {team_id}.')
                return member_checkin_data
            else:
                logger.error('Check-in list page structure seems to have changed, expected at least one checkbox input.')
                raise ParsingError('Check-in list page structure seems to have changed, expected at least one checkbox input.')

            # if doc.status (from db) is true for member_id, then set status to 'signed-out' and add doc.reason if it exists
            for doc in docs:
                # logger.info(dict(doc))
                if doc.member_id == member_id:
                    logger.trace(f'member name: {member_name_norm}')
                    logger.trace(f'signed-out: {doc.status}')
                if doc.member_id == member_id and doc.status is not None and doc.status is True:
                    logger.debug(f'{member_name_norm} signed-out!')
                    status = 'signed-out'  # l10n -- this gets localized later in the script
                    if doc.reason:
                        status += ' (' + localize_reason(doc.reason) + ')'  # re-join the reason string and add it to the status
                    break

            # this will overwrite the db status, because they were marked as showing up (presumably after they signed out)
            if 'checked' in member.find('input', type='checkbox').attrs:
                status = 'checked-in'
            logger.debug(f'Member ID: {member_id}, Name: {member_name_norm}, Status: {status}')
            member_checkin_data.append({'member_id': member_id, 'member_name': member_name_norm, 'status': status})
        return member_checkin_data
    else:
        logger.error('Check-in list page structure seems to have changed, expected div with class "row body simple", but none found')
        raise ParsingError('Check-in list page structure seems to have changed, expected div with class "row body simple", but none found')


def read_checkin_list_start_page(headers, cookies):
    """Return the Conventus check-in-list landing page content.

    Raise ``SiteError`` when the request fails, redirects, or has an empty body.
    """
    # don't use normal request "params" because the / will be converted to %2f, and while it's not strictly necessary in this case, still better to be consistent
    params = urlencode({'page': 'adressebog/afkrydsningslister/start.php'}, safe='/')
    url: str = f'https://www.conventus.dk/login/loggedin.php?{params}'
    hdrs: CaseInsensitiveDict = header_passthru(headers)
    response: requests.Response = requests.get(
        url,
        headers=hdrs,
        cookies=cookies,
        allow_redirects=False,
        timeout=REQUEST_TIMEOUT,
    )
    logger.trace(cookies)
    if not response.ok or log_redirect(response):
        logger.error(f'Failed to fetch check-in list main page: {response.status_code}')
        # logger.traceback()
        raise SiteError(f'Failed to fetch check-in list main page: {response.status_code}')

    logger.debug('Fetched check-in list main page successfully')
    if response.content:
        if os.environ.get('FLASK_ENV', '') == 'trace':
            with open(path_handler('data_path') / 'afkrydsningslister_start.html', 'wb') as f:
                f.write(response.content)
        return response.content
    raise SiteError(f'Failed to fetch check-in list main page, despite response OK: {response.status_code}')


def parse_checkin_list_start_page(content, match_text='Trampolin') -> list[dict[str, str]]:
    soup = BeautifulSoup(content, features='html.parser')
    if os.environ.get('FLASK_ENV', '') == 'trace':
        if not (path_handler('data_path') / 'checkin_lists_start_pretty.html').exists():
            with open(path_handler('data_path') / 'checkin_lists_start_pretty.html', 'w', encoding='utf-8') as f:
                f.write(str(soup.prettify()))
    c = soup.find('div', class_='side-collapse-container')
    if c is None:
        logger.error('Check-in list start page structure seems to have changed')
        raise ParsingError('Check-in list start page structure seems to have changed')
    team_divs: ResultSet[Tag] = c.find_all('div', class_='pointer')
    logger.debug(f'Team divs: {len(team_divs)}')
    teams: list[dict[str, str]] = []
    for div in team_divs:
        if div.get_text() and f' {match_text} ' in div.get_text():
            logger.trace(div)
            logger.trace(div.get_text().strip())
            team_name = div.get_text().strip()
            onclick = div.get('onclick') or ''
            if 'vaelg_gruppe(' not in onclick:
                logger.warning(f'Skipping team "{team_name}": unexpected onclick format: {onclick!r}')
                continue
            team_id = onclick.split('vaelg_gruppe(', 1)[-1].rstrip(');')
            if not team_id.isdigit():
                logger.warning(f'Skipping team "{team_name}": could not parse valid team_id from onclick: {onclick!r} -> {team_id!r}')
                continue
            logger.debug(f'Found team {team_name} with id {team_id}')
            teams.append(make_team_dict(team_id, team_name))
    return teams


def get_member_team_info_page(team_id, headers, cookies) -> str:
    """Return a member's Conventus team-information page content.

    Raise ``SessionError`` for an expired session and ``SiteError`` for other
    failed, redirected, or empty responses.
    """
    # https://www.conventus.dk/medlemslogin/popup.php?page=profil/mine_hold_info.php&idv1=1044963
    params = urlencode({'page': 'profil/mine_hold_info.php', 'idv1': team_id}, safe='/')
    url: str = f'https://www.conventus.dk/medlemslogin/popup.php?{params}'
    hdrs: CaseInsensitiveDict = header_passthru(headers)
    response: requests.Response = requests.get(
        url,
        headers=hdrs,
        cookies=cookies,
        allow_redirects=False,
        timeout=REQUEST_TIMEOUT,
    )
    logger.trace(cookies)
    if not response.ok or log_redirect(response):
        if response.status_code == 302:
            # session expired
            raise SessionError(f'Failed to fetch member team info page, because the Conventus session has expired: {response.status_code}')
        else:
            logger.error(f'Failed to fetch member team info page: {response.status_code}')
            raise SiteError(f'Failed to fetch member team info page: {response.status_code}')

    logger.debug('Fetched member team info page successfully')
    if response.content:
        if os.environ.get('FLASK_ENV', '') == 'trace':
            with open(path_handler('data_path') / 'member_team_info.html', 'wb') as f:
                f.write(response.content)
        return response.content
    raise SiteError(f'Failed to fetch member team info page, despite response OK: {response.status_code}')


def parse_member_team_info_page(content: str) -> dict[str, str]:
    soup: BeautifulSoup = BeautifulSoup(content, features='html.parser')
    info: dict = {}
    time_and_place_tr: Tag | None = soup.find(attrs={'id': 'tr_5'})
    if not time_and_place_tr:
        raise ParsingError('Member team info page structure seems to have changed -- no "Tid og Sted" tr')

    if time_and_place_tr and len(time_and_place_tr.find_all('td')) == 2:
        time_and_place: str = time_and_place_tr.find_all('td')[1].decode_contents().strip()
        logger.debug(f'Found time and place: {time_and_place}')
        if 'Holmeagerskolen' in time_and_place:
            time_and_place = time_and_place.replace('Holmeagerskolen', 'GIC hal 3')
        time_and_place = time_and_place.replace(' - Greve Gymnastik og Trampolin', '')
        schedule: list[dict[str, str]] = []
        days: list[str] = i18n.t('conventus.weekday_plurals', locale='da').split(', ')
        for line in time_and_place.split('<br/>'):
            if line.strip():
                # Parse each line to extract day, time, and place
                # This is a simplified example - you may need to adjust the parsing logic based on the actual format
                parts = line.split(', ')
                if len(parts) == 2:
                    day_time = parts[0]
                    place = parts[1]
                    parts = day_time.split(' kl. ')  # Extract the day from the string
                    if len(parts) == 2:
                        day = parts[0]
                        time = parts[1]
                        time = time.replace(' ', '')  # Remove spaces from the time string
                    else:
                        raise ParsingError('Member team info page structure seems to have changed -- cannot split day and time')
                    day_index: int | None = None
                    for index, l10n_day in enumerate(days):
                        if day.lower() == l10n_day:
                            day_index = index
                            break
                    if day_index is None:
                        raise ParsingError(f'Member team info page has an unrecognized weekday: {day!r}')
                    schedule.append({'day': day_index, 'time': time, 'place': place})
                else:
                    raise ParsingError('Member team info page structure seems to have changed -- cannot split time and place')
        info['schedule'] = schedule

        period_tr: Tag | None = soup.find(attrs={'id': 'tr_6'})
        if period_tr and len(period_tr.find_all('td')) == 2:
            period: str = period_tr.find_all('td')[1].decode_contents().strip()
            (start_date_da, end_date_da) = period.split(' - ')
            tz = ZoneInfo('Europe/Copenhagen')
            date = datetime.now(tz)
            start_date = datetime.strptime(start_date_da, '%d-%m-%Y').replace(tzinfo=tz)
            end_date = datetime.strptime(end_date_da, '%d-%m-%Y').replace(tzinfo=tz)
            season = f'{start_date.strftime("%y")}'
            if start_date.year != end_date.year:
                season += f'/{end_date.strftime("%y")}'
            info['season'] = season
            info['active'] = False
            if start_date <= date <= end_date:
                info['active'] = True
            info['start_date'] = start_date
            info['end_date'] = end_date
            info['expires'] = datetime(end_date.year + 1, 1, 1, tzinfo=tz)
        else:
            raise ParsingError('Member team info page structure seems to have changed -- no "Periode" tr')
    else:
        raise ParsingError('Member team info page structure seems to have changed -- td count != 2')

    return info


if __name__ == '__main__':
    # the app won't call this directly, but it can be useful for testing
    parser = argparse.ArgumentParser()
    parser.add_argument('--debug', action='store_true')
    parser.add_argument('--trace', action='store_true')
    parser.add_argument('--username', action='store_true')
    parser.add_argument('--password', action='store_true')

    # If REQUEST_METHOD exists, we are likely in a CGI environment
    if os.environ.get('REQUEST_METHOD'):
        args = parser.parse_args([])  # Pass an empty list to ignore CLI input
    else:
        args = parser.parse_args()

    logger.debug(args)
    configure_local_logger(debug=args.debug, trace=args.trace)
