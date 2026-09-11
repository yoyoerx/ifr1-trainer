"""Unit tests for datasrc.faa. No real network: the HTTP layer is stubbed."""

import hashlib
import io
import os
import sys
import zipfile
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from datasrc import faa  # noqa: E402
from datasrc.airac import cycle_from_ident  # noqa: E402

CYCLE = cycle_from_ident("2609")  # effective 2026-09-03, expires 2026-10-01


def _zip(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


CIFP_ZIP = _zip({"FAACIFP18": b"SUSAP KJFK" * 100, "CIFP Readme.pdf": b"%PDF-1.4 fake"})
NASR_ZIP = _zip(
    {
        "NAV_BASE.csv": b"NAV_ID,FREQ\nJFK,115.9\n",
        "APT_BASE.csv": b"ARPT_ID\nKJFK\n",
        "ILS_BASE.csv": b"x\n",
        "NOTES.txt": b"ignore me",
        "TWR_something.csv": b"not in keep list",
    }
)
NASR_LANDING_HTML = """
<html><body>
  <a href="https://nfdc.faa.gov/webContent/28DaySub/extra/2026-09-03_APT_TXT.zip">TXT</a>
  <a href="https://nfdc.faa.gov/webContent/28DaySub/extra/2026-09-03_NASR_CSV.zip">CSV download</a>
</body></html>
"""


# --------------------------------------------------------------------------- #
# URL resolution                                                             #
# --------------------------------------------------------------------------- #
def test_cifp_url_candidates_use_effective_date():
    urls = faa.cifp_url_candidates(CYCLE)
    assert urls == [
        "https://aeronav.faa.gov/Upload_313-d/cifp/CIFP_260903.zip",
        "https://aeronav.faa.gov/Upload_313-d/cifp/cifp_260903.zip",
    ]


def test_resolve_nasr_url_prefers_csv_and_absolutizes():
    url = faa.resolve_nasr_url(CYCLE, get_text=lambda u: NASR_LANDING_HTML)
    assert url == "https://nfdc.faa.gov/webContent/28DaySub/extra/2026-09-03_NASR_CSV.zip"


def test_resolve_nasr_url_no_zip_link():
    with pytest.raises(faa.DataUnavailable):
        faa.resolve_nasr_url(CYCLE, get_text=lambda u: "<html>nothing here</html>")


def test_resolve_nasr_url_landing_missing():
    def boom(_url):
        raise faa.DataUnavailable("404")

    with pytest.raises(faa.DataUnavailable, match="not published yet"):
        faa.resolve_nasr_url(CYCLE, get_text=boom)


# --------------------------------------------------------------------------- #
# fetch                                                                      #
# --------------------------------------------------------------------------- #
def _fake_get(url: str) -> bytes:
    if "cifp" in url.lower():
        return CIFP_ZIP
    if url.endswith(".zip"):
        return NASR_ZIP
    raise faa.DataUnavailable(f"unexpected url {url}")


def _fake_get_text(url: str) -> str:
    return NASR_LANDING_HTML


def test_fetch_extracts_expected_files(tmp_path):
    m = faa.fetch(CYCLE, root=tmp_path, get=_fake_get, get_text=_fake_get_text)
    d = faa.cache_dir(tmp_path, CYCLE)

    assert (d / "FAACIFP18").exists()
    assert (d / "CIFP Readme.pdf").exists()
    assert (d / "NAV_BASE.csv").exists()
    assert (d / "APT_BASE.csv").exists()
    assert (d / "ILS_BASE.csv").exists()
    # not in NASR_KEEP -> skipped
    assert not (d / "NOTES.txt").exists()
    assert not (d / "TWR_something.csv").exists()

    assert m.cycle == "2609"
    assert m.effective == date(2026, 9, 3)
    assert m.expires == date(2026, 10, 1)
    assert set(m.products) == {"cifp", "nasr"}
    assert m.products["cifp"].bytes == len(CIFP_ZIP)
    assert m.fetched_at.endswith("Z")


def test_fetch_records_correct_checksums(tmp_path):
    m = faa.fetch(CYCLE, root=tmp_path, get=_fake_get, get_text=_fake_get_text)
    d = faa.cache_dir(tmp_path, CYCLE)
    for rec in m.products.values():
        for fname, digest in rec.sha256.items():
            assert digest == hashlib.sha256((d / fname).read_bytes()).hexdigest()


def test_manifest_round_trips_on_disk(tmp_path):
    faa.fetch(CYCLE, root=tmp_path, get=_fake_get, get_text=_fake_get_text)
    mpath = faa.cache_dir(tmp_path, CYCLE) / faa.MANIFEST_NAME
    reloaded = faa.Manifest.read(mpath)
    assert reloaded.cycle == "2609"
    assert reloaded.products["cifp"].files == ["CIFP Readme.pdf", "FAACIFP18"]
    assert reloaded.as_cycle().expiration == date(2026, 10, 1)


def test_fetch_is_incremental_unless_forced(tmp_path):
    calls = []

    def counting_get(url):
        calls.append(url)
        return _fake_get(url)

    faa.fetch(CYCLE, root=tmp_path, kinds=("cifp",), get=counting_get, get_text=_fake_get_text)
    assert len(calls) == 1

    # already cached -> no new download
    faa.fetch(CYCLE, root=tmp_path, kinds=("cifp",), get=counting_get, get_text=_fake_get_text)
    assert len(calls) == 1

    # force -> re-download
    faa.fetch(CYCLE, root=tmp_path, kinds=("cifp",), force=True,
              get=counting_get, get_text=_fake_get_text)
    assert len(calls) == 2

    # a new kind is fetched even without force
    faa.fetch(CYCLE, root=tmp_path, kinds=("cifp", "nasr"),
              get=counting_get, get_text=_fake_get_text)
    assert len(calls) == 3  # +1 for nasr only


def test_fetch_raises_when_all_cifp_candidates_missing(tmp_path):
    def all_404(url):
        raise faa.DataUnavailable(f"404 {url}")

    with pytest.raises(faa.DataUnavailable):
        faa.fetch(CYCLE, root=tmp_path, kinds=("cifp",), get=all_404, get_text=_fake_get_text)


# --------------------------------------------------------------------------- #
# zip safety                                                                 #
# --------------------------------------------------------------------------- #
def test_safe_extract_rejects_path_escape(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../evil.txt", b"pwned")
    with zipfile.ZipFile(io.BytesIO(buf.getvalue())) as zf:
        with pytest.raises(ValueError, match="unsafe"):
            faa._safe_extract(zf, tmp_path, None)
    assert not (tmp_path.parent / "evil.txt").exists()


# --------------------------------------------------------------------------- #
# manifest validity line                                                     #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "on, needle",
    [
        (date(2026, 9, 10), "current, expires in 21 days"),
        (date(2026, 9, 30), "expires in 1 day"),
        (date(2026, 10, 1), "EXPIRES TODAY"),
        (date(2026, 10, 5), "EXPIRED 4 days ago"),
    ],
)
def test_validity_line(on, needle):
    m = faa.Manifest(cycle="2609", effective=date(2026, 9, 3),
                     expires=date(2026, 10, 1), fetched_at="")
    assert needle in m.validity_line(on)


# --------------------------------------------------------------------------- #
# cache discovery + CLI                                                      #
# --------------------------------------------------------------------------- #
def test_cached_cycles_and_newest(tmp_path):
    for ident in ("2607", "2609", "2608"):
        c = cycle_from_ident(ident)
        d = faa.cache_dir(tmp_path, c)
        d.mkdir(parents=True)
        faa.Manifest(cycle=ident, effective=c.effective,
                     expires=c.expiration, fetched_at="").write(d / faa.MANIFEST_NAME)
    assert faa.cached_cycles(tmp_path) == ["2607", "2608", "2609"]
    assert faa.newest_cached_manifest(tmp_path).cycle == "2609"


def test_cached_cycles_empty(tmp_path):
    assert faa.cached_cycles(tmp_path) == []
    assert faa.newest_cached_manifest(tmp_path) is None


def test_cli_status_without_cache(tmp_path, capsys):
    rc = faa.main(["--data-dir", str(tmp_path), "status"])
    assert rc == 1
    assert "no cached FAA data" in capsys.readouterr().out


def test_cli_update_calls_fetch(tmp_path, monkeypatch, capsys):
    seen = {}

    def fake_fetch(cycle, *, root, kinds, force, **kw):
        seen.update(cycle=cycle.ident, root=root, kinds=kinds, force=force)
        return faa.Manifest(cycle=cycle.ident, effective=cycle.effective,
                            expires=cycle.expiration, fetched_at="2026-09-09T00:00:00Z")

    monkeypatch.setattr(faa, "fetch", fake_fetch)
    rc = faa.main(["--data-dir", str(tmp_path), "update", "--cycle", "2609", "--kinds", "cifp"])
    assert rc == 0
    assert seen == {"cycle": "2609", "root": Path(str(tmp_path)), "kinds": ("cifp",), "force": False}


def test_cli_update_rejects_bad_cycle(tmp_path):
    with pytest.raises(ValueError):
        faa.main(["--data-dir", str(tmp_path), "update", "--cycle", "2699"])
