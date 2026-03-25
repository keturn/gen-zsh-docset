#!/usr/bin/env python3
import argparse
import logging
import os
import plistlib
import shutil
import sqlite3
import tarfile
from contextlib import nullcontext
from pathlib import Path
from typing import cast

import bs4
import httpx
import tqdm

HERE = Path().cwd()
DOCSET = HERE / 'Zsh.docset'
DOCSET_TARBALL = HERE / 'Zsh.tgz'
CONTENTS = DOCSET / 'Contents'
RESOURCES = CONTENTS / 'Resources'
INFO_PLIST = CONTENTS / 'Info.plist'
DOCUMENTS_DIR = RESOURCES / 'Documents'
INDEX = RESOURCES / 'docSet.dsidx'


logger = logging.getLogger(__name__)

def _download_to_file(url: str, destination: Path, show_progress=True):
    if show_progress:
        progress_indicator = tqdm.tqdm(desc=destination.name, unit='B', unit_scale=2)
    else:
        progress_indicator = nullcontext()
    with httpx.stream('GET', url, follow_redirects=True) as response, destination.open('wb') as out_file, progress_indicator as progress:
        if progress is not None and (content_length := response.headers.get("Content-Length")):
            progress.reset(int(content_length))
        for data in response.iter_bytes():
            out_file.write(data)
            if progress:
                progress.update(response.num_bytes_downloaded)


def download(version):
    url = f'https://downloads.sourceforge.net/project/zsh/zsh-doc/{version}/zsh-{version}-doc.tar.xz'
    file = HERE / f'zsh-{version}-doc.tar.xz'
    _download_to_file(url, file)
    archive: tarfile.TarFile
    with tarfile.open(file) as archive:
        archive.extractall()


INFO_PLIST_DATA = dict(
    CFBundleIdentifier="zsh",
    CFBundleName="Zsh",
    DocSetPlatformFamily="zsh",
    isDashDocset=True,
    dashIndexFilePath="index.html",
    DashDocSetFallbackURL="https://zsh.sourceforge.net/Doc/Release/"
)

def generate_info_plist():
    if not os.path.exists(os.path.dirname(INFO_PLIST)):
        os.makedirs(os.path.dirname(INFO_PLIST))
    with open(INFO_PLIST, 'wb') as plist_file:
        plistlib.dump(INFO_PLIST_DATA, plist_file, sort_keys=False)


def copy_documents(version):
    DOCUMENTS_DIR.mkdir(parents=True, exist_ok=True)
    source_dir = HERE / f'zsh-{version}' / 'Doc'
    for file in source_dir.glob('*.html'):
        shutil.copy(file, DOCUMENTS_DIR)


def generate_index():
    entries = parse_index_entries()
    write_dsidx(entries)


def parse_index_entries():
    entries: list[tuple[str, str, str]] = []
    for file in DOCUMENTS_DIR.iterdir():
        with open(file) as fp:
            soup = bs4.BeautifulSoup(fp, 'html.parser')
        title = soup.title.text if soup.title else file.stem
        if title.startswith('zsh: '):
            title = title[5:]
        entries.append((title, 'Guide', file.name))

    index_documents = [
        ('Concept-Index.html', 'index-cp', 'Entry'),
        ('Variables-Index.html', 'index-vr', 'Variable'),
        ('Options-Index.html', 'index-pg', 'Option'),
        ('Functions-Index.html', 'index-fn', 'Function'),
        ('Editor-Functions-Index.html', 'index-tp', 'Function'),
        ('Style-and-Tag-Index.html', 'index-ky', lambda name: 'Tag' if name.endswith(' tag') else 'Style')
    ]
    for filename, index_class, type_ in index_documents:
        with open(DOCUMENTS_DIR / filename) as fp:
            soup = bs4.BeautifulSoup(fp, 'html.parser')
        if (table := soup.find('table', class_=index_class)) is None:
            logger.warning("table.%s not found in %s", index_class, filename)
            continue
        for tr in table.find_all('tr'):
            # Each row is a mapping from term to section. The term is the first link.
            if link := tr.select_one('td a[href]'):
                row_type = type_ if isinstance(type_, str) else type_(link.text)
                entries.append((link.text, row_type, cast(str, link['href'])))

    return entries


def write_dsidx(entries: list[tuple[str, str, str]]):
    with sqlite3.connect(INDEX, autocommit=False) as conn:
        cur = conn.cursor()
        cur.executescript('''\
          DROP TABLE IF EXISTS searchIndex;
          CREATE TABLE searchIndex(id INTEGER PRIMARY KEY, name TEXT, type TEXT, path TEXT);
          CREATE UNIQUE INDEX anchor ON searchIndex (name, type, path);
        ''')
        cur.executemany('INSERT OR IGNORE INTO searchIndex(name, type, path) VALUES (?,?,?);', entries)
        conn.commit()


def add_icon():
    _download_to_file('https://zsh.sourceforge.net/favicon.png', DOCSET / 'icon.png')


def exclude_name(name: str):
    return lambda tarinfo: None if (Path(tarinfo.name).name != name) else tarinfo


def tarup():
    archive: tarfile.TarFile
    with tarfile.open(DOCSET_TARBALL.name, mode='w:gz') as archive:
        archive.add(DOCSET.name, filter=exclude_name('.DS_Store'))


def main():
    # logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser()
    parser.add_argument('zsh_version')
    parser.add_argument(
            '--no-download',
            help='don\'t download docs and assume they are available in the current directory',
            action='store_true',
            )
    args = parser.parse_args()

    version = args.zsh_version
    if not args.no_download:
        download(version)
    generate_info_plist()
    copy_documents(version)
    generate_index()
    add_icon()
    tarup()


if __name__ == '__main__':
    main()
