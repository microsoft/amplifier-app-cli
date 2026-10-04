"""Real installed CLI producer plus separately installed catalog; fixture-only."""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace

from amplifier_app_cli.catalog_notices import ENVIRONMENT
from amplifier_app_cli.main import CommandProcessor
from amplifier_app_cli.session_store import SessionStore
from amplifier_session_catalog import Catalog
from amplifier_session_catalog.discovery import Discovery
from amplifier_session_catalog.hints import HintInbox


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True);args=parser.parse_args()
    report={'scope':'Installed CLI save/rename producer and independently installed catalog on isolated physical sources; no agents/provider calls/live mutation',
            'cliPackage':__import__('amplifier_app_cli').__file__,'catalogPackage':__import__('amplifier_session_catalog').__file__}
    with tempfile.TemporaryDirectory(prefix='cli-catalog-producer-') as temporary:
        root=Path(temporary).resolve();workspace=root/'workspace';workspace.mkdir();home=root/'native';inbox=root/'hints';inbox.mkdir(mode=0o700)
        previous={key:os.environ.get(key) for key in (ENVIRONMENT,'AMPLIFIER_HOME')};cwd=Path.cwd()
        os.environ[ENVIRONMENT]=str(inbox);os.environ['AMPLIFIER_HOME']=str(home);os.chdir(workspace)
        try:
            store=SessionStore();catalog=Catalog(root/'index.sqlite');app=root/'configured-native-owner';discovery=Discovery(catalog,[home],app_homes=[app]);consumer=HintInbox(inbox,discovery)
            store.save_new('selected',[{'role':'user','content':'Canonical selected text'}],{'working_dir':str(workspace),'name':'Initial'})
            directory=store.base_dir/'selected';transcript=(directory/'transcript.jsonl').read_bytes();history_digest=hashlib.sha256(transcript).hexdigest()
            assert consumer.drain(limit=1)['processed']==1;consumer.close()
            selected=catalog.list(connectionId='producer')['items'][0];assert selected['nativeSessionId']=='selected'
            # Actual /rename implementation, not a substitute metadata mutation.
            processor=CommandProcessor.__new__(CommandProcessor);processor.session=SimpleNamespace(coordinator=SimpleNamespace(session_id='selected'))
            assert 'CLI name' in asyncio.run(processor._rename_session('CLI name'))
            for number in range(5):store.update_metadata('selected',{'name':'CLI name','description':f'Coalesced {number}'})
            notices=list(inbox.iterdir());assert len(notices)==1 and notices[0].stat().st_size<=8192 and stat.S_IMODE(notices[0].stat().st_mode)==0o600
            value=json.loads(notices[0].read_text());assert value=={'version':1,'sessionDirectory':str(directory.resolve())}
            opens=[];original=os.open
            def guarded(path,*a,**k):
                path=Path(path);assert path.name not in {'events.jsonl','transcript.jsonl','transcript.jsonl.backup'}
                if path.name in {'metadata.json','metadata.json.backup','lifecycle.json'}:opens.append(str(path))
                return original(path,*a,**k)
            os.open=guarded
            try:assert consumer.drain(limit=1)['processed']==1
            finally:os.open=original;consumer.close()
            assert len(opens)==1 and catalog.get(selected['uri'])['title']=='CLI name' and catalog.get(selected['uri'])['description']=='Coalesced 4'
            assert (directory/'transcript.jsonl').read_bytes()==transcript
            # Existing configured native owner supplies lifecycle authority;
            # the CLI notice carries only location and does not forge tombstones.
            marker=app/'sessions'/'selected'/'lifecycle.json';marker.parent.mkdir(parents=True)
            marker.write_text(json.dumps({'sessionId':'selected','historyCwd':str(workspace),'deleted':True,'commandId':'native-owner-delete'}))
            store.update_metadata('selected',{'description':'Owner deletion notice trigger'})
            assert consumer.drain(limit=1)['processed']==1;consumer.close();assert catalog.get(selected['uri'])['nativeDeleted'] and not catalog.list(connectionId='deleted')['items']
            marker.write_text(json.dumps({'sessionId':'selected','historyCwd':str(workspace),'deleted':False,'commandId':'native-owner-restore'}))
            store.update_metadata('selected',{'description':'Owner restoration notice trigger'})
            assert consumer.drain(limit=1)['processed']==1;consumer.close();assert not catalog.get(selected['uri'])['nativeDeleted']
            # A producer failure cannot veto the CLI canonical save.
            os.environ[ENVIRONMENT]=str(root/'uncreated-inbox')
            store.rename('selected','Canonical success despite missing inbox');assert store.get_metadata('selected')['name']=='Canonical success despite missing inbox' and not (root/'uncreated-inbox').exists()
            assert (directory/'transcript.jsonl').read_bytes()==transcript
            report.update(newSession='discovered from actual CLI save',rename='actual CLI /rename action discovered',coalescedUpdates=5,coalescedNoticeFiles=1,renameConsumerMetadataReads=1,transcriptSha256=history_digest,canonicalHistoryPreserved=True,
                          nativeOwnerTombstone='existing configured owner proof consumed; CLI emitted no lifecycle authority',nativeOwnerRestore='consumed',failedOptIn='canonical save succeeded; missing inbox not created',nativeTranscriptEventReadsByNoticeConsumer=0,agentsStarted=0,providerCalls=0)
            report['limits']=['Physical legacy CLI delete has no authoritative tombstone integration in this patch','Existing tombstone fixture is supplied by configured native owner, not invented by CLI','No watcher/background historical directory tracking','No browser/device/real-account/live deployment acceptance']
        finally:
            os.chdir(cwd)
            for key,value in previous.items():
                if value is None:os.environ.pop(key,None)
                else:os.environ[key]=value
    Path(args.output).write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))


if __name__=='__main__':main()
