import { createHash } from "node:crypto";
import { buildConversationTurnListExpression, buildConversationTurnSemantics } from "./conversationTurns.js";
export function browserPromptFingerprint(value, messageId) {
    return createHash("sha256")
        .update(JSON.stringify([messageId, String(value ?? "").replace(/\r\n?/g, "\n")]))
        .digest("hex");
}
export function readUserMessageIds(runtime, timeoutMs = 0) {
    return readDomUntil(runtime, `(() => { ${buildConversationTurnSemantics()}
      const actual = Array.from(document.querySelectorAll('[data-message-author-role="user"]'));
      if (actual.length) return actual.map(user => user.getAttribute('data-message-id'));
      return (${buildConversationTurnListExpression()}).filter(turn => oracleReadTurnRole(turn) === 'user')
        .map(turn => (oracleReadTurnContentRoot(turn) || turn).getAttribute('data-message-id'));
    })()`, timeoutMs, (value) => Array.isArray(value) && value.every((id) => typeof id === "string" && id.trim())
        ? value
        : undefined);
}
export async function readSubmittedPromptFingerprint(runtime, previousMessageIds, timeoutMs = 0) {
    if (previousMessageIds === undefined)
        return undefined;
    const previous = new Set(previousMessageIds);
    return readDomUntil(runtime, `(() => {
        ${buildConversationTurnSemantics()}
        const turns = ${buildConversationTurnListExpression()};
        for (let index = turns.length - 1; index >= 0; index--) {
          const turn = turns[index];
          const user = turn.matches('[data-message-author-role="user"]') ? turn : turn.querySelector('[data-message-author-role="user"]') || (oracleReadTurnRole(turn) === 'user' ? oracleReadTurnContentRoot(turn) : null);
          if (user) return { text: oracleReadTurnContentRoot(turn) ? oracleReadTurnText(turn) : user.textContent, messageId: user.getAttribute('data-message-id') };
        }
        return null;
      })()`, timeoutMs, (value) => {
        const turn = value;
        if (typeof turn?.text === "string" &&
            turn.text.trim() &&
            typeof turn.messageId === "string" &&
            turn.messageId.trim() &&
            !previous.has(turn.messageId))
            return browserPromptFingerprint(turn.text, turn.messageId);
        return undefined;
    });
}
/** Content observation has a distinct domain; it is never a provider message-ID fingerprint. */
export async function readSubmittedPromptContentEvidence(runtime, expectedPrompt, baselineTurns, expectedConversationId, timeoutMs = 0) {
    if (!Number.isInteger(baselineTurns) || baselineTurns < 0 || typeof expectedConversationId !== "string" || !expectedConversationId) return undefined;
    return readDomUntil(runtime, `(() => {
      const currentId = location.pathname.match(/\\/c\\/([a-zA-Z0-9-]+)/)?.[1];
      if (currentId !== ${JSON.stringify(expectedConversationId)}) return null;
      ${buildConversationTurnSemantics()}
      const turns = ${buildConversationTurnListExpression()};
      const index = turns.reduce((last, turn, at) => oracleReadTurnRole(turn) === 'user' ? at : last, -1);
      const user = turns[index];
      if (index < ${baselineTurns} || !oracleReadTurnContentRoot(user)) return null;
      const normalize = value => String(value ?? '').replace(/\\r\\n?/g, '\\n').trim();
      const text = normalize(oracleReadTurnText(user));
      if (text !== normalize(${JSON.stringify(expectedPrompt)})) return null;
      return { text, userTurnIndex: index, conversationId: currentId };
    })()`, timeoutMs, value => typeof value?.text === "string" && value.text
        ? { kind: "observed-heading-content-sha256", contentSha256: createHash("sha256").update(value.text).digest("hex"),
            conversationId: value.conversationId, userTurnIndex: value.userTurnIndex,
            normalization: "CRLF-to-LF-and-outer-trim" }
        : undefined);
}
async function readDomUntil(runtime, expression, timeoutMs, select) {
    const deadline = Date.now() + Math.max(0, timeoutMs);
    for (;;) {
        try {
            const result = await runtime.evaluate({ expression, returnByValue: true });
            const selected = select(result.result?.value);
            if (selected !== undefined)
                return selected;
        }
        catch (error) {
            const message = error instanceof Error ? error.message : String(error);
            if (!/Cannot find (?:default )?(?:execution )?context|Execution context (?:was destroyed|is not available)/i.test(message))
                return undefined;
        }
        if (Date.now() >= deadline)
            return undefined;
        await new Promise((resolve) => setTimeout(resolve, Math.min(100, deadline - Date.now())));
    }
}
