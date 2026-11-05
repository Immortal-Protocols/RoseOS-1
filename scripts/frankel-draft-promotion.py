#!/usr/bin/env python3
"""Source-only prepared, one-shot promotion of the verified Frankel 2026110501 draft."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('recovery', ROOT/'scripts/frankel-draft-recovery.py')
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)
RELEASE = 435513431
CANDIDATE = 'cc238285b3aaf379e89dc6724ff1f68e478f8920'
RECOVERY_RUN = 42075447865
WORKFLOW = r.REPO+'/.github/workflows/frankel-draft-promotion.yml@refs/heads/main'


def admission(env):
    if (env.get('GITHUB_REPOSITORY') != r.REPO or env.get('GITHUB_EVENT_NAME') != 'workflow_dispatch'
            or env.get('GITHUB_REF') != 'refs/heads/main' or env.get('GITHUB_RUN_ATTEMPT') != '1'
            or env.get('GITHUB_WORKFLOW_REF') != WORKFLOW):
        raise r.Refused('only first-attempt manual main promotion is admitted')


class ExistingDraft:
    simulated = False

    def __init__(self, client):
        self.client = client
        self.attempted = False

    def check(self, release, manifest):
        if (release.get('id') != RELEASE or release.get('tag_name') != r.TAG
                or release.get('draft') is not True or release.get('prerelease') is not False
                or release.get('published_at') is not None):
            raise r.Refused('exact unpublished draft identity differs')
        body = release.get('body', '')
        if r.SOURCE not in body or f'/actions/runs/{RECOVERY_RUN}' not in body:
            raise r.Refused('draft description lacks verified source/recovery binding')
        rows = release.get('assets', [])
        actual = {a['name']: a for a in rows}
        expected = {a['name']: a for a in manifest['assets']}
        if len(actual) != len(rows) or set(actual) != set(expected):
            raise r.Refused('draft asset set differs; never repair or replace assets')
        for name, want in expected.items():
            got = actual[name]
            if (got.get('state') != 'uploaded' or got.get('size') != want['size']
                    or got.get('digest') not in (None, 'sha256:'+want['sha256'])):
                raise r.Refused('draft asset metadata mismatch')
        return sorted((a['name'], a['id'], a['size'], a.get('digest')) for a in rows)

    def publish(self, config, cand, directory, receipt_dir, notes=None, fresh=False):
        if cand['sha'] != CANDIDATE or cand['build'] != r.BUILD or cand['device'] != 'frankel':
            raise r.Refused('candidate differs from retained producer')
        self.manifest = json.loads((Path(receipt_dir)/'manifest.json').read_text())
        release = self.client.get_release(RELEASE)
        self.snapshot = self.check(release, self.manifest)
        download = Path(receipt_dir).parent/'promotion-download'
        download.mkdir()  # existing scratch refuses, never resume
        for row in self.manifest['assets']:
            asset = next(a for a in release['assets'] if a['name'] == row['name'])
            self.client.download(asset['id'], download/row['name'], row['size'])
            if r.digest(download/row['name']) != row['sha256']:
                raise r.Refused('downloaded draft asset digest differs')
        return download

    def promote(self, config, cand, draft):
        if self.attempted:
            raise r.Refused('promotion already attempted; inspect outcome, never retry')
        current = self.client.get_release(RELEASE)
        if self.check(current, self.manifest) != self.snapshot:
            raise r.Refused('draft assets changed during verification')
        self.attempted = True  # set before PATCH; transport failure is uncertain
        self.client.promote(RELEASE)
        after = self.client.get_release(RELEASE)
        if (after.get('id') != RELEASE or after.get('tag_name') != r.TAG or after.get('draft') is not False
                or after.get('prerelease') is not False or not after.get('published_at')
                or self.client.latest_tag() != r.TAG):
            raise r.Refused('promotion/Latest readback differs; inspect outcome, never retry')
        rows = after.get('assets', [])
        if sorted((a['name'], a['id'], a['size'], a.get('digest')) for a in rows) != self.snapshot:
            raise r.Refused('post-promotion asset identities changed')
        channel = next(a for a in self.manifest['assets'] if a['name'] == 'frankel-stable')
        output = Path(draft)/'public-channel-readback'
        self.client.public_download('https://github.com/Immortal-Protocols/pixel-ota/releases/latest/download/frankel-stable',
                                    output, channel['size'])
        if r.digest(output) != channel['sha256']:
            raise r.Refused('public Latest channel differs; promotion may have completed')


def promote(home, client):
    record = r.allocation(home)
    if record['candidateSha'] != CANDIDATE:
        raise r.Refused('allocation candidate differs')
    pipeline = r.load('release-pipeline')
    candidate = dict(sha=CANDIDATE, build=r.BUILD, timestamp=str(record['buildTimestamp']), channel='beta', device='frankel')
    adapter = ExistingDraft(client)
    args = SimpleNamespace(artifact_dir=str(home/'ota'), allocation=str(home/'allocation/allocation.json'),
                           promote=True, draft_only=False)
    result = pipeline.publish(args, pipeline.device_config('frankel', None), candidate, adapter)
    return dict(result, releaseId=RELEASE, producerRun=r.RUN, recoveryRun=RECOVERY_RUN,
                candidate=candidate, manifestSha256=r.digest(home/'ota/manifest.json'),
                url='https://github.com/Immortal-Protocols/pixel-ota/releases/tag/'+r.TAG)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('ingest', 'promote'))
    parser.add_argument('--dir', type=Path, required=True)
    args = parser.parse_args()
    try:
        admission(os.environ)
        if args.command == 'ingest':
            result = r.ingest(args.dir, r.github.GitHubReleases(r.REPO, os.environ.get('GITHUB_TOKEN')))
        else:
            result = promote(args.dir, r.github.GitHubReleases('Immortal-Protocols/pixel-ota', os.environ.get('PIXEL_OTA_TOKEN')))
        (args.dir/(args.command+'-receipt.json')).write_text(json.dumps(result, indent=2)+'\n')
        print(json.dumps(result, sort_keys=True))
    except Exception as error:
        # Remote errors may contain credentials/URLs; emit only a bounded local refusal or exception class.
        detail = str(error) if isinstance(error, r.Refused) else type(error).__name__
        print('Frankel promotion refused: '+detail, file=sys.stderr)
        if args.dir.is_dir() and not args.dir.is_symlink():
            (args.dir/'failure-receipt.json').write_text(json.dumps(dict(error=detail,
                stage=args.command, promotionMayHaveStarted=args.command == 'promote', automaticRetry=False))+'\n')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
