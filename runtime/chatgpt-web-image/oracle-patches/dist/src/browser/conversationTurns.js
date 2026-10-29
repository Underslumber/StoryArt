import { CONVERSATION_TURN_CONTAINER_SELECTOR, CONVERSATION_TURN_SELECTOR } from "./constants.js";
/** Read observed heading topology without modifying provider DOM or fabricating IDs. */
export function buildConversationTurnSemantics() {
    const parseNativeImageUser = process.env.ORACLE_REQUIRE_NATIVE_IMAGE_TOOL === '1' || process.env.ORACLE_PARSE_NATIVE_IMAGE_USER === '1';
    return `
    function oracleHeadingTurnInfo(node) {
      if (!node || node.tagName !== 'DIV') return null;
      const children = Array.from(node.children || []);
      let headingIndex = 0;
      if (${parseNativeImageUser} && children.length === 3) {
        const marker = children[0];
        const attributes = Array.from(marker.attributes || [], attribute => attribute.name);
        if (marker.tagName !== 'SPAN' || marker.textContent !== '' || marker.children.length !== 0 ||
            !marker.hasAttribute('hidden') || !marker.hasAttribute('data-chatgpt-agent-turn-start') ||
            attributes.length !== 2 || attributes.some(name => !['hidden', 'data-chatgpt-agent-turn-start'].includes(name)) ||
            children[1].tagName !== 'H4' || String(children[1].textContent || '').trim() !== 'ChatGPT said:' ||
            children[2].tagName !== 'DIV') return null;
        headingIndex = 1;
      } else if (children.length !== 2 || children[0].tagName !== 'H4' || children[1].tagName !== 'DIV') return null;
      const label = String(children[headingIndex].textContent || '').trim();
      const role = label === 'You said:' ? 'user' : label === 'ChatGPT said:' ? 'assistant' : null;
      return role ? { role, content: children[headingIndex + 1] } : null;
    }
    function oracleHasHeadingLayout(root = document) {
      const mains = root?.matches?.('main') ? [root] : Array.from(root?.querySelectorAll?.('main') || []);
      // Presence is separate from acceptance: ambiguous/rejected new layouts must not
      // re-enable a legacy whole-document image rescue.
      return mains.some(main => Array.from(main.querySelectorAll('h4')).some(heading =>
        ['You said:', 'ChatGPT said:'].includes(String(heading.textContent || '').trim())));
    }
    function oracleHeadingTurns(root = document) {
      const mains = root?.matches?.('main') ? [root] : Array.from(root?.querySelectorAll?.('main') || []);
      if (mains.length !== 1) return [];
      const main = mains[0];
      // Real provider containers keep precedence, including during partial hydration.
      if (main.querySelector(${JSON.stringify(CONVERSATION_TURN_CONTAINER_SELECTOR + ', ' + CONVERSATION_TURN_SELECTOR)})) return [];
      const candidates = Array.from(main.querySelectorAll('h4')).map(heading => heading.parentElement)
        .filter(node => main.contains(node) && oracleHeadingTurnInfo(node));
      const unique = Array.from(new Set(candidates));
      // Quoted speaker headings inside an actual turn cannot become separate turns.
      const turns = unique.filter(node => !unique.some(other => other !== node && other.contains(node)));
      if (!turns.length) return [];
      for (let index = 0; index < turns.length; index++) {
        const role = oracleHeadingTurnInfo(turns[index]).role;
        if (role !== (index % 2 === 0 ? 'user' : 'assistant')) return [];
        if (index && !(turns[index - 1].compareDocumentPosition(turns[index]) & 4)) return [];
      }
      return turns;
    }
    function oracleReadTurnRole(node) {
      const actual = String(node?.getAttribute?.('data-message-author-role') || node?.getAttribute?.('data-turn') || '').toLowerCase();
      if (actual === 'user' || actual === 'assistant') return actual;
      const descendant = node?.querySelector?.('[data-message-author-role="user"], [data-turn="user"], [data-message-author-role="assistant"], [data-turn="assistant"]');
      const nested = String(descendant?.getAttribute?.('data-message-author-role') || descendant?.getAttribute?.('data-turn') || '').toLowerCase();
      if (nested === 'user' || nested === 'assistant') return nested;
      return oracleHeadingTurns().includes(node) ? oracleHeadingTurnInfo(node).role : null;
    }
    function oracleReadTurnContentRoot(node) {
      if (!oracleHeadingTurns().includes(node)) return null;
      const info = oracleHeadingTurnInfo(node);
      if (info.role === 'user') {
        const bubbles = [
          ...(info.content.matches?.('[data-user-message-bubble]') ? [info.content] : []),
          ...info.content.querySelectorAll('[data-user-message-bubble]'),
        ];
        if (bubbles.length === 1) {
          const payloads = bubbles[0].querySelectorAll('[class~="whitespace-pre-wrap"]');
          if (payloads.length === 1) return payloads[0];
        }
      }
      return info.content;
    }
    function oracleReadTurnText(node) {
      const content = oracleReadTurnContentRoot(node);
      if (!content) return node?.innerText ?? node?.textContent ?? '';
      const readAssistantBlocks = current => {
        if (current.nodeType === 3) return current.nodeValue ?? current.textContent ?? '';
        if (current.nodeType !== 1) return '';
        const tag = String(current.tagName || '').toUpperCase();
        if (tag === 'BR') return '\\n';
        if (tag === 'PRE') return current.textContent ?? '';
        const blocks = ['P', 'DIV', 'PRE', 'UL', 'OL', 'LI', 'BLOCKQUOTE', 'H1', 'H2', 'H3', 'H4', 'H5', 'H6', 'SECTION', 'ARTICLE'];
        const pieces = Array.from(current.childNodes || [])
          .map(child => ({ child, text: readAssistantBlocks(child) })).filter(piece => piece.text !== '');
        let text = '';
        for (let index = 0; index < pieces.length; index++) {
          const piece = pieces[index];
          const block = blocks.includes(String(piece.child.tagName || '').toUpperCase());
          if (block && text && !text.endsWith('\\n')) text += '\\n';
          text += piece.text;
          if (block && index + 1 < pieces.length && !text.endsWith('\\n')) text += '\\n';
        }
        return text;
      };
      // Read a detached clone only to omit UI controls from content; original DOM stays untouched.
      if (typeof content.cloneNode === 'function') {
        const copy = content.cloneNode(true);
        // The observed native image action is UI, never a textual prompt prefix.
        // Restrict this to image operations and the exact unique provider topology.
        if (${parseNativeImageUser} && oracleHeadingTurnInfo(node).role === 'user') {
          const links = Array.from(copy.querySelectorAll('[data-prompt-link-href], [data-prompt-link-label]'));
          const markdowns = [
            ...(copy.matches?.('div[data-selected-text-overlay-target][data-markdown-text-tone]') ? [copy] : []),
            ...copy.querySelectorAll('div[data-selected-text-overlay-target][data-markdown-text-tone]'),
          ];
          if (links.length === 1 && markdowns.length === 1) {
            const pill = links[0];
            const paragraph = pill.parentElement;
            const markdown = markdowns[0];
            const meaningful = current => Array.from(current?.childNodes || []).filter(child =>
              child.nodeType === 1 || (child.nodeType === 3 && String(child.textContent || '').trim() !== ''));
            const paragraphNodes = meaningful(paragraph);
            const pillChildren = Array.from(pill.children || []);
            if (pill.tagName === 'SPAN' && pill.getAttribute('data-prompt-link-href') === 'chatgpt-system-hint://picture_v2' &&
                pill.getAttribute('data-prompt-link-label') === 'Create image' && pill.textContent === 'Create image' &&
                paragraph?.tagName === 'P' && paragraph.parentElement === markdown &&
                meaningful(markdown).length === 1 && meaningful(markdown)[0] === paragraph &&
                paragraphNodes.length === 2 && paragraphNodes[0] === pill && paragraphNodes[1].tagName === 'SPAN' &&
                pillChildren.length === 2 && pillChildren[0].textContent === '' &&
                pillChildren[1].tagName === 'SPAN' && pillChildren[1].textContent === 'Create image') {
              pill.remove();
            }
          }
        }
        copy.querySelectorAll('button, [role="button"], script, style').forEach(control => control.remove());
        if (oracleHeadingTurnInfo(node).role === 'assistant') return readAssistantBlocks(copy);
        return copy.textContent ?? '';
      }
      return content.textContent ?? '';
    }
  `;
}
/** Build a browser-context expression that returns one DOM node per conversation turn. */
export function buildConversationTurnListExpression(rootExpression = "document") {
    const containerSelector = JSON.stringify(CONVERSATION_TURN_CONTAINER_SELECTOR);
    const fallbackSelector = JSON.stringify(CONVERSATION_TURN_SELECTOR);
    return `(() => {
    ${buildConversationTurnSemantics()}
    const root = ${rootExpression};
    const containers = Array.from(root.querySelectorAll(${containerSelector}));
    if (containers.length > 0) return containers;
    const legacy = Array.from(root.querySelectorAll(${fallbackSelector}));
    return legacy.length > 0 ? legacy : oracleHeadingTurns(root);
  })()`;
}
export function buildConversationTurnCountExpression(rootExpression = "document") {
    return `(${buildConversationTurnListExpression(rootExpression)}).length`;
}
