#!/usr/bin/python

import argparse
from bs4 import BeautifulSoup, ResultSet, Tag  # https://pypi.org/project/beautifulsoup4/
from bs4.element import NavigableString
import datetime as dt
from dataclasses import dataclass
from functools import lru_cache
import html
from icalendar import Calendar, Event, vText  # https://pypi.org/project/icalendar/
import json
import logging
import os
from pathlib import Path
import re
import requests  # https://pypi.org/project/requests/
import sys
from time import sleep, time
from types import SimpleNamespace
from typing import cast
import urllib.parse


REQUEST_TIMEOUT = (5, 30)  # (connect, read) seconds
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


@dataclass(frozen=True)
class Paths:
    script: Path
    base_path: Path
    data_path: Path
    log_path: Path
    cal_file: Path
    cal_json_file: Path
    month_map_file: Path
    trampoline_events_file: Path
    manual_events_file: Path


@lru_cache(maxsize=1)
def get_paths() -> Paths:
    script = Path(__file__)
    base_path = script.parent.parent
    data_path = base_path / 'data'
    log_path = base_path / 'logs'
    if not data_path.exists():
        data_path.mkdir()
    if not log_path.exists():
        log_path.mkdir(parents=True)
    return Paths(
        script=script,
        base_path=base_path,
        data_path=data_path,
        log_path=log_path,
        cal_file=data_path / 'greve_tra.ics',
        cal_json_file=data_path / 'trampoline_events.json',
        month_map_file=data_path / 'month_map.json',
        trampoline_events_file=data_path / 'trampoline_events.json',
        manual_events_file=data_path / 'manual_events.json',
    )


def _configure_local_logger(debug=False) -> None:
    """Call this when running as standalone to create the schedule.log file and directory structure."""
    log_file = get_paths().log_path / f'{get_paths().script.stem}.log'
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(filename=log_file, encoding='utf-8', level=level, format='%(asctime)s %(levelname)s:%(name)s: %(message)s')
    print(f'Log file can be found at {log_file}')


def publish(args: argparse.Namespace) -> None:
    import paramiko
    import socket

    paths = get_paths()
    publish_manual_events: bool = args.write_manual_events and paths.manual_events_file.exists()

    if args.skip_publish and not publish_manual_events:
        print('Publishing all files skipped')
        return
    if args.skip_publish_json and args.skip_publish_ics and not publish_manual_events:
        print('Publishing all files skipped')
        return
    if args.skip_publish_json:
        print('Publishing JSON file skipped')
    elif args.skip_publish_ics:
        print('Publishing iCalendar file skipped')

    files: list[dict[str, str | Path]] = []
    if not args.skip_publish_json:
        files.append(
            {
                'name': paths.cal_json_file.name,
                'local': paths.cal_json_file,
                'remote': f'/customers/1/d/8/{args.username}/webroots/www/{paths.cal_json_file.name}',
            }
        )
    if publish_manual_events:
        files.append(
            {
                'name': paths.manual_events_file.name,
                'local': paths.manual_events_file,
                'remote': f'/customers/1/d/8/{args.username}/webroots/www/{paths.manual_events_file.name}',
            }
        )
    if not args.skip_publish_ics:
        files.append(
            {
                'name': paths.cal_file.name,
                'local': paths.cal_file,
                'remote': f'/customers/1/d/8/{args.username}/webroots/www/{paths.cal_file.name}',
            }
        )

    # Connect to the server
    ssh = paramiko.SSHClient()
    try:
        ssh.load_host_keys(os.path.expanduser('~/.ssh/known_hosts'))
        # ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(hostname=f'ssh.{args.username}.service.one', username=f'{args.username}_ssh')

        with ssh.open_sftp() as sftp:
            for file in files:
                sftp.put(file['local'], cast(str, file['remote']))  # use cast to avoid mypy error (the type is correct but mypy can't figure it out)
                print(f'{file["name"]} published successfully')
                logger.info(f'{file["name"]} published successfully')
        ssh.close()
        return

    except paramiko.AuthenticationException:
        print('Publishing failed: Authentication error. Check your username/SSH keys.')
        logger.exception('Publishing failed: Authentication error. Check your username/SSH keys.')
    except (paramiko.SSHException, socket.error) as e:
        print(f'Publishing failed: Network or SSH connection error. ({e})')
        logger.exception(f'Publishing failed: Network or SSH connection error. ({e})')
    except Exception as e:
        print(f'Publishing failed: An unexpected error occurred: {e}')
        logger.exception(f'Publishing failed: An unexpected error occurred: {e}')
    finally:
        ssh.close()
    sys.exit(1)


def get_events_from_gymdanmark(config):
    print('Finding trampoline event urls')
    current_year = dt.datetime.now().year

    file = get_paths().data_path / 'gymdanmark_events.html'
    content = None
    if (
        file.exists() and time() - os.path.getmtime(file) < 3 * 24 * 3600
    ):  # if the file exists and is less than 3 days old, use it, otherwise fetch new data
        print('Using the cached event table')
        with open(file, 'r', encoding='utf-8') as f:
            content = f.read()
    else:
        url: str = 'https://gymdanmark.dk/wp-admin/admin-ajax.php'
        payload = {
            'post_type': 'all',
            'disciplin_type': 'trampolin',
            'start_date': f'01-01-{current_year}',
            'end_date': '',
            'omraade': 'all',
            'show-as-list': 'on',
            's': '',
            'action': 'calendar_loadmore',
            'orderBy': 'start',
            'page': '1',
            'list': 'yes',
        }
        response = requests.post(url, data=payload, timeout=REQUEST_TIMEOUT)
        if not response.ok:
            response.raise_for_status()
        content = response.text
        with open(file, 'w', encoding='utf-8') as f:
            f.write(content)
    soup = BeautifulSoup(content, features='html.parser')
    trampoline_event_urls = []
    links = soup.find_all('a')
    for link in links:
        if link.get('href'):
            trampoline_event_urls.append(link.get('href'))
    return get_event_data_from_gymdanmark(trampoline_event_urls, config)


def get_link_from_event(text_holder: Tag, regex: str = r'Program|Tidsplan', event_url: str = '') -> str | None:
    header: Tag | None = text_holder.find('h4', string=re.compile(regex, re.IGNORECASE))  # type: ignore[call-overload]
    if header:
        link = text_holder.find('a')
        if link and isinstance(link, Tag):
            href = link.get('href')
            if href and isinstance(href, str):
                return href.strip()
        logger.info(f'{event_url} does not yet have a link for {regex}')
    logger.debug(f'{event_url} text-holder does not have h4 that matches {regex}')
    return None


def get_event_data_from_gymdanmark(trampoline_event_urls, config) -> tuple[list[dict], dict | None]:
    print('Reading activity data for each event')
    events = []
    month_map: dict[str, int] | None = None
    for event_url in trampoline_event_urls:
        if not event_url:
            continue
        event_data = {}
        event_data['url'] = event_url
        logger.debug(event_url)
        event = requests.get(event_url, timeout=REQUEST_TIMEOUT)
        if not event.ok and event.status_code == 522:
            # retry if request fails due to 522 Server Error. Perhaps use a library like 'tenacity' for this?
            sleep(10)
            event = requests.get(event_url, timeout=REQUEST_TIMEOUT)
        if not event.ok:
            event.raise_for_status()

        soup = BeautifulSoup(event.text, features='html.parser')
        # save sample event, if config.save_test_data and not month_map is set, it will save the first event, because month_map isn't set until we read the first event
        if config.save_test_data and not month_map:
            with open(get_paths().data_path / 'event_raw.html', 'w', encoding='utf-8') as f:
                f.write(event.text)
            with open(get_paths().data_path / 'event.html', 'w', encoding='utf-8') as f:
                f.write(str(soup.prettify()))
        if not month_map:
            # only read the month names once, since they are the same for all events
            script_tag = soup.find('script', id='jquery-ui-datepicker-js-after')
            if script_tag and script_tag.string:
                # Extract the JSON object using regex by looking for the first '{' and the last '}' inside the setDefaults(); call
                match = re.search(r'setDefaults\((?P<json_data>\{[^\)]*?\})\);', script_tag.string)

                if match:
                    json_str = match.group('json_data')
                    logger.debug(json_str)

                    # json to dict, then get the month names, and create a mapping from month name to month number
                    data = json.loads(json_str)
                    short_months = data.get('monthNamesShort')

                    if short_months:
                        month_map = {name: i + 1 for i, name in enumerate(short_months)}
                        logger.debug(month_map)
                        with open(get_paths().month_map_file, 'w', encoding='utf-8') as f:
                            json.dump(month_map, f, ensure_ascii=False)

        if not month_map:
            raise Exception('Could not find month names in the page, cannot continue without this mapping')

        article = soup.article
        year: int = 0
        if article:
            logger.debug(article.prettify())

            # if the find fails, it will raise an AttributeError
            try:
                year = int(article.find('span', class_='event-date').find('strong').find('span', class_='year').string.strip())  # type: ignore[union-attr]
            except AttributeError as e:
                raise Exception(f'{event_url} has unexpected format (missing expected year element): {e}') from e

            if year >= dt.datetime.now().year:
                event_data['year'] = year

                try:
                    # <h1 class="entry-title">Trampolin Dan Camp 1</h1>	<span class="event-date"><i class="icon-calendar"></i><strong>31 jan - 1 feb <span class="year">2026</span></strong></span>
                    # <h1 class="entry-title">Klubtræf 2 område 3 &#8211; Trampolin</h1>	<span class="event-date"><i class="icon-calendar"></i><strong>25 jan <span class="year">2026</span></strong></span>
                    event_data['title'] = article.find('h1', class_='entry-title').string  # type: ignore[union-attr]
                    event_data['days'] = article.find('span', class_='event-date').find('strong').find(string=True).strip()  # type: ignore[union-attr]
                except AttributeError as e:
                    raise Exception(f'{event_url} has unexpected format (missing expected element): {e}') from e

                # skip klubtræf events except "område 2"
                if re.search(r'område (?!2)', event_data['title']):
                    continue

                # <div class="info-block">
                # <strong>Sted</strong> Vendsyssel Idrætscenter <br>
                # <strong>Adresse</strong> Vendsyssel Idrætscenter, Stadionvej 17, 9760 Vrå <br>
                # <strong>Tilmeldingsfrist</strong> 17. februar 2026 <br>
                # <strong>Efter&shy;tilmeldings&shy;frist</strong> 24. februar 2026 <br>
                try:
                    place: NavigableString | None = article.find('div', class_='info-block').find('strong', string='Sted')  # type: ignore[union-attr, call-overload]
                    if place:
                        event_data['place'] = place.next_sibling.strip()  # type: ignore[union-attr]
                except AttributeError as e:
                    # raise Exception(f'{event_url} has unexpected format (missing expected place element): {e}') from e
                    logger.warning(f'{event_url} has unexpected format (missing expected place element): {e}')
                try:
                    address: NavigableString | None = article.find('div', class_='info-block').find('strong', string='Adresse')  # type: ignore[union-attr, call-overload]
                    if address:
                        event_data['address'] = address.next_sibling.strip()  # type: ignore[union-attr]
                except AttributeError as e:
                    # raise Exception(f'{event_url} has unexpected format (missing expected address element): {e}') from e
                    logger.warning(f'{event_url} has unexpected format (missing expected address element): {e}')
                try:
                    deadline: NavigableString | None = article.find('div', class_='info-block').find('strong', string='Tilmeldingsfrist')  # type: ignore[union-attr, call-overload]
                    if deadline:
                        event_data['deadline'] = deadline.next_sibling.strip()  # type: ignore[union-attr]
                except AttributeError:
                    logger.warning(f'{event_url} has unexpected format (missing expected deadline element)')

                # <div class="text-holder">
                # <h4 class="info-title">Infobrev og program</h4>
                # <p>Tilgængelig den 25. februar 2026</p>
                # <a href="https://gymdanmark.dk/wp-content/uploads/2026/02/2026-Program-DanCup-Odense.pdf" target="_blank" class="btn btn-secondary">Læs mere</a>
                # </div>
                # <div class="text-holder">
                # <h4 class="info-title">Tidsplan</h4>
                # <p>Tilgængelig den 6. marts 2026</p>
                # <a href="https://gymdanmark.dk/wp-content/uploads/2026/03/Tidsplan_KT2O2_2026.pdf" target="_blank" class="btn btn-secondary">Læs mere</a>
                # </div>
                program_found: bool = False
                text_holders: ResultSet[Tag] = article.find_all('div', class_='text-holder')
                if not text_holders:
                    logger.warning(f'{event_url} has unexpected format (no text-holder elements -- this element is expected for most competitions)')
                info_link: str | None = None
                for text_holder in text_holders:
                    if not info_link and 'klubtraef' in event_url:
                        info_link = get_link_from_event(text_holder, r'Infobrev', event_url)
                    program_link = get_link_from_event(text_holder, r'Program|Tidsplan', event_url)
                    if program_link and info_link:
                        event_data['program'] = f'{program_link}\n{info_link}'
                    elif program_link:
                        if 'klubtraef' in event_url:
                            logger.warning(f'{event_url} does not have an info letter)')
                        event_data['program'] = program_link
                    if program_link:
                        program_found = True
                        break
                if not program_found and 'konkurrence' not in event_url:
                    # if url does not contain "konkurrence", then don't warn about a missing program
                    logger.warning(f'{event_url} has unexpected format (missing expected program element)')
                logger.debug(event_data)
                events.append(event_data)
            elif year == 0:
                raise Exception(f'{event_url} has unexpected format (no year found)')
        else:
            raise Exception(f'{event_url} has unexpected format (no article found)')
    if config.save_test_data:
        write_events_to_json(events)
    return events, month_map


def get_manually_configured_events():
    """Read the events that have been added to manual_events.json."""
    print('Getting manually configured events')
    events = []
    if get_paths().manual_events_file.exists():
        with open(get_paths().manual_events_file, 'r', encoding='utf-8') as f:
            events = json.load(f)
    logger.debug(events)
    return events


def parse_event_dates(e, month_map) -> tuple[dt.datetime | dt.date, dt.datetime | dt.date]:
    """Parse event day strings extracted from GymDanmark event pages.
    Parameters
    *  e: dict Event dict with keys:
    *    'year' (int): event year.
    *    'days' (str): day/month text from the page, expected formats:
    *      "DD mmm"             (single day, e.g. "25 jan")
    *      "DD mmm - DD mmm"    (range, e.g. "31 jan - 1 feb") Other keys are ignored.
    *  month_map: dict Mapping of short month name (string) -> month number (1-12), e.g. {'jan': 1, 'feb': 2, ...} as produced from the site JS.
    Returns
    * (event_start: datetime.date, event_end: datetime.date) event_start is the start date. event_end is the exclusive end date:
    * For a single-day event both dates span one day (end = start + 1 day).
    * For a range a - b the end is set to the day after b, so it can be used directly as an exclusive dtend in iCalendar generation.
    Raises
    * Exception on any unexpected or unparseable days value or missing mapping. This function is intentionally fail-fast: if the page format changes it will raise immediately so the caller (and logs) identify the failing input.
    Notes / limitations
    * Expects the site's current format (exact ' - ' separator and single-space tokens).
    * It is assumed that there are no trampoline events roll over year boundaries
    """
    day: int = 0
    event_start: dt.datetime | dt.date | None = None
    event_end: dt.datetime | dt.date | None = None
    year = e.get('year')
    days = e.get('days', '')
    if not days and e.get('start') and e.get('end'):
        # 19980119T230000 (year, month, day, T, hour, minute, second)
        event_start = dt.datetime.fromisoformat(e.get('start'))
        event_end = dt.datetime.fromisoformat(e.get('end'))
    elif ' - ' in days:  # multiple days in format "day month - day month", e.g. 31 jan - 1 feb, split into start and end
        start, end = days.split(' - ')
        day, month_txt = start.split()
        day_end, month_txt_end = end.split()
        event_end = dt.date(year, month_map[month_txt_end], int(day_end)) + dt.timedelta(days=1)
    elif days and ' ' in days:  # single day in format "day month", e.g. "31 jan", split into day and month
        day, month_txt = days.split()
    else:
        raise Exception(f'Unexpected value for event days: {e.get("days")}')
    if not event_start:
        event_start = dt.date(year, month_map[month_txt], int(day))
    if not event_end:
        event_end = event_start + dt.timedelta(days=1)
    return event_start, event_end


def compute_event_uid(e, event_start, fallback_title) -> str:
    """Compute a stable identifier for an event, so the same event can be recognised
    across runs even if its position in the events list changes.
    Uses the same logic as the iCalendar UID: the event's own url if it has one,
    otherwise a combination of its title and start date.
    """
    title = html.unescape(e.get('title', fallback_title))
    title_based_uid = f'{title}_{event_start.isoformat()}'
    return e.get('url', title_based_uid)


def assign_dtstamps(events: list[dict], events_old: list[dict], month_map: dict) -> list[dict]:
    """Set a 'dtstamp' field on each event in `events`.
    If an event already existed in `events_old` and is otherwise unchanged (comparing
    every field except 'dtstamp'), its old dtstamp is carried over so DTSTAMP in the
    iCalendar file only changes for events that were actually added or modified.
    Events that are new or have changed get a fresh 'now' dtstamp.
    """
    old_by_uid: dict[str, dict] = {}
    for count, old_event in enumerate(events_old, start=1):
        old_event_start, _ = parse_event_dates(old_event, month_map)
        uid = compute_event_uid(old_event, old_event_start, f'TRA{count}')
        old_by_uid[uid] = old_event

    dtstamp_now: str = dt.datetime.now().replace(microsecond=0).isoformat()
    for count, event in enumerate(events, start=1):
        event_start, _ = parse_event_dates(event, month_map)
        uid = compute_event_uid(event, event_start, f'TRA{count}')
        matched_old_event = old_by_uid.get(uid)
        if matched_old_event is not None:
            old_event_without_stamp = {k: v for k, v in matched_old_event.items() if k != 'dtstamp'}
            event_without_stamp = {k: v for k, v in event.items() if k != 'dtstamp'}
            if old_event_without_stamp == event_without_stamp:
                event['dtstamp'] = matched_old_event.get('dtstamp', dtstamp_now)
                continue
        event['dtstamp'] = dtstamp_now
    return events


def create_ical_file(events, month_map) -> bool:
    print('Writing iCalendar file')
    cal = Calendar()
    cal.add('prodid', '-//Greve Gymnastik og Trampolin//greve-gymnastik.dk//')
    cal.add('version', '2.0')
    cal.add('summary', 'Trampolin Aktiviteter')
    cal.add('X-WR-CALNAME', 'Trampolin Aktiviteter')

    count = 0
    for e in events:
        count += 1
        event = Event()
        event_start, event_end = parse_event_dates(e, month_map)
        title = html.unescape(e.get('title', f'TRA{count}'))
        uid = compute_event_uid(e, event_start, f'TRA{count}')
        event.add('uid', urllib.parse.quote_plus(f'{uid}'))
        dtstamp = dt.datetime.fromisoformat(e['dtstamp']) if e.get('dtstamp') else dt.datetime.now()
        event.add('dtstamp', dtstamp)
        event.add('summary', title)
        location = e.get('address', e.get('place', ''))
        if location:
            event['location'] = vText(location)
        description = ''
        if e.get('url'):
            description += e['url']
            # there is a bug in the wrapping of characters in icalendar, if the 75th character is \ and the 76th character is n (to create a newline),
            # then the resulting ical file will have "\\\r\n n" instead of "\r\n \\n", which causes Google Calendar to ignore the event
            # BAD -- DESCRIPTION:https://gymdanmark.dk/arrangement/boblersamling-2026-traener/\\\r\n n Tilmeldingsfrist: 6. juni 2026\r\nLOCATION:Femhøje Idrætscenter\\, Femhøje 5\\, 9800 Hjørring\r\n
            # GOOD - DESCRIPTION:https://gymdanmark.dk/arrangement/boblersamling-2026-traener/ \r\n \\n Tilmeldingsfrist: 6. juni 2026\r\nLOCATION:Femhøje Idrætscenter\\, Femhøje 5\\, 9800 Hjørring\r\n
            if len(e['url']) == 61:  # if the url is exactly 61 characters long
                description += ' '
        if e.get('deadline'):
            if description:
                description += '\n '
            description += f'Tilmeldingsfrist: {e["deadline"]}'
        if e.get('program'):
            if description:
                description += '\n '
            description += f'Program: {e["program"]}'
        if description:
            event.add('description', description)
        event.add('dtstart', event_start)
        event.add('dtend', event_end)
        logger.debug(event)
        cal.add_component(event)

    try:
        with open(get_paths().cal_file, 'wb') as f:
            ics_bytes = cal.to_ical()
            # Check for a fold splitting \n: (\\\r\n n)
            # ...\<CRLF><space>n... (split after backslash)
            pattern = re.compile(rb'\\\r\n n')
            matches = pattern.findall(ics_bytes)
            if matches:
                raise Exception('ERROR: Found fold splitting issue in iCalendar data')
            f.write(ics_bytes)
            # print(repr(cal.to_ical().decode('utf-8')))
    except Exception as e:
        print(f'Error writing iCalendar file: {e}')
        logger.error(f'Error writing iCalendar file: {e}')
        sys.exit(1)

    return True


def write_events_to_json(events=None) -> None:
    """This function is just used to write hard coded events to a json file, so that they can be read by get_manually_configured_events() or to
    write events from get_events_from_gymdanmark to a json file, so that the data can be read by tests without the website data changing and
    breaking the expected values
    """
    data_path = get_paths().data_path
    if events is None:
        events = []

        # 11. april 2027 Forårsopvisning
        event_data = {
            'year': 2027,
            'title': 'Forårsopvisning',
            'days': '11 apr',
            'place': 'Greve Idrætscenter',
            'address': 'Greve Idrætscenter, Lillevangsvej 88, 2670 Greve',
        }
        events.append(event_data)

        # Frivolten
        event_data = {
            'year': 2027,
            'title': 'Frivolten Cup',
            'days': '7 maj - 8 maj',
            'place': 'Herrljunga Sim & Idrottshall',
            'url': 'https://cup.frivolten.com/',
        }
        events.append(event_data)

        # 27. august 2026 Instruktørmøde kl. 18.00-21.00 Borgerhuset
        event_data = {
            'year': 2026,
            'title': 'Instruktørmøde',
            'start': '20260827T160000Z',
            'end': '20260827T190000Z',
            'place': 'Greve Borgerhus',
            'address': 'Greveager 9, 2670 Greve',
        }
        events.append(event_data)

        with open(data_path / 'manual_events.json', 'w', encoding='utf-8') as f:
            json.dump(events, f, ensure_ascii=False)
    else:
        with open(data_path / 'gymdanmark_events.json', 'w', encoding='utf-8') as f:
            json.dump(events, f, ensure_ascii=False)
    return


def download_existing_state(args) -> None:
    """Download trampoline_events.json and manual_events.json from the web server before
    running, so a fresh checkout (e.g. a GitHub Actions runner) has the previous state to
    compare against for the dtstamp diffing in assign_dtstamps(). A missing remote file
    (e.g. the very first run, or manual_events.json never having been published) is treated
    as 'no previous state' rather than an error.
    """
    import paramiko
    import socket

    if getattr(args, 'skip_download', False):
        print('Downloading existing state skipped')
        return

    paths = get_paths()
    files: list[dict] = [
        {
            'name': paths.cal_json_file.name,
            'local': paths.trampoline_events_file,
            'remote': f'/customers/1/d/8/{args.username}/webroots/www/{paths.cal_json_file.name}',
        },
        {
            'name': paths.manual_events_file.name,
            'local': paths.manual_events_file,
            'remote': f'/customers/1/d/8/{args.username}/webroots/www/{paths.manual_events_file.name}',
        },
    ]

    ssh = paramiko.SSHClient()
    try:
        ssh.load_host_keys(os.path.expanduser('~/.ssh/known_hosts'))
        ssh.connect(hostname=f'ssh.{args.username}.service.one', username=f'{args.username}_ssh')

        with ssh.open_sftp() as sftp:
            for file in files:
                try:
                    sftp.get(str(file['remote']), str(file['local']))
                    print(f'{file["name"]} downloaded successfully')
                    logger.info(f'{file["name"]} downloaded successfully')
                except FileNotFoundError:
                    print(f'{file["name"]} not found on server, skipping (first run?)')
                    logger.info(f'{file["name"]} not found on server, skipping (first run?)')

    except paramiko.AuthenticationException:
        print('Downloading existing state failed: Authentication error. Check your username/SSH keys.')
        logger.exception('Downloading existing state failed: Authentication error. Check your username/SSH keys.')
    except (paramiko.SSHException, socket.error) as e:
        print(f'Downloading existing state failed: Network or SSH connection error. ({e})')
        logger.exception(f'Downloading existing state failed: Network or SSH connection error. ({e})')
    except Exception as e:
        print(f'Downloading existing state failed: An unexpected error occurred: {e}')
        logger.exception(f'Downloading existing state failed: An unexpected error occurred: {e}')
    finally:
        ssh.close()


def main(args):
    # Setup local logging based on CLI args
    _configure_local_logger(debug=args.debug)

    config = SimpleNamespace(save_test_data=args.save_test_data)

    paths = get_paths()
    if args.clear_cache:
        events_cache = paths.data_path / 'gymdanmark_events.html'
        if os.path.exists(events_cache):
            os.remove(events_cache)

    download_existing_state(args)

    trampoline_events, month_map = get_events_from_gymdanmark(config)
    logger.debug(trampoline_events)
    if config.save_test_data and trampoline_events:
        # write the events to gymdanmark.json, so they can be read by tests without the website data changing and breaking the expected values
        write_events_to_json(trampoline_events)

    if args.write_manual_events:
        # this is just a lazy way to create a json file with manual events that are hardcoded in write_events_to_json
        write_events_to_json()
    trampoline_events += get_manually_configured_events()
    logger.debug(trampoline_events)

    update = True
    events_old: list[dict] = []
    trampoline_events_file = paths.trampoline_events_file
    if (trampoline_events_file).exists():
        # if trampoline_events.json exists, then read it as events_old and compare to trampoline_events
        try:
            with open(trampoline_events_file, 'r', encoding='utf-8') as f:
                # f = None #inject error for testing error handling
                events_old = json.load(f) or []
        except json.JSONDecodeError:
            # treat empty/invalid file as no previous events
            pass
        except Exception as e:
            raise Exception(f'Error reading trampoline_events.json: {e}') from e
            # sys.exit(1)

    # give each event a dtstamp, carrying over the old one for events that are unchanged
    # (comparing every field except dtstamp itself), and only stamping 'now' for events
    # that are new or have actually changed
    trampoline_events = assign_dtstamps(trampoline_events, events_old, month_map)

    # if there are differences, create a new ical file, otherwise skip creating the ical file, since it would be the same as the existing one
    if events_old == trampoline_events:
        update = False

    if update or not paths.cal_file.exists():
        with open(trampoline_events_file, 'w', encoding='utf-8') as f:
            json.dump(trampoline_events, f, ensure_ascii=False)
        create_ical_file(trampoline_events, month_map)
        publish(args)

    else:
        print('No changes in events, skipping update of iCalendar file')
    return


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--save-test-data', action='store_true', help='write data files that are using for testing')
    parser.add_argument(
        '--write-manual-events', action='store_true', help='this is just used to write hard coded events to a json file and validate it manually'
    )
    parser.add_argument('--debug', action='store_true', help='enable debug logging')
    parser.add_argument('--clear-cache', action='store_true', help='clear the cache to force all new data to be read from GymDanmark')
    parser.add_argument(
        '--republish',
        action='store_true',
        help='republish the the files -- you can also skip files if needed, but --republish together with --skip-publish will do nothing',
    )
    parser.add_argument('--skip-publish', action='store_true', help='skip publishing the iCalendar and json files')
    parser.add_argument('--skip-publish-ics', action='store_true', help='skip publishing the iCalendar file')
    parser.add_argument('--skip-publish-json', action='store_true', help='skip publishing the json file')
    parser.add_argument(
        '--skip-download', action='store_true', help='skip downloading the previously published trampoline_events.json / manual_events.json'
    )
    requiredNamed = parser.add_argument_group('required named arguments')
    requiredNamed.add_argument('--username', '-u', help='ssh username', required=True)

    args = parser.parse_args()
    if args.republish and get_paths().cal_file.exists():
        publish(args)
    elif args.republish and get_paths().trampoline_events_file.exists() and get_paths().month_map_file.exists():
        with open(get_paths().trampoline_events_file, 'r', encoding='utf-8') as f:
            events = json.load(f)
        with open(get_paths().month_map_file, 'r', encoding='utf-8') as f:
            month_map = json.load(f)
        create_ical_file(events, month_map)
        publish(args)
    elif args.republish:
        print('Error: No calendar or events file found. Run without --republish.')
        sys.exit(1)
    else:
        main(args)
