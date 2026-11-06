"""Publish the existing Zapper Sports EPG contract from explicit feed bindings."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET

UTC = timezone.utc
PLEX_MAP = 'https://raw.githubusercontent.com/iptv-org/epg/master/sites/plex.tv/plex.tv_us.channels.xml'
ROOT = Path(__file__).resolve().parents[2]

def fetch(url, headers=None, data=None):
    request = urllib.request.Request(url, headers={'User-Agent': 'Zapper-EPG/1.0',
        'Accept': 'application/json,application/xml,text/xml', 'x-plex-provider-version': '7.2',
        **(headers or {})}, data=json.dumps(data).encode() if data is not None else None)
    with urllib.request.urlopen(request, timeout=25) as response:
        if response.status != 200:
            raise ValueError('HTTP ' + str(response.status))
        data = response.read(4_000_001)
    if len(data) > 4_000_000:
        raise ValueError('Response exceeds bound')
    return data

def parse_winplay(data, signal):
    matching = [r['content'] for r in json.loads(data).get('result', []) if r.get('content', {}).get('signalId') == signal]
    if len(matching) != 1:
        raise ValueError('Exact Win signal missing or ambiguous')
    programmes = []
    for p in matching[0].get('epg', []):
        # The official provider updates title while programName can retain an older title.
        title = p.get('title') or p.get('programName')
        programmes.append({'title': title, 'description': p.get('description') or '',
            # XMLTV serializes seconds. Normalize the provider's millisecond jitter
            # before overlap validation; genuine whole-second overlaps still fail.
            'start': datetime.fromisoformat(p['startTime'].replace('Z', '+00:00')).replace(microsecond=0),
            'stop': datetime.fromisoformat(p['endTime'].replace('Z', '+00:00')).replace(microsecond=0)})
    return programmes

def winplay_schedule(signal, now, read):
    base = 'https://unity.tbxapis.com/v0'
    auth = json.loads(read(base + '/auth/public', headers={'Content-Type': 'application/json'},
        data={'auth': {'sub': '6a561d048728db7c786b53b0941d0dd9', 'country': None,
                       'currentProfile': None, 'device': None, 'language': None}}))
    # Public anonymous client session only; token stays in memory and is never output.
    headers = {'Authorization': 'JWT ' + auth['token']['access_token']}
    sections = json.loads(read(base + '/sections?page=1&pageSize=400', headers=headers))
    section = next(x for x in sections['result'] if x.get('name') == 'Programación')
    components = json.loads(read(base + '/sections/' + urllib.parse.quote(section['id'], safe='') + '/components', headers=headers))
    link = next(x['itemsURL'] for x in components['result'] if x.get('active') and x.get('componentType') == 'epg_grid')
    parsed = urllib.parse.urlsplit(link)
    if parsed.scheme != 'https' or parsed.hostname != 'unity.tbxapis.com' or parsed.username or parsed.password:
        raise ValueError('Unexpected official Win EPG host')
    query = urllib.parse.urlencode({'pageSize': 25, 'page': 1,
        'fromEpg': iso(now - timedelta(hours=6)), 'toEpg': iso(now + timedelta(hours=72))})
    url = link + '?' + query
    return parse_winplay(read(url, headers=headers), signal), [url]

def parse_sky(data, sid):
    rows = [s for s in json.loads(data).get('schedule', []) if str(s.get('sid')) == sid]
    if len(rows) != 1:
        raise ValueError('Exact Sky service missing or ambiguous')
    return [{'start': datetime.fromtimestamp(e['st'], UTC),
        'stop': datetime.fromtimestamp(e['st'] + e['d'], UTC),
        'title': e['t'], 'description': e.get('sy') or ''} for e in rows[0].get('events', [])]

def iso(value):
    return value.astimezone(UTC).isoformat(timespec='seconds').replace('+00:00', 'Z')

def xmltime(value):
    return value.astimezone(UTC).strftime('%Y%m%d%H%M%S +0000')

def parse_plex(data):
    result = []
    for item in json.loads(data)['MediaContainer']['Metadata']:
        title = item.get('grandparentTitle') or item['title']
        for segment in item.get('Media', []):
            start = datetime.fromtimestamp(segment['beginsAt'], UTC)
            stop = datetime.fromtimestamp(segment['endsAt'], UTC)
            result.append({'start': start, 'stop': stop, 'title': title,
                'description': item.get('summary', ''),
                'subtitle': item.get('title', '') if item.get('grandparentTitle') else ''})
    return result

def parse_realmadrid(data):
    starts = {}
    for day in json.loads(data).values():
        for container in day:
            for period, contents in container.get('contenido', {}).items():
                for item in contents.get('item', []):
                    start = datetime.fromisoformat(item['hora'].replace('Z', '+00:00'))
                    if start.utcoffset() != timedelta(0):
                        raise ValueError('Unexpected Real Madrid API timezone')
                    # The official player treats after-midnight items in noche as next day.
                    if period == 'noche' and start.hour < 19:
                        start += timedelta(days=1)
                    program = item['programa']
                    title = program if isinstance(program, str) else program['content']
                    record = {'start': start, 'title': title, 'description': item.get('descripcion') or ''}
                    if start in starts and starts[start]['title'] != title:
                        raise ValueError('Conflicting Real Madrid programmes')
                    starts[start] = record
    ordered = sorted(starts.values(), key=lambda x: x['start'])
    # End times are the next published start; omit the final item rather than invent its end.
    return [dict(item, stop=following['start']) for item, following in zip(ordered, ordered[1:])
        if timedelta(0) < following['start'] - item['start'] <= timedelta(hours=6)]

def normalize(programmes, now):
    unique = {}
    for p in programmes:
        if not (timedelta(0) < p['stop'] - p['start'] <= timedelta(hours=24)):
            raise ValueError('Invalid programme interval')
        if p['stop'] <= now - timedelta(hours=6) or p['start'] > now + timedelta(hours=72):
            continue
        if not p['title'].strip():
            raise ValueError('Missing programme title')
        key = (p['start'], p['stop'], p['title'])
        unique[key] = p
    ordered = sorted(unique.values(), key=lambda x: (x['start'], x['stop']))
    if any(a['stop'] > b['start'] for a, b in zip(ordered, ordered[1:])):
        raise ValueError('Overlapping provider programmes')
    if not any(p['stop'] > now for p in ordered):
        raise ValueError('No current or future schedule')
    return ordered

def provider_schedule(binding, now, channel_map, read=fetch):
    schedule = binding['schedule']
    if schedule['provider'] == 'plex':
        matches = [e for e in channel_map.findall('channel') if e.get('site_id') == schedule['key']]
        if len(matches) != 1 or matches[0].get('lang') != schedule['language'] or matches[0].get('xmltv_id') != schedule['providerChannelID'] or (matches[0].text or '').casefold() != binding['name'].casefold():
            raise ValueError('Exact Plex edition mapping changed')
        programmes = []
        urls = []
        for offset in (-1, 0, 1, 2):
            date = (now + timedelta(days=offset)).strftime('%Y-%m-%d')
            url = 'https://epg.provider.plex.tv/grid?channelGridKey=' + schedule['key'] + '&date=' + date
            programmes.extend(parse_plex(read(url)))
            urls.append(url)
    elif schedule['provider'] == 'realmadrid':
        url = 'https://publish.realmadrid.com/content/sling/app-servlets/realmadrid/tv-schedule.json?lang=' + schedule['key'] + '&days=4'
        programmes = parse_realmadrid(read(url))
        urls = [url]
    elif schedule['provider'] == 'winplay':
        programmes, urls = winplay_schedule(schedule['key'], now, read)
    elif schedule['provider'] == 'sky':
        if schedule['region'] != 'GB' or schedule['providerChannelID'] != 'SkySportsCricket.uk@HD' or schedule['key'] != '1702':
            raise ValueError('Unapproved Sky regional edition')
        programmes, urls = [], []
        for offset in (-1, 0, 1, 2):
            date = (now + timedelta(days=offset)).strftime('%Y%m%d')
            url = 'https://awk.epgsky.com/hawk/linear/schedule/' + date + '/' + schedule['key']
            programmes.extend(parse_sky(read(url, headers={'X-SkyOTT-Territory': 'GB'}), schedule['key']))
            urls.append(url)
    else:
        raise ValueError('Unsupported provider')
    return normalize(programmes, now), urls

def validate(root, manifest, now):
    inventory = {c['id']: c for c in manifest['channels']}
    channels = root.findall('channel')
    if len(channels) != 15 or {c.get('id') for c in channels} != set(inventory):
        raise ValueError('Sports inventory changed')
    for channel in channels:
        binding = inventory[channel.get('id')]
        if channel.get('zapper-content-id') != binding['contentID'] or channel.get('zapper-source-id') != binding['sourceID'] or channel.get('zapper-presentation-slot') != str(binding['presentationSlot']):
            raise ValueError('Channel/source binding changed')
        if channel.get('zapper-source-id') not in binding['sourceIDs']:
            raise ValueError('Source is not a known alternative')
        if not binding['schedule'] and channel.get('zapper-verification-status') != 'UNKNOWN':
            raise ValueError('Unbound channel must remain unknown')
    by_channel = {}
    for program in root.findall('programme'):
        key = program.get('channel')
        if key not in inventory or not inventory[key]['schedule']:
            raise ValueError('Unapproved schedule identity')
        if program.get('zapper-verification-status') != 'VERIFIED_PROGRAMME' or not program.get('zapper-provenance'):
            raise ValueError('Missing programme provenance')
        start = datetime.strptime(program.get('start'), '%Y%m%d%H%M%S %z')
        stop = datetime.strptime(program.get('stop'), '%Y%m%d%H%M%S %z')
        checked = datetime.fromisoformat(program.get('zapper-last-verified-utc').replace('Z', '+00:00'))
        if checked > now or not (start < stop and start >= now - timedelta(days=30) and stop <= now + timedelta(days=14)):
            raise ValueError('Invalid programme dates')
        by_channel.setdefault(key, []).append((start, stop))
    if not any(stop > now for rows in by_channel.values() for _, stop in rows):
        raise ValueError('No usable sports programming')
    return by_channel

def build(directory=ROOT, read=fetch, now=None):
    now = now or datetime.now(UTC)
    manifest = json.loads((directory / 'publication/sports/guide-bindings.json').read_text())
    previous_path = directory / 'guide-sports-news.xml'
    previous = ET.parse(previous_path).getroot() if previous_path.exists() else ET.Element('tv')
    try:
        channel_map = ET.fromstring(read(PLEX_MAP))
    except Exception:
        channel_map = ET.Element('channels')
    root = ET.Element('tv', {'generator-info-name': 'Zapper verified Sports EPG v1'})
    report = {'generatedAtUTC': iso(now), 'channels': [], 'failures': []}
    def obtain(binding):
        if not binding['schedule']:
            return binding, None, [], None
        try:
            programmes, urls = provider_schedule(binding, now, channel_map, read)
            return binding, programmes, urls, None
        except Exception as error:
            # Keep diagnostics sanitized: no request headers, tokens or consumer address.
            return binding, None, [], type(error).__name__ + ': ' + str(error)[:160]
    results = list(ThreadPoolExecutor(max_workers=5).map(obtain, manifest['channels']))
    for binding, programmes, urls, error in results:
        provenance = binding['schedule']['identityEvidence'] if binding['schedule'] else 'publication/sports/guide-bindings.json#' + binding['contentID']
        status = 'VERIFIED_PROGRAMME' if programmes else 'UNKNOWN'
        attributes = {'id': binding['id'], 'zapper-content-id': binding['contentID'], 'zapper-source-id': binding['sourceID'], 'zapper-presentation-slot': str(binding['presentationSlot']), 'zapper-verification-status': status, 'zapper-provenance': provenance}
        if programmes:
            attributes['zapper-last-verified-utc'] = iso(now)
        channel = ET.SubElement(root, 'channel', attributes)
        ET.SubElement(channel, 'display-name').text = binding['name']
        if programmes:
            for item in programmes:
                proof = urls[0]
                attrs = {'channel': binding['id'], 'start': xmltime(item['start']), 'stop': xmltime(item['stop']), 'zapper-verification-status': 'VERIFIED_PROGRAMME', 'zapper-provenance': proof, 'zapper-last-verified-utc': iso(now)}
                attrs['zapper-programme-id'] = hashlib.sha256((binding['id'] + attrs['start'] + item['title']).encode()).hexdigest()[:24]
                p = ET.SubElement(root, 'programme', attrs)
                ET.SubElement(p, 'title', {'lang': binding['schedule']['language']}).text = item['title']
                if item.get('subtitle'):
                    ET.SubElement(p, 'sub-title').text = item['subtitle']
                if item.get('description'):
                    ET.SubElement(p, 'desc').text = item['description']
        elif binding['schedule']:
            old = previous.find("channel[@id='" + binding['id'] + "']")
            retained = [p for p in previous.findall('programme') if p.get('channel') == binding['id'] and datetime.strptime(p.get('stop'), '%Y%m%d%H%M%S %z') > now - timedelta(hours=6)]
            if old is not None and any(datetime.strptime(p.get('stop'), '%Y%m%d%H%M%S %z') > now for p in retained):
                root.remove(channel)
                root.append(old)
                root.extend(retained)
                status = 'RETAINED_LAST_VALID'
        report['channels'].append({'number': binding['zapperNumber'], 'name': binding['name'], 'status': status, 'freshProgrammeCount': len(programmes or []), 'reason': error or binding.get('unavailableReason')})
        if error:
            report['failures'].append({'name': binding['name'], 'error': error})
    # XMLTV requires all channel declarations before any programme records.
    root[:] = root.findall('channel') + sorted(root.findall('programme'), key=lambda p: (p.get('channel'), p.get('start')))
    validate(root, manifest, now)
    ET.indent(root)
    temporary = directory / 'guide-sports-news-candidate.xml'
    ET.ElementTree(root).write(temporary, encoding='utf-8', xml_declaration=True)
    (directory / 'sports-guide-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    return report

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=ROOT)
    args = parser.parse_args()
    print(json.dumps(build(args.root), ensure_ascii=False))
