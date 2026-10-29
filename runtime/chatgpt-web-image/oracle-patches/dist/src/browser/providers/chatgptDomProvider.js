import { ensurePromptReady } from "../actions/navigation.js";
import { submitPrompt } from "../actions/promptComposer.js";
import { waitForAssistantResponse } from "../actions/assistantResponse.js";
function requireState(ctx) {
    const state = ctx.state;
    if (!state?.runtime || !state?.input || !state?.logger) {
        throw new Error("chatgptDomProvider requires runtime/input/logger in context.state.");
    }
    return state;
}
async function waitForUi(ctx) {
    const state = requireState(ctx);
    await ensurePromptReady(state.runtime, state.inputTimeoutMs ?? 30_000, state.logger);
}
async function typePrompt(_ctx) {
    // submitPrompt() handles typing + send for ChatGPT.
}
async function submitPromptViaAdapter(ctx) {
    const state = requireState(ctx);
    const committedTurns = await submitPrompt({
        runtime: state.runtime,
        input: state.input,
        page: state.page,
        attachmentNames: state.attachmentNames ?? [],
        attachmentNavigationUrl: state.attachmentNavigationUrl,
        baselineTurns: state.baselineTurns ?? undefined,
        inputTimeoutMs: state.inputTimeoutMs ?? undefined,
        attachmentTimeoutMs: state.attachmentTimeoutMs ?? undefined,
        onPromptSubmitted: state.onPromptSubmitted,
        webSearch: state.webSearch,
        nativeImageSettings: state.nativeImageSettings,
    }, ctx.prompt, state.logger);
    state.committedTurns =
        typeof committedTurns === "number" && Number.isFinite(committedTurns) ? committedTurns : null;
    if (state.committedTurns != null &&
        (state.baselineTurns == null || state.committedTurns > state.baselineTurns)) {
        state.baselineTurns = Math.max(0, state.committedTurns - 1);
    }
}
async function waitForResponse(ctx) {
    const state = requireState(ctx);
    const answer = await waitForAssistantResponse(state.runtime, state.timeoutMs, state.logger, state.baselineTurns ?? undefined);
    return {
        text: answer.text,
        html: answer.html,
        meta: answer.meta,
    };
}
export const chatgptDomProvider = {
    providerName: "chatgpt-web",
    waitForUi,
    typePrompt,
    submitPrompt: submitPromptViaAdapter,
    waitForResponse,
};
