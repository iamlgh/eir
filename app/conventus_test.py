#!/usr/local/bin/python
# -*- coding: UTF-8 -*-
"""
This module contains functions for logging into Conventus and handling basic Conventus functions that don't require specific profile setup
"""

from bs4 import BeautifulSoup
from datetime import datetime
from genericpath import exists
import i18n
import logging
import pytest  # https://pypi.org/project/beautifulsoup4/
from typing import Any  # , TypedDict
from zoneinfo import ZoneInfo
from conventus import (
    find_member_checkins,
    member_get_profile_options,
    find_team_checkin_list_id_from_content,
    parse_member_profile,
    extract_teams,
    parse_team_td,
    make_team_dict,
    get_members_from_csv,
    log_redirect,
    parse_member_team_info_page,
    # get_trampolinists_from_csv,
)
from shared import (
    path_handler,
)
from trace_logging import setup_trace_level

i18n.load_path.append(path_handler('i18n_path'))
i18n.set('file_format', 'json')
i18n.set('skip_locale_root_data', True)
i18n.set('fallback', 'en')

# global
# script config
HEADERS = {
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


# logger
logger = logging.getLogger(__name__)
setup_trace_level()


def test_log_redirect():
    class MockResponse:
        def __init__(self, status_code, headers):
            self.status_code = status_code
            self.headers = headers

    # Test case 1: Valid redirect
    response1 = MockResponse(302, {'Location': 'https://www.example.com'})
    assert log_redirect(response1) == 'https://www.example.com'
    # Test case 2: No redirect
    response2 = MockResponse(200, {})
    assert log_redirect(response2) is None
    # Test case 3: Redirect with missing Location header
    response3 = MockResponse(301, {})
    assert log_redirect(response3) == 'unknown'


def test_make_team_dict():
    test_dict = make_team_dict(
        'team04',
        'Team 8 - Trampolin - Konkurrence/Elite (Lukket hold)',
    )
    assert test_dict == {'team_name': 'Team 8 - Trampolin - Konkurrence/Elite', 'id': 'team04', 'ref': 'Team 8'}


def test_parse_team_td():
    html = """       <td class="uos" onclick="roa2field('team01', 'grp_liste'); inv_dis('grp_team01'); inv_pil_1('pic_grp_team01');
 											makeRequest('popup.php?page=adressebog/medlemmer/medlemmer.php&amp;idv1=team01', 'grp_team01_content', '1');" onmouseout="this.style.cursor='';" onmouseover="this.style.cursor='pointer';">
 											Team 5 - Trampolin - Mini 3 år til skolestart												</td>"""
    soup: BeautifulSoup = BeautifulSoup(html, features='html.parser')
    td = soup.find('td', class_='uos')
    full_team_name, team_id = parse_team_td(td)
    assert team_id == 'team01'
    assert full_team_name == 'Team 5 - Trampolin - Mini 3 år til skolestart'


def test_extract_teams():
    class MockTd:
        def __init__(self, text: str, onclick: str | None = None):
            self.text: str = text
            self.onclick: str | None = onclick

        def get_text(self) -> str:
            return self.text

        def get(self, attr: str, default: Any = None) -> Any:
            if attr == 'onclick':
                return self.onclick
            return default

    team_tds = [
        MockTd(
            'Team 1 - Trampolin - Begynder/Øvet',
            "roa2field('team07', 'grp_liste'); inv_dis('grp_team07'); inv_pil_1('pic_grp_team07'); makeRequest('popup.php?page=adresses/member.php&idv1=team07', 'grp_team07_content', '1');",
        ),
        MockTd(
            'Team 2 - Trampolin - Begynder/Øvet 8 år+',
            "roa2field('team09', 'grp_liste'); inv_dis('grp_team09'); inv_pil_1('pic_grp_team09'); makeRequest('popup.php?page=adresses/member.php&idv1=team09', 'grp_team09_content', '1');",
        ),
        MockTd(
            'Team 8 - Trampolin - Konkurrence/Elite (Lukket hold)',
            "roa2field('team04', 'grp_liste'); inv_dis('grp_team04'); inv_pil_1('pic_grp_team04'); makeRequest('popup.php?page=adresses/member.php&idv1=team04', 'grp_team04_content', '1');",
        ),
        # MockTd(None, None) #TODO: handle the case where the td doesn't have the expected structure
    ]
    expected = [
        {'team_name': 'Team 1 - Trampolin - Begynder/Øvet', 'ref': 'Team 1', 'id': 'team07'},
        {'team_name': 'Team 2 - Trampolin - Begynder/Øvet 8 år+', 'ref': 'Team 2', 'id': 'team09'},
        {'team_name': 'Team 8 - Trampolin - Konkurrence/Elite', 'ref': 'Team 8', 'id': 'team04'},
    ]
    assert extract_teams(team_tds) == expected


def test_get_member_profiles():
    pass


def test_parse_member_profile():
    content = """
    <table id="roottable">
        <tr>
            <td>
                <link href="umenu.css" rel="stylesheet" type="text/css" media="all" />
                <table class="bt">
                    <tr><td>Profil</td></tr>
                </table>
            </td>
            <td>
                <table class="bt">
                    <tr>
                        <td>
                            <div>
                                <table class="bt">
                                    <tr>
                                        <td>Id:</td>
                                        <td>
                                            1234576
                                        </td>
                                    </tr>
                                    <tr>
                                        <td>Køn:</td>
                                        <td>
                                            Kvinde
                                        </td>
                                    </tr>
                                    <tr>
                                        <td>Navn:</td>
                                        <td>
                                            Fru Danmark
                                        </td>
                                    </tr>
                                </table>

                            </div>

                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>"""
    assert parse_member_profile(content) == {'name': 'Fru Danmark', 'member_id': '1234576'}


def test_get_members_from_csv():
    csv_file = path_handler('data_path') / 'conventus_members.csv'
    if exists(csv_file):
        csv_text = None
        with open(csv_file, mode='r', encoding='utf-8') as f:
            csv_text = f.read().lstrip('\ufeff')
        result = get_members_from_csv(csv_text)
        assert type(result) is list
        logger.warning(result[0])
        assert type(result[0]) is dict
    else:
        pytest.skip(f'{csv_file} file missing, test skipped')


def test_find_team_checkin_list_id_from_content():
    test_content = """
    <div class="row body simple">
        <div class="col-xs-3 hidden-print">
            <button type="button" onclick="vaelg_liste(6)" style="width: 100%">Vis</button>
        </div>
        <div class="col-xs-9">08-04-2026</div>
    </div>
    <div class="row body simple">
        <div class="col-xs-3 hidden-print">
            <button type="button" onclick="vaelg_liste(7)" style="width: 100%">Vis</button>
        </div>
        <div class="col-xs-9">01-04-2026</div>
    </div>
    """
    list_id = find_team_checkin_list_id_from_content('testteam1', '2026-04-08', test_content)
    assert list_id == '6', f'Expected list_id 6, got {list_id}'
    list_id = find_team_checkin_list_id_from_content('testteam1', '2026-04-01', test_content)
    assert list_id == '7', f'Expected list_id 7, got {list_id}'
    list_id = find_team_checkin_list_id_from_content('testteam1', '2026-03-30', test_content)
    assert list_id == '', f'Expected empty list_id, got {list_id}'


def test_member_get_profile_options():
    content = """
    <table class="uos">
        <tr><td>
            <table class="bt">
                <tr><td onMouseOver="this.style.cursor='pointer';" onMouseOut="this.style.cursor='';" onClick="location.href='choose_profil_action.php?medlem=1010101';"><img src="medlem.bmp" border="0" style="margin-bottom: -3px;" /> Fru Danmark</td></tr>
                <tr><td onMouseOver="this.style.cursor='pointer';" onMouseOut="this.style.cursor='';" onClick="location.href='choose_profil_action.php?medlem=101010';"><img src="medlem.bmp" border="0" style="margin-bottom: -3px;" /> Frøken Danmark</td></tr>
            </table>
        </td></tr>
    </table>"""

    expected_member_profile_options = [{'name': 'Fru Danmark', 'member_id': '1010101'}, {'name': 'Frøken Danmark', 'member_id': '101010'}]
    assert member_get_profile_options(content) == expected_member_profile_options


def test_find_member_checkins():
    test_content = """
    <div class="row body simple">
        <div class="col-xs-1">
            <input type="checkbox" class="pull-right" onclick="medlem_clicked(1234566, 2);" name="medlem_1234566_2" id="medlem_1234566_2" checked />
        </div>
        <div class="col-xs-11 arrow" onclick="$('#medlem_1234566_2').click();">Ava green johnson</div>
    </div>
    <div class="row body simple">
        <div class="col-xs-1">
            <input type="checkbox" class="pull-right" onclick="medlem_clicked(1234567, 2);" name="medlem_1234567_2" id="medlem_1234567_2" />
        </div>
        <div class="col-xs-11 arrow" onclick="$('#medlem_1234567_2').click();"> Bob smith  </div>
    </div>
    """
    # pass docs=[] to find_member_checkins to avoid database access during testing
    member_checkin_data = find_member_checkins('test_team', '2026-04-08', test_content, docs=[])
    assert len(member_checkin_data) == 2
    assert {'member_id': '1234566', 'member_name': 'Ava Green Johnson', 'status': 'checked-in'} in member_checkin_data
    assert member_checkin_data == [
        {'member_id': '1234566', 'member_name': 'Ava Green Johnson', 'status': 'checked-in'},
        {'member_id': '1234567', 'member_name': 'Bob Smith', 'status': ''},
    ]


def test_l10n_plurals():
    # Test English pluralization
    assert i18n.t('conventus.member_updates_summary', count=1, locale='en') == 'Members added or removed within the last 24 hours:'
    assert i18n.t('conventus.member_updates_summary', count=2, locale='en') == 'Members added or removed within the last 2 days:'
    # Test Danish pluralization
    assert i18n.t('conventus.member_updates_summary', count=1, locale='da') == 'Tilmeldte og frameldte medlemmer det seneste døgn:'
    assert i18n.t('conventus.member_updates_summary', count=2, locale='da') == 'Tilmeldte og frameldte medlemmer de seneste 2 dage:'


def test_parse_member_team_info_page():
    content = """    <tr id="tr_5" style="display: ;">
        <td valign="top">Tid og sted:</td>
        <td>Mandage kl. 18:30 - 21:00 - Greve Gymnastik og Trampolin, GIC hal 3<br>Onsdage kl. 18:30 - 21:00 - Greve Gymnastik og Trampolin, GIC hal 3<br>Fredage kl. 17:00 - 19:30 - Greve Gymnastik og Trampolin, Holmeagerskolen<br>Søndage kl. 13:00 - 14:30 - Greve Gymnastik og Trampolin, GIC hal 4<br>Torsdage kl. 20:00 - 22:00 - Greve Gymnastik og Trampolin, GIC hal 4<br></td>
    </tr>
    <tr id="tr_6" style="display: ;">
        <td>Periode:</td>
        <td>10-08-2026 - 15-08-2027</td>
    </tr>"""
    team_info: dict[str, str] = parse_member_team_info_page(content)
    logger.info(f'Team info: {team_info}')
    assert team_info.get('schedule') == [
        {'day': 0, 'time': '18:30-21:00', 'place': 'GIC hal 3'},
        {'day': 2, 'time': '18:30-21:00', 'place': 'GIC hal 3'},
        {'day': 4, 'time': '17:00-19:30', 'place': 'GIC hal 3'},
        {'day': 6, 'time': '13:00-14:30', 'place': 'GIC hal 4'},
        {'day': 3, 'time': '20:00-22:00', 'place': 'GIC hal 4'},
    ]
    training_days = [item['day'] for item in team_info.get('schedule', [])]
    training_days.sort()
    assert training_days == [0, 2, 3, 4, 6]
    tz = ZoneInfo('Europe/Copenhagen')
    date: datetime = datetime.now(tz)
    active: bool | None = team_info.get('active')
    assert active is not None
    if date >= team_info.get('start_date'):
        assert active is True
    else:
        assert active is False
    if date <= team_info.get('end_date'):
        assert active is True
    else:
        assert active is False
    assert team_info.get('season') == '26/27'
    assert team_info.get('expires') > team_info.get('end_date')
    print(f'Team info: {team_info}')
