#!/usr/local/bin/python
# -*- coding: UTF-8 -*-

# from icu4py.messageformat import MessageFormat
import i18n
import logging
import sys
from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache
from logging.handlers import RotatingFileHandler
from pathlib import Path
from requests.cookies import RequestsCookieJar
from trace_logging import TraceLogger
from typing import TypedDict

logger: TraceLogger = logging.getLogger(__name__)  # type: ignore[assignment]

# general config (to be moved to cfg)
REQUEST_TIMEOUT = (5, 30)  # (connect, read) seconds
SUPPORTED_LANGUAGES: list = ['en', 'da']

# club config (to be moved to DB)
CONVENTUS_CLUB_NAME = 'Greve Gymnastik og Trampolin'
CONVENTUS_CLUB_ID = '662'  # Greve Gymnastik og Trampolin's Conventus club id, used for member login
CONVENTUS_DEPT_ID = '2875'  # Trampolingymnastik dept id
CONVENTUS_DEPT_REPORT_ID = 'a_2875'  # Trampolingymnastik dept id, used for the report generation
# g_<team_id> is for querying teams (g = gruppe)
# a_<dept_id> is for querying depts (a = afdeling)
CONVENTUS_DEPT_NAME = 'Trampolingymnastik'  # The name of the dept in Conventus
DB_NAME = 'conventus_addons'  # the name of the MongoDB database to store the trampolinist and sign-out data for Conventus
PAIRED_TEAMS: list[PairedTeam] = [
    {
        'name': 'X25Hold 31 & 32',
        'teams': [
            {'team_name': 'X25Hold 31 - Trampolin - Begynder/Øvet fra skolestart til 8 år', 'ref': 'X25Hold 31', 'id': '984967'},
            {'team_name': 'X25Hold 32 - Trampolin - Begynder/Øvet 8 år+', 'ref': 'X25Hold 32', 'id': '984969'},
        ],
    },
    {
        'name': 'Hold 37*',
        'teams': [
            {'team_name': 'Hold 37 - Trampolin - Aspirantholdet', 'ref': 'Hold 37', 'id': '1044960'},
            {'team_name': 'Hold 37 B - Trampolin - Aspirantholdet', 'ref': 'Hold 37 B', 'id': '1047925'},
        ],
    },
    {
        'name': 'Hold 38*',
        'teams': [
            {'team_name': 'Hold 38 - Trampolin - Konkurrence/Elite', 'ref': 'Hold 38', 'id': '1044963'},
            {'team_name': 'Hold 38 B - Trampolin - Konkurrence/Elite', 'ref': 'Hold 38 B', 'id': '1044965'},
        ],
    },
]


class TeamDataBase(TypedDict):
    ref: str
    id: str


class TeamData(TeamDataBase, total=False):
    team_name: str


class LoginResultBase(TypedDict):
    login_success: bool


class LoginResult(LoginResultBase, total=False):
    cookies: RequestsCookieJar
    home_page: bytes


class PairedTeam(TypedDict):
    name: str
    teams: list[TeamData]


@dataclass(frozen=True)
class PathConfig:
    script: Path
    script_name: str
    script_path: Path
    base_path: Path
    data_path: Path
    app_path: Path
    i18n_path: Path
    log_path: Path


@lru_cache(maxsize=1)
def _cached_paths():
    script = Path(__file__)
    script_path = script.parent
    base_path = script_path.parent
    data_path = base_path / 'data'
    try:
        if not data_path.exists():
            # need exist_ok=True to avoid Gunicorn workers trying to create the same directory at the same
            data_path.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        # Fallback: /tmp/data
        data_path = Path('/tmp/data')
        data_path.mkdir(exist_ok=True)
    app_folder = 'cgi-bin'
    app_path = base_path / app_folder
    if not app_path.exists():
        app_folder = 'app'
        app_path = base_path / app_folder
    i18n_folder = 'i18n'
    i18n_path = app_path / i18n_folder
    log_path = base_path / 'logs'
    try:
        if not log_path.exists():
            log_path.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        # Fallback: /tmp/logs
        log_path = Path('/tmp/logs')
        log_path.mkdir(exist_ok=True)
    return PathConfig(
        script=script,
        script_name=script.name,
        script_path=script_path,
        base_path=base_path,
        data_path=data_path,
        app_path=app_path,
        i18n_path=i18n_path,
        log_path=log_path,
    )


def path_handler(request: str | None = None) -> Path:
    paths = _cached_paths()
    if request is None:
        return paths
    # return the requested attribute from the PathConfig dataclass (or raise a clear error)
    try:
        return getattr(paths, request)
    except AttributeError as e:
        raise KeyError(f'Unknown path key: {request}') from e


def configure_flask_logger(debug=False, trace=False):
    paths = _cached_paths()
    # Hardcode the name or use 'app.log' so it doesn't change based on how flask is launched
    log_file = paths.log_path / 'flask.log'
    level = logging.TRACE if trace else (logging.DEBUG if debug else logging.INFO)  # type: ignore[attr-defined]

    # Create a formatter consistent with your existing style
    formatter = logging.Formatter('%(asctime)s %(levelname)s:%(name)s: %(message)s')

    # Create a File Handler
    # file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler = RotatingFileHandler(log_file, encoding='utf-8', maxBytes=10485760, backupCount=10)
    file_handler.setFormatter(formatter)
    file_handler.setLevel(level)

    # Get the ROOT logger and add the handler
    # This ensures trampolinists.py and app.py both log to the same file
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Remove existing handlers to avoid duplicate logs in console/file
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    root_logger.addHandler(file_handler)

    # Optional: Also send logs to Console so 'flask run' still shows them
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # This specifically silences the heartbeat noise even when the rest of the app is in DEBUG/TRACE
    logging.getLogger('pymongo').setLevel(logging.INFO)
    logging.getLogger('pymongo.command').setLevel(logging.INFO)
    logging.getLogger('pymongo.server').setLevel(logging.INFO)


def configure_local_logger(debug=False, trace=False):
    """
    Call this ONLY when running as standalone to create the <script>.log file and directory structure.
    """
    paths = _cached_paths()
    log_file = (
        paths.log_path / f'{Path(sys.argv[0]).stem}.log'
    )  # use Path(sys.argv[0]) instead of paths.script, so that the debugging log file is named after the actual script being run
    level = logging.TRACE if trace else (logging.DEBUG if debug else logging.INFO)  # type: ignore[attr-defined]

    # Switches on logging of the requests module.
    logging.basicConfig(filename=log_file, encoding='utf-8', level=level, format='%(asctime)s %(levelname)s:%(name)s: %(message)s')

    # This specifically silences the heartbeat noise even when the rest of the app is in DEBUG/TRACE
    logging.getLogger('pymongo').setLevel(logging.INFO)
    logging.getLogger('pymongo.command').setLevel(logging.INFO)
    logging.getLogger('pymongo.server').setLevel(logging.INFO)


def date_da_to_std(date_da: str) -> str:
    # Convert a date string from "dd-mm-yyyy" to "yyyy-mm-dd"
    return datetime.strptime(date_da, '%d-%m-%Y').strftime('%Y-%m-%d')


def date_std_to_da(date_std: str) -> str:
    # Convert a date string from "yyyy-mm-dd" to "dd-mm-yyyy"
    return datetime.strptime(date_std, '%Y-%m-%d').strftime('%d-%m-%Y')


def current_season():
    # if current month is between September and December, return "last 2 digits of this year/last 2 digits of next year", otherwise return "last 2
    # digits of last year/last 2 digits of this year". NB: current_year + 1 on a year ending in 99 yields 99/100 rather than 99/00.
    # current_year - 1 on a year ending in 00 yields 00/-1 rather than 99/00. if the current year ends in 01-09, this would havve a sigle digie year.
    # Adjusting the logic to handle these edge cases isn't necessary, because i don't expect this to be in use in the year 2100 or above.
    current_year = date.today().year % 100  # Adjust this based on the current date when you run the test
    return f'{current_year}/{current_year + 1}' if date.today().month >= 9 else f'{current_year - 1}/{current_year}'


def get_pairing(team: TeamData, paired_teams: list[PairedTeam] | None = None) -> str | None:
    if paired_teams is None:
        # paired_teams = fetch_paired_teams_from_db()
        paired_teams = PAIRED_TEAMS  # until we implement the actual fetching from the database
    for pair in paired_teams:
        if any(team['ref'] == t['ref'] for t in pair['teams']):
            return pair['name']
    return None


def get_paired_team_ids(paired_name, paired_teams: list[PairedTeam] | None = None) -> str | None:
    if paired_teams is None:
        # paired_teams = fetch_paired_teams_from_db()
        paired_teams = PAIRED_TEAMS  # until we implement the actual fetching from the database
    for pair in paired_teams:
        if paired_name == pair['name']:
            return ','.join(t['id'] for t in pair['teams'])
    return None  # if no pair found, return None


def get_pairing_from_team_id(team_id: str, paired_teams: list[PairedTeam] | None = None) -> str | None:
    if paired_teams is None:
        # paired_teams = fetch_paired_teams_from_db()
        paired_teams = PAIRED_TEAMS  # until we implement the actual fetching from the database
    for pair in paired_teams:
        if any(team['id'] == team_id for team in pair['teams']):
            return pair['name']
    return None


def localize_reason(reason: str) -> str:
    logger.debug('----------------')
    logger.debug(reason)
    reason_l10n: list[str] = reason.split(':')  # split the reason into reason and comment
    logger.debug(str(reason_l10n))
    reason_l10n[0] = i18n.t('app.' + reason_l10n[0])  # localize the reason, but not any comment added
    logger.debug(reason_l10n[0])
    logger.debug(':'.join(reason_l10n))
    logger.debug('----------------')
    return ':'.join(reason_l10n)
