"""Test degli script di rilascio (scripts/, .github/workflows/release.yml).

Il workflow si fida di questi script per decisioni che non si possono
sbagliare: pubblicare solo da un tag firmato dalla sottochiave dei tag del
maintainer (non dalla sottochiave della CI né dalla primaria), firmare gli
allegati solo con la sottochiave della CI, pubblicare su AUR solo un PKGBUILD
che costruisce dal tag firmato. Ruoli in scripts/release-keys.sh.

Chiavi e repository sono temporanei, mai quelli reali: GNUPGHOME e HOME
puntano sempre in una cartella di prova. Nessuna rete.

Uso: python3 tests/release_tools_test.py
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
PASSPHRASE = "segreta di prova"


def _env(home: Path, **extra) -> dict:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("GPG_", "GNUPG", "GIT_", "OLLADESK_", "AUR_"))}
    env.update(HOME=str(home), LC_ALL="C", **extra)
    return env


def _run(cmd, home: Path, cwd=None, check=True, **extra):
    proc = subprocess.run([str(c) for c in cmd], cwd=cwd, env=_env(home, **extra),
                          capture_output=True, text=True, timeout=120)
    if check and proc.returncode != 0:
        raise AssertionError(f"{cmd} -> {proc.returncode}\n{proc.stdout}\n{proc.stderr}")
    return proc


class Keyring:
    """Portachiavi temporaneo con una chiave «di rilascio» come quella vera
    (primaria [SC], una sottochiave per i tag e una per la CI) e una chiave
    estranea."""

    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.home = tmp / "gnupg"
        self.home.mkdir(mode=0o700)
        self.gpg("--quick-gen-key", "Rilascio di prova <rilascio@example.org>",
                 "ed25519", "sign,cert", "1y")
        self.release = self._fprs("rilascio@example.org")[0]
        self.gpg("--quick-add-key", self.release, "ed25519", "sign", "1y")
        self.gpg("--quick-add-key", self.release, "ed25519", "sign", "1y")
        self.tag_subkey, self.ci_subkey = self._fprs("rilascio@example.org")[1:3]
        self.gpg("--quick-gen-key", "Estranea <estranea@example.org>", "ed25519", "sign,cert", "1y")
        self.other = self._fprs("estranea@example.org")[0]
        self.pubkey = tmp / "release-key.asc"
        self.pubkey.write_text(self.gpg("--armor", "--export", self.release).stdout)

    def gpg(self, *args, check=True):
        return _run(["gpg", "--homedir", self.home, "--batch", "--pinentry-mode", "loopback",
                     "--passphrase", PASSPHRASE, *args], self.tmp, check=check)

    def _fprs(self, uid):
        out = self.gpg("--with-colons", "--list-keys", uid).stdout
        return [line.split(":")[9] for line in out.splitlines() if line.startswith("fpr:")]

    def subkey_secret(self, sub) -> str:
        """Come nel secret della CI: solo quella sottochiave."""
        return self.gpg("--armor", "--export-secret-subkeys", f"{sub}!").stdout

    def roles(self) -> dict:
        """Ruoli della chiave di prova per scripts/release-keys.sh."""
        return {"OLLADESK_RELEASE_PRIMARY": self.release,
                "OLLADESK_TAG_SIGNING_SUBKEYS": self.tag_subkey,
                "OLLADESK_CI_SIGNING_SUBKEY": self.ci_subkey}

    def close(self):
        subprocess.run(["gpgconf", "--homedir", str(self.home), "--kill", "gpg-agent"],
                       env=_env(self.tmp), capture_output=True)


def _git(repo: Path) -> list:
    """git sul repository di prova, senza la configurazione dell'utente."""
    return ["git", "-C", repo, "-c", "user.name=prova", "-c", "user.email=prova@example.org",
            "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false"]


def _project(tmp: Path, keys: Keyring) -> Path:
    """Un repository minimo con gli script veri e la chiave di prova."""
    repo = tmp / "progetto"
    (repo / "scripts").mkdir(parents=True)
    (repo / "packaging").mkdir()
    for name in ("verify-tag.sh", "sign-release.sh", "publish-aur.sh", "release-keys.sh"):
        shutil.copy(SCRIPTS / name, repo / "scripts" / name)
    shutil.copy(keys.pubkey, repo / "packaging" / "olladesk-release-key.asc")
    (repo / "README").write_text("x")
    _run(["git", "init", "-q", repo], tmp)
    _run([*_git(repo), "add", "."], tmp)
    _run([*_git(repo), "commit", "-q", "-m", "inizio"], tmp)
    return repo


def _gpg_wrapper(tmp: Path, keys: Keyring) -> Path:
    wrapper = tmp / "gpg-prova"
    wrapper.write_text(
        f'#!/bin/sh\nexec gpg --homedir "{keys.home}" --pinentry-mode loopback '
        f'--passphrase "{PASSPHRASE}" "$@"\n'
    )
    wrapper.chmod(0o755)
    return wrapper


def _tag(repo, tmp, keys, name, *, key=None, annotated=True):
    if not annotated:
        args = [name]
    elif key is None:
        args = ["-a", "-m", name, name]
    else:
        args = ["-s", "-u", key, "-m", name, name]
    _run([*_git(repo), "-c", f"gpg.program={_gpg_wrapper(tmp, keys)}", "tag", *args], tmp)


# -- scripts/verify-tag.sh ------------------------------------------------------

def test_tag_firmato_dalla_sottochiave_dei_tag_accettato(tmp, keys):
    repo = _project(tmp, keys)
    _tag(repo, tmp, keys, "v1.0.0", key=f"{keys.tag_subkey}!")
    proc = _run(["scripts/verify-tag.sh", "v1.0.0"], tmp, cwd=repo, **keys.roles())
    assert f"firmato dal maintainer (sottochiave {keys.tag_subkey}" in proc.stdout, proc.stdout


def test_tag_firmato_dalla_sottochiave_della_ci_rifiutato(tmp, keys):
    # firma valida della chiave di rilascio: è il caso di una CI compromessa
    # che firma un tag. Non deve passare.
    repo = _project(tmp, keys)
    _tag(repo, tmp, keys, "v1.0.0", key=f"{keys.ci_subkey}!")
    proc = _run(["scripts/verify-tag.sh", "v1.0.0"], tmp, cwd=repo, check=False, **keys.roles())
    assert proc.returncode != 0
    assert f"firmato da {keys.ci_subkey}, che non è una sottochiave dei tag" in proc.stderr, proc.stderr


def test_tag_firmato_dalla_primaria_rifiutato(tmp, keys):
    # in OllaDesk la primaria resta [SC] (ha firmato le release fino alla
    # 0.2.4) ma non firma più: un tag firmato con lei non passa
    repo = _project(tmp, keys)
    _tag(repo, tmp, keys, "v1.0.0", key=f"{keys.release}!")
    proc = _run(["scripts/verify-tag.sh", "v1.0.0"], tmp, cwd=repo, check=False, **keys.roles())
    assert proc.returncode != 0
    assert f"firmato da {keys.release}, che non è una sottochiave dei tag" in proc.stderr, proc.stderr


def test_tag_non_valido_rifiutato(tmp, keys):
    for caso in ("altra chiave", "annotato senza firma", "leggero"):
        sub = tmp / caso.replace(" ", "_")
        sub.mkdir()
        repo = _project(sub, keys)
        if caso == "altra chiave":
            _tag(repo, sub, keys, "v1.0.0", key=keys.other)
        else:
            _tag(repo, sub, keys, "v1.0.0", annotated=(caso != "leggero"))
        proc = _run(["scripts/verify-tag.sh", "v1.0.0"], sub, cwd=repo, check=False, **keys.roles())
        assert proc.returncode != 0, caso
        assert "firmato dal maintainer" not in proc.stdout, caso


# -- scripts/sign-release.sh ----------------------------------------------------

def _dist(tmp: Path) -> Path:
    dist = tmp / "dist"
    dist.mkdir()
    (dist / "olladesk_1.0.0_all.deb").write_bytes(b"deb")
    (dist / "olladesk-1.0.0-1-any.pkg.tar.zst").write_bytes(b"arch")
    return dist


def test_firma_con_la_sottochiave_della_ci(tmp, keys):
    repo = _project(tmp, keys)
    dist = _dist(tmp)
    _run(["scripts/sign-release.sh", dist], tmp, cwd=repo, **keys.roles(),
         GPG_PRIVATE_KEY=keys.subkey_secret(keys.ci_subkey), GPG_PASSPHRASE=PASSPHRASE)
    names = sorted(p.name for p in dist.iterdir())
    assert names == sorted([
        "olladesk_1.0.0_all.deb", "olladesk_1.0.0_all.deb.sig",
        "olladesk-1.0.0-1-any.pkg.tar.zst", "olladesk-1.0.0-1-any.pkg.tar.zst.sig",
        "SHA256SUMS", "SHA256SUMS.sig",
    ]), names
    sums = (dist / "SHA256SUMS").read_text()
    assert "olladesk_1.0.0_all.deb" in sums and "pkg.tar.zst" in sums
    # firmato proprio dalla sottochiave della CI, non da un'altra
    status = keys.gpg("--status-fd", "1", "--verify", dist / "SHA256SUMS.sig",
                      dist / "SHA256SUMS").stdout
    assert f"VALIDSIG {keys.ci_subkey} " in status


def test_firma_con_un_altra_chiave_rifiutata(tmp, keys):
    # con un secret sbagliato (sottochiave dei tag, la primaria intera come
    # fino alla 0.2.4, una chiave estranea) gli allegati non si firmano
    secrets = {
        "sottochiave dei tag": keys.subkey_secret(keys.tag_subkey),
        "primaria (secret fino alla 0.2.4)":
            keys.gpg("--armor", "--export-secret-keys", f"{keys.release}!").stdout,
        "chiave estranea": keys.gpg("--armor", "--export-secret-keys", keys.other).stdout,
    }
    for caso, secret in secrets.items():
        sub = tmp / re.sub(r"\W+", "_", caso)
        sub.mkdir()
        repo = _project(sub, keys)
        dist = _dist(sub)
        proc = _run(["scripts/sign-release.sh", dist], sub, cwd=repo, check=False,
                    **keys.roles(), GPG_PRIVATE_KEY=secret, GPG_PASSPHRASE=PASSPHRASE)
        assert proc.returncode != 0, caso
        assert not list(dist.glob("*.sig")), caso


# -- scripts/publish-aur.sh -----------------------------------------------------

def _aur_files(tmp: Path, url: str) -> Path:
    aur = tmp / "aur"
    aur.mkdir()
    (aur / "PKGBUILD").write_text(f'pkgname=olladesk\npkgver=1.0.0\npkgrel=1\nurl="{url}"\n')
    (aur / ".SRCINFO").write_text("pkgbase = olladesk\n\tpkgver = 1.0.0\n")
    return aur


def test_pubblicazione_aur_solo_con_il_tag_pubblicato(tmp, keys):
    repo = _project(tmp, keys)
    # «GitHub»: un repository nudo raggiungibile come $url.git
    upstream = tmp / "upstream"
    _run(["git", "clone", "-q", "--bare", repo, f"{upstream}.git"], tmp)
    remote = tmp / "aur.git"
    _run(["git", "init", "-q", "--bare", "-b", "master", remote], tmp)
    aur = _aur_files(tmp, f"file://{upstream}")

    proc = _run(["scripts/publish-aur.sh", aur], tmp, cwd=repo, check=False, AUR_REMOTE=remote)
    assert proc.returncode != 0 and "tag v1.0.0 non clonabile" in proc.stderr, proc.stderr

    _tag(repo, tmp, keys, "v1.0.0", key=f"{keys.tag_subkey}!")
    _run(["git", "-C", repo, "push", "-q", f"{upstream}.git", "v1.0.0"], tmp)
    first = _run(["scripts/publish-aur.sh", aur], tmp, cwd=repo, AUR_REMOTE=remote)
    assert "pubblicato su AUR: olladesk 1.0.0" in first.stdout, first.stdout
    files = _run(["git", "-C", remote, "ls-tree", "--name-only", "master"], tmp).stdout
    assert files.split() == [".SRCINFO", "PKGBUILD"], files
    again = _run(["scripts/publish-aur.sh", aur], tmp, cwd=repo, AUR_REMOTE=remote)
    assert "già aggiornato" in again.stdout


# -- il repository vero: PKGBUILD, workflow, chiave pubblica ---------------------

def _roles() -> dict:
    return dict(re.findall(r'^(\w+)="\$\{OLLADESK_\w+:-([0-9A-Z_ ]+)\}"',
                           (SCRIPTS / "release-keys.sh").read_text(), re.M))


def test_pkgbuild_dal_tag_firmato(tmp, keys):
    out = tmp / "aur"
    _run([SCRIPTS / "prepare-aur.sh", out], tmp, cwd=ROOT, PATH="/usr/bin:/bin")
    pkgbuild = (out / "PKGBUILD").read_text()
    assert re.search(r'^source=\("\$pkgname::git\+\$url\.git#tag=v\$pkgver\?signed"\)$',
                     pkgbuild, re.M), pkgbuild
    assert f"validpgpkeys=('{_roles()['RELEASE_PRIMARY']}')" in pkgbuild
    assert not re.search(r"@(PKGVER|FINGERPRINT|SHA256)@", pkgbuild), pkgbuild


def test_note_di_rilascio(tmp, keys):
    # la descrizione della release viene dal repository: la versione del
    # codice deve averle, e lo script rifiuta note mancanti o in bozza
    version = re.search(r'__version__ = "(.+)"', (ROOT / "olladesk/__init__.py").read_text()).group(1)
    ok = _run([SCRIPTS / "release-notes.sh", f"v{version}"], tmp)
    assert ok.stdout.startswith("## ") and f"...v{version}" in ok.stdout, ok.stdout[:200]

    notes = tmp / "note"
    notes.mkdir()
    (notes / "1.0.0.md").write_text("bozza senza sezioni\n")
    (notes / "1.1.0.md").write_text("## Novità\n\n- voce\n")
    (notes / "1.2.0.md").write_text(
        "## Novità\n\n- voce\n\n**Full Changelog**: https://x/compare/v1.1.0...v1.2.0\n")
    cases = {"1.0.0": "nessuna sezione", "1.1.0": "Full Changelog", "9.9.9": "mancanti"}
    for v, msg in cases.items():
        proc = _run([SCRIPTS / "release-notes.sh", v], tmp, check=False,
                    OLLADESK_RELEASE_NOTES_DIR=notes)
        assert proc.returncode != 0 and msg in proc.stderr, (v, proc.stderr)
    good = _run([SCRIPTS / "release-notes.sh", "v1.2.0"], tmp, OLLADESK_RELEASE_NOTES_DIR=notes)
    assert good.stdout.startswith("## Novità")


def test_deb_installed_size_dal_contenuto(tmp, keys):
    # Installed-Size veniva da «du -sk»: blocchi allocati, diversi tra tmpfs
    # ed ext4 (328 contro 396 KiB per la 0.2.5), quindi .deb diverso a
    # parità di contenuto. Ora deve valere quanto dice il contenuto stesso:
    # file arrotondati al KiB, 1 KiB per directory e collegamenti.
    if not shutil.which("dpkg-deb"):
        print("    (dpkg-deb assente: test saltato)")
        return
    clone = tmp / "clone"
    _run(["git", "clone", "-q", ROOT, clone], tmp)
    shutil.copy(SCRIPTS / "build-deb.sh", clone / "scripts" / "build-deb.sh")
    _run([clone / "scripts" / "build-deb.sh"], tmp, cwd=clone)
    deb = next((clone / "dist").glob("*.deb"))
    declared = int(_run(["dpkg-deb", "-f", deb, "Installed-Size"], tmp).stdout)
    expected = 0
    for line in _run(["dpkg-deb", "-c", deb], tmp).stdout.splitlines():
        perms, _owner, size, _d, _t, name = line.split(None, 5)
        if name.rstrip("/") == ".":
            continue
        expected += -(-int(size) // 1024) if perms[0] == "-" else 1
    assert declared == expected, (declared, expected)


def _jobs(text: str) -> dict:
    """Testo di ogni job di release.yml, per nome (indentazione a 2 spazi)."""
    body = text[text.index("\njobs:\n"):]
    parts = re.split(r"\n  ([\w-]+):\n", body)
    return dict(zip(parts[1::2], parts[2::2]))


def test_workflow_verifica_il_tag_e_isola_i_secret(tmp, keys):
    text = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    jobs = _jobs(text)
    assert "scripts/verify-tag.sh" in jobs["verify"]
    # note di rilascio dal repository, controllate prima di pubblicare
    assert "scripts/release-notes.sh" in jobs["verify"]
    assert "--notes-file" in jobs["publish"] and "--generate-notes" not in text
    assert "needs: verify" in jobs["arch"] and "verify" in jobs["dist"].split("\n")[0]
    # nessun job firma tag o esporta chiavi
    assert "git tag" not in text and "export-secret-keys" not in text
    # i secret solo nel job publish, nell'environment protetto
    assert [name for name, body in jobs.items() if "secrets." in body] == ["publish"]
    assert "environment: release" in jobs["publish"]
    # ogni action esterna è fissata per SHA
    for ref in re.findall(r"uses:\s*([^\s#]+)", text):
        assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", ref), ref


def test_ruoli_delle_chiavi_coerenti_con_la_chiave_pubblica(tmp, keys):
    # scripts/release-keys.sh e packaging/olladesk-release-key.asc devono
    # parlare della stessa chiave: due sottochiavi di firma distinte, ognuna
    # con il suo ruolo. Fallisce finché le sottochiavi non sono state create
    # e la chiave pubblica riesportata (packaging/README.md).
    roles = _roles()
    out = _run(["gpg", "--homedir", keys.home, "--batch", "--with-colons", "--show-keys",
                ROOT / "packaging/olladesk-release-key.asc"], tmp).stdout
    found, kind = {}, None
    for r in (line.split(":") for line in out.splitlines()):
        if r[0] in ("pub", "sub"):
            kind = (r[0], r[11])
        elif r[0] == "fpr" and kind:
            found[r[9]] = kind
            kind = None
    primary = roles["RELEASE_PRIMARY"]
    tag_subkeys = roles["TAG_SIGNING_SUBKEYS"].split()
    ci = roles["CI_SIGNING_SUBKEY"]
    assert found.get(primary, ("",))[0] == "pub", primary
    for sub in (*tag_subkeys, ci):
        assert found.get(sub) == ("sub", "s"), f"{sub}: sottochiave di firma mancante nella chiave pubblica"
    assert ci not in tag_subkeys


def main() -> int:
    if not all(shutil.which(t) for t in ("gpg", "git", "bash")):
        print("SKIP: servono gpg, git e bash")
        return 0
    base = Path(tempfile.mkdtemp(prefix="olladesk_release_test_"))
    keys = Keyring(base)
    failed = 0
    try:
        for name, fn in sorted(globals().items()):
            if not (name.startswith("test_") and callable(fn)):
                continue
            tmp = base / name
            tmp.mkdir()
            try:
                fn(tmp, keys)
                print(f"  ✓ {name}")
            except AssertionError as e:
                failed += 1
                print(f"  ✗ {name}: {e}")
            except Exception as e:  # noqa: BLE001
                failed += 1
                print(f"  ✗ {name}: ERRORE {e.__class__.__name__}: {e}")
    finally:
        keys.close()
        shutil.rmtree(base, ignore_errors=True)
    print("RELEASE TOOLS OK" if not failed else f"RELEASE TOOLS FAILED ({failed})")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
