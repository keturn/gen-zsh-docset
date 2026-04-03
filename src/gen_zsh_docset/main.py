#!/usr/bin/env python3
import argparse
import importlib.resources
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
DOCSET = HERE / "Zsh.docset"
DOCSET_TARBALL = HERE / "Zsh.tgz"
CONTENTS = DOCSET / "Contents"
RESOURCES = CONTENTS / "Resources"
INFO_PLIST = CONTENTS / "Info.plist"
DOCUMENTS_DIR = RESOURCES / "Documents"
INDEX = RESOURCES / "docSet.dsidx"

logger = logging.getLogger(__name__)


def _download_to_file(url: str, destination: Path, show_progress=True):
    if show_progress:
        progress_indicator = tqdm.tqdm(desc=destination.name, unit="B", unit_scale=True)
    else:
        progress_indicator = nullcontext()
    with (
        httpx.stream("GET", url, follow_redirects=True) as response,
        destination.open("wb") as out_file,
        progress_indicator as progress,
    ):
        if progress is not None and (content_length := response.headers.get("Content-Length")):
            progress.reset(int(content_length))
        for data in response.iter_bytes():
            out_file.write(data)
            if progress is not None:
                progress.update(response.num_bytes_downloaded)


def download(version):
    url = (
        f"https://downloads.sourceforge.net/project/zsh/zsh-doc/{version}/zsh-{version}-doc.tar.xz"
    )
    file = HERE / f"zsh-{version}-doc.tar.xz"
    _download_to_file(url, file)
    archive: tarfile.TarFile
    with tarfile.open(file) as archive:
        archive.extractall()


def download_sources(version):
    source_url = f"https://downloads.sourceforge.net/project/zsh/zsh/{version}/zsh-{version}.tar.xz"
    file = HERE / f"zsh-{version}.tar.xz"
    _download_to_file(source_url, file)
    archive: tarfile.TarFile
    with tarfile.open(file) as archive:
        archive.extractall()


INFO_PLIST_DATA = dict(
    CFBundleIdentifier="zsh",
    CFBundleName="Zsh",
    DocSetPlatformFamily="zsh",
    isDashDocset=True,
    dashIndexFilePath="index.html",
    DashDocSetFallbackURL="https://zsh.sourceforge.net/Doc/Release/",
)


def generate_info_plist():
    if not os.path.exists(os.path.dirname(INFO_PLIST)):
        os.makedirs(os.path.dirname(INFO_PLIST))
    with open(INFO_PLIST, "wb") as plist_file:
        plistlib.dump(INFO_PLIST_DATA, plist_file, sort_keys=False)


def copy_documents(version):
    DOCUMENTS_DIR.mkdir(parents=True, exist_ok=True)
    source_dir = HERE / f"zsh-{version}" / "Doc"
    for file in source_dir.glob("*.html"):
        shutil.copy(file, DOCUMENTS_DIR)


def generate_index():
    entries = parse_index_entries()
    write_dsidx(entries)


def parse_index_entries():
    with open(DOCUMENTS_DIR / "index.html") as fp:
        soup = bs4.BeautifulSoup(fp, "html.parser")
    if generator := soup.select_one('meta[name="Generator"]'):
        if "texi2any" in generator["content"]:
            return parse_index_entries_texi2any()
    return parse_index_entries_texi2html()


def entry_for_each_page():
    entries: list[tuple[str, str, str]] = []
    for file in DOCUMENTS_DIR.iterdir():
        with open(file) as fp:
            soup = bs4.BeautifulSoup(fp, "html.parser")
        title = soup.title.text if soup.title else file.stem
        if title.startswith("zsh: "):
            title = title[5:]
        entries.append((title, "Guide", file.name))
    return entries


def parse_index_entries_texi2any():
    entries: list[tuple[str, str, str]] = []
    entries.extend(entry_for_each_page())

    index_documents = [
        ("Concept-Index.html", "cp-entries-printindex", "Entry"),
        ("Variables-Index.html", "vr-entries-printindex", "Variable"),
        ("Options-Index.html", "pg-entries-printindex", "Option"),
        ("Functions-Index.html", "fn-entries-printindex", "Function"),
        ("Editor-Functions-Index.html", "tp-entries-printindex", "Function"),
        (
            "Style-and-Tag-Index.html",
            "ky-entries-printindex",
            lambda name: "Tag" if name.endswith(" tag") else "Style",
        ),
    ]
    for filename, index_class, type_ in index_documents:
        with open(DOCUMENTS_DIR / filename) as fp:
            soup = bs4.BeautifulSoup(fp, "html.parser")
        if (table := soup.find("table", class_=index_class)) is None:
            logger.warning("table.%s not found in %s", index_class, filename)
            continue
        # texi2any kindly distinctly labels the entry and section links
        for link in table.select(".printindex-index-entry a[href]"):
            row_type = type_ if isinstance(type_, str) else type_(link.text)
            entries.append((link.text, row_type, cast(str, link["href"])))

    return entries


def parse_index_entries_texi2html():
    entries: list[tuple[str, str, str]] = []
    entries.extend(entry_for_each_page())

    # Beware: Some versions of texi2html will split a large index over multiple HTML pages.
    # This is the case with the upstream zsh-5.9-doc.tar.xz, which was built with texi2html 5.0.
    # We could fix this to crawl multi-page indicies, but rebuilding the docs with a current
    # version of texi2any makes that problem go away… and hopefully future releases won't continue
    # to use old texi2html.
    index_documents = [
        ("Concept-Index.html", "index-cp", "Entry"),
        ("Variables-Index.html", "index-vr", "Variable"),
        ("Options-Index.html", "index-pg", "Option"),
        ("Functions-Index.html", "index-fn", "Function"),
        ("Editor-Functions-Index.html", "index-tp", "Function"),
        (
            "Style-and-Tag-Index.html",
            "index-ky",
            lambda name: "Tag" if name.endswith(" tag") else "Style",
        ),
    ]
    for filename, index_class, type_ in index_documents:
        with open(DOCUMENTS_DIR / filename) as fp:
            soup = bs4.BeautifulSoup(fp, "html.parser")
        if (table := soup.find("table", class_=index_class)) is None:
            logger.warning("table.%s not found in %s", index_class, filename)
            continue
        for tr in table.find_all("tr"):
            # Each row is a mapping from term to section. The term is the first link.
            if link := tr.select_one("td a[href]"):
                row_type = type_ if isinstance(type_, str) else type_(link.text)
                entries.append((link.text, row_type, cast(str, link["href"])))

    return entries


def write_dsidx(entries: list[tuple[str, str, str]]):
    with sqlite3.connect(INDEX, autocommit=False) as conn:
        cur = conn.cursor()
        # fmt: off
        cur.executescript('''\
          DROP TABLE IF EXISTS searchIndex;
          CREATE TABLE searchIndex(id INTEGER PRIMARY KEY, name TEXT, type TEXT, path TEXT);
          CREATE UNIQUE INDEX anchor ON searchIndex (name, type, path);
        ''')
        # fmt: on
        cur.executemany(
            "INSERT OR IGNORE INTO searchIndex(name, type, path) VALUES (?,?,?);", entries
        )
        conn.commit()


ZSH_ART_URL = "https://github.com/Zsh-art/logo/raw/refs/heads/main/"


def add_icon(*, no_download=False):
    zsh_art_assets = [
        (DOCSET / "icon.svg", ZSH_ART_URL + "favicon/favicon.svg"),
        (DOCSET / "icon@2x.png", ZSH_ART_URL + "app-icons/zsh-icon-32x32.png"),
    ]
    for asset_path, url in zsh_art_assets:
        if not asset_path.exists() or not no_download:
            _download_to_file(url, asset_path)

    # keturn decided the SVG is illegible when rendered at 16px, and took liberties.
    png_data = importlib.resources.read_binary("gen_zsh_docset.assets", "icon-16px.png")
    (DOCSET / "icon.png").write_bytes(png_data)


def exclude_name(name: str):
    """Return an exclusion filter for files with the given name."""
    return lambda tarinfo: None if (Path(tarinfo.name).name == name) else tarinfo


def tarup():
    archive: tarfile.TarFile
    with tarfile.open(DOCSET_TARBALL.name, mode="w:gz") as archive:
        archive.add(DOCSET.name, filter=exclude_name(".DS_Store"))


def main():
    # logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser()
    parser.add_argument("zsh_version")
    parser.add_argument(
        "--no-download",
        help="don't download docs and assume they are available in the current directory",
        action="store_true",
    )
    args = parser.parse_args()

    version = args.zsh_version
    if not args.no_download:
        download(version)
    generate_info_plist()
    copy_documents(version)
    generate_index()
    add_icon(no_download=args.no_download)
    tarup()


if __name__ == "__main__":
    main()
