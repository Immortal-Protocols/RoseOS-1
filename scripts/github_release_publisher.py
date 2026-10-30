"""Publish an exact verified asset set to a GitHub release with a short-lived installation token.

Drafts are found by listing releases (the tag endpoint hides drafts). A published release whose assets
differ is never modified; an identical one is an idempotent no-op. Our draft is reconciled: assets that
are unexpected or differ are deleted, missing ones uploaded, then every asset is downloaded with
authentication for full verification. The storage redirect is followed without the token. Errors name
only the method, endpoint template and status; response bodies and the token are never reported.
"""
import hashlib
import http.client
import json
import os
from pathlib import Path
import urllib.parse

class PublishError(Exception):
    pass

class GitHubReleases:
    def __init__(self, repository, token, api='https://api.github.com', uploads='https://uploads.github.com', allow_http=False,
                 web='https://github.com'):
        for base in (api, uploads, web):
            if urllib.parse.urlsplit(base).scheme != 'https' and not allow_http:
                raise PublishError('GitHub API endpoints must use https')
        if not token:
            raise PublishError('no publisher token')
        self.repository, self.api, self.uploads, self.web = repository, api.rstrip('/'), uploads.rstrip('/'), web.rstrip('/')
        self.allow_http = allow_http
        self._token = token

    def _connect(self, url):
        parts = urllib.parse.urlsplit(url)
        cls = http.client.HTTPSConnection if parts.scheme == 'https' else http.client.HTTPConnection
        return cls(parts.netloc, timeout=600), parts.path + (('?' + parts.query) if parts.query else '')

    def _request(self, method, url, label, body=None, headers=None, authenticated=True, expect=(200,), stream_to=None, limit=None):
        connection, target = self._connect(url)
        send = {'User-Agent': 'roseos-os-release', 'X-GitHub-Api-Version': '2022-11-28',
            'Accept': 'application/vnd.github+json', **(headers or {})}
        if authenticated:
            send['Authorization'] = 'Bearer ' + self._token
        try:
            if isinstance(body, Path):
                send['Content-Length'] = str(body.stat().st_size)
                with open(body, 'rb') as f:
                    connection.request(method, target, body=f, headers=send)
                    response = connection.getresponse()
            else:
                data = json.dumps(body).encode() if body is not None else None
                if data is not None:
                    send['Content-Type'] = 'application/json'
                connection.request(method, target, body=data, headers=send)
                response = connection.getresponse()
            if response.status not in expect:
                response.read()
                raise PublishError(f'{method} {label} returned HTTP {response.status}')
            if response.status in (301, 302, 307):
                response.read()
                return response.status, response.getheader('Location'), None
            if stream_to is not None:
                size = 0
                with open(stream_to, 'wb') as out:
                    while block := response.read(1 << 20):
                        size += len(block)
                        if limit is not None and size > limit:
                            raise PublishError(f'{method} {label} returned more bytes than expected')
                        out.write(block)
                return response.status, None, size
            raw = response.read()
            return response.status, None, (json.loads(raw) if raw else None)
        except (OSError, http.client.HTTPException, ValueError) as error:
            raise PublishError(f'{method} {label} failed: {error.__class__.__name__}')
        finally:
            connection.close()

    def find_release(self, tag):
        for page in range(1, 11):
            _, _, releases = self._request('GET', f'{self.api}/repos/{self.repository}/releases?per_page=100&page={page}',
                'list releases')
            for release in releases:
                if release.get('tag_name') == tag:
                    return release
            if len(releases) < 100:
                return None
        raise PublishError('release list is longer than this publisher scans')

    def get_release(self, release_id):
        return self._request('GET', f'{self.api}/repos/{self.repository}/releases/{int(release_id)}', 'get release')[2]

    def create_draft(self, tag, name, body):
        return self._request('POST', f'{self.api}/repos/{self.repository}/releases', 'create draft release',
            body={'tag_name': tag, 'name': name, 'body': body, 'draft': True, 'prerelease': False, 'make_latest': 'false'},
            expect=(201,))[2]

    def delete_asset(self, asset_id):
        self._request('DELETE', f'{self.api}/repos/{self.repository}/releases/assets/{int(asset_id)}', 'delete draft asset',
            expect=(204,))

    def upload(self, release_id, path):
        name = urllib.parse.quote(path.name)
        self._request('POST', f'{self.uploads}/repos/{self.repository}/releases/{int(release_id)}/assets?name={name}',
            'upload release asset', body=path, headers={'Content-Type': 'application/octet-stream'}, expect=(201,))

    def download(self, asset_id, destination, size):
        status, location, _ = self._request('GET', f'{self.api}/repos/{self.repository}/releases/assets/{int(asset_id)}',
            'download release asset', headers={'Accept': 'application/octet-stream'}, expect=(200, 302),
            stream_to=destination, limit=size)
        if status == 302:
            # Signed storage URL: the token must not travel there.
            self._request('GET', location, 'download release asset storage', headers={'Accept': 'application/octet-stream'},
                authenticated=False, stream_to=destination, limit=size)

    def latest_tag(self):
        status, _, release = self._request('GET', f'{self.api}/repos/{self.repository}/releases/latest', 'get latest release', expect=(200, 404))
        return release.get('tag_name') if status == 200 and release else None

    def create_prerelease_draft(self, tag, name, body):
        return self._request('POST', f'{self.api}/repos/{self.repository}/releases', 'create draft prerelease',
            body={'tag_name': tag, 'name': name, 'body': body, 'draft': True, 'prerelease': True, 'make_latest': 'false'},
            expect=(201,))[2]

    def publish_prerelease(self, release_id):
        return self._request('PATCH', f'{self.api}/repos/{self.repository}/releases/{int(release_id)}', 'publish prerelease',
            body={'draft': False, 'prerelease': True, 'make_latest': 'false'})[2]

    def public_download(self, url, destination, size):
        """The asset as an anonymous client gets it: the public github.com URL, then at most one redirect to the release
        asset storage host, without any token."""
        if not url.startswith('https://github.com/'):
            raise PublishError('public download URL is not on github.com')
        status, location, _ = self._request('GET', self.web + url[len('https://github.com'):], 'public release asset',
            headers={'Accept': 'application/octet-stream'}, authenticated=False, expect=(200, 302), stream_to=destination, limit=size)
        if status == 302:
            target = urllib.parse.urlsplit(location or '')
            if not self.allow_http and (target.scheme != 'https' or target.netloc != 'release-assets.githubusercontent.com'):
                raise PublishError('public release asset redirected to an unexpected host')
            self._request('GET', location, 'public release asset storage', headers={'Accept': 'application/octet-stream'},
                authenticated=False, stream_to=destination, limit=size)

    def finalize_public(self, release_id):
        """Publish a verified draft as a public release that never becomes Latest (separate-device releases)."""
        return self._request('PATCH', f'{self.api}/repos/{self.repository}/releases/{int(release_id)}', 'finalize public release',
            body={'draft': False, 'prerelease': False, 'make_latest': 'false'})[2]

    def promote(self, release_id):
        return self._request('PATCH', f'{self.api}/repos/{self.repository}/releases/{int(release_id)}', 'promote release',
            body={'draft': False, 'make_latest': 'true'})[2]

def _sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()

def _matches(asset, expected):
    size, sha = expected
    digest = asset.get('digest')
    return (asset.get('state', 'uploaded') == 'uploaded' and asset.get('size') == size
        and (digest is None or digest == 'sha256:' + sha))

def publish_release(client, tag, name, body, manifest, assets_dir, download_dir):
    """Make release `tag` hold exactly the manifest's assets; return (release, download_dir, already_published)."""
    expected = {row['name']: (row['size'], row['sha256']) for row in manifest['assets']}
    release = client.find_release(tag)
    if release and not release.get('draft'):
        assets = {a['name']: a for a in release.get('assets', [])}
        if set(assets) != set(expected) or not all(_matches(assets[n], expected[n]) for n in expected):
            raise PublishError(f'published release {tag} differs from this candidate; it is not modified')
        already = True
    else:
        already = False
        if release is None:
            release = client.create_draft(tag, name, body)
        for asset in release.get('assets', []):
            if asset['name'] not in expected or not _matches(asset, expected[asset['name']]):
                client.delete_asset(asset['id'])
        present = {a['name'] for a in client.get_release(release['id']).get('assets', [])}
        for asset_name in sorted(set(expected) - present):
            client.upload(release['id'], Path(assets_dir)/asset_name)
        release = client.get_release(release['id'])
        assets = {a['name']: a for a in release.get('assets', [])}
        if set(assets) != set(expected) or not all(_matches(assets[n], expected[n]) for n in expected):
            raise PublishError('draft assets differ from the candidate after upload')
    download_dir = Path(download_dir)
    download_dir.mkdir(parents=True)
    for asset_name, asset in sorted(assets.items()):
        client.download(asset['id'], download_dir/asset_name, expected[asset_name][0])
        if _sha256(download_dir/asset_name) != expected[asset_name][1]:
            raise PublishError(f'downloaded {asset_name} does not match the candidate')
    return release, download_dir, already

def client_from_environment(config):
    publication = config['publication']
    return GitHubReleases(publication['repository'], os.environ.get('PIXEL_OTA_TOKEN', ''),
        publication.get('api', 'https://api.github.com'), publication.get('uploads', 'https://uploads.github.com'),
        allow_http=bool(config.get('fixture')))
