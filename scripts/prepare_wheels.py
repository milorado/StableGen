"""Fetch the exact manifest wheels and verify their PyPI SHA-256 digests.

Run with Python 3.11+ before `blender --command extension build`.
Only downloads package archives; never installs or executes them.
"""
import argparse
import hashlib
import json
from pathlib import Path
import tomllib
import urllib.request


def prepare(manifest, output_dir):
    manifest = Path(manifest)
    wheels = tomllib.loads(manifest.read_text())['wheels']
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    metadata = {}
    receipts = []
    for wheel in wheels:
        filename = Path(wheel).name
        package, version = filename.split('-')[:2]
        key = (package, version)
        if key not in metadata:
            url = f'https://pypi.org/pypi/{package}/{version}/json'
            with urllib.request.urlopen(url, timeout=30) as response:
                metadata[key] = json.load(response)
        release = next((entry for entry in metadata[key]['urls'] if entry['filename'] == filename), None)
        if release is None or release.get('yanked'):
            raise ValueError(f'Wheel missing or yanked on PyPI: {filename}')
        expected = release['digests']['sha256']
        destination = output_dir / filename
        if destination.is_file() and hashlib.sha256(destination.read_bytes()).hexdigest() == expected:
            print(f'Verified {filename}')
        else:
            temporary = destination.with_suffix('.download')
            try:
                digest = hashlib.sha256()
                with urllib.request.urlopen(release['url'], timeout=120) as response, temporary.open('wb') as handle:
                    while chunk := response.read(1024 * 1024):
                        digest.update(chunk)
                        handle.write(chunk)
                if digest.hexdigest() != expected:
                    raise ValueError(f'Wheel hash mismatch: {filename}')
                temporary.replace(destination)
            finally:
                temporary.unlink(missing_ok=True)
            print(f'Downloaded {filename}')
        receipts.append({'filename': filename, 'sha256': expected})
    (output_dir / 'verified-wheels.json').write_text(json.dumps(receipts, indent=2) + '\n')
    return receipts


if __name__ == '__main__':
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, default=root / 'stablegen/blender_manifest.toml')
    parser.add_argument('--output-dir', type=Path, default=root / 'stablegen/wheels')
    args = parser.parse_args()
    prepare(args.manifest, args.output_dir)
