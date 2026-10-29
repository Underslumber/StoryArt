/* Oracle MCP mapping: browser-only consult with durable detached receipt, then same-session wait. */
const browserModels = Object.freeze({
  "GPT-5.6 Sol":"gpt-5.6-sol", "GPT-5.5":"gpt-5.5-pro",
  "GPT-5.5 Instant":"gpt-5.5-instant", "Thinking 5.5":"gpt-5.5", "Thinking 5.4":"gpt-5.4"
});
async function runImageTransport(request, call, emit, beforeProviderCall = async () => {}) {
  if (!['submit', 'inspect'].includes(request.operation) || request.opt_in !== true ||
      request.safety_history !== 'CLEAR' || !['current', 'select'].includes(request.model_strategy))
    throw new Error('No authorized request');
  let id = request.session_id;
  if (request.operation === 'submit') {
    const project = request.project_binding?.route;
    if (!project || !/^g-p-[a-f0-9]+$/.test(project.project_id) ||
        project.project_url !== `https://chatgpt.com/g/${project.project_id}/project`)
      throw new Error('Dedicated project destination required; no home fallback');
    if (request.status !== 'UNKNOWN' || id) throw new Error('Submission already consumed');
    const args = {engine:'browser', prompt:request.prompt,
      files:request.references.map(ref => ref.path), browserAttachments:'always',
      browserBundleFiles:false, browserArchive:'never', browserModelStrategy:request.model_strategy,
      generateImage:require('node:path').join(request.oracle_home,'generated','result.png'),
      waitForCompletion:false};
    if (request.model_strategy === 'select') {
      if (!Object.hasOwn(browserModels, request.model_label)) throw new Error('Unsupported browser model label');
      args.model = browserModels[request.model_label];
      args.browserModelLabel = request.model_label;
    }
    await beforeProviderCall({ name: 'consult' });
    const response = await call('consult', args);
    id = response.sessionId;
    if (typeof id !== 'string' || !id) throw new Error('No durable Oracle session receipt; no resubmit');
    await emit({type:'receipt', session_id:id, prompt_sha256:request.prompt_sha256});
  } else if (typeof id !== 'string' || !id) throw new Error('Original session required');
  const result = await call('wait', {id, timeoutMs:request.wait_ms});
  if (result.sessionId !== id) throw new Error('Different Oracle session');
  await emit({type:'result', result});
}
module.exports = { runImageTransport, browserModels };
