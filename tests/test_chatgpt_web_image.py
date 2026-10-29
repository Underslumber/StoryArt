import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
import subprocess
import shutil
import os
from PIL import Image
from tools import chatgpt_web_image as web
from tools import task_execution_guard as guard

RECOVERY_URL = (Path(__file__).resolve().parents[1] / "tools" / "chatgpt_web_image_recovery.mjs").as_uri()

class WebImageTests(unittest.TestCase):
    def oracle_fixture_root(self):
        configured=os.environ.get("ORACLE_FIXTURE_ROOT")
        root=Path(configured) if configured else (Path(__file__).resolve().parents[1]/".local/chatgpt-web-image/installation/node_modules/@steipete/oracle")
        if not (root/"dist/src/browser/chatgptImages.js").is_file():
            self.skipTest("Complete auth-free Oracle fixture is absent; run project runtime setup or set ORACLE_FIXTURE_ROOT")
        return str(root)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.tmp.cleanup); self.folder = Path(self.tmp.name)
        registry = patch("tools.task_execution_guard._chat_request_root", return_value=self.folder / "chat_requests")
        registry.start(); self.addCleanup(registry.stop)
        self.ref = self.folder / "ref.png"; Image.new("RGB", (16,16), "blue").save(self.ref)
        patcher = patch.object(web, "NEUTRAL_FIXTURE_SHA256", web.digest(self.ref.read_bytes()))
        patcher.start(); self.addCleanup(patcher.stop)
        self.project = self.folder / "project.json"
        self.project.write_text(json.dumps({"project_id":"g-p-abc", "project_name":"Test",
            "project_url":"https://chatgpt.com/g/g-p-abc/project"}),encoding="utf-8")
        self.journal = self.folder / "request.json"; self.output = self.folder / "out.png"
    def test_replacement_handoff_requires_guard_seal_and_preserves_unknown_journal(self):
        old_path = self.folder / "old.json"
        new_path = self.folder / "new.json"
        guard_path = self.folder / "guard.json"
        receipt_path = self.folder / "replacement-authorization.json"
        old_id, new_id = "old-unknown", "new-authorized"
        receipt = {"request_id": "request-1", "attempt_id": old_id,
            "question": guard.REPLACEMENT_AUTHORIZATION_QUESTION,
            "answer": guard.REPLACEMENT_AUTHORIZATION_ANSWER,
            "source": guard.REPLACEMENT_AUTHORIZATION_SOURCE}
        raw = json.dumps(receipt, ensure_ascii=False).encode("utf-8")
        receipt_path.write_bytes(raw)
        guard_state = {"request_id": "request-1",
            "attempts": [{"attempt_id": old_id, "status": "ABANDONED_UNKNOWN", "outcome": "NOT_KNOWN"}],
            "replacement_authorization": {"path": str(receipt_path), "sha256": web.digest(raw),
                "attempt_id": old_id, "consumed": True, "consumed_by_attempt_id": new_id}}
        guard_path.write_text(json.dumps(guard_state), encoding="utf-8")
        output_path = self.folder / "old-output.png"
        oracle_home = self.folder / "old.oracle-runtime"
        previous = {"schema_version": 1, "status": "UNKNOWN", "requested_output": str(output_path),
            "oracle_home": str(oracle_home.resolve()),
            "events": [{"status": "PREPARED"}, {"status": "UNKNOWN"}],
            "scenario_evidence": {"path": str(self.folder / "proof.json"), "sha256": "proof-hash",
                "branch": "NATIVE_DEFAULT", "plan": {"path": str(self.folder / "call.json"), "sha256": "call-hash"},
                "guard": {"path": str(guard_path)},
                "attempt": {"attempt_id": old_id, "request_id": "request-1",
                    "snapshot": {"request_id": "request-1", "provider_operation_receipt": "",
                        "execution_call": {"request_id": "request-1"}}}}}
        evidence = {"request_id": "request-1", "branch": "NATIVE_DEFAULT",
            "guard": {"path": str(guard_path)}, "attempt": {"attempt_id": new_id}}
        original_bytes = json.dumps(previous).encode("utf-8")
        old_path.write_bytes(original_bytes)
        web._validate_replacement_handoff(old_path, new_path, previous, evidence)
        self.assertEqual(old_path.read_bytes(), original_bytes)
        output_path.write_bytes(b"generated")
        with self.assertRaisesRegex(ValueError, "Replacement must reuse"):
            web._validate_replacement_handoff(old_path, new_path, previous, evidence)
        output_path.unlink()
        (oracle_home).mkdir()
        receipt_file = oracle_home / "receipt.json"
        receipt_file.write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Replacement must reuse"):
            web._validate_replacement_handoff(old_path, new_path, previous, evidence)
        receipt_file.unlink()
        generated_file = oracle_home / "generated" / "result.png"
        generated_file.parent.mkdir()
        generated_file.write_bytes(b"image")
        with self.assertRaisesRegex(ValueError, "Replacement must reuse"):
            web._validate_replacement_handoff(old_path, new_path, previous, evidence)
        generated_file.unlink()
        guard_state["replacement_authorization"]["consumed_by_attempt_id"] = "other-attempt"
        guard_path.write_text(json.dumps(guard_state), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Guard does not prove"):
            web._validate_replacement_handoff(old_path, new_path, previous, evidence)

    def prepare(self):
        return web.prepare(self.journal, web.NEUTRAL_PROMPT, [self.ref], "current",
            opt_in=True, safety_history="CLEAR", neutral_test=True,
            output=self.output, profile_dir=self.folder, oracle_root=str(self.folder / "oracle-install"),
            project_config=self.project)
    def transport(self, payload, timeout):
        self.assertEqual(web.load(self.journal)["status"], "UNKNOWN")
        yield {"type":"receipt", "session_id":"s1"}
        self.assertEqual(web.load(self.journal)["session_id"], "s1")
        out = Path(payload["oracle_home"]) / "generated" / "result.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (32,32), "white").save(out)
        yield {"type":"result", "result":{"sessionId":"s1", "status":"completed",
            "images":[{"kind":"image", "path":str(out)}]}}
    def test_autonomous_download_and_no_resubmit(self):
        self.prepare(); result=web.execute(self.journal,transport=self.transport)
        self.assertEqual(result["status"], "DOWNLOADED"); self.assertTrue(self.output.is_file())
        with self.assertRaises(ValueError): web.execute(self.journal,transport=self.transport)
    def test_timeout_without_receipt_never_resubmits(self):
        self.prepare()
        def lost(payload, timeout):
            raise TimeoutError("lost"); yield
        with self.assertRaises(TimeoutError): web.execute(self.journal,transport=lost)
        self.assertEqual(web.load(self.journal)["status"], "UNKNOWN")
        with self.assertRaises(ValueError): web.execute(self.journal,inspect=True,transport=lost)
        with self.assertRaises(ValueError): web.execute(self.journal,transport=lost)
    def test_preconnect_failure_has_one_durable_no_send_reconciliation(self):
        state = self.prepare()
        def disconnected(payload, timeout):
            Path(payload["oracle_home"]).mkdir(parents=True, exist_ok=True)
            phase_path = Path(payload["oracle_home"]) / f"transport-phase-{payload['submission_operation_id']}.jsonl"
            phase_path.write_text("\n".join(json.dumps({
                "schema_version": 1,
                "operation_id": payload["submission_operation_id"],
                "prompt_sha256": payload["prompt_sha256"],
                "phase": phase,
                "provider_call_started": False,
                **({"error_code": "CONNECTION_CLOSED"} if phase == "CONNECT_FAILED" else {}),
            }) for phase in ("CLIENT_CONNECTING", "CONNECT_FAILED")) + "\n", encoding="utf-8")
            raise ValueError("connect failed")
            yield
        with self.assertRaises(ValueError): web.execute(self.journal, transport=disconnected)
        failed = web.load(self.journal)
        self.assertEqual(failed["status"], "UNKNOWN")
        self.assertEqual(failed["events"][-1]["status"], "UNKNOWN")
        reconciled = web.reconcile_pre_receipt_failure(self.journal)
        self.assertEqual(reconciled["status"], "PREPARED")
        self.assertEqual(reconciled["pre_receipt_reconciliations"], 1)
        self.assertTrue(any(e.get("reason") == "Committed before possible provider submission" for e in reconciled["events"]))
        with self.assertRaises(ValueError): web.reconcile_pre_receipt_failure(self.journal)

    def test_legacy_unknown_or_ambiguous_phase_fails_closed(self):
        state = self.prepare()
        state["status"] = "UNKNOWN"
        web.event(state, "UNKNOWN", reason="legacy operation has no phase receipt")
        web.save(self.journal, state)
        with self.assertRaisesRegex(ValueError, "Legacy UNKNOWN has no durable operation ID"):
            web.reconcile_pre_receipt_failure(self.journal)
        failed = web.load(self.journal)
        self.assertEqual(failed["status"], "UNKNOWN")
        self.assertIsNone(failed.get("session_id"))
        self.assertFalse((Path(failed["oracle_home"]) / "generated" / "result.png").exists())

    def test_connect_success_or_consult_fence_cannot_authorize_resend(self):
        self.prepare()
        def ambiguous(payload, timeout):
            Path(payload["oracle_home"]).mkdir(parents=True, exist_ok=True)
            phase_path = Path(payload["oracle_home"]) / f"transport-phase-{payload['submission_operation_id']}.jsonl"
            rows = ["CLIENT_CONNECTING", "CLIENT_CONNECTED", "PROVIDER_CALL_MAY_HAVE_BEEN_SENT"]
            phase_path.write_text("\n".join(json.dumps({
                "schema_version": 1, "operation_id": payload["submission_operation_id"],
                "prompt_sha256": payload["prompt_sha256"], "phase": phase,
                "provider_call_started": phase == "PROVIDER_CALL_MAY_HAVE_BEEN_SENT",
            }) for phase in rows) + "\n", encoding="utf-8")
            raise TimeoutError("lost after provider fence")
            yield
        with self.assertRaises(TimeoutError): web.execute(self.journal, transport=ambiguous)
        with self.assertRaisesRegex(ValueError, "Phase history does not prove failure"):
            web.reconcile_pre_receipt_failure(self.journal)
        self.assertEqual(web.load(self.journal)["status"], "UNKNOWN")

    def test_runtime_consult_fence_runs_before_only_provider_submission(self):
        runtime = Path(web.__file__).with_name("chatgpt_web_image_runtime.js")
        node = shutil.which("node")
        if not node:
            self.skipTest("Node runtime unavailable")
        script = r'''const assert=require('node:assert/strict');
const {runImageTransport}=require(process.argv[1]);
(async()=>{
 const calls=[]; const phases=[];
 await runImageTransport({operation:'submit',status:'UNKNOWN',opt_in:true,safety_history:'CLEAR',model_strategy:'current',
  project_binding:{route:{project_id:'g-p-abc',project_url:'https://chatgpt.com/g/g-p-abc/project'}},
  prompt:'p',references:[],oracle_home:'fixture',wait_ms:1},
  async name=>{calls.push(name);return name==='consult'?{sessionId:'s1'}:{sessionId:'s1',status:'running'};},
  async value=>{}, async value=>{phases.push(value.name);});
 assert.deepEqual(phases,['consult']); assert.deepEqual(calls,['consult','wait']);
})().catch(error=>{console.error(error);process.exitCode=1;});'''
        result = subprocess.run([node, "-e", script, str(runtime)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
    def test_receipt_timeout_reconnects_only_same_session(self):
        self.prepare()
        def lost(payload,timeout):
            yield {"type":"receipt","session_id":"s1"}; raise TimeoutError("lost")
        with self.assertRaises(TimeoutError): web.execute(self.journal,transport=lost)
        def reconnect(payload,timeout):
            self.assertEqual(payload["operation"],"inspect"); self.assertEqual(payload["session_id"],"s1")
            yield {"type":"result","result":{"sessionId":"s1","status":"running"}}
        self.assertEqual(web.execute(self.journal,inspect=True,transport=reconnect)["status"],"RUNNING")
    def test_invalid_output_fails_closed(self):
        state=self.prepare(); state["session_id"]="s1"
        actual=Path(state["oracle_home"])/"generated"/"result.png"; actual.parent.mkdir(parents=True)
        for data in (self.ref.read_bytes(),b"text instead of image"):
            actual.write_bytes(data)
            with self.assertRaises(Exception): web.validate_result(state,{"sessionId":"s1","status":"completed","images":[{"kind":"image","path":str(actual)}]})
        with self.assertRaises(ValueError): web.validate_result(state,{"sessionId":"other","status":"completed","images":[]})
        with self.assertRaises(ValueError): web.validate_result(state,{"sessionId":"s1","status":"completed","images":[{"kind":"image","path":str(self.ref)}]})
    def test_no_output_overwrite(self):
        self.prepare(); self.output.write_bytes(b"keep")
        with self.assertRaises(ValueError): web.execute(self.journal,transport=self.transport)
        self.assertEqual(self.output.read_bytes(),b"keep")
    def test_tamper_and_required_authorization(self):
        with self.assertRaises(ValueError): web.prepare(self.journal,web.NEUTRAL_PROMPT,[self.ref],"current",neutral_test=True)
        self.prepare(); Image.new("RGB",(16,16),"red").save(self.ref)
        with self.assertRaises(ValueError): web.execute(self.journal,transport=self.transport)
    def test_os_lock_single_submission(self):
        self.prepare()
        def run(_):
            try: return web.execute(self.journal,transport=self.transport)
            except ValueError: return None
        with ThreadPoolExecutor(max_workers=4) as pool: results=list(pool.map(run,range(4)))
        self.assertEqual(sum(r is not None for r in results),1)
    def test_second_active_journal_rejected(self):
        self.prepare()
        with self.assertRaises(ValueError):
            web.prepare(self.folder/"second.json",web.NEUTRAL_PROMPT,[self.ref],"current",
                opt_in=True,safety_history="CLEAR",neutral_test=True,
                output=self.folder/"other.png",profile_dir=self.folder,project_config=self.project)
    def test_provider_failure_terminal(self):
        self.prepare()
        def failed(payload,timeout):
            yield {"type":"receipt","session_id":"s1"}
            yield {"type":"result","result":{"sessionId":"s1","status":"error"}}
        self.assertEqual(web.execute(self.journal,transport=failed)["status"],"PROVIDER_FAILED")
        with self.assertRaises(ValueError): web.execute(self.journal,transport=failed)

    def test_neutral_prompt_independent_contract(self):
        literal = web.NEUTRAL_PROMPT
        self.assertEqual(web.digest(literal.encode("utf-8")),
            "0a9a8517a791a05e748581313bea1a034ccbeb362ac157b516d714ba55b52239")
        self.assertEqual(web.NEUTRAL_PROMPT, literal)
        self.assertEqual(web.digest(web.NEUTRAL_PROMPT.encode("utf-8")),
            "0a9a8517a791a05e748581313bea1a034ccbeb362ac157b516d714ba55b52239")
    def test_select_unsupported_preparation_rejected(self):
        with self.assertRaises(ValueError):
            web.prepare(self.journal,web.NEUTRAL_PROMPT,[self.ref],"select",
                model_label="invented label",opt_in=True,safety_history="CLEAR",neutral_test=True,
                output=self.output,profile_dir=self.folder,project_config=self.project)
        self.assertFalse(self.journal.exists())
    def test_actual_installed_oracle_selection_and_literal_transport(self):
        oracle_root=self.oracle_fixture_root()
        runtime=Path(web.__file__).with_name("chatgpt_web_image_runtime.js")
        script=r'''const {runImageTransport,browserModels}=require(process.argv[1]);
const fs=require('node:fs'), {pathToFileURL}=require('node:url');
(async()=>{
 const base=process.argv[3]+'/dist/src/';
 const {mapConsultToRunOptions}=await import(pathToFileURL(base+'mcp/utils.js'));
 const {buildConsultBrowserConfig}=await import(pathToFileURL(base+'mcp/tools/consult.js'));
 const literal=fs.readFileSync(process.argv[2],'utf8');
 if(require('node:crypto').createHash('sha256').update(literal).digest('hex')!==
   '0a9a8517a791a05e748581313bea1a034ccbeb362ac157b516d714ba55b52239')throw Error('literal contract');
 process.env.ORACLE_HOME_DIR='fixture-home';
 for(const [label,model] of Object.entries(browserModels)){
  let sent=0;
  await runImageTransport({project_binding:{route:{project_id:'g-p-abc',project_url:'https://chatgpt.com/g/g-p-abc/project'}},operation:'submit',status:'UNKNOWN',opt_in:true,safety_history:'CLEAR',
   model_strategy:'select',model_label:label,prompt:literal,references:[{path:'fixture.png'}],oracle_home:'fixture-home',wait_ms:0},
   async(name,args)=>{
    if(name==='consult'){
     sent++;
     if(args.prompt!==literal || args.model!==model || args.browserModelLabel!==label)throw Error('actual args mismatch');
     const mapped=mapConsultToRunOptions({...args,userConfig:{},env:{ORACLE_HOME_DIR:'fixture-home'}});
     const projectURL='https://chatgpt.com/g/g-p-abc/project';
     const config=buildConsultBrowserConfig({userConfig:{browser:{chatgptUrl:projectURL,url:projectURL}},env:{},runModel:mapped.runOptions.model,
       inputModel:args.model,browserModelLabel:args.browserModelLabel,browserModelStrategy:args.browserModelStrategy});
     if(config.chatgptUrl!==projectURL || config.url!==projectURL)throw Error('project URL lost');
     if(mapped.resolvedEngine!=='browser' || config.desiredModel!==label)throw Error('installed desiredModel mismatch '+JSON.stringify(config));
     return {sessionId:'exact-session'};
    }
    return {sessionId:'exact-session',status:'running'};
   },async()=>{});
  if(sent!==1)throw Error('send count');
 }
 let sent=0;
 try { await runImageTransport({project_binding:{route:{project_id:'g-p-abc',project_url:'https://chatgpt.com/g/g-p-abc/project'}},operation:'submit',status:'UNKNOWN',opt_in:true,safety_history:'CLEAR',
  model_strategy:'select',model_label:'unknown',prompt:literal,references:[],oracle_home:'fixture-home'},async()=>{sent++;},async()=>{});
  throw Error('unsupported accepted');
 }catch(error){if(sent!==0)throw Error('unsupported label reached send');}
 console.log('installed Oracle desiredModel and actual literal args verified');
})().catch(e=>{console.error(e);process.exit(1)});'''
        literal_file=self.folder/"neutral-prompt.txt";literal_file.write_bytes(web.NEUTRAL_PROMPT.encode("utf-8"))
        result=subprocess.run([shutil.which("node"),"-e",script,str(runtime),str(literal_file),oracle_root],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_project_tampering_blocks_submission_but_not_reconnect(self):
        self.prepare()
        state=web.load(self.journal); state["project_binding"]["route"]["project_url"]="https://chatgpt.com/"
        web.save(self.journal,state)
        with self.assertRaises(ValueError): web.execute(self.journal,transport=self.transport)
        state["project_binding"]=web.project_binding(self.project); web.save(self.journal,state)
        def running(payload,timeout):
            yield {"type":"receipt","session_id":"s1"}
            yield {"type":"result","result":{"sessionId":"s1","status":"running"}}
        web.execute(self.journal,transport=running)
        self.project.write_text("{}",encoding="utf-8")
        def reconnect(payload,timeout):
            self.assertEqual(payload["project_binding"]["route"]["project_url"],"https://chatgpt.com/g/g-p-abc/project")
            yield {"type":"result","result":{"sessionId":"s1","status":"running"}}
        self.assertEqual(web.execute(self.journal,inspect=True,transport=reconnect)["status"],"RUNNING")
    def test_changed_local_project_blocks_before_unknown(self):
        self.prepare(); route=json.loads(self.project.read_text());route["project_name"]="Changed"
        self.project.write_text(json.dumps(route),encoding="utf-8")
        with self.assertRaises(ValueError): web.execute(self.journal,transport=self.transport)
        self.assertEqual(web.load(self.journal)["status"],"PREPARED")
    def test_bind_project_upgrade_preserves_call_and_blocks_consumed(self):
        self.prepare(); state=web.load(self.journal); del state["project_binding"]; del state["oracle_config"]; del state["oracle_config_sha256"]; web.save(self.journal,state)
        before={k:state[k] for k in ("prompt","prompt_sha256","references","requested_output","scenario_evidence")}
        bound=web.bind_project(self.journal,config_path=self.project)
        self.assertEqual(before,{k:bound[k] for k in before})
        self.assertEqual(bound["status"],"PREPARED")
        state=web.load(self.journal); web.event(state,"UNKNOWN"); web.save(self.journal,state)
        with self.assertRaises(ValueError): web.bind_project(self.journal,config_path=self.project)
        state["status"]="PREPARED"; web.save(self.journal,state)
        with self.assertRaises(ValueError): web.bind_project(self.journal,config_path=self.project)

    def test_pinned_runtime_config_tamper_and_legacy_reconcile(self):
        self.prepare(); state=web.load(self.journal)
        self.assertEqual(state["oracle_config"]["browser"]["chatgptUrl"],"https://chatgpt.com/g/g-p-abc/project")
        state["oracle_config"]["browser"]["url"]="https://chatgpt.com/"; web.save(self.journal,state)
        with self.assertRaises(ValueError): web.load(self.journal)
    def test_bind_project_rejects_receipt_even_prepared(self):
        self.prepare(); state=web.load(self.journal)
        home=Path(state["oracle_home"]); home.mkdir(); (home/"receipt.json").write_text("{}")
        with self.assertRaises(ValueError): web.bind_project(self.journal,config_path=self.project)
    def test_missing_project_never_reaches_runtime_consult(self):
        runtime=Path(web.__file__).with_name("chatgpt_web_image_runtime.js")
        script=r'''const {runImageTransport}=require(process.argv[1]);let sends=0;
runImageTransport({operation:'submit',status:'UNKNOWN',opt_in:true,safety_history:'CLEAR',model_strategy:'current'},
 async()=>{sends++;},async()=>{}).then(()=>process.exit(2)).catch(()=>{if(sends)process.exit(3);console.log('closed');});'''
        result=subprocess.run([shutil.which("node"),"-e",script,str(runtime)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_real_node_transport_contract(self):
        runtime=Path(web.__file__).with_name("chatgpt_web_image_runtime.js")
        script = r'''const {runImageTransport}=require(process.argv[1]);
const request={project_binding:{route:{project_id:'g-p-abc',project_url:'https://chatgpt.com/g/g-p-abc/project'}},operation:'submit',status:'UNKNOWN',opt_in:true,safety_history:'CLEAR',model_strategy:'current',
 prompt:'exact',references:[{path:'fixture.png'}],oracle_home:'fixture-home',wait_ms:20};
let calls=[],events=[];
runImageTransport(request,async(name,args)=>{calls.push({name,args});
 if(name==='consult')return {sessionId:'s'};
 if(events[0]?.session_id!=='s')throw Error('receipt not before wait');
 return {sessionId:'s',status:'completed',images:[{kind:'image',path:'fixture-home/generated/result.png'}]};},x=>events.push(x))
.then(async()=>{
 const c=calls[0]; if(c.name!=='consult'||c.args.engine!=='browser'||c.args.browserAttachments!=='always'||
 c.args.files[0]!=='fixture.png'||c.args.prompt!=='exact'||c.args.waitForCompletion!==false||
 c.args.browserArchive!=='never'||calls[1].args.id!=='s')throw Error('contract');
 calls=[]; await runImageTransport({...request,operation:'inspect',session_id:'s'},async(name,args)=>{
 if(name!=='wait'||args.id!=='s')throw Error('new send on reconnect'); return {sessionId:'s',status:'running'};},()=>{});
 console.log('autonomous exact attachments receipt reconnect contract');
}).catch(e=>{console.error(e);process.exit(1)});
'''
        result=subprocess.run([shutil.which("node"),"-e",script,str(runtime)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
    def test_production_guard_and_artifact_tampering(self):
        prompt = "Production exact text"
        refs = [web.image_binding(self.ref)]
        ph = web.digest(prompt.encode())
        plan_path = self.folder / "plan.json"
        guard_path = self.folder / "guard.json"
        proof_path = self.folder / "proof.json"
        plan = {"execution_call": {"request_id": "r1", "stage_id": "SINGLE_PASS", "prompt": {
            "text": prompt, "text_sha256": ph}, "slots": refs}}
        guard = {"request_id": "r1", "events": [{"event": "READY_FOR_EXECUTION"}]}
        proof = {"validated": True, "request_id": "r1", "prompt_sha256": ph,
                 "references": refs, "guard_path": str(guard_path),
                 "reference_plan_path": str(plan_path)}
        proof["attempt_id"] = "attempt1"
        for path, value in ((plan_path, plan), (guard_path, guard), (proof_path, proof)):
            path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaises(ValueError):
            web.prepare(self.journal, prompt, [self.ref], "current", output=self.output, profile_dir=self.folder, oracle_root=str(self.folder / "oracle-install"), project_config=self.project, opt_in=True,
                        safety_history="CLEAR", evidence_path=proof_path)
        guard["events"].append({"event": "EXECUTION_STARTED"})
        binding = {"path": str(plan_path), "sha256": web.digest(plan_path.read_bytes()),
                   "references": refs,
                   "prompt_sha256": ph, "stage": "SINGLE_PASS",
                   "execution_call_sha256": web.canonical_hash(plan["execution_call"])}
        snapshot_path = self.folder / "executed.json"
        snapshot_path.write_bytes(plan_path.read_bytes())
        attempt = {"attempt_id": "attempt1", "task_revision": 0, "stage": "SINGLE_PASS",
                   "status": "ACTIVE", "reference_binding": binding, "execution_snapshot": {
                       "request_id": "r1", "attempt_id": "attempt1", "task_revision": 0,
                       "execution_stage": "SINGLE_PASS", "guard_stage": "SINGLE_PASS",
                       "execution_call": plan["execution_call"],
                       "execution_call_sha256": binding["execution_call_sha256"],
                       "reference_binding": binding, "reference_plan_snapshot_path": str(snapshot_path),
                       "reference_plan_snapshot_sha256": web.digest(snapshot_path.read_bytes())}}
        guard.update(status="ACTIVE", phase="EXECUTION", task_revision=0,
                     active_attempt=attempt, attempts=[attempt])
        guard_path.write_text(json.dumps(guard), encoding="utf-8")
        web.prepare(self.journal, prompt, [self.ref], "current", output=self.output, profile_dir=self.folder, oracle_root=str(self.folder / "oracle-install"), project_config=self.project, opt_in=True,
                    safety_history="CLEAR", evidence_path=proof_path)
        guard["phase"] = "REVIEW"; guard["active_attempt"] = None
        guard_path.write_text(json.dumps(guard), encoding="utf-8")
        web.load(self.journal)
        with self.assertRaises(ValueError): web.execute(self.journal, transport=self.transport)
        snapshot_path.write_text("{}", encoding="utf-8")
        with self.assertRaises(ValueError): web.load(self.journal)

    def native_fixture(self):
        from tools import task_execution_guard as guard
        from tests.test_task_execution_guard import _native_default_call
        environment = patch.dict("os.environ", {"CODEX_THREAD_ID": "native-web-unit-test"})
        environment.start(); self.addCleanup(environment.stop)
        registry = patch.object(guard, "_chat_request_root", return_value=self.folder / "registry")
        registry.start(); self.addCleanup(registry.stop)
        call = _native_default_call(self.folder, "A watercolor fox beneath a pine tree.")
        guard_path = self.folder / "EXECUTION_GUARD.json"
        guard.create_guard(guard_path, request_id="request-1", goal="Create one fox image.",
            deliverable="One image.", task_kind="IMAGE_GENERATION_NATIVE_DEFAULT")
        guard.checkpoint(guard_path, event="READY_FOR_EXECUTION", summary="Exact call ready.", execution_call=call)
        state = guard.checkpoint(guard_path, event="EXECUTION_STARTED", summary="Generate requested image.",
            execution_call=call, output_contract="REQUESTED_DELIVERABLE")
        call_path = self.folder / "execution_call.json"; web.save(call_path, call)
        proof_path = self.folder / "proof.json"
        proof = {"validated": True, "request_id": "request-1", "references": [],
            "prompt_sha256": call["prompt"]["text_sha256"], "guard_path": str(guard_path),
            "execution_call_path": str(call_path), "attempt_id": state["active_attempt"]["attempt_id"]}
        web.save(proof_path, proof)
        return call, guard_path, call_path, proof_path

    def prepare_native(self, call, proof_path):
        return web.prepare(self.journal, call["prompt"]["text"], [], "current", opt_in=True,
            safety_history="CLEAR", evidence_path=proof_path, output=self.output,
            profile_dir=self.folder, oracle_root=str(self.folder / "oracle-install"),
            project_config=self.project)

    def test_actual_native_guard_execution_and_reconciliation(self):
        call, guard_path, call_path, proof_path = self.native_fixture()
        state = self.prepare_native(call, proof_path)
        self.assertEqual(state["scenario_evidence"]["branch"], "NATIVE_DEFAULT")
        self.assertEqual(web.load(self.journal, for_submission=True)["references"], [])
        guard = json.loads(guard_path.read_bytes()); guard["active_attempt"] = None; guard["phase"] = "REVIEW"
        web.save(guard_path, guard)
        self.assertEqual(web.load(self.journal)["prompt"], call["prompt"]["text"])
        with self.assertRaises(ValueError): web.load(self.journal, for_submission=True)
        guard["attempts"][0]["execution_snapshot"]["execution_call"]["prompt"]["text"] = "Changed"
        web.save(guard_path, guard)
        with self.assertRaises(ValueError): web.load(self.journal)

    def test_native_call_file_tampering_blocks_submission(self):
        call, guard_path, call_path, proof_path = self.native_fixture()
        self.prepare_native(call, proof_path)
        call["user_selections"]["style"]["user_quote"] = "Changed"
        web.save(call_path, call)
        with self.assertRaises(ValueError): web.load(self.journal, for_submission=True)
        web.load(self.journal)  # Reconnect uses the original immutable guard call.

    def test_native_forged_proof_without_real_attempt_is_rejected(self):
        call, guard_path, call_path, proof_path = self.native_fixture()
        guard = json.loads(guard_path.read_bytes()); guard["attempts"] = []
        web.save(guard_path, guard)
        with self.assertRaises(ValueError): self.prepare_native(call, proof_path)

    def test_native_invalid_selection_rejected_even_with_matching_hashes(self):
        call, guard_path, call_path, proof_path = self.native_fixture()
        call["user_selections"]["character"]["choice"] = "CHAR_001"
        web.save(call_path, call)
        with self.assertRaises(ValueError): self.prepare_native(call, proof_path)


    def test_windows_default_prepare_is_headless(self):
        with patch.object(web.os, "name", "nt"):
            state = web.prepare(self.journal, web.NEUTRAL_PROMPT, [self.ref], "current",
                opt_in=True, safety_history="CLEAR", neutral_test=True,
                output=self.output, profile_dir=self.folder, oracle_root=str(self.folder / "oracle-install"),
                project_config=self.project)
        self.assertFalse(state["windows_isolated_browser"])
        self.assertEqual(state["browser_mode"], "headless")
        self.assertTrue(state["oracle_config"]["browser"]["headless"])

    def test_windows_isolated_mode_pin_and_tamper(self):
        with patch.object(web.os, "name", "nt"):
            state = web.prepare(self.journal, web.NEUTRAL_PROMPT, [self.ref], "current",
                opt_in=True, safety_history="CLEAR", neutral_test=True,
                output=self.output, profile_dir=self.folder, oracle_root=str(self.folder / "oracle-install"),
                project_config=self.project,
                windows_isolated_browser=True)
        self.assertFalse(state["oracle_config"]["browser"]["headless"])
        self.assertEqual(state["browser_mode"], "windows-isolated")
        state["windows_isolated_browser"] = False; web.save(self.journal, state)
        with self.assertRaisesRegex(ValueError, "browser mode"):
            web.load(self.journal)

    def test_windows_isolated_rejected_on_other_os(self):
        with patch.object(web.os, "name", "posix"):
            with self.assertRaisesRegex(ValueError, "requires Windows"):
                web.prepare.__wrapped__(self.journal, "image", [], "current", windows_isolated_browser=True)
        self.assertFalse(self.journal.exists())

    def test_legacy_headless_journal_reconciles(self):
        state = self.prepare()
        state.pop("windows_isolated_browser"); state.pop("browser_mode")
        web.save(self.journal, state)
        self.assertTrue(web.load(self.journal)["oracle_config"]["browser"]["headless"])

    def test_staged_ui_only_collector_no_cookie_or_fetch_fallback(self):
        node = shutil.which("node")
        if not node: self.skipTest("Node unavailable")
        source = (Path(__file__).resolve().parents[1] / "runtime/chatgpt-web-image/oracle-patches/dist/src/browser/chatgptImages.js").read_text()
        collector = source[source.index("export async function collectGeneratedImageArtifacts"):].replace("export async function", "async function", 1)
        program = """import path from 'node:path'; import assert from 'node:assert/strict';
let buttonCalls=0, networkCalls=0, fail=false;
const readAssistantGeneratedImagesWithFallback=async()=>[{url:'dom-generated-image'}];
const saveGeneratedImageButtonArtifacts=async()=>{buttonCalls++;return fail?[]:[{path:'/result.png',url:'browser-download'}]};
const formatButtonImageArtifacts=(images)=>({savedImages:images});
const saveChatGptGeneratedImages=async()=>{networkCalls++;return {saved:true,savedImages:[{path:'/result.png'}],imageCount:1}};
""" + collector + """
process.env.ORACLE_IMAGE_BROWSER_DOWNLOAD_ONLY='1';
const params={Runtime:{},generateImagePath:'/result.png',answerText:'ready'};
assert.equal((await collectGeneratedImageArtifacts(params)).savedImages[0].path,'/result.png');
assert.equal(buttonCalls,1);assert.equal(networkCalls,0);
fail=true;await assert.rejects(collectGeneratedImageArtifacts(params),/no cookie or fetch fallback/);
assert.equal(networkCalls,0);
delete process.env.ORACLE_IMAGE_BROWSER_DOWNLOAD_ONLY;
await collectGeneratedImageArtifacts(params);assert.equal(networkCalls,1);assert.equal(buttonCalls,2);
"""
        script=self.folder / "offline-check.mjs";script.write_text(program.replace("__RECOVERY_URL__",RECOVERY_URL),encoding="utf-8")
        result=subprocess.run([node,str(script)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_detached_worker_inherits_ui_only_environment(self):
        node=shutil.which("node")
        if not node: self.skipTest("Node unavailable")
        oracle_root=self.oracle_fixture_root()
        module=(Path(oracle_root)/"dist/src/cli/detachedSession.js").as_uri()
        program=f"import {{buildDetachedSessionSpawnSpec}} from {json.dumps(module)}; import assert from 'node:assert/strict'; const spec=buildDetachedSessionSpawnSpec({{sessionId:'test',env:{{ORACLE_IMAGE_BROWSER_DOWNLOAD_ONLY:'1'}}}}); assert.equal(spec.options.env.ORACLE_IMAGE_BROWSER_DOWNLOAD_ONLY,'1'); assert.equal(spec.options.windowsHide,true);"
        script=self.folder / "offline-check.mjs";script.write_text(program.replace("__RECOVERY_URL__",RECOVERY_URL),encoding="utf-8")
        result=subprocess.run([node,str(script)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_image_only_pipeline_suppresses_adjacent_file_capture(self):
        node=shutil.which("node")
        if not node: self.skipTest("Node unavailable")
        root=Path(__file__).resolve().parents[1] / "runtime/chatgpt-web-image/oracle-patches/dist/src/browser"
        images=(root/"chatgptImages.js").read_text()
        images=images[images.index("export async function collectGeneratedImageArtifacts"):].replace("export async function", "async function",1)
        files=(root/"chatgptFiles.js").read_text()
        files=files[files.index("export async function collectChatGptFileArtifacts"):files.index("export const __test__")].replace("export async function","async function",1)
        program="""import path from 'node:path'; import assert from 'node:assert/strict';
let nativeDownloads=0,cookieCalls=0,fetchCalls=0,fileReads=0;
const candidate={url:'https://chatgpt.com/backend-api/files/example/download',label:'file'};
const readAssistantGeneratedImagesWithFallback=async()=>[{url:'dom-generated-image'}];
const saveGeneratedImageButtonArtifacts=async()=>{nativeDownloads++;return [{path:'/result.png',url:'browser-download'}]};
const formatButtonImageArtifacts=images=>({savedImages:images});
const readAssistantDownloadableFiles=async()=>{fileReads++;return [candidate]};
const readTextDownloadableFiles=()=>[candidate];const dedupeFiles=v=>v;
const normalizeChatGptDownloadUrl=v=>v;const downloadUrlFromSandboxUrl=v=>v;
const describeDownloadableCandidate=()=>'';
const Network={getCookies:async()=>{cookieCalls++;return {cookies:[]}}};
const fetch=async()=>{fetchCalls++;return {}};
const saveChatGptDownloadableFiles=async(params)=>{await params.Network.getCookies();await fetch(candidate.url);return {failedFiles:[],savedFiles:[{path:'/file.txt'}],saved:true}};
"""+images+files+"""
const params={Runtime:{},Network,generateImagePath:'/result.png',answerText:'file link'};
process.env.ORACLE_IMAGE_BROWSER_DOWNLOAD_ONLY='1';
await collectGeneratedImageArtifacts(params);
assert.deepEqual(await collectChatGptFileArtifacts(params),{files:[],savedFiles:[],fileCount:0});
assert.equal(nativeDownloads,1);assert.equal(fileReads,0);assert.equal(cookieCalls,0);assert.equal(fetchCalls,0);
delete process.env.ORACLE_IMAGE_BROWSER_DOWNLOAD_ONLY;
assert.equal((await collectChatGptFileArtifacts(params)).savedFiles.length,1);
assert.equal(fileReads,1);assert.equal(cookieCalls,1);assert.equal(fetchCalls,1);
"""
        script=self.folder / "offline-check.mjs";script.write_text(program.replace("__RECOVERY_URL__",RECOVERY_URL),encoding="utf-8")
        result=subprocess.run([node,str(script)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_recovery_original_prompt_no_send_and_cleanup(self):
        node=shutil.which("node")
        if not node: self.skipTest("Node unavailable")
        module=(Path(__file__).resolve().parents[1]/"tools/chatgpt_web_image_recovery.mjs").as_uri()
        program=r"""import assert from 'node:assert/strict';import path from 'node:path';import {createHash} from 'node:crypto';
import {validateRecovery,matchObservedPrompt,recoverOriginal,configureNativeUserRecovery} from MODULE;
const prompt='Exact full original prompt\n';const hash=v=>createHash('sha256').update(v).digest('hex');
const request={operation:'recover',opt_in:true,safety_history:'CLEAR',windows_isolated_browser:true,browser_mode:'windows-isolated',session_id:'original',prompt,prompt_sha256:hash(prompt),oracle_home:HOME,profile_dir:'profile',project_binding:{route:{project_id:'g-p-abc'}},oracle_config:{browser:{}}};
const url='https://chatgpt.com/g/g-p-abc-art/c/12345678-1234-1234-1234-123456789abc';
const meta={id:'original',mode:'browser',options:{prompt,generateImage:path.join(HOME,'generated','result.png')},browser:{config:{manualLoginProfileDir:'profile',headless:false,cookieSync:false,manualLoginCookieSync:false,captureProviderNative:false},runtime:{userDataDir:'profile',tabUrl:url,conversationId:'12345678-1234-1234-1234-123456789abc',submittedPromptHash:null}}};
const observed={url,conversationId:meta.browser.runtime.conversationId,lastUserMessageId:'user-original',lastUserContentText:prompt.trim(),lastUserTurnIndex:0,assistantFollowsLatestUser:true,stopExists:false};
assert.equal(validateRecovery(request,meta),url);
const parseEnv={};assert.throws(()=>configureNativeUserRecovery({...request,opt_in:false},meta,parseEnv),/authorized/);assert.deepEqual(parseEnv,{});configureNativeUserRecovery(request,meta,parseEnv);assert.deepEqual(parseEnv,{ORACLE_PARSE_NATIVE_IMAGE_USER:'1'});assert.throws(()=>configureNativeUserRecovery(request,meta,{ORACLE_REQUIRE_NATIVE_IMAGE_TOOL:'1'}),/activation/);
const projectUrl='https://chatgpt.com/g/g-p-abc/project';const projectRequest={...request,operation:'image-tool-inspect',activate_native_image_tool:true,pinned_project_editor:true,project_binding:{route:{project_id:'g-p-abc',project_url:projectUrl}},oracle_config:{browser:{chatgptUrl:projectUrl,url:projectUrl}}};
const projectMeta={...meta,browser:{...meta.browser,runtime:{...meta.browser.runtime,tabUrl:projectUrl,conversationId:null,promptSubmitted:false}}};
assert.equal(validateRecovery(projectRequest,projectMeta),projectUrl);const projectEnv={};configureNativeUserRecovery(projectRequest,projectMeta,projectEnv);assert.deepEqual(projectEnv,{});
assert.throws(()=>validateRecovery(projectRequest,{...projectMeta,browser:{...projectMeta.browser,runtime:{...projectMeta.browser.runtime,tabUrl:'https://chatgpt.com/'}}}),/Pinned/);
assert.throws(()=>validateRecovery({...projectRequest,activate_native_image_tool:false},projectMeta),/Pinned/);

assert.throws(()=>matchObservedPrompt(request,meta,{...observed,lastUserContentText:'Exact full'}),/full prompt/);
assert.throws(()=>matchObservedPrompt(request,meta,{...observed,conversationId:'other'}),/full prompt/);
assert.throws(()=>validateRecovery(request,{...meta,id:'other'}),/session/);
        let killed=0,closed=0,released=0,collected=0,fail=false,reuse=false,last=true,clientFail=false,claimId=null;const recoveryOrder=[];
        const actions={turnRootsExpression:()=> '[]',turnSemantics:()=> '',config:v=>v,acquireLease:async()=>({update:async()=>{},release:async({onRelease})=>{released++;await onRelease({isLastLease:last})}}),acquireChrome:async()=>({reusedChrome:reuse,chrome:{pid:123,port:12345,kill:async()=>{killed++}}}),connect:async()=>({targetId:'owned',client:{Page:{enable:async()=>{}},Runtime:{enable:async()=>{}},close:async()=>{if(clientFail)throw new Error('close');}}}),closeTarget:async()=>{closed++},navigate:async()=>{},guard:async()=>{},auth:async()=>{},harvest:async()=>observed,delay:async()=>{},claimTarget:async(runtime,id)=>{assert(runtime);claimId=id;recoveryOrder.push('claim')},collect:async params=>{collected++;recoveryOrder.push('collect');assert.equal(params.expectedPrompt,request.prompt);assert.equal(params.generateImagePath,meta.options.generateImage);assert.equal(params.minTurnIndex,observed.lastUserTurnIndex);assert.equal(params.nativeRuntime.tabUrl,url);assert.equal(params.nativeRuntime.ownedRecoveryTarget.claimId,claimId);if(fail)throw new Error('UI failed');return {savedImages:[{path:meta.options.generateImage}]}}};
let result=await recoverOriginal(request,meta,actions);assert.equal(result.sendOperations,0);assert.equal(result.sessionId,'original');assert.equal(killed,1);assert.equal(closed,1);assert.equal(released,1);assert.equal(meta.browser.runtime.submittedPromptHash,null);
        assert.deepEqual(recoveryOrder,['claim','collect']);
last=false;await recoverOriginal(request,meta,actions);assert.equal(killed,1);assert.equal(closed,2);
reuse=true;await assert.rejects(recoverOriginal(request,meta,actions),/reuse refused/);assert.equal(killed,1);assert.equal(collected,2);reuse=false;last=true;
fail=true;await assert.rejects(recoverOriginal(request,meta,actions),/UI failed/);assert.equal(killed,2);assert.equal(released,4);
fail=false;clientFail=true;await assert.rejects(recoverOriginal(request,meta,actions),/cleanup failed/);assert.equal(killed,3);assert.equal(released,5);
clientFail=false;const statusRequest={...request,operation:'recovery-status'};let redirected=false,structuralReads=0,nativeReads=0;const delays=[];
actions.delay=async value=>{delays.push(value)};
actions.connect=async()=>({targetId:'owned',client:{Page:{enable:async()=>{}},Runtime:{enable:async()=>{},evaluate:async({expression})=>{
 if(expression==='location.href')return {result:{value:redirected?'https://chatgpt.com/':url}};

 if(expression.includes('const users=roots.filter')){nativeReads++;return {result:{value:{userCount:1,matchCount:0,leaves:[]}}};}
 if(expression.includes('const mains=')){
  const vm=await import('node:vm');let foreignReads=0;

  const main={tagName:'MAIN',innerText:prompt.trim()+'\nAuthorized answer',children:[],getAttribute:()=>null,hasAttribute:()=>false,contains:node=>node===main||node.parentElement===main,querySelectorAll:selector=>selector==='*'?main.children:[]};
  Object.defineProperty(main,'parentElement',{get(){throw new Error('outside main ancestor read')}});
  const leaf={tagName:'DIV',textContent:prompt.trim(),children:[],parentElement:main,getAttribute:attribute=>attribute==='id'?'PRIVATE_ID':null,hasAttribute:attribute=>attribute==='id'};
  const heading={tagName:'H5',textContent:'You said:',children:[],parentElement:main,getAttribute:()=>null,hasAttribute:()=>false};main.children=[leaf,heading];
  const document={querySelectorAll:selector=>{assert.equal(selector,'main');return [main]}};
  Object.defineProperty(document,'body',{get(){foreignReads++;throw new Error('body/sidebar access forbidden')}});
  const value=vm.runInNewContext(expression,{location:{href:url},document});
  assert.equal(foreignReads,0);assert.equal(value.fullPromptPresent,true);assert.equal(value.text,main.innerText);assert.equal(value.messageTopology.exactPromptLeaves.length,1);assert.deepEqual(Array.from(value.messageTopology.exactPromptLeaves[0].ancestors,node=>node.tag),['div','main']);assert.equal(JSON.stringify(value.messageTopology).includes('PRIVATE_ID'),false);
  assert.equal(JSON.stringify(value).includes('href'),false);return {result:{value}};
 }
 structuralReads++;return {result:{value:{url,readyState:'complete',bodyPresent:true,bodyTextLength:100,bodyElements:10}}};
}},close:async()=>{}}});
const beforeCollect=collected;const status=await recoverOriginal(statusRequest,meta,actions);
assert.equal(status.status,'diagnostic');assert.equal(status.snapshots.length,3);assert.equal(status.imageCollectOperations,0);assert.equal(status.sendOperations,0);assert.equal(collected,beforeCollect);assert.equal(structuralReads,3);assert.equal(nativeReads,3);assert.deepEqual(delays,[5000,10000]);assert.equal(JSON.stringify(status).includes('lastUserContentText'),false);
redirected=true;await assert.rejects(recoverOriginal(statusRequest,meta,actions),/redirected/);assert.equal(structuralReads,3);assert.equal(collected,beforeCollect);
redirected=false;reuse=true;await assert.rejects(recoverOriginal(statusRequest,meta,actions),/reuse refused/);assert.equal(structuralReads,3);
reuse=false;let domReads=0;const vm=await import('node:vm');
actions.connect=async()=>({targetId:'owned',client:{Page:{enable:async()=>{}},Runtime:{enable:async()=>{},evaluate:async({expression})=>{
 if(expression==='location.href')return {result:{value:url}};
 const value=vm.runInNewContext(expression,{location:{href:'https://chatgpt.com/'},document:new Proxy({}, {get(){domReads++;throw new Error('foreign DOM read')}})});
 return {result:{value}};
}},close:async()=>{}}});
await assert.rejects(recoverOriginal(statusRequest,meta,actions),/redirected during observation/);assert.equal(domReads,0);assert.equal(collected,beforeCollect);
fail=true;await assert.rejects(recoverOriginal(request,meta,actions),/UI failed/);assert.equal(domReads,0);



""".replace('MODULE',json.dumps(module)).replace('HOME',json.dumps(self.folder.as_posix()))
        script=self.folder / "offline-check.mjs";script.write_text(program.replace("__RECOVERY_URL__",RECOVERY_URL),encoding="utf-8")
        result=subprocess.run([node,str(script)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_recovery_collector_pins_follow_validated_runtime_manifest_and_keep_legacy(self):
        node=shutil.which("node")
        if not node: self.skipTest("Node unavailable")
        module=(Path(__file__).resolve().parents[1]/"tools/chatgpt_web_image_recovery.mjs").as_uri()
        source=(Path(__file__).resolve().parents[1]/"runtime/chatgpt-web-image/oracle-patches/dist/src/browser").as_posix()
        program=r"""import assert from 'node:assert/strict';import fs from 'node:fs/promises';import path from 'node:path';import {createHash} from 'node:crypto';
import {resolveRecoveryCollectorPins} from MODULE;const hash=value=>createHash('sha256').update(value).digest('hex');
const oracle=path.join(HOME,'oracle');const base=path.join(oracle,'node_modules/@steipete/oracle/dist/src/browser');await fs.mkdir(base,{recursive:true});
const files=[];for(const name of ['chatgptImages.js','chatgptFiles.js']){const bytes=await fs.readFile(path.join(SOURCE,name));const target=path.join(base,name);await fs.writeFile(target,bytes);files.push({path:target,sha256:hash(bytes)});}
const manifestPath=path.join(HOME,'installation-manifest.json');const manifestBytes=Buffer.from(JSON.stringify({files}));await fs.writeFile(manifestPath,manifestBytes);
const request={oracle_root:oracle,runtime_binding:{installation:oracle,manifest_path:manifestPath,manifest_sha256:hash(manifestBytes)}};
const pins=await resolveRecoveryCollectorPins(request,base);assert.equal(pins['chatgptImages.js'],files[0].sha256);assert.equal(pins['chatgptFiles.js'],files[1].sha256);
assert.equal(pins['chatgptImages.js'],hash(await fs.readFile(path.join(SOURCE,'chatgptImages.js'))));
await assert.rejects(resolveRecoveryCollectorPins({...request,runtime_binding:{...request.runtime_binding,manifest_sha256:'0'.repeat(64)}},base),/manifest changed/);
await fs.appendFile(path.join(base,'chatgptImages.js'),'drift');await assert.rejects(resolveRecoveryCollectorPins(request,base),/manifest changed|source changed/);
const legacy=await resolveRecoveryCollectorPins({oracle_root:oracle},base);assert.equal(legacy['chatgptImages.js'],'ceae7c37a063ba06a8a59527053b1438eaa54ae69605592792392a9ebf7d3085');
console.log('current patched collector pin, drift rejection, and legacy pin fixtures passed');
""".replace('MODULE',json.dumps(module)).replace('HOME',json.dumps(self.folder.as_posix())).replace('SOURCE',json.dumps(source))
        script=self.folder/"recovery-pins.mjs";script.write_text(program.replace("__RECOVERY_URL__",RECOVERY_URL),encoding="utf-8")
        result=subprocess.run([node,str(script)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def recovery_fixture(self):
        state=self.prepare();state.update(windows_isolated_browser=True,browser_mode="windows-isolated",session_id="original",status="PROVIDER_FAILED")
        web.pin_runtime_config(state);web.save(self.journal,state)
        home=Path(state["oracle_home"]);(home/"sessions/original").mkdir(parents=True)
        web.save(home/"receipt.json",{"type":"receipt","session_id":"original","prompt_sha256":state["prompt_sha256"]})
        web.save(home/"sessions/original/meta.json",{"id":"original","options":{"prompt":state["prompt"]},"browser":{"runtime":{"promptSubmitted":True,"conversationId":"original-conversation","submittedPromptHash":None}}})
        (home/"last-result.json").write_text('original-error',encoding="utf-8")
        return state

    def test_canonical_native_expression_root_has_no_free_main(self):
        import subprocess, tempfile
        oracle_root=self.oracle_fixture_root()
        project=Path(__file__).resolve().parents[1]
        turns=(Path(oracle_root)/"dist/src/browser/conversationTurns.js").as_uri()
        native=(project/"runtime/chatgpt-web-image/oracle-patches/dist/src/browser/actions/native-image-production.mjs").as_uri()
        program = r"""
import vm from 'node:vm';
import assert from 'node:assert/strict';
const {buildConversationTurnListExpression}=await import(process.env.ORACLE_TURNS);
const {buildNativeImageExpression}=await import(process.env.ORACLE_NATIVE);
const actions=root=>({plusResolver:()=> 'null',navigationValidation:()=> 'true',turnRootsExpression:()=>buildConversationTurnListExpression(root)});
assert.throws(()=>vm.runInNewContext(buildNativeImageExpression('capture','test','owned',actions('main')),{location:{href:'owned'}}),/main is not defined/);
const corrected=buildNativeImageExpression('capture','test','owned',actions("document.querySelector('main')"));
// Redirect guard in the real turn expression returns [] before any DOM access;
// the corrected argument composes without an undeclared local variable.
const root={querySelectorAll:()=>[]};
const context={window:{},location:{href:'redirected'},document:{querySelector:()=>root,querySelectorAll:()=>[]}};
const result=vm.runInNewContext(corrected,context);
assert.equal(result.ok,false);
assert.equal(result.reason,'navigation-unverified');
"""
        env=os.environ.copy();env["ORACLE_TURNS"]=turns;env["ORACLE_NATIVE"]=native
        with tempfile.TemporaryDirectory(dir=str(self.folder)) as folder:
            script=Path(folder)/"native-composition.mjs"
            script.write_text(program.replace("__RECOVERY_URL__",RECOVERY_URL),encoding="utf-8")
            completed=subprocess.run(["node",str(script)],capture_output=True,text=True,env=env)
            self.assertEqual(completed.returncode,0,completed.stderr)

    def test_native_probe_forwards_canonical_input_selectors(self):
        source=Path("tools/chatgpt_web_image_recovery.mjs").read_text(encoding="utf-8")
        self.assertIn("'constants.js','targetClaim.js'].map(load)",source)
        self.assertIn("inputSelectors:constants.INPUT_SELECTORS",source)
        oracle_root=self.oracle_fixture_root()
        constants=(Path(oracle_root)/"dist/src/browser/constants.js").as_uri()
        program=f"import {{INPUT_SELECTORS}} from {json.dumps(constants)}; import assert from 'node:assert/strict'; assert(Array.isArray(INPUT_SELECTORS)); assert(INPUT_SELECTORS.some(s=>s.includes('textbox')));"
        script=self.folder/"canonical-inputs.mjs";script.write_text(program.replace("__RECOVERY_URL__",RECOVERY_URL),encoding="utf-8")
        completed=subprocess.run(["node",str(script)],capture_output=True,text=True)
        self.assertEqual(completed.returncode,0,completed.stderr)

    def test_session_broker_route_opt_in_and_pending_pin(self):
        source=Path("tools/chatgpt_web_image_backend.mjs").read_text(encoding="utf-8")
        function=source[source.index("export function desktopBrokerConfiguration"):source.index("export function nativeImageEnvironment")].replace("export function","function")
        program="import vm from 'node:vm'; import assert from 'node:assert/strict'; const context={}; vm.createContext(context); vm.runInContext("+json.dumps(function)+",context); const old=context.desktopBrokerConfiguration(false), opt=context.desktopBrokerConfiguration(true); assert(old.relativePath.endsWith('/DesktopBroker.exe')); assert.equal(old.allowDisconnectedSession,false); assert.match(old.sha256,/^[a-f0-9]{64}$/); assert(opt.relativePath.endsWith('/session-broker/DesktopBrokerSession.exe')); assert.equal(opt.allowDisconnectedSession,true); assert.equal(opt.sha256,'37df09b866684140b9182003e4bdfaa2e21b74717f9cb51f3bbc43db7eb1a0e1');"
        script=self.folder/"broker-config.mjs";script.write_text(program.replace("__RECOVERY_URL__",RECOVERY_URL),encoding="utf-8")
        completed=subprocess.run(["node",str(script)],capture_output=True,text=True)
        self.assertEqual(completed.returncode,0,completed.stderr)
        self.assertLess(source.index("Session desktop broker fingerprint not approved"),source.index("const brokerHash") if "const brokerHash" in source else source.index("const child = spawn"))
        self.assertIn("allowDisconnectedSession:true",source)

    def test_vendored_oracle_launcher_is_optional_and_defaults_unchanged(self):
        source=Path("runtime/chatgpt-web-image/oracle-client.mjs").read_text(encoding="utf-8")
        self.assertIn("const launcher = context?.launcher;",source)
        self.assertIn("command: launcher?.command ?? process.execPath",source)
        self.assertIn('args: launcher ? [...launcher.args] : [resolveOracleEntrypoint(installationRoot, "oracle-mcp.js")]',source)
        self.assertNotRegex(source,r"[A-Za-z]:[\/]")

    def test_isolated_desktop_receipt_failure_blocks_consult(self):
        source=Path("tools/chatgpt_web_image_backend.mjs").read_text(encoding="utf-8")
        function=source[source.index("export function validateDesktopLaunchReceipt"):source.index("async function verifyIsolatedDesktop")].replace("export function","function")
        program="import vm from 'node:vm';import assert from 'node:assert/strict';const c={};vm.createContext(c);vm.runInContext("+json.dumps(function)+",c);const d='OracleIsolated_'+'a'.repeat(32);const r={workerPid:7,state:'RUNNING',desktop:d,inputReadable:true,inputBefore:'Default',threadDesktop:'Default',allowDisconnectedSession:false,prelaunch:{inputReadable:true,inputName:'Default'}};assert.equal(c.validateDesktopLaunchReceipt(r),true);for(const [key,value] of Object.entries({workerPid:0,state:'FAILED',desktop:'Default',inputReadable:false,inputBefore:'Other',threadDesktop:d,allowDisconnectedSession:true,prelaunch:null}))assert.equal(c.validateDesktopLaunchReceipt({...r,[key]:value}),false,key);"
        script=self.folder/"desktop-receipt.mjs";script.write_text(program.replace("__RECOVERY_URL__",RECOVERY_URL),encoding="utf-8")
        completed=subprocess.run(["node",str(script)],capture_output=True,text=True)
        self.assertEqual(completed.returncode,0,completed.stderr)
        self.assertLess(source.index("await verifyIsolatedDesktop(desktopReceiptPath,"),source.index("await runImageTransport(request"))

    def test_shared_advisor_submit_allows_only_receipt_gated_isolated_mode(self):
        source=Path("tools/chatgpt_web_image_backend.mjs").read_text(encoding="utf-8")
        self.assertIn("new ProjectLocalOracleMcpClient(installation,launchContext)",source)
        self.assertNotIn("Web-Advisor",source)
        self.assertIn("await client.callTool(name, args)",source)
        self.assertIn("request.browser_mode !== 'headless' && !isolatedMode",source)
        self.assertIn("config.browser?.headless !== (isolatedMode ? false : true)",source)
        self.assertIn("if (!request.runtime_binding?.manifest_path)",source)
        self.assertIn("validateDesktopLaunchReceipt(receipt)",source)
        self.assertIn("process.env.ORACLE_HOME_DIR = request.oracle_home",source)
        self.assertIn("process.env.ORACLE_BROWSER_PROFILE_DIR = request.profile_dir",source)
        self.assertIn("for (const key of envKeys)",source)
        ordinary=source[source.index("const { ProjectLocalOracleMcpClient, resolveOracleEntrypoint }"):]
        self.assertNotIn("new StdioClientTransport",ordinary)
        self.assertLess(source.index("Manifest-pinned DesktopBrokerSession changed; no consult"),source.index("await client.connect()"))
        self.assertLess(source.index("await verifyNativeImageInstallation"),source.index("new ProjectLocalOracleMcpClient"))

    def test_disconnected_receipt_strict_contract(self):
        program=r"""
import assert from 'node:assert/strict';
import {validateDesktopReceipt as check} from '__RECOVERY_URL__';
const desktop='OracleIsolated_'+'a'.repeat(32), request={windows_isolated_browser:true};
const pre={sessionId:1,inputReadable:false,inputName:null,inputError:5,sessionQuerySucceeded:true,connectState:4,sessionQueryError:null,threadDesktop:'Default'};
const good={workerPid:7,state:'RUNNING',inheritedHandleCount:3,desktop,inputBefore:null,inputReadable:false,inputError:5,threadDesktop:'Default',sessionQuerySucceeded:true,connectState:4,allowDisconnectedSession:true,sessionId:1,sessionQueryError:null,prelaunch:pre};
assert(check(good,request,7));assert(check({...good,prelaunch:{...pre,threadDesktop:desktop}},request,7));
for(const [key,value] of Object.entries({workerPid:8,state:'UNKNOWN',inheritedHandleCount:4,desktop:'Default',inputBefore:undefined,inputReadable:null,inputError:0,threadDesktop:'Other',sessionQuerySucceeded:false,connectState:0,allowDisconnectedSession:false,sessionId:0,sessionQueryError:5,prelaunch:null}))assert.equal(check({...good,[key]:value},request,7),false,key);
for(const [key,value] of Object.entries({sessionId:2,inputReadable:true,inputName:'Default',inputError:0,sessionQuerySucceeded:false,connectState:0,sessionQueryError:5,threadDesktop:'OracleIsolated_'+'b'.repeat(32)}))assert.equal(check({...good,prelaunch:{...pre,[key]:value}},request,7),false,'pre:'+key);
assert.equal(check(good,{windows_isolated_browser:false},7),false);
assert(check({workerPid:7,state:'RUNNING',inheritedHandleCount:3,desktop,inputBefore:'Default'},{},7));
"""
        script=self.folder/"desktop-receipt.mjs";script.write_text(program.replace("__RECOVERY_URL__",RECOVERY_URL),encoding="utf-8")
        result=subprocess.run(["node",str(script)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_native_capture_redirect_reads_no_dom(self):
        program=r"""
import vm from 'node:vm';import assert from 'node:assert/strict';
import {buildScreenshotProbe} from '__RECOVERY_URL__';
let reads=0;const expression=buildScreenshotProbe('owned','key',true);
assert(expression.includes('__oracleNativeImageActions'));assert(!expression.includes('__storyartPopupIdentity'));
const context={location:{href:'other'}};Object.defineProperty(context,'document',{get(){reads++;throw Error('private DOM')}});Object.defineProperty(context,'window',{get(){reads++;throw Error('private map')}});
const result=vm.runInNewContext(expression,context);assert.equal(result.allowed,false);assert.equal(reads,0);
"""
        script=self.folder/"native-capture.mjs";script.write_text(program.replace("__RECOVERY_URL__",RECOVERY_URL),encoding="utf-8")
        result=subprocess.run(["node",str(script)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_native_failed_receipt_preserves_valid_dto_and_unknown_malformed(self):
        program=r"""
import assert from 'node:assert/strict';import {nativeFailureDiagnostic as convert} from '__RECOVERY_URL__';
const dto={method:'trusted-pointer',selectionOperations:1,selectionInputAttempts:1,modeReady:true,delivery:{sawKeyDown:false,clicked:false,pointerDown:true,blocked:'pointer-predicate-changed',traces:[{event:'pointerdown',predicates:{evaluation:true,url:true,navigation:true,plusIdentity:true,ownedForm:true,blank:true,portal:true,leaf:true,target:true,hit:true,enabled:true,trusted:true,eventTarget:true,focus:true,sequence:true,notPreviouslyBlocked:true},allowed:true,transitionObserved:false}]},post:{ok:true,observedOnly:true,plusExpanded:'false',portalConnected:false,editor:[{tag:'div',placeholder:null,ariaLabel:'Ask anything',empty:true}],controls:[{type:'button',label:'Image',pressed:null,disabled:true}]}};
const got=convert({nativeImageDiagnostic:dto});assert.equal(got.selectionInputAttempts,1);assert.deepEqual(got.post,dto.post);assert.deepEqual(got.delivery,dto.delivery);assert.equal(got.modeReady,false);assert.equal(got.failedNativeDiagnostic,true);
for(const bad of [null,{...dto,selectionOperations:9},{...dto,post:{private:'SECRET'}}]){const unknown=convert({nativeImageDiagnostic:bad});assert.equal(unknown.selectionOperations,null);assert.equal(unknown.selectionInputAttempts,null);assert.equal(unknown.modeReady,false);}
"""
        script=self.folder/"native-failure.mjs";script.write_text(program.replace("__RECOVERY_URL__",RECOVERY_URL),encoding="utf-8")
        result=subprocess.run(["node",str(script)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_native_token_redirect_no_dom_reads(self):
        program="import vm from 'node:vm'; import assert from 'node:assert/strict'; import {buildNativeTokenProbe} from '__RECOVERY_URL__'; let reads=0;const ctx={location:{href:'other'}};Object.defineProperty(ctx,'document',{get(){reads++;throw Error('private DOM')}});const result=vm.runInNewContext(buildNativeTokenProbe('owned',['[role=textbox]']),ctx);assert.equal(result.mismatch,true);assert.equal(reads,0);const expression=buildNativeTokenProbe('owned',['[role=textbox]']);for(const field of ['parentAtomIndex','containsExactLabel','textLength','visible','childTags'])assert(expression.includes(field));"
        script=self.folder/"native-token.mjs";script.write_text(program.replace("__RECOVERY_URL__",RECOVERY_URL),encoding="utf-8")
        result=subprocess.run(["node",str(script)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_production_native_env_and_fingerprint_gate(self):
        source=Path("tools/chatgpt_web_image_backend.mjs").read_text(encoding="utf-8")
        fragment=source[source.index("export function nativeImageEnvironment"):source.index("let raw =")].replace("export function","function").replace("export const","const").replace("export async function","async function")
        program="import vm from 'node:vm';import assert from 'node:assert/strict';import fs from 'node:fs/promises';import path from 'node:path';import {createHash} from 'node:crypto';const ctx={fs,path,createHash};vm.createContext(ctx);vm.runInContext("+json.dumps(fragment)+",ctx);assert.equal(Object.keys(ctx.nativeImageEnvironment({operation:'status'})).length,0);assert.throws(()=>ctx.nativeImageEnvironment({operation:'submit'}));const env=ctx.nativeImageEnvironment({operation:'submit',project_binding:{route:{project_id:'g-p-abc',project_url:'https://chatgpt.com/g/g-p-abc/project'}}});assert.equal(env.ORACLE_REQUIRE_NATIVE_IMAGE_TOOL,'1');assert.equal(env.ORACLE_IMAGE_PROJECT_ID,'g-p-abc');await assert.rejects(ctx.verifyNativeImageInstallation('.',{'pending':'PENDING'}),/not approved/);await assert.rejects(ctx.verifyNativeImageInstallation('.',{'tools/chatgpt_web_image_backend.mjs':'a'.repeat(64)}),/changed/);const root=HOME;await fs.mkdir(root,{recursive:true});const target=path.join(root,'oracle.js');const payload=Buffer.from('pinned oracle');await fs.writeFile(target,payload);const manifest=Buffer.from(JSON.stringify({files:[{path:target,sha256:createHash('sha256').update(payload).digest('hex')}]}));const manifestPath=path.join(root,'installation-manifest.json');await fs.writeFile(manifestPath,manifest);const binding={installation:root,manifest_path:manifestPath,manifest_sha256:createHash('sha256').update(manifest).digest('hex')};await ctx.verifyRuntimeBinding(binding,root);await fs.appendFile(target,'drift');await assert.rejects(ctx.verifyRuntimeBinding(binding,root),/runtime file changed/);await assert.rejects(ctx.verifyRuntimeBinding(binding,path.join(root,'other')),/binding root changed/);".replace('HOME',json.dumps(self.folder.as_posix()))
        script=self.folder/"native-production-gate.mjs";script.write_text(program.replace("__RECOVERY_URL__",RECOVERY_URL),encoding="utf-8")
        result=subprocess.run(["node",str(script)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertLess(source.index("await verifyNativeImageInstallation"),source.index("const child = spawn"))
        self.assertLess(source.index("await verifyRuntimeBinding(request.runtime_binding,request.oracle_root)"),source.index("const child = spawn"))

    def test_production_mode_proof_diagnostic_is_read_only_and_safe(self):
        source=Path("tools/chatgpt_web_image_recovery.mjs").read_text(encoding="utf-8")
        self.assertIn("nativeModeProof=nativeModule.assertNativeImageMode",source)
        self.assertIn("proof?.promptLength===0",source)
        self.assertIn("reason:'native-mode-proof-refused'",source)
        self.assertNotIn("modeReady:true",source)

    def test_native_mode_proof_only_rejects_activation_and_unpinned(self):
        for kwargs in ({"native_mode_proof_only":True},{"native_mode_proof_only":True,"activate_native_image_tool":True,"pinned_project_editor":True}):
            with self.assertRaisesRegex(ValueError,"Proof-only"):
                web.recovery_status(self.journal,operation="image-tool-inspect",transport=lambda *_:self.fail("no launch"),**kwargs)

    def test_native_user_probe_redirect_before_canonical_or_dom(self):
        program="import vm from 'node:vm';import assert from 'node:assert/strict';import {buildNativeUserRenderProbe} from '__RECOVERY_URL__';let reads=0;const ctx={location:{href:'other'}};Object.defineProperty(ctx,'document',{get(){reads++;throw Error('private')}});const r=vm.runInNewContext(buildNativeUserRenderProbe('owned','document.body.children',''),ctx);assert.equal(r.mismatch,true);assert.equal(reads,0);"
        script=self.folder/"native-user.mjs";script.write_text(program.replace("__RECOVERY_URL__",RECOVERY_URL),encoding="utf-8")
        result=subprocess.run(["node",str(script)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_native_user_probe_owned_bounded_dto(self):
        module=(Path(__file__).resolve().parents[1]/"tools/chatgpt_web_image_recovery.mjs").as_uri()
        program=r"""import vm from 'node:vm';import assert from 'node:assert/strict';import {buildNativeUserRenderProbe} from MODULE;
let outsideReads=0;
class E {constructor(tag,text='',attrs={}){this.tagName=tag;this.textContent=text;this.attrs=attrs;this.attributes=Object.keys(attrs).map(name=>({name}));this.children=[];this.parentElement=null;}getAttribute(k){return this.attrs[k]??null;}hasAttribute(k){return k in this.attrs;}querySelectorAll(){return this.children.flatMap(c=>[c,...c.querySelectorAll()]);}contains(n){return n===this||this.children.some(c=>c.contains(n));}}
const root=new E('ARTICLE'),pill=new E('SPAN','Create image',{'class':'PRIVATE_CLASS','data-system-hint-type':'image','data-system-hint-label':'Create image','href':'https://PRIVATE/SECRET','data-inline-selection-pill':'<secret>'}),leaf=new E('SPAN','Create image');
root.children=[pill];pill.parentElement=root;pill.children=[leaf];leaf.parentElement=pill;
Object.defineProperty(root,'parentElement',{get(){outsideReads++;throw Error('outside');}});
const ctx={location:{href:'https://chatgpt.com/c/owned'},fixtureRoots:[root],oracleReadTurnRole:()=> 'user'};
const probe=()=>vm.runInNewContext(buildNativeUserRenderProbe('https://chatgpt.com/c/owned','fixtureRoots',''),ctx);
let r=probe();assert.equal(r.userCount,1);assert.equal(r.matchCount,1);assert.equal(r.leaves[0].ancestors.length,3);assert.equal(outsideReads,0);
assert.ok(r.leaves[0].ancestors[1].attributeNames.includes('href'));assert.equal(r.leaves[0].ancestors[1].nativeValues['data-system-hint-type'],'image');assert.equal(r.leaves[0].ancestors[1].nativeValues['data-system-hint-label'],'Create image');assert.equal(r.leaves[0].ancestors[1].nativeValues['data-inline-selection-pill'],'OTHER');assert.equal(JSON.stringify(r).includes('PRIVATE'),false);
const paragraph=new E('P');paragraph.nodeType=1;pill.nodeType=1;paragraph.children=[pill];paragraph.childNodes=[{nodeType:3,textContent:' '},pill,{nodeType:3,textContent:'PRIVATE_PROMPT'}];paragraph.parentElement=root;pill.parentElement=paragraph;root.children=[paragraph];pill.attrs['data-prompt-link-href']='https://chatgpt.com/images?tool=create_image&secret=PRIVATE_SECRET#PRIVATE_HASH';pill.attrs['data-prompt-link-label']='Create image';pill.attributes=Object.keys(pill.attrs).map(name=>({name}));ctx.URL=URL;
r=probe();let actual=r.leaves[0];assert.equal(actual.rootReached,true);assert.equal(actual.pillDirectParagraph,true);assert.equal(actual.pillFirstNonWhitespace,true);assert.equal(actual.paragraphMeaningfulNodeCount,2);let href=actual.ancestors[1].promptLinkHref;assert.equal(href.sameOrigin,true);assert.equal(href.path,'/images');assert.equal(href.query[0].value,'create_image');assert.equal(href.query[1].value,'OTHER');assert.equal(href.hashPresent,true);assert.equal(JSON.stringify(r).includes('PRIVATE'),false);
pill.attrs['data-prompt-link-href']='system-hint://image';let custom=probe().leaves[0].ancestors[1].promptLinkHref;assert.equal(custom.protocol,'system-hint:');assert.equal(custom.host,'image');assert.equal(custom.bareNativeMarker,'system-hint://image');pill.attrs['data-prompt-link-href']='unknown://native_token';assert.equal(probe().leaves[0].ancestors[1].promptLinkHref.bareNativeMarker,'unknown://native_token');pill.attrs['data-prompt-link-href']='unknown://PRIVATE_SECRET/path?auth=PRIVATE_TOKEN';assert.equal(probe().leaves[0].ancestors[1].promptLinkHref.bareNativeMarker,null);assert.equal(JSON.stringify(probe()).includes('PRIVATE'),false);
paragraph.childNodes.unshift({nodeType:3,textContent:'before'});assert.equal(probe().leaves[0].pillFirstNonWhitespace,false);pill.attrs['data-prompt-link-href']='create_image';assert.equal(probe().leaves[0].ancestors[1].promptLinkHref.value,'create_image');
ctx.fixtureRoots=[root,root];r=probe();assert.equal(r.ambiguous,true);assert.equal(r.leaves,undefined);assert.equal(outsideReads,0);
ctx.fixtureRoots=[];assert.equal(probe().userCount,0);
""".replace('MODULE',json.dumps(module))
        script=self.folder/"native-user-dto.mjs";script.write_text(program.replace("__RECOVERY_URL__",RECOVERY_URL),encoding="utf-8")
        result=subprocess.run(["node",str(script)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_strict_prompt_diagnostic_binding_preserves_exact_contract(self):
        module=(Path(__file__).resolve().parents[1]/"tools/chatgpt_web_image_recovery.mjs").as_uri()
        program=r"""import assert from 'node:assert/strict';import {observeStrictPromptBinding} from MODULE;
const request={prompt:'full prompt',prompt_sha256:'expected'},meta={browser:{runtime:{tabUrl:'owned',conversationId:'original'}}};const observed={url:'owned',conversationId:'original',lastUserMessageId:'u',lastUserContentText:'full prompt'};
assert.equal(observeStrictPromptBinding(request,meta,{}).available,false);let binding=observeStrictPromptBinding(request,meta,observed);assert.equal(binding.matched,true);assert.equal(binding.prompt_sha256,'expected');assert.equal(binding.userMessageId,'u');assert.equal(binding.observedContentSha256.length,64);
for(const change of [{lastUserContentText:'Create image full prompt'},{lastUserContentText:'full prompt extra'},{url:'other'},{conversationId:'other'},{lastUserMessageId:null}])assert.equal(observeStrictPromptBinding(request,meta,{...observed,...change}).matched,false);
assert.equal(observeStrictPromptBinding(request,meta,{...observed,lastUserContentText:' full prompt\r\n'}).matched,true);assert.equal(JSON.stringify(binding).includes('full prompt'),false);
""".replace('MODULE',json.dumps(module))
        script=self.folder/"strict-binding.mjs";script.write_text(program.replace("__RECOVERY_URL__",RECOVERY_URL),encoding="utf-8")
        result=subprocess.run(["node",str(script)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_strict_no_id_actual_heading_binding_and_negative_boundaries(self):
        module=(Path(__file__).resolve().parents[1]/"tools/chatgpt_web_image_recovery.mjs").as_uri()
        program=r"""import assert from 'node:assert/strict';import {matchObservedPrompt,observeStrictPromptBinding} from MODULE;
const request={prompt:'full prompt',prompt_sha256:'expected'},meta={browser:{runtime:{tabUrl:'owned',conversationId:'original'}}};const actual={url:'owned',conversationId:'original',lastUserContentText:' full prompt\r\n',turnLayout:'observed-heading',lastUserTurnIndex:0,lastAssistantTurnIndex:1,assistantFollowsLatestUser:true,assistantCount:1};
let binding=matchObservedPrompt(request,meta,actual);assert.equal(binding.kind,'observed-content');assert.equal(binding.userTurnIndex,0);assert.equal(binding.assistantTurnIndex,1);assert.equal(binding.prompt_sha256,'expected');assert.equal(binding.userMessageId,undefined);assert.equal(binding.observedContentSha256.length,64);assert.equal(binding.normalizedObservedContentSha256.length,64);assert.notEqual(binding.observedContentSha256,binding.normalizedObservedContentSha256);assert.equal(observeStrictPromptBinding(request,meta,actual).matched,true);
for(const change of [{lastUserMessageId:''},{lastUserTurnIndex:undefined},{lastUserTurnIndex:null},{lastUserTurnIndex:-1},{lastUserTurnIndex:0.5},{lastAssistantTurnIndex:undefined},{lastAssistantTurnIndex:0},{lastAssistantTurnIndex:2},{assistantFollowsLatestUser:false},{assistantCount:0},{assistantCount:undefined},{assistantCount:1.5},{turnLayout:'legacy-message'},{turnLayout:'invalid-heading'},{turnLayout:undefined},{lastUserContentText:'Create image full prompt'},{lastUserContentText:'full prompt extra'},{url:'other'},{conversationId:'other'}]){assert.throws(()=>matchObservedPrompt(request,meta,{...actual,...change}));assert.equal(observeStrictPromptBinding(request,meta,{...actual,...change}).matched,false);}
let later=matchObservedPrompt(request,meta,{...actual,lastUserTurnIndex:4,lastAssistantTurnIndex:5,assistantCount:3});assert.equal(later.userTurnIndex,4);assert.equal(later.userMessageId,undefined);
const legacy=matchObservedPrompt(request,meta,{...actual,lastUserMessageId:'actual-id',turnLayout:'legacy-message',lastUserTurnIndex:undefined,lastAssistantTurnIndex:undefined});assert.equal(legacy.userMessageId,'actual-id');assert.equal(legacy.kind,undefined);
""".replace('MODULE',json.dumps(module))
        script=self.folder/"strict-no-id.mjs";script.write_text(program.replace("__RECOVERY_URL__",RECOVERY_URL),encoding="utf-8")
        result=subprocess.run(["node",str(script)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_recovery_parse_flag_precedes_module_load_after_validation_and_pins(self):
        source=Path("tools/chatgpt_web_image_recovery.mjs").read_text(encoding="utf-8")
        main=source[source.index("async function main()") :]
        set_flag=main.index("configureNativeUserRecovery(request,meta,process.env)")
        self.assertLess(main.index("validateDesktopReceipt(receipt,request,process.pid)"),set_flag)
        self.assertLess(main.index("Original metadata changed"),set_flag)
        self.assertLess(main.index("Reviewed UI-only collector changed"),set_flag)
        self.assertLess(set_flag,main.index("await Promise.all"))
        self.assertNotIn("ORACLE_REQUIRE_NATIVE_IMAGE_TOOL='1'",source)

    def test_pure_live_status_no_dom_before_url_and_claim_and_recheck(self):
        module=(Path(__file__).resolve().parents[1]/"tools/chatgpt_web_image_live_status.mjs").as_uri()
        program=r"""import vm from 'node:vm';import assert from 'node:assert/strict';import {webcrypto} from 'node:crypto';import {buildLiveStatusProbe} from MODULE;
const runtime={tabUrl:'https://chatgpt.com/c/owned',ownedRecoveryTarget:{claimId:'original'}};let domReads=0;let claim={id:'original',retiring:false};const location={href:'other'};const ctx={location,sessionStorage:{getItem:()=>JSON.stringify(claim)},crypto:webcrypto,TextEncoder};Object.defineProperty(ctx,'document',{get(){domReads++;throw Error('foreign DOM');}});
let r=await vm.runInNewContext(buildLiveStatusProbe(runtime,'prompt','','[]'),ctx);assert.equal(r.allowed,false);assert.equal(domReads,0);location.href=runtime.tabUrl;claim.id='other';r=await vm.runInNewContext(buildLiveStatusProbe(runtime,'prompt','','[]'),ctx);assert.equal(r.allowed,false);assert.equal(domReads,0);claim.id='original';claim.retiring=true;r=await vm.runInNewContext(buildLiveStatusProbe(runtime,'prompt','','[]'),ctx);assert.equal(r.allowed,false);assert.equal(domReads,0);
claim.retiring=false;const assistant={text:'Authorized answer',querySelectorAll:s=>s==='img'?[{complete:true,naturalWidth:1024,naturalHeight:768,currentSrc:'https://estuary.invalid/PRIVATE_SECRET'}]:[]};const image={tagName:'IMG',children:[],complete:true,naturalWidth:1024,naturalHeight:1536,currentSrc:'https://estuary.invalid/PRIVATE_MEDIA_URL',getAttribute:()=>null};const button={tagName:'BUTTON',children:[],attributes:[{name:'data-testid'},{name:'aria-label'}],hasAttribute:k=>['data-testid','aria-label'].includes(k),textContent:'Download image',getAttribute:k=>k==='aria-label'?'Download image':k==='data-testid'?'generated-image':null};const heading={tagName:'H4',children:[],textContent:'ChatGPT said:',getAttribute:()=>null};const span={tagName:'SPAN',children:[],attributes:[{name:'aria-hidden'}],textContent:'Response complete',hasAttribute:k=>k==='aria-hidden',getAttribute:k=>k==='aria-hidden'?'true':null};const main={tagName:'MAIN',children:[span,heading,image,button],contains:n=>[heading,image,button].includes(n),getAttribute:()=>null,querySelectorAll:s=>s==='img'?[image]:s==='button,[role="button"]'?[button]:s==='h4,h5'?[heading]:[]};image.parentElement=button;button.children=[image];button.parentElement=heading.parentElement=span.parentElement=main;const positive={location,sessionStorage:ctx.sessionStorage,crypto:webcrypto,TextEncoder,document:{querySelectorAll:()=>[main]},fixtureTurns:[{role:'user',text:'prompt',contains:()=>false},{role:'assistant',...assistant}]};const semantics='const oracleReadTurnRole=n=>n.role;const oracleReadTurnText=n=>n.text;const oracleReadTurnContentRoot=n=>n;';const probe=()=>vm.runInNewContext(buildLiveStatusProbe(runtime,'prompt',semantics,'fixtureTurns'),positive);
r=await probe();assert.equal(r.allowed,true);assert.equal(r.exactPromptMatches,true);assert.equal(r.adjacentAssistant,true);assert.equal(r.normalizedContentSha256.length,64);assert.equal(r.images[0].category,'estuary');assert.equal(JSON.stringify(r).includes('PRIVATE_SECRET'),false);assert.equal(r.assistantText,'Authorized answer');assert.equal(r.mainImages[0].naturalHeight,1536);assert.equal(r.mainImages[0].category,'estuary');assert.equal(r.mainImages[0].ancestors[0].tag,'img');assert.equal(r.headings[0].label,'ChatGPT said:');assert.equal(r.headings[0].leadingSpan.markerText,'Response complete');assert.equal(r.headings[0].leadingSpan.literalAttributes['aria-hidden'],'true');assert.equal(r.mainImages[0].button.literalAttributes['data-testid'],'generated-image');assert.equal(r.controls[0].intent,'download');assert.equal(r.controls[0].nearestImageAncestor.imageCount,1);assert.equal(r.controls[0].knownLabel,'Download image');assert.equal(JSON.stringify(r).includes('PRIVATE_'),false);
positive.crypto={subtle:{digest:async(...args)=>{const result=await webcrypto.subtle.digest(...args);claim.retiring=true;return result;}}};r=await probe();assert.equal(r.allowed,false);assert.equal(r.reason,'original-target-changed');claim.retiring=false;
positive.crypto=webcrypto;positive.fixtureTurns[0].text='wrong';r=await probe();assert.equal(r.exactPromptMatches,false);assert.equal(r.normalizedContentSha256,null);assert.equal(r.mainImages.length,0);assert.equal(r.controls.length,0);assert.equal(r.headings.length,0);positive.fixtureTurns=[{role:'user',text:'prompt',contains:()=>false}];r=await probe();assert.equal(r.adjacentAssistant,false);assert.equal(r.assistantTextLength,0);
""".replace('MODULE',json.dumps(module))
        script=self.folder/"pure-live.mjs";script.write_text(program.replace("__RECOVERY_URL__",RECOVERY_URL),encoding="utf-8")
        result=subprocess.run(["node",str(script)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_pure_live_status_original_state_binding_negative_guards(self):
        module=(Path(__file__).resolve().parents[1]/"tools/chatgpt_web_image_live_status.mjs").as_uri()
        program=r"""import assert from 'node:assert/strict';import path from 'node:path';import {createHash} from 'node:crypto';import {validateLiveStatus,validateLiveFacts} from MODULE;
const hash=s=>createHash('sha256').update(s).digest('hex');const state={status:'RUNNING',opt_in:true,safety_history:'CLEAR',browser_mode:'windows-isolated',session_id:'session',prompt:'exact',prompt_sha256:hash('exact'),profile_dir:'profile',oracle_home:'home',project_binding:{route:{project_id:'g-p-abc'}},scenario_evidence:{attempt:{attempt_id:'original'}}};const runtime={promptSubmitted:true,userDataDir:'profile',chromeHost:'127.0.0.1',chromePort:12345,chromePid:10,controllerPid:11,chromeTargetId:'A'.repeat(32),tabUrl:'https://chatgpt.com/g/g-p-abc-art/c/12345678-1234-1234-1234-123456789abc',conversationId:'12345678-1234-1234-1234-123456789abc',ownedRecoveryTarget:{host:'127.0.0.1',port:12345,targetId:'A'.repeat(32),claimId:'12345678-1234-1234-1234-123456789abc'}};const meta={id:'session',mode:'browser',options:{prompt:'exact',generateImage:path.join('home','generated','result.png')},browser:{runtime,config:{manualLoginProfileDir:'profile'}}};const receipt={session_id:'session',prompt_sha256:state.prompt_sha256};const guard={active_attempt:{status:'ACTIVE',attempt_id:'original',reference_binding:{prompt_sha256:state.prompt_sha256}}};assert.equal(validateLiveStatus(state,meta,receipt,guard).runtime,runtime);
const terminalState={...state,status:'UNKNOWN',recovery_started:true};const terminalMeta={...meta,status:'error',error:{details:{stage:'assistant-timeout',runtime:{...runtime}}}};const terminalGuard={active_attempt:null,attempts:[{status:'UNKNOWN',attempt_id:'original',reference_binding:{prompt_sha256:state.prompt_sha256}}]};let retained=validateLiveStatus(terminalState,terminalMeta,receipt,terminalGuard);assert.equal(retained.retainedOriginal,true);const facts={chromeAlive:true,controllerAlive:false,chromeParent:11,listenerOwned:true};validateLiveFacts(facts,runtime,true);assert.throws(()=>validateLiveFacts(facts,runtime,false));for(const change of [{chromeAlive:false},{listenerOwned:false},{chromeParent:999}])assert.throws(()=>validateLiveFacts({...facts,...change},runtime,true));for(const badMeta of [{...terminalMeta,status:'completed'},{...terminalMeta,error:{details:{stage:'other',runtime}}},{...terminalMeta,error:{details:{stage:'assistant-timeout',runtime:{...runtime,chromeTargetId:'OTHER'}}}}])assert.throws(()=>validateLiveStatus(terminalState,badMeta,receipt,terminalGuard));for(const badGuard of [{...terminalGuard,active_attempt:guard.active_attempt},{active_attempt:null,attempts:[]},{...terminalGuard,attempts:[{...terminalGuard.attempts[0],status:'REFUSED'}]},{...terminalGuard,attempts:[terminalGuard.attempts[0],terminalGuard.attempts[0]]}])assert.throws(()=>validateLiveStatus(terminalState,terminalMeta,receipt,badGuard));assert.throws(()=>validateLiveStatus({...terminalState,status:'DOWNLOADED'},terminalMeta,receipt,terminalGuard));
for(const change of [{status:'UNKNOWN'},{opt_in:false},{session_id:'other'},{prompt:'tampered'},{profile_dir:'other'},{safety_history:'BLOCKED'}])assert.throws(()=>validateLiveStatus({...state,...change},meta,receipt,guard));assert.throws(()=>validateLiveStatus(state,meta,{...receipt,session_id:'other'},guard));assert.throws(()=>validateLiveStatus(state,meta,receipt,{active_attempt:{...guard.active_attempt,status:'REFUSED'}}));assert.throws(()=>validateLiveStatus(state,meta,receipt,{active_attempt:{...guard.active_attempt,attempt_id:'other'}}));for(const change of [{chromeHost:'remote'},{chromeTargetId:'OTHER'},{tabUrl:'https://chatgpt.com/c/12345678-1234-1234-1234-123456789abc'},{conversationId:'other'},{controllerPid:null},{ownedRecoveryTarget:{...runtime.ownedRecoveryTarget,claimId:'bad'}}])assert.throws(()=>validateLiveStatus(state,{...meta,browser:{...meta.browser,runtime:{...runtime,...change}}},receipt,guard));
""".replace('MODULE',json.dumps(module))
        script=self.folder/"live-state-guards.mjs";script.write_text(program.replace("__RECOVERY_URL__",RECOVERY_URL),encoding="utf-8")
        result=subprocess.run(["node",str(script)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_pure_live_status_source_has_no_browser_mutation_and_exact_attach(self):
        source=Path("tools/chatgpt_web_image_live_status.mjs").read_text(encoding="utf-8")
        for forbidden in ("navigateToChatGPT", "createTarget", "closeTarget", "activateTarget", "captureScreenshot", "Network.getCookies", "fetch(", "sessionStorage.setItem", "acquireLease", "acquireManualLoginChrome", "harvestChatGptTab"):
            self.assertNotIn(forbidden,source)
        self.assertIn("{targetId:runtime.chromeTargetId}",source)
        self.assertNotIn("browserWSEndpoint",source)
        self.assertLess(source.index("validateLiveFacts(facts,runtime,disposition.retainedOriginal)"),source.index("await lifecycle.connectToRemoteChromeTarget"))

    def test_native_preview_matches_generic_runtime_fixture(self):
        fixture = Path(__file__).resolve().with_name("test_chatgpt_web_image_native_download.mjs")
        result = subprocess.run(["node", str(fixture)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_one_off_reconciliation_paths_are_absent_from_portable_collector(self):
        source = Path("tools/chatgpt_web_image_native_download.mjs").read_text(encoding="utf-8")
        for forbidden in ("PRECLICK_REFUSAL", "titanic-web-result", "D:/Projects/StoryArt", "SAME_V5_ONLY"):
            self.assertNotIn(forbidden, source)

    def test_native_download_uses_variable_preview_dimensions(self):
        fixture = Path(__file__).resolve().with_name("test_chatgpt_web_image_native_download.mjs")
        result = subprocess.run(["node", str(fixture)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_native_download_finalization_preserves_original_history_and_is_idempotent(self):
        call,guard,call_path,proof_path=self.native_fixture();state=self.prepare_native(call,proof_path)
        state.update(windows_isolated_browser=True,browser_mode="windows-isolated",session_id="original",status="UNKNOWN",recovery_started=True)
        web.pin_runtime_config(state);home=Path(state["oracle_home"]);(home/"sessions/original").mkdir(parents=True)
        web.save(home/"receipt.json",{"type":"receipt","session_id":"original","prompt_sha256":state["prompt_sha256"]})
        web.save(home/"sessions/original/meta.json",{"id":"original","options":{"prompt":state["prompt"]},"browser":{"runtime":{"conversationId":"original-conversation"}}})
        (home/"last-result.json").write_text('original-error',encoding="utf-8")
        state["events"].append({"status":"UNKNOWN","recovery_error":"original-reuse-refusal"});web.save(self.journal,state)
        directory=home/"native-original-download";directory.mkdir();original=directory/"portable-result.png";Image.new("RGB",(2048,1024),"white").save(original)
        requested=Path(state["requested_output"]);shutil.copyfile(original,requested)
        viewer=home/"native-download-viewer-123.json";viewer_data={"operation":"inspect-original-viewer","sessionId":"original","conversationId":"original-conversation","viewer":{}}
        viewer_hash=web.digest(json.dumps(viewer_data,ensure_ascii=False,separators=(",",":")).encode())
        viewer.write_text(json.dumps({**viewer_data,"sha256":viewer_hash},ensure_ascii=False,separators=(",",":")),encoding="utf-8")
        receipt=home/"native-download-123.json"
        result={"operation":"download-original-image","sessionId":state["session_id"],"conversationId":"original-conversation","journalSha256":web.digest(self.journal.read_bytes()),"metaSha256":web.digest((home/"sessions/original/meta.json").read_bytes()),"receiptSha256":web.digest((home/"receipt.json").read_bytes()),"guardSha256":web.digest(guard.read_bytes()),"sendOperations":0,"downloadOperations":1,"cleanup":"observer-client-closed","download":{"originalPath":str(original),"outputPath":str(requested),"sha256":web.digest(original.read_bytes()),"click":{"clicked":True,"sourceMatchesPreview":True},"downloadProof":{"viewerReceiptPath":str(viewer),"viewerReceiptSha256":web.digest(viewer.read_bytes())}}}
        bad=dict(result,conversationId="other");web.save(receipt,bad)
        with self.assertRaisesRegex(ValueError,"binding"):
            web.finalize_native_download(self.journal,receipt)
        self.assertEqual(web.load(self.journal)["status"],"UNKNOWN")
        web.save(receipt,result);completed=web.finalize_native_download(self.journal,receipt)
        self.assertEqual(completed["status"],"COMPLETED");self.assertTrue(completed["recovery_started"])
        self.assertEqual(completed["events"][:-1],state["events"])
        self.assertEqual((home/"last-result.json").read_text(),"original-error")
        self.assertEqual(completed["output"]["sha256"],result["download"]["sha256"])
        self.assertEqual(web.finalize_native_download(self.journal,receipt),completed)
        self.assertEqual(web.execute(self.journal,inspect=True,transport=lambda *_:self.fail("no transport")),completed)

    def test_native_preview_source_scope_and_durable_no_resend(self):
        source=Path("tools/chatgpt_web_image_native_download.mjs").read_text(encoding="utf-8")
        for forbidden in ("createTarget", "closeTarget", "activateTarget", "navigateToChatGPT", "Network.getCookies", "fetch(", "sessionStorage.setItem", "acquireLease", "captureScreenshot", "collectGeneratedImageArtifacts", "readFileSync"):
            self.assertNotIn(forbidden,source)
        self.assertIn("{targetId:runtime.chromeTargetId}",source)
        self.assertIn("native-download-open-attempt.json",source)
        self.assertIn("{flag:'wx'}",source)
        self.assertIn("send!=='ZERO'",source)
        self.assertIn("stage.id==='REVIEW'",source)
        self.assertIn("buildNativePreviewOpen",source)

    def test_native_probe_flag_rejects_other_operation_before_transport(self):
        with self.assertRaisesRegex(ValueError, "requires image-tool-inspect"):
            web.recovery_status(self.journal, activate_native_image_tool=True,
                                transport=lambda *_: self.fail("must not launch"))

    def test_native_probe_source_exclusive_and_fails_closed_pending_pin(self):
        source = Path("tools/chatgpt_web_image_recovery.mjs").read_text(encoding="utf-8")
        self.assertIn("node_modules/@steipete/oracle/dist/src/browser/actions/native-image-production.mjs", source)
        self.assertNotIn("D:/Projects/StoryArt", source)
        self.assertIn("menu=await actions.nativeImageTool", source)
        self.assertIn("else menu=await inspectImageToolMenu", source)
        self.assertLess(source.index("await fs.access(candidate)"), source.index("const nativeModule=await import"))

    def test_recovery_cli_exclusive_download_preserves_original_error(self):
        state=self.recovery_fixture();home=Path(state["oracle_home"])
        def transport(payload,timeout):
            self.assertEqual(payload["operation"],"recover");self.assertEqual(payload["session_id"],"original")
            self.assertEqual(web.load(self.journal)["status"],"UNKNOWN")
            out=home/"generated/result.png";out.parent.mkdir();Image.new("RGB",(32,32),"white").save(out)
            yield {"type":"result","result":{"sessionId":"original","status":"completed","sendOperations":0,"images":[{"kind":"image","path":str(out)}],"recoveryEvidence":{"prompt_sha256":state["prompt_sha256"],"conversationId":"original-conversation","userMessageId":"full-prompt-user"}}}
        result=web.recover(self.journal,transport=transport)
        self.assertEqual(result["status"],"DOWNLOADED");self.assertEqual((home/"last-result.json").read_text(),"original-error")
        self.assertIsNone(json.loads((home/"sessions/original/meta.json").read_bytes())["browser"]["runtime"]["submittedPromptHash"])
        with self.assertRaises(ValueError):web.recover(self.journal,transport=transport)

    def test_recovery_cli_failure_remains_unknown_without_second_recovery(self):
        self.recovery_fixture()
        def lost(payload,timeout):
            raise ValueError("Original prompt not observed");yield
        with self.assertRaises(ValueError):web.recover(self.journal,transport=lost)
        self.assertEqual(web.load(self.journal)["status"],"UNKNOWN")
        with self.assertRaisesRegex(ValueError,"already consumed"):web.recover(self.journal,transport=lost)

    def test_recovery_cli_rejects_changed_receipt_before_launch(self):
        state=self.recovery_fixture();web.save(Path(state["oracle_home"])/"receipt.json",{"type":"receipt","session_id":"other","prompt_sha256":state["prompt_sha256"]})
        with self.assertRaisesRegex(ValueError,"receipt mismatch"):web.recover(self.journal,transport=lambda *_:self.fail("must not launch"))
        self.assertFalse(web.load(self.journal).get("recovery_started"))

    def test_recovery_status_append_only_preserves_consumed_unknown_journal(self):
        state=self.recovery_fixture();state.update(status="UNKNOWN",recovery_started=True);web.save(self.journal,state)
        original=self.journal.read_bytes();home=Path(state["oracle_home"]);meta=(home/"sessions/original/meta.json").read_bytes()
        def inspect(payload,timeout):
            self.assertEqual(payload["operation"],"recovery-status")
            yield {"type":"result","result":{"sessionId":"original","status":"diagnostic","sendOperations":0,"imageCollectOperations":0,"conversationId":"original-conversation","snapshots":[{"index":n,"structure":{"readyState":"complete"}} for n in range(3)]}}
        first=web.recovery_status(self.journal,transport=inspect);second=web.recovery_status(self.journal,transport=inspect)
        self.assertNotEqual(first["diagnostic_receipt"],second["diagnostic_receipt"])
        self.assertTrue(Path(first["diagnostic_receipt"]).is_file());self.assertTrue(Path(second["diagnostic_receipt"]).is_file())
        self.assertEqual(self.journal.read_bytes(),original);self.assertEqual((home/"sessions/original/meta.json").read_bytes(),meta)
        self.assertEqual((home/"last-result.json").read_text(),"original-error")
        def refused(payload,timeout):
            raise ValueError("redirect diagnostic refused");yield
        with self.assertRaises(ValueError):web.recovery_status(self.journal,transport=refused)
        self.assertEqual(len(list(home.glob("recovery-status-*.json"))),3)
        self.assertEqual(self.journal.read_bytes(),original)


    def test_native_image_tool_inspect_focus_ownership_and_no_send(self):
        node=shutil.which("node")
        if not node:self.skipTest("Node unavailable")
        module=(Path(__file__).resolve().parents[1]/"tools/chatgpt_web_image_recovery.mjs").as_uri()
        program=r"""import assert from 'node:assert/strict';import vm from 'node:vm';import {inspectImageToolMenu,buildImageMenuProbe} from MODULE;
const url='https://chatgpt.com/g/g-p-abc/c/12345678-1234-1234-1234-123456789abc';
let plusCalls=0;const keys=[];const Input={dispatchKeyEvent:async value=>{keys.push(value.key)}};
const actions={plus:async()=>{plusCalls++;return {method:'trusted-keyboard',startUrl:url,delivery:{sawKeyDown:true,clicked:false,blocked:false}}},plusStay:async()=>{},delay:async()=>{}};
const owned={owned:true,activeContext:true,menuCount:1,entries:['Create image','Search']};
const baseline={plusReady:true,focused:true,menuCount:0};
const runtime=values=>({evaluate:async({expression})=>({result:{value:expression.includes('__storyartPopupIdentity')?(expression.includes('stage=\"capture\"')?{captured:true}:expression.includes('stage=\"cleanup\"')?{cleaned:true}:{bound:false}):values.shift()}})});
const result=await inspectImageToolMenu(runtime([baseline,owned,owned,{menuCount:0}]),Input,url,actions);
assert.equal(result.delivery.clicked,false);assert.equal(result.delivery.sawKeyDown,true);assert.equal(result.menuClosed,true);assert.equal(result.selectionOperations,0);assert.deepEqual(keys,['Escape','Escape']);assert.equal(plusCalls,1);
await assert.rejects(inspectImageToolMenu(runtime([{...baseline,focused:false}]),Input,url,actions),/focus\/menu baseline/);assert.equal(plusCalls,1);
await assert.rejects(inspectImageToolMenu(runtime([{ambiguous:true,menuCount:2}]),Input,url,actions),/baseline/);assert.equal(plusCalls,1);
await assert.rejects(inspectImageToolMenu(runtime([baseline,{owned:false,menuCount:1}]),Input,url,actions),/context unverified/);assert.equal(keys.length,2);assert.equal(plusCalls,2);
await assert.rejects(inspectImageToolMenu(runtime([baseline,owned,{mismatch:true}]),Input,url,actions),/Escape refused/);assert.equal(keys.length,2);assert.equal(plusCalls,3);
const noMenu={ambiguous:true,menuCount:0,plusExpanded:'false',plusFocused:true,documentFocused:false,knownToolControls:[]};
await assert.rejects(inspectImageToolMenu(runtime([baseline,...Array(20).fill(noMenu)]),Input,url,actions),error=>{assert.equal(error.menuInspection.delivery.clicked,false);assert.equal(error.menuInspection.post.menuCount,0);assert.equal(error.menuInspection.post.documentFocused,false);return /unavailable/.test(error.message)});assert.equal(keys.length,2);

let reads=0;const expression=buildImageMenuProbe(url,'after');const redirected=vm.runInNewContext(expression,{location:{href:'https://chatgpt.com/'},document:new Proxy({}, {get(){reads++;throw new Error('foreign DOM')}})});
assert.equal(redirected.mismatch,true);assert.equal(reads,0);assert.equal(keys.includes('Enter'),false);
class Element{constructor(attributes={},text=''){this.attributes=attributes;this.type='button';this.innerText=text;this.textContent=text;}getBoundingClientRect(){return {width:20,height:20}}getAttribute(name){return this.attributes[name]??null}hasAttribute(name){return name in this.attributes}contains(node){return node===this;}querySelectorAll(){return []}}
const russianCreate=new Element({'role':'menuitem'},'\u0421\u043e\u0437\u0434\u0430\u0442\u044c \u0438\u0437\u043e\u0431\u0440\u0430\u0436\u0435\u043d\u0438\u0435');
const russianImage=new Element({'role':'menuitem'},'\u0418\u0437\u043e\u0431\u0440\u0430\u0436\u0435\u043d\u0438\u0435');
const unknown=new Element({'role':'menuitem'},'private-account-value');
const menu=new Element({'role':'menu'});menu.querySelectorAll=()=>[russianCreate,russianImage,unknown];
const plus=new Element({'aria-expanded':'true','aria-controls':'owned-menu'});const main=new Element();main.querySelector=()=>plus;
const document={activeElement:plus,hasFocus:()=>false,querySelectorAll:selector=>selector==='main'?[main]:[menu],getElementById:id=>id==='owned-menu'?menu:null};
document.querySelectorAll=selector=>selector==='main'?[main]:selector==='button,[role="button"],[role="menuitem"],[role="option"],[data-radix-collection-item]'?[russianCreate,russianImage,unknown]:[menu];
const realProbe=vm.runInNewContext(buildImageMenuProbe(url,'after'),{location:{href:url},document,HTMLElement:Element});
assert.deepEqual(Array.from(realProbe.knownToolControls,node=>node.label),['Create image','Image']);assert.equal(realProbe.documentFocused,false);assert.equal(realProbe.plusExpanded,'true');assert.equal(realProbe.owned,true);assert.deepEqual(Array.from(realProbe.entries),['Create image','Image']);assert.equal(JSON.stringify(realProbe).includes('private-account-value'),false);
plus.attributes['aria-expanded']='PRIVATE_SENTINEL';const badExpanded=vm.runInNewContext(buildImageMenuProbe(url,'after'),{location:{href:url},document,HTMLElement:Element});assert.equal(badExpanded.plusExpanded,null);assert.equal(JSON.stringify(badExpanded).includes('PRIVATE_SENTINEL'),false);plus.attributes['aria-expanded']='true';

main.querySelector=()=>null;main.querySelectorAll=selector=>selector==='button,[role="button"]'?[new Element({'aria-label':'Add   files and more','aria-haspopup':'menu'}),new Element({'aria-label':'private-account-value','aria-haspopup':'PRIVATE_ID'})]:[];
const missing=vm.runInNewContext(buildImageMenuProbe(url,'before'),{location:{href:url},document,HTMLElement:Element});
assert.equal(missing.plusReady,false);assert.equal(missing.focused,false);assert.equal(missing.plusSelectorCount,0);assert.equal(missing.buttons[0].label,'Add files and more');assert.equal(missing.buttons[1].label,'OTHER');assert.equal(missing.buttons[1].haspopup,'OTHER');assert.equal(JSON.stringify(missing).includes('private-account-value'),false);assert.equal(JSON.stringify(missing).includes('PRIVATE_ID'),false);
plus.attributes={'aria-label':'Add files and more','aria-expanded':'false'};plus.focus=()=>{document.activeElement=plus};
const composerForm=new Element();composerForm.querySelectorAll=()=>[plus];const editor=new Element();editor.closest=()=>composerForm;main.contains=node=>node===composerForm;
main.querySelectorAll=selector=>selector==='#prompt-textarea, textarea[name="prompt-textarea"]'?[editor]:selector==='button,[role="button"]'?[plus]:selector==='form'?[composerForm]:[];
document.querySelectorAll=selector=>selector==='main'?[main]:[];
const fallback=vm.runInNewContext(buildImageMenuProbe(url,'before'),{location:{href:url},document,HTMLElement:Element});assert.equal(fallback.plusReady,true);assert.equal(fallback.focused,true);assert.equal(fallback.plusSelectorCount,0);
plus.type='submit';const submit=vm.runInNewContext(buildImageMenuProbe(url,'before'),{location:{href:url},document,HTMLElement:Element});assert.equal(submit.plusReady,false);plus.type='button';
composerForm.querySelectorAll=()=>[plus,new Element({'aria-label':'Add files and more'})];const duplicate=vm.runInNewContext(buildImageMenuProbe(url,'before'),{location:{href:url},document,HTMLElement:Element});assert.equal(duplicate.ambiguous,true);assert.equal(duplicate.plusReady,false);



""".replace('MODULE',json.dumps(module))
        script=self.folder/"menu-inspect-test.mjs";script.write_text(program.replace("__RECOVERY_URL__",RECOVERY_URL),encoding="utf-8")
        result=subprocess.run([node,str(script)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_native_menu_diagnostic_preserves_journal_and_error(self):
        state=self.recovery_fixture();original=self.journal.read_bytes();home=Path(state["oracle_home"])
        def inspect(payload,timeout):
            self.assertEqual(payload["operation"],"image-tool-inspect")
            yield {"type":"result","result":{"sessionId":"original","status":"image-tool-diagnostic","sendOperations":0,"imageCollectOperations":0,"conversationId":"original-conversation","menu":{"selectionOperations":0,"entries":["Create image"],"menuOwned":True,"menuClosed":True}}}
        result=web.recovery_status(self.journal,operation="image-tool-inspect",transport=inspect)
        self.assertTrue(Path(result["diagnostic_receipt"]).is_file());self.assertEqual(self.journal.read_bytes(),original)
        self.assertEqual((home/"last-result.json").read_text(),"original-error")

    def test_recovery_worker_source_is_strict_utf8(self):
        source=(Path(__file__).resolve().parents[1]/"tools/chatgpt_web_image_recovery.mjs").read_bytes().decode("utf-8",errors="strict")
        self.assertNotIn("\ufffd",source)

    def test_popup_identity_binds_new_focus_and_excludes_old_sidebar(self):
        node=shutil.which("node")
        if not node:self.skipTest("Node unavailable")
        module=(Path(__file__).resolve().parents[1]/"tools/chatgpt_web_image_recovery.mjs").as_uri()
        program=r"""import assert from 'node:assert/strict';import vm from 'node:vm';import {buildPopupIdentityProbe} from MODULE;
const url='https://chatgpt.com/c/owned';let privateReads=0;
class Element{constructor(tag='DIV',text=''){this.tagName=tag;this.isConnected=true;this.textContent=text;this.innerText=text;this.children=[];this.attributes={};this.parentElement=null;}getBoundingClientRect(){return {x:10,y:10,width:10,height:10}}getAttribute(name){return this.attributes[name]??null}hasAttribute(name){return name in this.attributes}contains(node){return node===this||this.children.some(child=>child.contains(node))}closest(selector){for(let node=this;node;node=node.parentElement){if(selector==='aside,nav,[role=\"navigation\"]'&&(['ASIDE','NAV'].includes(node.tagName)||node.attributes.role==='navigation'))return node;if(node.tagName===selector.toUpperCase())return node;}return null;}querySelectorAll(){return this.children.flatMap(child=>[child,...child.querySelectorAll()])}}
const body=new Element('BODY'),main=new Element('MAIN'),plus=new Element('BUTTON'),sidebar=new Element('ASIDE');body.children=[main,sidebar];main.parentElement=body;sidebar.parentElement=body;const form=new Element('FORM');form.parentElement=main;main.children=[form];form.children=[plus];plus.parentElement=form;plus.attributes['aria-expanded']='false';
Object.defineProperty(sidebar,'innerText',{get(){privateReads++;throw new Error('sidebar text read')}});Object.defineProperty(sidebar,'textContent',{get(){privateReads++;throw new Error('sidebar text read')}});
const document={body,activeElement:plus,querySelectorAll:selector=>selector==='main'?[main]:[],getElementById:id=>id==='popup'?popup:null};const window={getComputedStyle:node=>({display:'block',visibility:'visible',opacity:node.opacity??'1'})};const location={href:url};const ctx=vm.createContext({document,window,location,HTMLElement:Element});const resolver='({button:window.plus})';window.plus=plus;
const run=stage=>vm.runInContext(buildPopupIdentityProbe(url,'operation',stage,resolver),ctx);
assert.equal(run('capture').captured,true);assert.equal(privateReads,0);
const popup=new Element(),leaf=new Element('SPAN','Create image'),unknown=new Element('SPAN','PRIVATE_LABEL');popup.children=[leaf,unknown];leaf.parentElement=popup;unknown.parentElement=popup;popup.parentElement=body;body.children.push(popup);plus.attributes['aria-expanded']='true';document.activeElement=leaf;
let proof=run('inspect');assert.equal(proof.bound,true);assert.equal(proof.binding,'new-focus-ancestor');assert.equal(proof.knownLabels[0].label,'Create image');assert.equal(proof.otherLabelCount,1);assert.equal(JSON.stringify(proof).includes('PRIVATE_LABEL'),false);assert.equal(privateReads,0);
leaf.textContent=leaf.innerText='\u0421\u043e\u0437\u0434\u0430\u0442\u044c \u0438\u0437\u043e\u0431\u0440\u0430\u0436\u0435\u043d\u0438\u0435';assert.equal(run('inspect').knownLabels[0].label,'Create image');
plus.attributes['aria-controls']='popup';proof=run('inspect');assert.equal(proof.binding,'aria-controls');
plus.attributes['aria-controls']='wrong';proof=run('inspect');assert.equal(proof.bound,false);assert.equal(proof.controlsMismatch,true);delete plus.attributes['aria-controls'];
document.activeElement=plus;assert.equal(run('inspect').bound,false);document.activeElement=leaf;
location.href='https://chatgpt.com/c/other';assert.equal(run('inspect').mismatch,true);assert.equal(privateReads,0);location.href=url;
assert.equal(run('cleanup').cleaned,true);assert.equal(window.__storyartPopupIdentity.size,0);
plus.attributes['aria-expanded']='false';document.activeElement=plus;popup.opacity='0';assert.equal(run('capture').captured,true);popup.opacity='1';plus.attributes['aria-expanded']='true';document.activeElement=leaf;
main.attributes['aria-hidden']='true';const modalOwner=run('inspect');assert.equal(modalOwner.bound,true);assert.equal(modalOwner.plusCSSVisible,false);assert.equal(modalOwner.plusAriaHidden,true);assert.equal(modalOwner.activeAncestors[0].cssVisible,true);delete main.attributes['aria-hidden'];
const cssOpened=run('inspect');assert.equal(cssOpened.bound,true);assert.equal(cssOpened.newNode,false);assert.equal(cssOpened.newlyVisible,true);assert.equal(cssOpened.activeAncestors[0].existedBefore,true);assert.equal(cssOpened.activeAncestors[0].cssVisible,true);
run('cleanup');document.activeElement=plus;plus.attributes['aria-expanded']='false';assert.equal(run('capture').captured,true);plus.attributes['aria-expanded']='true';document.activeElement=sidebar;assert.equal(run('inspect').bound,false);assert.equal(privateReads,0);run('cleanup');
const composer=new Element('DIV');composer.attributes.role='textbox';composer.parentElement=main;main.children.push(composer);document.activeElement=plus;plus.attributes['aria-expanded']='false';run('capture');plus.attributes['aria-expanded']='true';document.activeElement=composer;const oldComposer=run('inspect');assert.equal(oldComposer.bound,false);assert.equal(oldComposer.active.role,'textbox');assert.equal(oldComposer.activeAncestors[0].existedBefore,true);run('cleanup');
const formComposer=new Element('DIV');formComposer.attributes.role='textbox';formComposer.parentElement=form;form.children.push(formComposer);document.activeElement=plus;plus.attributes['aria-expanded']='false';run('capture');
const pane=new Element(),imageLabel=new Element('SPAN','Create image');pane.children=[imageLabel];imageLabel.parentElement=pane;pane.parentElement=form;form.children.push(pane);
const freshSidebar=new Element('DIV');freshSidebar.parentElement=sidebar;sidebar.children.push(freshSidebar);Object.defineProperty(freshSidebar,'innerText',{get(){privateReads++;throw new Error('fresh sidebar text read')}});
plus.attributes['aria-expanded']='true';document.activeElement=formComposer;const inline=run('inspect');assert.equal(inline.inlinePalette.bound,true);assert.equal(inline.inlinePalette.knownLabels[0].label,'Create image');assert.equal(inline.bound,false);assert.equal(privateReads,0);
const secondPane=new Element();secondPane.parentElement=form;form.children.push(secondPane);Object.defineProperty(secondPane,'innerText',{get(){privateReads++;throw new Error('multiple panes text read')}});const ambiguous=run('inspect');assert.equal(ambiguous.inlinePalette.bound,false);assert.equal(ambiguous.inlinePalette.ownedRootCount,2);assert.equal(privateReads,0);run('cleanup');
form.children=form.children.filter(node=>node!==secondPane);document.activeElement=plus;plus.attributes['aria-expanded']='false';run('capture');plus.attributes['aria-expanded']='true';document.activeElement=formComposer;assert.equal(run('inspect').inlinePalette.bound,false);assert.equal(privateReads,0);run('cleanup');
document.activeElement=plus;plus.attributes['aria-expanded']='false';run('capture');
const externalGroup=new Element(),itemA=new Element(),itemB=new Element(),externalLabel=new Element('SPAN','Create image');itemA.attributes.role=itemB.attributes.role='listitem';itemA.children=[externalLabel];externalLabel.parentElement=itemA;itemB.textContent=itemB.innerText='PRIVATE_RECENT_FILE';externalGroup.children=[itemA,itemB];itemA.parentElement=itemB.parentElement=externalGroup;externalGroup.parentElement=body;body.children.push(externalGroup);plus.attributes['aria-expanded']='true';document.activeElement=formComposer;
const external=run('inspect');assert.equal(external.externalPalette.bound,true);assert.equal(external.externalPalette.knownLabels[0].label,'Create image');assert.equal(JSON.stringify(external).includes('PRIVATE_RECENT_FILE'),false);assert.equal(privateReads,0);run('cleanup');
document.elementFromPoint=()=>null;document.activeElement=plus;plus.attributes['aria-expanded']='false';run('capture');plus.attributes['aria-expanded']='true';document.activeElement=formComposer;document.elementFromPoint=()=>imageLabel;const preloadedImage=run('inspect');assert.equal(preloadedImage.imageRow.bound,true);assert.equal(preloadedImage.imageRow.renderedCount,1);assert.equal(preloadedImage.imageRow.matches.find(match=>match.hitInsideLeaf).before.hitInside,false);assert.equal(preloadedImage.imageRow.matches.find(match=>match.hitInsideLeaf).existedBefore,true);run('cleanup');document.elementFromPoint=()=>null;

body.children=body.children.filter(node=>node!==externalGroup);const privateOld=new Element('P','PRIVATE_OLD_CONTENT');externalGroup.children=[privateOld];privateOld.parentElement=externalGroup;body.children.push(externalGroup);document.activeElement=plus;plus.attributes['aria-expanded']='false';run('capture');externalGroup.children.push(itemA,itemB);plus.attributes['aria-expanded']='true';document.activeElement=formComposer;const unrelated=run('inspect');assert.equal(unrelated.externalPalette.bound,false);assert.ok(unrelated.externalPalette.preexistingOutside>0);assert.equal(JSON.stringify(unrelated).includes('PRIVATE_OLD_CONTENT'),false);run('cleanup');
body.children=body.children.filter(node=>node!==externalGroup);document.activeElement=plus;plus.attributes['aria-expanded']='false';run('capture');
const secondGroup=new Element(),itemC=new Element(),itemD=new Element();itemC.attributes.role=itemD.attributes.role='listitem';secondGroup.children=[itemC,itemD];itemC.parentElement=itemD.parentElement=secondGroup;secondGroup.parentElement=body;externalGroup.children=[itemA,itemB];body.children.push(externalGroup,secondGroup);plus.attributes['aria-expanded']='true';document.activeElement=formComposer;const split=run('inspect');assert.equal(split.externalPalette.bound,false);assert.equal(split.externalPalette.noUniqueExternalRoot,true);run('cleanup');
body.children=body.children.filter(node=>node!==externalGroup&&node!==secondGroup);document.activeElement=plus;plus.attributes['aria-expanded']='false';run('capture');secondGroup.parentElement=sidebar;sidebar.children.push(secondGroup);Object.defineProperty(itemC,'innerText',{get(){privateReads++;throw new Error('new sidebar list text read')}});plus.attributes['aria-expanded']='true';document.activeElement=formComposer;assert.equal(run('inspect').externalPalette.bound,false);assert.equal(privateReads,0);run('cleanup');
main.getBoundingClientRect=()=>({x:0,y:0,width:1000,height:800});form.getBoundingClientRect=()=>({x:100,y:700,width:600,height:60});document.activeElement=plus;plus.attributes['aria-expanded']='false';run('capture');
const portal=new Element(),portalRow=new Element(),portalLabel=new Element('SPAN','Create image');portal.getBoundingClientRect=()=>({x:100,y:400,width:600,height:288});portalLabel.getBoundingClientRect=()=>({x:130,y:450,width:70,height:20});portalRow.children=[portalLabel];portalLabel.parentElement=portalRow;portalRow.parentElement=portal;portal.children=[portalRow];portal.parentElement=body;body.children.push(portal);document.elementFromPoint=()=>portalLabel;plus.attributes['aria-expanded']='true';document.activeElement=formComposer;
const provenPortal=run('inspect');assert.equal(provenPortal.nativePortal.bound,true);assert.equal(provenPortal.nativePortal.candidateCount,1);assert.equal(provenPortal.nativePortal.imageRowBound,true);assert.equal(provenPortal.nativePortal.renderedImageCount,1);
const competingPortal=new Element();competingPortal.getBoundingClientRect=()=>({x:100,y:400,width:600,height:288});competingPortal.parentElement=body;body.children.push(competingPortal);assert.equal(run('inspect').nativePortal.bound,false);assert.equal(run('inspect').nativePortal.candidateCount,2);run('cleanup');





""".replace('MODULE',json.dumps(module))
        script=self.folder/"popup-identity-test.mjs";script.write_text(program.replace("__RECOVERY_URL__",RECOVERY_URL),encoding="utf-8")
        result=subprocess.run([node,str(script)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_main_screenshot_clip_excludes_sidebar_and_redirects(self):
        node=shutil.which("node")
        if not node:self.skipTest("Node unavailable")
        module=(Path(__file__).resolve().parents[1]/"tools/chatgpt_web_image_recovery.mjs").as_uri()
        import base64
        program=r"""import assert from 'node:assert/strict';import vm from 'node:vm';import fs from 'node:fs/promises';import {buildMainScreenshotProbe,captureOwnedMain} from MODULE;
const url='https://chatgpt.com/c/original',key='snapshot';let width=700,navWidth=300;
const form={},plus={isConnected:true,closest:()=>form},nav={getBoundingClientRect:()=>({x:0,y:0,width:navWidth,height:800})};
const main={getBoundingClientRect:()=>({x:300,y:0,width,height:800}),contains:node=>node===form||node===plus};
const saved={plus,form,nodes:new WeakSet([nav]),visibility:new WeakMap([[nav,true]])};
const window={innerWidth:1000,innerHeight:800,__storyartPopupIdentity:new Map([[key,saved]])};const document={querySelectorAll:selector=>selector==='main'?[main]:[nav]};const location={href:url};
const ctx=vm.createContext({window,document,location});const probe=()=>vm.runInContext(buildMainScreenshotProbe(url,key),ctx);
assert.equal(probe().allowed,true);navWidth=350;assert.equal(probe().reason,'main-overlaps-preexisting-navigation');navWidth=300;width=701;assert.equal(probe().reason,'main-not-viewport-bounded');width=700;
location.href='https://chatgpt.com/c/other';assert.equal(probe().reason,'url-changed');location.href=url;
let screenshots=0;const Page={captureScreenshot:async options=>{screenshots++;assert.equal(options.clip.x,300);assert.equal(options.clip.width,700);assert.equal(options.captureBeyondViewport,false);return {data:PNG}}};
const Runtime={evaluate:async({expression})=>({result:{value:expression==='location.href'?url:probe()}})};
const result=await captureOwnedMain(Runtime,Page,url,key,OUTPUT);assert.equal(result.captured,true);assert.equal(screenshots,1);assert.ok((await fs.stat(OUTPUT)).size>0);
navWidth=350;assert.equal((await captureOwnedMain(Runtime,Page,url,key,OUTPUT+'-blocked')).captured,false);assert.equal(screenshots,1);
""".replace('MODULE',json.dumps(module)).replace('PNG',json.dumps(base64.b64encode(self.ref.read_bytes()).decode())).replace('OUTPUT',json.dumps(str(self.folder/"main-diag.png")))
        script=self.folder/"screenshot-test.mjs";script.write_text(program.replace("__RECOVERY_URL__",RECOVERY_URL),encoding="utf-8")
        result=subprocess.run([node,str(script)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

if __name__ == "__main__": unittest.main()

