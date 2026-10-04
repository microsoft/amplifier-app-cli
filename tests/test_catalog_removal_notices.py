"""Actual native lifecycle commands; notices never confer deletion authority."""
import json
import os
import shutil
from pathlib import Path

import click
from click.testing import CliRunner
import pytest

from amplifier_app_cli.catalog_notices import (
    ENVIRONMENT, capture_session_removal, announce_removed_session,
)
from amplifier_app_cli.commands import session as commands
from amplifier_app_cli.session_store import SessionStore
from amplifier_app_cli.shared_root_state import SharedRootSession, read_shared_root

pytestmark=pytest.mark.skipif(os.name!='posix',reason='Private catalog notices are POSIX-only')


def fixture(tmp_path,monkeypatch):
    root=tmp_path.resolve();workspace=root/'workspace';workspace.mkdir();monkeypatch.chdir(workspace)
    monkeypatch.setenv('AMPLIFIER_HOME',str(root/'native'));monkeypatch.setenv('AMPLIFIER_SESSION_STATE_HOME',str(root/'shared'))
    monkeypatch.delenv(ENVIRONMENT,raising=False);store=SessionStore();store.save_new('selected',[{'role':'user','content':'canonical selected'}],{'working_dir':str(workspace),'turn_count':1})
    inbox=root/'hints';inbox.mkdir(mode=0o700);monkeypatch.setenv(ENVIRONMENT,str(inbox))
    cli=click.Group()
    def forbidden(*a,**k):raise AssertionError('No native execution')
    commands.register_session_commands(cli,interactive_chat=forbidden,execute_single=forbidden,get_module_search_paths=list);monkeypatch.setattr(commands,'SessionStore',lambda:store)
    return store,inbox,cli


def notice(inbox,directory):
    files=list(inbox.iterdir());assert len(files)==1;assert files[0].stat().st_mode&0o777==0o600
    assert files[0].stat().st_size<=8192;assert json.loads(files[0].read_bytes())=={'version':1,'sessionDirectory':str(directory)}


@pytest.mark.parametrize('shared',[False,True])
def test_actual_delete_emits_only_after_native_success(tmp_path,monkeypatch,shared):
    store,inbox,cli=fixture(tmp_path,monkeypatch);directory=store.base_dir/'selected'
    if not shared:monkeypatch.setattr(commands,'_shared_root_platform_supported',lambda:False)
    else:
        root=SharedRootSession.acquire('selected')
        try:root.held.write([{'role':'user','content':'historical checkpoint'}],bundle='anchors',metadata={})
        finally:root.release()
    result=CliRunner().invoke(cli,['session','delete','selected','--force']);assert result.exit_code==0,result.output
    assert not directory.exists();notice(inbox,directory)
    if shared:assert read_shared_root('selected') is None


def test_busy_native_owner_emits_no_notice_and_preserves_history(tmp_path,monkeypatch):
    store,inbox,cli=fixture(tmp_path,monkeypatch);directory=store.base_dir/'selected';before={p.name:p.read_bytes()for p in directory.iterdir()if p.is_file()}
    root=SharedRootSession.acquire('selected')
    try:result=CliRunner().invoke(cli,['session','delete','selected','--force']);assert result.exit_code!=0
    finally:root.release()
    assert not list(inbox.iterdir());assert {p.name:p.read_bytes()for p in directory.iterdir()if p.is_file()}==before


@pytest.mark.parametrize('stage',['native-removal','checkpoint'])
def test_failed_delete_emits_no_notice(tmp_path,monkeypatch,stage):
    store,inbox,cli=fixture(tmp_path,monkeypatch)
    def failed(*a,**k):raise OSError('owned fixture failure')
    if stage=='native-removal':monkeypatch.setattr(shutil,'rmtree',failed)
    else:monkeypatch.setattr(SharedRootSession,'delete_checkpoint',failed)
    result=CliRunner().invoke(cli,['session','delete','selected','--force']);assert result.exit_code!=0;assert not list(inbox.iterdir())
    assert (store.base_dir/'selected').exists()==(stage=='native-removal')


@pytest.mark.parametrize('mode',['cli','store','callback'])
def test_cleanup_emits_after_successful_removal(tmp_path,monkeypatch,mode):
    store,inbox,cli=fixture(tmp_path,monkeypatch);directory=store.base_dir/'selected';os.utime(directory,(1,1))
    if mode=='cli':result=CliRunner().invoke(cli,['session','cleanup','--days','1','--force']);assert result.exit_code==0,result.output
    elif mode=='store':assert store.cleanup_old_sessions(days=1)==1
    else:
        def removed(path):shutil.rmtree(path);return True
        assert store.cleanup_old_sessions(days=1,remove_session=removed)==1
    assert not directory.exists();notice(inbox,directory)


@pytest.mark.parametrize('outcome',['skip','failed','false-success'])
def test_callback_nonremoval_does_not_announce(tmp_path,monkeypatch,outcome):
    store,inbox,_=fixture(tmp_path,monkeypatch);directory=store.base_dir/'selected';os.utime(directory,(1,1))
    def remover(path):
        if outcome=='failed':raise OSError('owned policy refusal')
        return outcome=='false-success'
    if outcome=='failed':
        with pytest.raises(OSError):store.cleanup_old_sessions(days=1,remove_session=remover)
    else:store.cleanup_old_sessions(days=1,remove_session=remover)
    assert directory.exists();assert not list(inbox.iterdir())


def test_cleanup_retains_shared_checkpoints_and_protected_rows_without_notices(tmp_path,monkeypatch):
    store,inbox,cli=fixture(tmp_path,monkeypatch);directory=store.base_dir/'selected';os.utime(directory,(1,1))
    assert store.cleanup_old_sessions(days=1,protected_ids={'selected'})==0;assert not list(inbox.iterdir())
    root=SharedRootSession.acquire('selected')
    try:root.held.write([{'role':'user','content':'preserved checkpoint'}],bundle='anchors',metadata={})
    finally:root.release()
    result=CliRunner().invoke(cli,['session','cleanup','--days','1','--force']);assert result.exit_code==0,result.output;assert directory.exists();assert not list(inbox.iterdir());assert read_shared_root('selected')


def test_unsafe_inbox_cannot_veto_completed_native_removal(tmp_path,monkeypatch):
    store,inbox,cli=fixture(tmp_path,monkeypatch);inbox.chmod(0o755)
    result=CliRunner().invoke(cli,['session','delete','selected','--force']);assert result.exit_code==0,result.output
    assert not (store.base_dir/'selected').exists();assert not list(inbox.iterdir())


@pytest.mark.parametrize('changed',['recreated','symlink','parent-replaced'])
def test_replaced_source_or_parent_is_not_announced(tmp_path,monkeypatch,changed):
    store,inbox,_=fixture(tmp_path,monkeypatch);directory=store.base_dir/'selected';location=capture_session_removal(directory);assert location
    shutil.rmtree(directory)
    if changed=='recreated':directory.mkdir()
    elif changed=='symlink':directory.symlink_to(tmp_path/'foreign')
    else:store.base_dir.rename(store.base_dir.with_name('old-sessions'));store.base_dir.mkdir()
    assert not announce_removed_session(location);assert not list(inbox.iterdir())


def test_capture_refuses_source_alias_and_missing_source(tmp_path,monkeypatch):
    store,inbox,_=fixture(tmp_path,monkeypatch);alias=tmp_path/'alias';alias.symlink_to(store.base_dir/'selected')
    assert capture_session_removal(alias) is None;assert capture_session_removal(store.base_dir/'absent') is None
    assert not announce_removed_session(None);assert not list(inbox.iterdir())


def test_opt_out_has_zero_notice_filesystem_io(tmp_path,monkeypatch):
    monkeypatch.delenv(ENVIRONMENT,raising=False)
    def forbidden(*a,**k):raise AssertionError('Opt-out touched filesystem')
    monkeypatch.setattr(Path,'lstat',forbidden);monkeypatch.setattr(Path,'resolve',forbidden);monkeypatch.setattr(os,'open',forbidden)
    assert capture_session_removal(tmp_path/'source') is None;assert not announce_removed_session(None)


def test_emitter_never_reads_bodies_or_lists_spool(tmp_path,monkeypatch):
    store,inbox,_=fixture(tmp_path,monkeypatch);directory=store.base_dir/'selected'
    def forbidden(*a,**k):raise AssertionError('Notice emitter opened bodies/listed spool')
    with monkeypatch.context()as m:
        m.setattr(Path,'open',forbidden);m.setattr(os,'scandir',forbidden);location=capture_session_removal(directory);assert location
    shutil.rmtree(directory)
    with monkeypatch.context()as m:
        m.setattr(Path,'open',forbidden);m.setattr(os,'scandir',forbidden);assert announce_removed_session(location)
    notice(inbox,directory)
