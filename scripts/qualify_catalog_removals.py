import argparse,json,os,sys,tempfile,hashlib
from pathlib import Path
parser=argparse.ArgumentParser();parser.add_argument('--output',required=True);parser.add_argument('--cli-target');parser.add_argument('--catalog-site',required=True);parser.add_argument('--expect-notice',action='store_true');args=parser.parse_args();args.output=str(Path(args.output).resolve())
if args.cli_target:sys.path.insert(0,args.cli_target)
import click
from click.testing import CliRunner
from amplifier_app_cli.commands import session as commands
from amplifier_app_cli.session_store import SessionStore
from amplifier_app_cli.shared_root_state import SharedRootSession
from amplifier_app_cli.catalog_notices import ENVIRONMENT
import amplifier_foundation
sys.path.insert(0,args.catalog_site)
from amplifier_session_catalog import Catalog
from amplifier_session_catalog.discovery import Discovery
from amplifier_session_catalog.hints import HintInbox
catalog_active=False
body_opens=0
def audit(event,values):
 global body_opens
 if catalog_active and event=='open' and isinstance(values[0],(str,bytes)) and Path(os.fsdecode(values[0])).name in {'transcript.jsonl','transcript.jsonl.backup','events.jsonl'}:
  body_opens+=1;raise AssertionError('Catalog must not open native history bodies')
sys.addaudithook(audit)
report={'cliPackage':__import__('amplifier_app_cli').__file__,'foundationPackage':amplifier_foundation.__file__,'catalogPackage':__import__('amplifier_session_catalog').__file__,'fixtureOnly':True,'cases':[]}
with tempfile.TemporaryDirectory(prefix='actual-cli-delete-notices-') as temporary:
 root=Path(temporary).resolve();workspace=root/'workspace';workspace.mkdir();home=root/'native';inbox=root/'hints';inbox.mkdir(mode=0o700);os.chdir(workspace);os.environ['AMPLIFIER_HOME']=str(home);os.environ['AMPLIFIER_SESSION_STATE_HOME']=str(root/'shared');os.environ.pop(ENVIRONMENT,None)
 store=SessionStore();catalog=Catalog(root/'index.sqlite');discovery=Discovery(catalog,[home]);consumer=HintInbox(inbox,discovery)
 def forbidden(*a,**k):raise AssertionError('No agent execution permitted')
 cli=click.Group();commands.register_session_commands(cli,interactive_chat=forbidden,execute_single=forbidden,get_module_search_paths=list);commands.SessionStore=lambda:store
 store.save_new('unrelated',[{'role':'user','content':'opaque unrelated original'}],{'working_dir':str(workspace),'name':'Preserve','turn_count':1});unrelated={p.name:p.read_bytes()for p in (store.base_dir/'unrelated').iterdir()if p.is_file()}
 for mode in ['delete','cleanup','store-cleanup']:
  os.environ.pop(ENVIRONMENT,None);native='selected-'+mode;directory=store.base_dir/native;store.save_new(native,[{'role':'user','content':'owned deletion original'}],{'working_dir':str(workspace),'name':mode,'turn_count':1})
  uri='ahp-session:/owned-'+mode;catalog.upsert({'uri':uri,'engineId':'amplifier','nativeSessionId':native,'workingDirectory':str(workspace),'storagePath':str(directory),'title':mode,'kind':'root','createdAt':'2026-01-01T00:00:00Z','modifiedAt':'2026-01-01T00:00:00Z'});catalog_active=True
  try:discovery.hint(str(directory))
  finally:catalog_active=False
  os.environ[ENVIRONMENT]=str(inbox)
  if mode=='delete':result=CliRunner().invoke(cli,['session','delete',native,'--force']);assert result.exit_code==0,result.output
  else:
   os.utime(directory,(1,1))
   if mode=='cleanup':result=CliRunner().invoke(cli,['session','cleanup','--days','1','--force']);assert result.exit_code==0,result.output
   else:assert store.cleanup_old_sessions(days=1)==1
  assert not directory.exists();notices=list(inbox.iterdir());assert len(notices)==int(args.expect_notice)
  if notices:
   payload=json.loads(notices[0].read_bytes());assert payload=={'version':1,'sessionDirectory':str(directory)};assert notices[0].stat().st_mode&0o777==0o600;assert notices[0].stat().st_size<=8192
  catalog_active=True
  try:drained=consumer.drain(limit=1)
  finally:catalog_active=False;consumer.close()
  listed=catalog.list(connectionId=mode)['items'];visible=any(r['uri']==uri for r in listed);assert visible!=args.expect_notice
  indexed=catalog.get(uri);assert indexed['nativeDeleted'] is False and indexed['productHidden'] is False
  if args.expect_notice:assert indexed['availability']=='read-only'
  else:
   catalog_active=True
   try:discovery.hint(str(directory))
   finally:catalog_active=False
   assert not any(r['uri']==uri for r in catalog.list(connectionId=mode+'-exact')['items'])
  report['cases'].append({'mode':mode,'nativeRemoved':True,'noticeFiles':len(notices),'drain':drained,'defaultVisibleAfterConsume':visible,'nativeDeleted':indexed['nativeDeleted'],'productHidden':indexed['productHidden'],'exactAbsenceHintSupported':True})
 assert {p.name:p.read_bytes()for p in (store.base_dir/'unrelated').iterdir()if p.is_file()}==unrelated
 report.update(unrelatedCanonicalSHA256={name:hashlib.sha256(raw).hexdigest()for name,raw in unrelated.items()},unrelatedCanonicalUnchanged=True,catalogHistoryBodyOpens=body_opens,agentsStarted=0)
Path(args.output).write_text(json.dumps(report,indent=2)+'\n')
