#!/usr/bin/env python3

# Requires Python 3.6+.

import argparse
import os
import plistlib
import shlex
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import bs4
import httpx

HERE = Path().cwd()
DOCSET = HERE / 'Zsh.docset'
DOCSET_TARBALL = HERE / 'Zsh.tgz'
CONTENTS = DOCSET / 'Contents'
RESOURCES = CONTENTS / 'Resources'
INFO_PLIST = CONTENTS / 'Info.plist'
DOCUMENTS_DIR = RESOURCES / 'Documents'
INDEX = RESOURCES / 'docSet.dsidx'


def run(cmd):
    print('> ' + ' '.join(shlex.quote(str(arg)) for arg in cmd), file=sys.stderr)
    subprocess.check_call(cmd)


def _download_to_file(url: str, destination: Path):
    with httpx.stream('GET', url, follow_redirects=True) as response, destination.open('wb') as out_file:
        for data in response.iter_bytes():
            out_file.write(data)


def download(version):
    url = f'https://downloads.sourceforge.net/project/zsh/zsh-doc/{version}/zsh-{version}-doc.tar.xz'
    file = HERE / f'zsh-{version}-doc.tar.xz'
    _download_to_file(url, file)
    run(['tar', 'xJf', file])


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
    entries = []
    for file in DOCUMENTS_DIR.iterdir():
        with open(file) as fp:
            title = bs4.BeautifulSoup(fp, 'html.parser').title.text
        if title.startswith('zsh: '):
            title = title[5:]
        entries.append((title, 'Guide', file.name))

    # Indexes
    for filename, index_class, type_ in [('Concept-Index.html', 'index-cp', 'Entry'),
                                         ('Variables-Index.html', 'index-vr', 'Variable'),
                                         ('Options-Index.html', 'index-pg', 'Option'),
                                         ('Functions-Index.html', 'index-fn', 'Function'),
                                         ('Editor-Functions-Index.html', 'index-tp', 'Function')]:
        with open(DOCUMENTS_DIR / filename) as fp:
            soup = bs4.BeautifulSoup(fp, 'html.parser')
        table = soup.find('table', class_=index_class)
        for tr in table.find_all('tr'):
            try:
                name = tr.a.text
                path = tr.a['href']
                entries.append((name, type_, path))
            except Exception:
                pass

    # Style and tag index
    with open(DOCUMENTS_DIR / 'Style-and-Tag-Index.html') as fp:
        soup = bs4.BeautifulSoup(fp, 'html.parser')
    table = soup.find('table', class_='index-ky')
    for tr in table.find_all('tr'):
        try:
            name = tr.a.text
            path = tr.a['href']
            entries.append((name, ('Tag' if name.endswith(' tag') else 'Style'), path))
        except Exception:
            pass

    conn = sqlite3.connect(os.fspath(INDEX))
    cur = conn.cursor()

    try:
        cur.execute('DROP TABLE searchIndex;')
    except Exception:
        pass
    cur.execute('CREATE TABLE searchIndex(id INTEGER PRIMARY KEY, name TEXT, type TEXT, path TEXT);')
    cur.execute('CREATE UNIQUE INDEX anchor ON searchIndex (name, type, path);')
    for name, type_, path in entries:
        print(f'|{name}|{type_}|{path}')
        cur.execute('INSERT OR IGNORE INTO searchIndex(name, type, path) VALUES (?,?,?)', (name, type_, path))
    conn.commit()
    conn.close()


def add_icon():
    _download_to_file('https://zsh.sourceforge.net/favicon.png', DOCSET / 'icon.png')


def tarup():
    run(['tar', '--exclude=.DS_Store', '-C', DOCSET.parent, '-cvzf', DOCSET_TARBALL.name, DOCSET.name])


def main():
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
