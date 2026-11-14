"""Contained filenames shared by bake writers and their consumers."""
import hashlib
import os
import re


def safe_output_path(directory, filename):
    """Keep ordinary names; disambiguate names that require sanitizing."""
    original = str(filename)
    stem, dot, extension = original.rpartition('.')
    if not dot or not extension or '/' in extension or '\\' in extension:
        stem, extension = original, ''
    else:
        extension = '.' + re.sub(r'[^A-Za-z0-9_-]+', '_', extension)
    name = re.sub(r'[^A-Za-z0-9_.-]+', '_', stem).strip('._') or 'mesh'
    if name != stem:
        digest = hashlib.sha256(stem.encode()).hexdigest()[:10]
        name = f'{name}_{digest}'
    name += extension
    root = os.path.realpath(directory)
    path = os.path.join(root, name)
    if os.path.commonpath((root, os.path.realpath(path))) != root:
        raise ValueError('Output path escapes the configured directory')
    return path
