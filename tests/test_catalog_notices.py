"""CLI-owned producer tests use the installed Foundation dependency."""
import hashlib
import json
import os
from pathlib import Path
import stat

import pytest

from amplifier_app_cli.catalog_notices import ENVIRONMENT, announce_saved_session
from amplifier_app_cli.session_store import SessionStore

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='Private catalog notice v1 is POSIX-only')


def configured(tmp_path,monkeypatch):
    inbox=tmp_path/'hints';inbox.mkdir(mode=0o700)
    monkeypatch.setenv(ENVIRONMENT,str(inbox))
    store=SessionStore(tmp_path/'native'/'sessions')
    return store,inbox


def payload(inbox,directory):
    name=hashlib.sha256(str(directory.resolve()).encode()).hexdigest()+'.json';path=inbox/name
    assert stat.S_IMODE(path.stat().st_mode)==0o600
    assert path.stat().st_size<=8192
    value=json.loads(path.read_text());assert value=={'version':1,'sessionDirectory':str(directory.resolve())}
    assert sorted(p.name for p in inbox.iterdir())==[name]
    return path


def test_successful_native_write_variants_coalesce_exact_location_only_notices(tmp_path,monkeypatch):
    store,inbox=configured(tmp_path,monkeypatch);directory=store.base_dir/'one'
    store.save_new('one',[{'role':'user','content':'private original text'}],{'working_dir':str(tmp_path),'name':'Original','access_token':'hidden'})
    path=payload(inbox,directory);before=(directory/'transcript.jsonl').read_bytes()
    store.rename('one','Renamed');payload(inbox,directory)
    store.update_metadata('one',{'description':'Private description'});payload(inbox,directory)
    store._save_metadata(directory,{'working_dir':str(tmp_path),'name':'Merged update'});payload(inbox,directory)
    assert (directory/'transcript.jsonl').read_bytes()==before
    store._save_transcript(directory,[{'role':'user','content':'Private changed text'}]);payload(inbox,directory)
    store.save('one',[{'role':'user','content':'Final private text'}],{'working_dir':str(tmp_path),'name':'Final'});payload(inbox,directory)
    assert 'Final private text' not in path.read_text() and 'access_token' not in path.read_text()


def test_opt_out_does_not_create_notice_directory(tmp_path,monkeypatch):
    monkeypatch.delenv(ENVIRONMENT,raising=False)
    store=SessionStore(tmp_path/'native');store.save_new('one',[],{'name':'Saved'})
    assert not (tmp_path/'hints').exists() and announce_saved_session(store.base_dir/'one') is False


@pytest.mark.parametrize('kind',['missing','relative','public','symlink','foreign-directory-entry'])
def test_bad_opt_in_cannot_fail_canonical_save_or_change_foreign_files(tmp_path,monkeypatch,kind):
    store,inbox=configured(tmp_path,monkeypatch)
    if kind=='missing':monkeypatch.setenv(ENVIRONMENT,str(tmp_path/'absent'))
    elif kind=='relative':monkeypatch.setenv(ENVIRONMENT,'relative-inbox')
    elif kind=='public':inbox.chmod(0o755)
    elif kind=='symlink':
        link=tmp_path/'alias';link.symlink_to(inbox);monkeypatch.setenv(ENVIRONMENT,str(link))
    else:
        name=hashlib.sha256(str((store.base_dir/'one').resolve()).encode()).hexdigest()+'.json'
        foreign=inbox/name;foreign.mkdir();(foreign/'preserved').write_text('foreign contents')
    store.save_new('one',[{'role':'user','content':'canonical'}],{'name':'Saved'})
    assert store.get_metadata('one')['name']=='Saved'
    assert not (tmp_path/'absent').exists() and not any(p.name.startswith('.catalog-notice-') for p in inbox.iterdir())
    if kind=='foreign-directory-entry':assert (foreign/'preserved').read_text()=='foreign contents'


def test_notice_after_canonical_failure_is_never_emitted(tmp_path,monkeypatch):
    store,inbox=configured(tmp_path,monkeypatch)
    from amplifier_foundation.session.history import SessionHistoryStore
    def failed(*args,**kwargs):raise OSError('fixture persistence failure')
    monkeypatch.setattr(SessionHistoryStore,'save',failed)
    with pytest.raises(OSError):store.save('one',[],{})
    assert not list(inbox.iterdir())
    with pytest.raises(OSError):store.save_new('two',[],{})
    assert not list(inbox.iterdir()) and not (store.base_dir/'two').exists()


def test_emitter_reads_no_canonical_bodies_or_spool_listing(tmp_path,monkeypatch):
    store,inbox=configured(tmp_path,monkeypatch);directory=store.base_dir/'one';directory.mkdir()
    def forbidden(*args,**kwargs):raise AssertionError('Emitter read/listed source bodies')
    with monkeypatch.context() as m:
        m.setattr(Path,'open',forbidden);m.setattr(os,'scandir',forbidden)
        assert announce_saved_session(directory)
    payload(inbox,directory)


def test_atomic_publication_is_fsynced_and_replaces_claim_without_deleting_it(tmp_path,monkeypatch):
    store,inbox=configured(tmp_path,monkeypatch);directory=store.base_dir/'one';directory.mkdir()
    assert announce_saved_session(directory)
    first=payload(inbox,directory);claim=first.with_name(first.stem+'.processing-'+'a'*32);first.rename(claim)
    original=os.fsync;synced=[]
    def observed(fd):synced.append(stat.S_ISDIR(os.fstat(fd).st_mode));return original(fd)
    monkeypatch.setattr(os,'fsync',observed)
    assert announce_saved_session(directory) and claim.exists() and first.exists() and synced==[False,True]
    assert json.loads(first.read_text())==json.loads(claim.read_text())


def test_opened_directory_identity_prevents_redirect_after_path_swap(tmp_path,monkeypatch):
    store,inbox=configured(tmp_path,monkeypatch);directory=store.base_dir/'one';directory.mkdir()
    other=tmp_path/'other';other.mkdir(mode=0o755);moved=tmp_path/'owned-moved'
    original=os.open;swapped=False
    def opened(path,flags,*args,**kwargs):
        nonlocal swapped
        fd=original(path,flags,*args,**kwargs)
        if Path(path)==inbox and not swapped:
            swapped=True;inbox.rename(moved);inbox.symlink_to(other)
        return fd
    monkeypatch.setattr(os,'open',opened)
    assert announce_saved_session(directory)
    assert not list(other.iterdir()) and len(list(moved.iterdir()))==1


def test_fsync_failure_and_close_failure_preserve_canonical_save(tmp_path,monkeypatch):
    store,inbox=configured(tmp_path,monkeypatch)
    original=os.fsync
    def failed(fd):
        if os.fstat(fd).st_ino==inbox.stat().st_ino:raise OSError('private fixture durability failure')
        return original(fd)
    # Only announcement directory fsync fails; Foundation canonical writes finish.
    monkeypatch.setattr(os,'fsync',failed)
    store.save('one',[{'role':'user','content':'preserved'}],{'name':'Preserved'})
    assert store.get_metadata('one')['name']=='Preserved'


@pytest.mark.asyncio
async def test_actual_cli_rename_action_emits_after_native_metadata_only_change(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from amplifier_app_cli.main import CommandProcessor
    workspace=tmp_path/'workspace';workspace.mkdir();monkeypatch.chdir(workspace)
    monkeypatch.setenv('AMPLIFIER_HOME',str(tmp_path/'native-home'))
    inbox=tmp_path/'hints';inbox.mkdir(mode=0o700);monkeypatch.setenv(ENVIRONMENT,str(inbox))
    store=SessionStore();store.save_new('selected',[{'role':'user','content':'Preserved transcript'}],{'working_dir':str(workspace),'name':'Before'})
    directory=store.base_dir/'selected';transcript=(directory/'transcript.jsonl').read_bytes()
    processor=CommandProcessor.__new__(CommandProcessor);processor.session=SimpleNamespace(coordinator=SimpleNamespace(session_id='selected'))
    result=await processor._rename_session('Actual CLI rename')
    assert 'Actual CLI rename' in result and store.get_metadata('selected')['name']=='Actual CLI rename'
    assert (directory/'transcript.jsonl').read_bytes()==transcript
    payload(inbox,directory)
