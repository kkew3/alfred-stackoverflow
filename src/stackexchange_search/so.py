import typing as ty
import time
import html
import argparse
import re
import itertools
import subprocess
import contextlib
import functools
import gzip
from pathlib import Path
import os
import hashlib
import json
import dataclasses
import io
import logging

import requests
from PIL import Image


@dataclasses.dataclass
class Answer:
    title: str
    link: str
    tags: ty.List[str]
    is_answered: bool
    score: int


@dataclasses.dataclass
class Site:
    id_: str
    name: str
    audience: str
    icon_url: str
    is_meta: bool


@dataclasses.dataclass
class QuotaInfo:
    quota_remaining: int


@dataclasses.dataclass
class EnvParams:
    cache_max_age: int
    ignore_meta_sites: bool
    result_count: int
    site_id: str
    site_name: str
    api_key: ty.Optional[str]
    client_id: ty.Optional[str]
    proxy: ty.Optional[str]


def get_cachedir() -> Path:
    path = Path(os.environ['alfred_workflow_cache'])
    path.mkdir(exist_ok=True)
    return path


def validate_env() -> EnvParams:
    logger = logging.getLogger('so.validate_env')
    cache_max_age = int(os.environ['cache_max_age'])
    logger.debug('Got cache_max_age=%d', cache_max_age)
    ignore_meta_sites = bool(int(os.environ['ignore_meta_sites']))
    logger.debug('Got ignore_meta_sites=%d', ignore_meta_sites)
    result_count = int(os.environ['result_count'])
    logger.debug('Got result_count=%d', result_count)
    site_id = os.getenv('site_id')
    logger.debug('Got site_id=%r', site_id)
    site_name = os.getenv('site_name')
    logger.debug('Got site_name=%r', site_name)
    api_key = os.environ['api_key'] or None
    logger.debug('Got api_key=%r', api_key)
    client_id = os.environ['client_id'] or None
    logger.debug('Got client_id=%r', client_id)
    proxy = os.environ['proxy'] or None
    logger.debug('Got proxy=%r', proxy)
    return EnvParams(
        cache_max_age,
        ignore_meta_sites,
        result_count,
        site_id,
        site_name,
        api_key,
        client_id,
        proxy,
    )


def build_requests_kwargs(env: EnvParams) -> ty.Dict[str, ty.Any]:
    ua = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:109.0) '
          'Gecko/20100101 Firefox/109.0')
    kwargs = {'headers': {'user-agent': ua}}
    if env.proxy:
        kwargs['proxies'] = {'http': env.proxy, 'https': env.proxy}
    return kwargs


def request_parse_search_api(
    query: str,
    tags: ty.List[str],
    site_id: str,
    env: EnvParams,
) -> ty.Tuple[ty.List[Answer], QuotaInfo]:
    """
    Request the search API and parse the result.

    :param query: the stripped query
    :param tags: the stripped tags
    :param site_id: the site ID
    :param env: preconfigured environment parameters
    """
    logger = logging.getLogger('so.request_search_api')
    url = 'https://api.stackexchange.com/2.2/search/advanced'
    params = {
        'page': 1,
        'pagesize': env.result_count,
        'order': 'desc',
        'sort': 'relevance',
        'site': site_id,
    }
    if env.api_key:
        params['key'] = env.api_key
    if env.client_id:
        params['client_id'] = env.client_id
    if query:
        params['q'] = query
    if tags:
        params['tagged'] = ';'.join(tags)

    logger.debug('Requesting %r for search', url)
    resp = requests.get(url, params, **build_requests_kwargs(env)).json()
    logger.debug('Done requesting %r for search', url)

    answers = []
    try:
        for item in resp['items']:
            answers.append(
                Answer(
                    html.unescape(item['title']),
                    item['link'],
                    item['tags'],
                    item['is_answered'],
                    int(item['score']),
                ))
    except KeyError:
        logger.error('Unexpected response json: %r', resp)
        raise
    logger.debug('Parsed %d answers out of the request', len(answers))
    answers.sort(key=lambda a: int(a.is_answered), reverse=True)
    qi = QuotaInfo(resp['quota_remaining'])
    logger.debug('Parsed the QuotaInfo out of the request')
    return answers, qi


def request_parse_sites_api(env: EnvParams) -> ty.List[Site]:
    """
    Request the sites API and parse the result.

    :param env: preconfigured environment parameters
    """
    logger = logging.getLogger('so.request_sites_api')
    url = 'https://api.stackexchange.com/2.2/sites'
    sites = []
    page = 1
    has_more = True
    while has_more:
        logger.debug('Fetching page %d', page)
        params = {
            'page': page,
            'pagesize': 100,
        }
        if env.api_key:
            params['key'] = env.api_key
        if env.client_id:
            params['client_id'] = env.client_id
        logger.debug('Requesting %r for sites', url)
        resp = requests.get(url, params, **build_requests_kwargs(env)).json()
        logger.debug('Done requesting %r for sites', url)

        try:
            for item in resp['items']:
                if item['site_state'] == 'closed_beta':
                    logger.info('Ignored %r (closed beta)',
                                item['api_site_parameter'])
                    continue
                sites.append(
                    Site(
                        item['api_site_parameter'],
                        html.unescape(item['name']),
                        item['audience'],
                        item['icon_url'],
                        item['site_type'] == 'meta_site',
                    ))
        except KeyError:
            logger.error('Unexpected response json: %r', resp)
            raise
        logger.debug('Parsed %d sites in total out of the request', len(sites))
        has_more = resp['has_more']
        page += 1
    return sites


def request_fzf(query: str, candidates: ty.List[str]) -> ty.List[str]:
    logger = logging.getLogger('so.request_fzf')
    try:
        resp = subprocess.run(['fzf', '--filter', query],
                              input=''.join(map('{}\n'.format, candidates)),
                              text=True,
                              capture_output=True,
                              check=True)
    except subprocess.CalledProcessError as err:
        if err.returncode == 1:
            return []
        logger.error('Error calling: fzf --filter %r', query)
        raise
    return re.findall(r'(.*)\n', resp.stdout)


class CacheDirectory:
    """
    Manage the location of caches.

    The ``get_XXX_cache`` methods return Path of the target cache file to
    read or write. The returned Path may or may not exist.
    """
    def __init__(self) -> None:
        self.cachedir = get_cachedir()
        self.key_len = 12
        self.answers_dir = self.cachedir / 'answers'
        with contextlib.suppress(FileExistsError):
            os.mkdir(self.answers_dir)
        self.icons_dir = self.cachedir / 'icons'
        with contextlib.suppress(FileExistsError):
            os.mkdir(self.icons_dir)

    def get_answers_cache(
        self,
        site_id: str,
        query: str,
        tags: ty.List[str],
    ) -> Path:
        """Get Path to a collection of gzipped cached answers."""
        sbuf = [site_id, query]
        sbuf.extend(tags)
        h = hashlib.sha1('_'.join(sbuf).encode('utf-8')).hexdigest()
        return self.answers_dir / (h[:self.key_len] + '.json.gz')

    def get_site_icon_cache(
        self,
        site_id: str,
    ) -> Path:
        """Get Path to a cached site icon."""
        return self.icons_dir / (site_id + '.png')

    def get_sites_cache(self,) -> Path:
        """Get Path to sites info."""
        return self.cachedir / 'all_sites.json'


def icon_need_update(cd: CacheDirectory, s: Site) -> bool:
    return not cd.get_site_icon_cache(s.id_).is_file()


def load_sites_from_cache(path: Path) -> ty.List[Site]:
    with open(path, encoding='utf-8') as infile:
        return list(itertools.starmap(Site, json.load(infile)))


def dump_sites_to_cache(sites: ty.List[Site], path: Path) -> None:
    with open(path, 'w', encoding='utf-8') as outfile:
        json.dump(sites, outfile, default=dataclasses.astuple)


def load_answers_from_cache(path: Path):
    with gzip.open(path, 'rb') as infile:
        return list(itertools.starmap(Answer, json.load(infile)))


def dump_answers_to_cache(answers: ty.List[Answer], path: Path) -> None:
    with gzip.open(path, 'wb') as outfile:
        outfile.write(
            json.dumps(answers, default=dataclasses.astuple).encode('utf-8'))


def older_than(path: Path, seconds: int) -> bool:
    return time.time() - os.path.getmtime(path) >= seconds


def response_written(func):
    def _wrapper(*args):
        try:
            resp = func(*args)
        except Exception as err:
            resp = {
                'items': [
                    {
                        'title': 'Error occurs: {}'.format(type(err).__name__),
                        'subtitle': 'Message: {}'.format(str(err)),
                        'valid': False,
                        'icon': {
                            'path': 'error-icon.png',
                        },
                    },
                ],
            }
        print(json.dumps(resp), end='')

    return _wrapper


def do_cache_sites(
    cd: CacheDirectory,
    env: EnvParams,
) -> None:
    """Update cached list of sites and download icons."""
    logger = logging.getLogger('so.do_cache_sites')
    logger.debug('Retrieving StackExchange sites')
    sites = request_parse_sites_api(env)
    dump_sites_to_cache(sites, cd.get_sites_cache())
    logger.info('Retrieved %d StackExchange sites', len(sites))
    outstanding_sites = filter(functools.partial(icon_need_update, cd), sites)
    correct_counter = 0
    for s in outstanding_sites:
        logger.debug('Cachine icon of site_id=%r', s.id_)
        logger.debug('Downloading icon from %s', s.icon_url)
        try:
            resp = requests.get(s.icon_url, {}, **build_requests_kwargs(env))
        except Exception as err:
            logger.warning('Error %s with message: %r',
                           type(err).__name__, str(err))
            continue
        logger.debug('Trying to read %s as image', s.icon_url)
        try:
            icon = Image.open(io.BytesIO(resp.content))
        except Exception as err:
            logger.warning('Error %s with message: %r',
                           type(err).__name__, str(err))
            continue
        path = cd.get_site_icon_cache(s.id_)
        logger.debug('Saving the image (width=%d, height=%d) to %r',
                     icon.width, icon.height, path)
        icon.save(path)
        correct_counter += 1
    logger.info('Correctly processed %d icons', correct_counter)


@response_written
def do_sites(cd: CacheDirectory, env: EnvParams, query: str) -> dict:
    """Script filter to choose a StackExchange site."""
    logger = logging.getLogger('so.do_sites')

    sites_path = cd.get_sites_cache()
    logger.info('Loading from cache')
    sites = load_sites_from_cache(sites_path) if sites_path.is_file() else []
    if not sites:
        return {
            'items': [
                {
                    'title':
                    'Sites not collected yet',
                    'subtitle': ('Please run `stack-cache-sites` to collect '
                                 'StackExchange sites'),
                    'valid':
                    False,
                    'icon': {
                        'path': 'error-icon.png',
                    },
                },
            ],
        }
    if env.ignore_meta_sites:
        sites = [s for s in sites if not s.is_meta]
    name_to_sites = {s.name: s for s in sites}
    if query:
        filtered_names = request_fzf(query, list(name_to_sites))
        sites = [name_to_sites[name] for name in filtered_names]
    items = []
    for s in sites:
        icon_path = cd.get_site_icon_cache(s.id_)
        if not icon_path.is_file():
            icon_path = Path('icon.png')
        items.append({
            'title': s.name,
            'subtitle': s.audience,
            'arg': s.id_,
            'uid': s.id_,
            'icon': {
                'path': str(icon_path),
            },
            'text': {
                'copy': s.id_,
                'largetype': s.id_,
            },
            'variables': {
                'site_id': s.id_,
                'site_name': s.name,
                'site_audience': s.audience,
                'site_icon': s.icon_url,
                'site_is_meta': '1' if s.is_meta else '0',
            },
            'mods': {
                'cmd': {
                    'subtitle': 'Reveal icon in Finder',
                },
            },
        })
    if not items:
        items.append({
            'title': 'No matching sites',
            'subtitle': 'Try a different query',
            'valid': False,
        })
    return {'items': items}


def parse_search_query(string: str) -> ty.Tuple[str, ty.List[str], str]:
    """
    Returns query, tags, and additional_query.
    """
    string = string.strip()
    if '//' in string:
        rest, _, additional_query = string.partition('//')
    elif string.endswith('/'):  # possibly first half of '//'
        rest, additional_query = string[:-1], ''
    else:
        rest, additional_query = string, ''
    words = rest.split()
    query_words = []
    tags = []
    for w in words:
        if w.startswith('#'):
            tags.append(w[1:])
        else:
            query_words.append(w)
    query = ' '.join(query_words)
    return query, tags, additional_query


@response_written
def do_search(
    cd: CacheDirectory,
    env: EnvParams,
    query: str,
    tags: ty.List[str],
    additional_query: str,
) -> dict:
    """Script filter to search StackExchange site."""
    logger = logging.getLogger('so.do_search')

    logger.info('site=%r, query=%r, additional_query=%r', env.site_id, query,
                additional_query)

    answers_path = cd.get_answers_cache(env.site_id, query, tags)
    if not answers_path.is_file() or older_than(answers_path,
                                                env.cache_max_age):
        logger.info('Requesting search API')
        answers, qi = request_parse_search_api(query, tags, env.site_id, env)
        quota_remaining = qi.quota_remaining
        dump_answers_to_cache(answers, answers_path)
    else:
        logger.info('Loading answers from cache')
        answers = load_answers_from_cache(answers_path)
        quota_remaining = 9999999
    if additional_query:
        title_to_answers = {a.title: a for a in answers}
        filtered_titles = request_fzf(additional_query, list(title_to_answers))
        answers = [title_to_answers[title] for title in filtered_titles]

    items = []
    if quota_remaining < 10:
        items.append({
            'title':
            'Remaining quota less than 10: {}!'.format(quota_remaining),
            'valid':
            False,
            'icon': {
                'path': 'error-icon.png',
            }
        })
    for a in answers:
        icon_path = cd.get_site_icon_cache(env.site_id)
        if not icon_path.is_file():
            icon_path = Path('icon.png')
        title = [a.title]
        if a.is_answered:
            title.append('✅')
        title.append('⭐️{}'.format(a.score))
        items.append({
            'title': ' '.join(title),
            'subtitle': ' '.join(map('#{}'.format, a.tags)),
            'arg': a.link,
            'uid': a.link,
            'text': {
                'copy': a.title,
                'largetype': a.title,
            },
            'icon': {
                'path': str(icon_path),
            },
        })
    if not answers:
        items.append({
            'title': 'No answer found',
            'subtitle': 'Try a different query',
            'valid': False,
        })
    return {'items': items}


def do_reveal_icon(cd: CacheDirectory, env: EnvParams) -> None:
    icon_path = cd.get_site_icon_cache(env.site_id)
    if not icon_path.is_file():
        icon_path = 'icon.png'
    subprocess.run(['open', '-R', str(icon_path)], check=True)


def config_logging():
    logging.basicConfig(
        level='WARNING', format='%(levelname)s@%(name)s: %(message)s')
    if os.environ.get('alfred_debug', '') == '1':
        logging.getLogger('so').setLevel(logging.DEBUG)


def make_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument('action')
    parser.add_argument('query')
    return parser


def main():
    env = validate_env()
    config_logging()
    cd = CacheDirectory()
    args = make_parser().parse_args()
    if args.action == 'cache_sites':
        do_cache_sites(cd, env)
    elif args.action == 'sites':
        do_sites(cd, env, args.query.strip())
    elif args.action == 'search':
        query, tags, additional_query = parse_search_query(args.query)
        do_search(cd, env, query, tags, additional_query)
    elif args.action == 'reveal_icon':
        do_reveal_icon(cd, env)
    else:
        raise NotImplementedError
