import { COMPOSER_MODEL_SIGNAL_SELECTOR, MENU_CONTAINER_SELECTOR, MENU_ITEM_SELECTOR, MODEL_BUTTON_SELECTOR, } from "../constants.js";
import { logDomFailure } from "../domDebug.js";
import { buildClickDispatcher } from "./domEvents.js";
import { throwIfThrottled } from "../chatgptThrottle.js";
import { delay } from "../utils.js";
const LEGACY_PRO_VERSION_WORD_TOKENS = ["5 4", "5 2", "5 1", "5 0", "gpt 5 pro"];
const LEGACY_PRO_VERSION_COMPACT_TOKENS = ["gpt54", "gpt52", "gpt51", "gpt50"];
// The model/effort picker is a composer pill that React mounts a beat after the page
// becomes interactive (~1-4s on a cold profile, e.g. cookie-sync's throwaway Chrome).
// Re-evaluate while it is still missing, up to a bounded deadline, so selection does not
// give up before the pill renders. Only "button-missing" waits; a genuine
// "option-not-found" surfaces immediately.
const MODEL_BUTTON_WAIT_MS = 8000;
const MODEL_BUTTON_POLL_MS = 250;
export async function ensureModelSelection(Runtime, desiredModel, logger, strategy = "select", options = {}) {
    const buttonWaitMs = options.buttonWaitMs ?? MODEL_BUTTON_WAIT_MS;
    const buttonPollMs = options.buttonPollMs ?? MODEL_BUTTON_POLL_MS;
    const probeDeadline = Date.now() + Math.max(0, buttonWaitMs);
    let deadline;
    let result;
    let announcedWait = false;
    for (;;) {
        if (options.implicitDefault && strategy === "select") {
            // Wait for observable selection before a default-driven switch, just as selection waits for the picker.
            const observed = await Runtime.evaluate({
                expression: buildModelSelectionExpression(desiredModel, "current"),
                awaitPromise: true,
                returnByValue: true,
            }).catch(() => null);
            const label = observed?.result?.value?.label;
            if ((typeof label !== "string" || !label.trim()) && Date.now() < probeDeadline) {
                await delay(buttonPollMs);
                continue;
            }
            if (typeof label === "string" && isNewerModelLabel(label, desiredModel)) {
                logger(`[browser] Model selection warning: no model was specified, so Oracle's default will switch ChatGPT from "${label}" to "${desiredModel}" before submission. Pass --model explicitly or --browser-model-strategy current to keep the selected model.`);
            }
        }
        deadline ??= Date.now() + Math.max(0, buttonWaitMs);
        const outcome = await Runtime.evaluate({
            expression: buildModelSelectionExpression(desiredModel, strategy),
            awaitPromise: true,
            returnByValue: true,
        });
        if (outcome.exceptionDetails) {
            // CDP reports page-JavaScript exceptions in a successful RPC response.
            // Do not relabel those exceptions as a missing model button. Retain only
            // structural exception metadata, never page text, arguments or stacks.
            const exception = outcome.exceptionDetails;
            const error = new Error("ChatGPT model selection JavaScript evaluation failed before model verification.");
            error.details = {
                stage: "model-selection",
                reason: "evaluation-exception",
                exceptionClass: exception.exception?.className ?? null,
                lineNumber: exception.lineNumber ?? null,
                columnNumber: exception.columnNumber ?? null,
            };
            throw error;
        }
        result = outcome.result?.value;
        if (!result || typeof result !== "object" || typeof result.status !== "string") {
            const error = new Error("ChatGPT model selection returned no valid result; requested model remains unverified.");
            error.details = { stage: "model-selection", reason: "invalid-evaluation-result" };
            throw error;
        }
        if (result?.status !== "button-missing" || Date.now() >= deadline) {
            break;
        }
        if (!announcedWait) {
            announcedWait = true;
            logger(`Model picker button not mounted yet; waiting up to ${Math.round(buttonWaitMs / 1000)}s for the composer pill to render.`);
        }
        await delay(buttonPollMs);
    }
    switch (result?.status) {
        case "already-selected":
        case "switched": {
            const observedLabel = result.label?.trim() || null;
            if (strategy !== "current" && observedLabel === null) {
                const error = new Error(`ChatGPT model selection did not expose a label for "${desiredModel}"; requested model remains unverified.`);
                error.details = { stage: "model-selection", reason: "selection-unverified" };
                throw error;
            }
            if (strategy !== "current" && observedLabel !== null) {
                assertResolvedModelSelection(desiredModel, observedLabel);
            }
            logger(`Model picker: ${observedLabel ?? "current model (label unavailable)"}`);
            return {
                requestedModel: desiredModel,
                // A picker target is intent, not observed UI evidence. Keep it separate from the
                // resolved label so display code cannot turn a fallback into a claimed selection.
                resolvedLabel: observedLabel,
                strategy,
                status: result.status,
                verified: strategy !== "current" && observedLabel !== null,
                source: "chatgpt-model-picker",
                capturedAt: new Date().toISOString(),
            };
        }
        case "option-not-found": {
            await logDomFailure(Runtime, logger, "model-switcher-option");
            // Check for a rate-limit notice before blaming the model name. The notice
            // is a plain dialog, so its "Got it" button reads as an available option to
            // the menu scrape — which turns "wait a few minutes" into "your model does
            // not exist", and sends the reader after the wrong bug.
            await throwIfThrottled(Runtime, { stage: "model-selection" }, logger);
            const isTemporary = result.hint?.temporaryChat ?? false;
            const available = (result.hint?.availableOptions ?? []).filter(Boolean);
            const availableHint = available.length > 0 ? ` Available: ${available.join(", ")}.` : "";
            const tempHint = isTemporary && /\bpro\b/i.test(desiredModel)
                ? " You are in Temporary Chat mode; model labels may differ there. If the current Temporary Chat already shows the desired Pro mode, retry with --browser-model-strategy current; otherwise choose an available model or turn Temporary Chat off."
                : "";
            throw new Error(`Unable to find model option matching "${desiredModel}" in the model switcher.${availableHint}${tempHint}`);
        }
        case "button-ambiguous": {
            const error = new Error("Multiple visible ChatGPT model selector candidates found; refusing to choose an arbitrary control.");
            error.details = { stage: "model-selection", reason: "button-ambiguous" };
            throw error;
        }
        case "button-missing": {
            // Always retain safe control evidence, even with verbose disabled.
            // logDomFailure snapshots conversation text, not picker structure.
            let diagnostic;
            try {
                const response = await Runtime.evaluate({
                    expression: buildModelSelectorDiagnosticExpression(),
                    returnByValue: true,
                });
                diagnostic = response.exceptionDetails
                    ? { status: "diagnostic-evaluation-failed" }
                    : response.result?.value ?? { status: "diagnostic-unavailable" };
            } catch {
                diagnostic = { status: "diagnostic-rpc-failed" };
            }
            logger(`[browser] Model selector structural diagnostic: ${JSON.stringify(diagnostic)}`);
            await throwIfThrottled(Runtime, { stage: "model-selection" }, logger);
            // Browser sessionRunner preserves error.message but replaces details,
            // and its nonverbose logger drops "Model selector" messages. Carry the
            // same safe structural snapshot through that durable error channel.
            const diagnosticMessage = serializeModelSelectorDiagnostic(diagnostic);
            const error = new Error(`Unable to locate a visible, enabled ChatGPT model selector button; requested model remains unverified. MODEL_SELECTOR_DIAGNOSTIC=${diagnosticMessage}`);
            error.details = { stage: "model-selection", reason: "button-missing", diagnostic };
            throw error;
        }
        default: {
            const error = new Error("Unexpected ChatGPT model selector result; requested model remains unverified.");
            error.details = { stage: "model-selection", reason: "unexpected-status" };
            throw error;
        }
    }
}
function isNewerModelLabel(current, target) {
    const latest = /^(?:Latest|最新|최신)$/i;
    if (latest.test(current.trim()))
        return !latest.test(target.trim());
    const version = (label) => {
        const match = label.match(/(?:^|gpt[- ]*|thinking\s+)(\d+)(?:\.(\d+))?/i);
        return match ? [Number(match[1]), Number(match[2] ?? 0)] : null;
    };
    const from = version(current);
    const to = version(target);
    return Boolean(from && to && (from[0] > to[0] || (from[0] === to[0] && from[1] > to[1])));
}
function assertResolvedModelSelection(desiredModel, resolvedLabel) {
    const desired = desiredModel.toLowerCase();
    const resolved = resolvedLabel.toLowerCase();
    const normalizedDesired = normalizeResolvedModelLabel(desired);
    const normalizedResolved = normalizeResolvedModelLabel(resolved);
    if (desired === "latest") {
        // The advanced radio is localized, but only the documented exact labels are
        // evidence of GPT-6 Astra. Do not let a generic picker result verify Latest.
        if (resolvedLabel.normalize("NFC").trim() === "Latest" ||
            resolvedLabel.normalize("NFC").trim() === "最新" ||
            resolvedLabel.normalize("NFC").trim() === "최신" ||
            resolvedLabel.normalize("NFC").trim() === "GPT-6") {
            return;
        }
        throw new Error(`Model picker selected "${resolvedLabel}" while "${desiredModel}" requires GPT-6 Astra (Latest).`);
    }
    const wantsGpt56Sol = /(?:^| )5 6(?: |$)/.test(normalizedDesired) && normalizedDesired.split(" ").includes("sol");
    if (wantsGpt56Sol) {
        const resolvedTokens = normalizedResolved.split(" ");
        if (!/(?:^| )5 6(?: |$)/.test(normalizedResolved) ||
            !resolvedTokens.includes("sol") ||
            resolvedTokens.includes("pro")) {
            throw new Error(`Model picker selected "${resolvedLabel}" while "${desiredModel}" requires GPT-5.6 Sol.`);
        }
        return;
    }
    const wantsGpt55Pro = desired === "pro" ||
        desired === "chatgpt pro" ||
        desired === "gpt-5.5-pro" ||
        desired.includes("5.5 pro") ||
        desired.includes("5-5 pro") ||
        (desired.includes("pro") && desired.includes("extended"));
    if (!wantsGpt55Pro || !resolved) {
        return;
    }
    if (!hasCurrentProSignal(resolved) ||
        hasLegacyProVersionLabel(resolved) ||
        resolved.includes("thinking")) {
        throw new Error(`Model picker selected "${resolvedLabel}" while "${desiredModel}" requires GPT-5.5 Pro. Use model "gpt-5.5" with browser thinking time for the Thinking variant.`);
    }
}
function normalizeResolvedModelLabel(value) {
    return value
        .replace(/[^a-z0-9]+/g, " ")
        .replace(/\s+/g, " ")
        .trim();
}
function hasCurrentProSignal(resolved) {
    return normalizeResolvedModelLabel(resolved).split(" ").includes("pro");
}
function hasLegacyProVersionLabel(resolved) {
    const normalized = normalizeResolvedModelLabel(resolved);
    return (LEGACY_PRO_VERSION_WORD_TOKENS.some((token) => normalized.includes(token)) ||
        LEGACY_PRO_VERSION_COMPACT_TOKENS.some((token) => resolved.includes(token)));
}
export function assertResolvedModelSelectionForTest(desiredModel, resolvedLabel) {
    assertResolvedModelSelection(desiredModel, resolvedLabel);
}
/**
 * Builds the DOM expression that runs inside the ChatGPT tab to select a model.
 * The string is evaluated inside Chrome, so keep it self-contained and well-commented.
 */
function buildModelSelectionExpression(targetModel, strategy) {
    const matchers = buildModelMatchersLiteral(targetModel);
    const composerSignalMatchers = buildComposerSignalMatchers(targetModel);
    const labelLiteral = JSON.stringify(matchers.labelTokens);
    const idLiteral = JSON.stringify(matchers.testIdTokens);
    const primaryLabelLiteral = JSON.stringify(targetModel);
    const strategyLiteral = JSON.stringify(strategy);
    const composerSignalSelectorLiteral = JSON.stringify(COMPOSER_MODEL_SIGNAL_SELECTOR);
    const composerIncludesLiteral = JSON.stringify(composerSignalMatchers.includesAny);
    const composerExcludesLiteral = JSON.stringify(composerSignalMatchers.excludesAny);
    const composerAllowBlankLiteral = JSON.stringify(composerSignalMatchers.allowBlank);
    const menuContainerLiteral = JSON.stringify(`${MENU_CONTAINER_SELECTOR}, [role="listbox"], [role="dialog"]`);
    const menuItemLiteral = JSON.stringify(`${MENU_ITEM_SELECTOR}, [role="option"], [role="radio"], [role="combobox"]`);
    return `(() => {
    ${buildClickDispatcher()}
    // Capture the selectors and matcher literals up front so the browser expression stays pure.
    const BUTTON_SELECTOR = '${MODEL_BUTTON_SELECTOR}';
    const COMPOSER_MODEL_SIGNAL_SELECTOR = ${composerSignalSelectorLiteral};
    const LABEL_TOKENS = ${labelLiteral};
    const TEST_IDS = ${idLiteral};
    const PRIMARY_LABEL = ${primaryLabelLiteral};
    const MODEL_STRATEGY = ${strategyLiteral};
    const COMPOSER_SIGNAL_INCLUDES = ${composerIncludesLiteral};
    const COMPOSER_SIGNAL_EXCLUDES = ${composerExcludesLiteral};
    const COMPOSER_SIGNAL_ALLOW_BLANK = ${composerAllowBlankLiteral};
    const INITIAL_WAIT_MS = 150;
    const REOPEN_INTERVAL_MS = 400;
    const MAX_WAIT_MS = 20000;
    const SETTLE_WAIT_MS = 1500;
    const normalizeText = (value) => {
      if (!value) {
        return '';
      }
      return value
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, ' ')
        .replace(/\\s+/g, ' ')
        .trim();
    };
    const hasToken = (value, token) => normalizeText(value).split(' ').includes(token);
    // Normalize every candidate token to keep fuzzy matching deterministic.
    const normalizedTarget = normalizeText(PRIMARY_LABEL);
    // "Latest" (GPT-6 since 2026-09) is a radio in the advanced view whose composer pill reads
    // "6 Pro" / "6 High"…, while GPT-5.6 Sol's reads "5.6 Pro". Declared up front: getResolvedLabel
    // runs on the picker-less path before the selection helpers below are initialized.
    const targetIsLatest = normalizedTarget === 'latest';
    // ChatGPT localizes the Latest radio itself (for example, Japanese "最新") but
    // keeps the GPT-6 composer pill numeric. Keep this allow-list exact so GPT-5.6
    // Sol or an arbitrary localized menu row can never satisfy a Latest request.
    const isLatestModelLabel = (value) => {
      const label = String(value ?? '').normalize('NFC').trim();
      // Session41 displays the same exact GPT-6 model as "GPT-6", while the
      // configured requested target remains Latest. This proves model version;
      // requested Pro effort is independently verified by ensureThinkingTime.
      return label === 'Latest' || label === '最新' || label === '최신' || label === 'GPT-6';
    };
    const normalizedTokens = Array.from(new Set([normalizedTarget, ...LABEL_TOKENS]))
      .map((token) => normalizeText(token))
      .filter(Boolean);
    const targetWords = normalizedTarget.split(' ').filter(Boolean);
    const desiredVersion = normalizedTarget.includes('5 6')
      ? '5-6'
      : normalizedTarget.includes('5 5')
        ? '5-5'
        : normalizedTarget.includes('5 4')
          ? '5-4'
        : normalizedTarget.includes('5 3')
          ? '5-3'
        : normalizedTarget.includes('5 2')
        ? '5-2'
        : normalizedTarget.includes('5 1')
          ? '5-1'
          : normalizedTarget.includes('5 0')
            ? '5-0'
          : null;
    const desiredModelVariant = normalizedTarget.includes(' sol') ? 'sol' : null;
    const wantsPro = normalizedTarget.includes(' pro') || normalizedTarget.endsWith(' pro') || normalizedTokens.includes('pro');
    const wantsInstant = normalizedTarget.includes('instant');
    const wantsThinking = normalizedTarget.includes('thinking');
    const targetUsesCurrentGpt55Alias =
      desiredVersion === '5-5' || normalizedTarget === 'pro' || normalizedTarget === 'chatgpt pro';
    const labelHasProWord = (label) => label === 'pro' || label.startsWith('pro ') || label.includes(' pro ') || label.endsWith(' pro');
    const legacyProVersionTokens = ['5 4', '5 2', '5 1', '5 0', 'gpt54', 'gpt52', 'gpt51', 'gpt50', 'gpt 5 pro'];
    const labelHasLegacyProVersion = (value) => {
      const label = normalizeText(value);
      return legacyProVersionTokens.some((token) => label.includes(token));
    };
    const isTargetGpt55VisibleAlias = (value) => {
      if (!targetUsesCurrentGpt55Alias) return false;
      const label = normalizeText(value);
      if (wantsPro) {
        // ChatGPT UI as of 2026-05: the picker shows just "Pro" (no longer "Pro Extended").
        // "Extended" is now a thinking-effort sub-setting, not part of the model label.
        // Accept bare "pro", legacy "pro extended", and reversed "extended pro" (composer pill).
        return (label === 'pro' || label === 'pro extended' || label === 'extended pro') && !label.includes('thinking');
      }
      if (wantsThinking) {
        // ChatGPT UI as of 2026-05: the picker shows "Thinking" or "Thinking · Extended"
        // (normalized to "thinking extended"). Accept both old "thinking heavy" and new labels.
        return (label === 'thinking' || label === 'thinking extended' || label === 'thinking heavy') && !label.includes('pro');
      }
      return false;
    };
    const hasProComposerPill = () => Boolean(
      Array.from(document.querySelectorAll('button.__composer-pill, button[aria-label]'))
        .filter((node) => {
          const label = normalizeText(node.getAttribute?.('aria-label') ?? '');
          return node.matches?.('button.__composer-pill') || label.includes('click to remove');
        })
        .some((node) => {
          const label = normalizeText(
            (node.getAttribute?.('aria-label') ?? '') + ' ' + (node.textContent ?? '')
          );
          return hasToken(label, 'pro') && !hasToken(label, 'thinking');
        })
    );

    const isVisibleElement = (node) => {
      if (typeof HTMLElement === 'undefined') return false;
      if (!(node instanceof HTMLElement)) return false;
      try {
        const rect = node.getBoundingClientRect?.();
        const style = window.getComputedStyle?.(node);
        if (!rect) return true;
        return (
          rect.width > 0 &&
          rect.height > 0 &&
          style?.display !== 'none' &&
          style?.visibility !== 'hidden'
        );
      } catch {
        // DOM test doubles and older Chromium nodes can omit one of these APIs.
        // Treat them as visible; real hidden picker rows still report a zero rect
        // or display:none and are filtered above.
        return true;
      }
    };
    const looksLikeModelPill = (node) => {
      if (!(node instanceof HTMLElement)) return false;
      // Session40's real picker is an unclassed form menu button. Its accessible
      // label identifies both ChatGPT and model; arbitrary form menus do not.
      const accessibleLabel = normalizeText(node.getAttribute('aria-label') ?? '');
      const isFormModelButton = Boolean(node.closest?.('form')) &&
        node.getAttribute('aria-haspopup') === 'menu' &&
        hasToken(accessibleLabel, 'chatgpt') && hasToken(accessibleLabel, 'model');
      if (!node.matches('button.__composer-pill') && !isFormModelButton) return false;
      if (!isVisibleElement(node)) return false;
      const label = normalizeText(
        (node.textContent ?? '') + ' ' + (node.getAttribute('aria-label') ?? '') + ' ' + (node.getAttribute('title') ?? '')
      );
      if (!label) return false;
      if (label.includes('click to remove')) return false;
      const modelTokens = [
        'chatgpt',
        'gpt',
        'instant',
        'thinking',
        'pro',
        'extended',
        'standard',
        'medium',
        'high',
        'heavy',
        'light',
      ];
      // thinkingTime.js already recognizes ChatGPT's textContent-concatenated
      // GPT-6 pill (for example "6Pro"). Finding a trigger is not model proof;
      // the exact requested-model checks below still govern acceptance.
      return modelTokens.some((token) => hasToken(label, token)) ||
        /^(?:chatgpt |gpt )?6(?:pro|high|medium|thinking|standard|extended)(?: |$)/.test(label);
    };
    let modelButtonAmbiguous = false;
    const usableModelButton = (node) =>
      isVisibleElement(node) &&
      !node.disabled &&
      node.getAttribute?.('aria-disabled') !== 'true';
    const findModelButton = () => {
      modelButtonAmbiguous = false;
      // Prefer the dedicated identity over generic menu pills, and scan all
      // matches: a stale hidden picker must not shadow the mounted control.
      const explicit = Array.from(document.querySelectorAll(BUTTON_SELECTOR)).filter(usableModelButton);
      const dedicated = explicit.filter((node) =>
        node.getAttribute?.('data-testid') === 'model-switcher-dropdown-button');
      const candidates = dedicated.length ? dedicated : Array.from(new Set([
        ...explicit.filter(looksLikeModelPill),
        ...Array.from(document.querySelectorAll('button.__composer-pill')).filter((node) =>
          usableModelButton(node) && looksLikeModelPill(node)),
        ...Array.from(document.querySelectorAll('form button[aria-haspopup="menu"]')).filter((node) =>
          usableModelButton(node) && looksLikeModelPill(node)),
      ]));
      modelButtonAmbiguous = candidates.length > 1;
      return candidates.length === 1 ? candidates[0] : null;
    };
    let clickedModelButton = null;
    let modelPopupBaseline = null;
    let ownedModelPopup = null;
    const getOwnedModelPopup = () => {
      if (!clickedModelButton) return null;
      if (ownedModelPopup) return isVisibleElement(ownedModelPopup) ? ownedModelPopup : null;
      const controls = (clickedModelButton.getAttribute?.('aria-controls') ?? '').split(/\\s+/).filter(Boolean);
      const controlled = controls.map((id) => document.getElementById?.(id)).filter((node) => node && isVisibleElement(node));
      if (controlled.length === 1) {
        ownedModelPopup = controlled[0];
        return ownedModelPopup;
      }
      const newlyVisible = Array.from(document.querySelectorAll(${menuContainerLiteral})).filter((node) =>
        isVisibleElement(node) && !modelPopupBaseline?.has(node));
      const roots = newlyVisible.filter((node) => !newlyVisible.some((other) => other !== node && other.contains?.(node)));
      if (roots.length !== 1) return null;
      ownedModelPopup = roots[0];
      return ownedModelPopup;
    };

    const closeMenu = () => {
      const dialogCloseButton = document.querySelector(
        '[role="dialog"] [data-testid="close-button"]',
      );
      if (dialogCloseButton) {
        try {
          if (dispatchClickSequence(dialogCloseButton)) return;
        } catch {}
      }
      const button = findModelButton();
      if (!button) return;
      try {
        if (dispatchClickSequence(button)) {
          lastPointerClick = performance.now();
          return;
        }
      } catch {}
      try {
        document.dispatchEvent(
          new KeyboardEvent('keydown', {
            key: 'Escape',
            code: 'Escape',
            keyCode: 27,
            which: 27,
            bubbles: true,
          }),
        );
      } catch {}
    };

    const getButtonLabel = () => (findModelButton()?.textContent ?? '').trim();
    // With the picker closed the only evidence for "Latest" is the composer pill, so a version-less
    // "latest" target must be decided on it: the blank composer signal would otherwise pass as
    // "already selected" while GPT-5.6 Sol is active. Defined here, before getResolvedLabel, because
    // the "current" strategy resolves the label before the selection helpers further down exist.
    const latestButtonSelected = () => {
      const label = normalizeText(getButtonLabel());
      return /^(chatgpt |gpt )?6(?![0-9 .]*[0-9])/.test(label) && !/(^| )5 6/.test(label);
    };
    const getComposerModelLabel = () =>
      (document.querySelector(COMPOSER_MODEL_SIGNAL_SELECTOR)?.textContent ?? '').trim();
    const readComposerModelSignal = () => normalizeText(getComposerModelLabel());
    const isEffortOnlyLabel = (label) =>
      label === 'medium' ||
      label === 'high' ||
      label === 'extra high' ||
      label === 'extended' ||
      label === 'standard' ||
      label === 'heavy' ||
      label === 'light';
    const formatModelOptionLabel = (label) => {
      const normalized = normalizeText(label ?? '');
      if (normalized === '5 6' || normalized === 'gpt 5 6') return 'GPT-5.6';
      if (normalized === '5 5' || normalized === 'gpt 5 5') return 'GPT-5.5';
      if (normalized === '5 4' || normalized === 'gpt 5 4') return 'GPT-5.4';
      if (normalized === '5 3' || normalized === 'gpt 5 3') return 'GPT-5.3';
      if (normalized === '5 2' || normalized === 'gpt 5 2') return 'GPT-5.2';
      if (normalized === '5 1' || normalized === 'gpt 5 1') return 'GPT-5.1';
      if (normalized === '5 0' || normalized === 'gpt 5 0') return 'GPT-5.0';
      return label || '';
    };
    const withProPillSignal = (label) => {
      const resolved = label || '';
      if (!wantsPro || !hasProComposerPill()) return resolved;
      const normalized = normalizeText(resolved);
      if (!normalized) return resolved;
      if (normalized.includes('thinking')) return 'Pro';
      if (normalized.includes('pro')) return resolved;
      return resolved + ' + Pro';
    };
    const isThinkingEffortLabel = (label) =>
      label === 'extended' ||
      label === 'standard' ||
      label === 'heavy' ||
      label === 'light' ||
      label === 'medium' ||
      label === 'high' ||
      label === 'extra high';
    const isNonProIntelligenceThinkingLabel = (label) =>
      label === 'medium' || label === 'high' || label === 'extra high';
    const scoreNonProGpt55ThinkingLabel = (label) => {
      if (label === 'extra high') return 1400;
      if (label === 'high') return 1200;
      return 1000;
    };
    const versionFromLabel = (label) => {
      const match = normalizeText(label).match(/(?:^| )(?:gpt )?(\\d+) (\\d+)(?: |$)/);
      return match ? match[1] + '-' + match[2] : null;
    };
    const versionFromTestId = (testid) => {
      const normalized = normalizeText(testid);
      if (normalized.includes('5 6') || normalized.includes('gpt56')) return '5-6';
      if (normalized.includes('5 5') || normalized.includes('gpt55')) return '5-5';
      if (normalized.includes('5 4') || normalized.includes('gpt54')) return '5-4';
      if (normalized.includes('5 3') || normalized.includes('gpt53')) return '5-3';
      if (normalized.includes('5 2') || normalized.includes('gpt52')) return '5-2';
      if (normalized.includes('5 1') || normalized.includes('gpt51')) return '5-1';
      if (normalized.includes('5 0') || normalized.includes('gpt50')) return '5-0';
      return null;
    };
    const getConfigurationDialog = () => document.querySelector('[role="dialog"]');
    const getConfiguredVersionLabel = () =>
      (getConfigurationDialog()
        ?.querySelector?.('[role="combobox"][aria-labelledby="model-selection-label"]')
        ?.textContent ?? '').trim();
    const getConfiguredVariantLabel = () => {
      const label =
        (getConfigurationDialog()
          ?.querySelector?.('[aria-label="Model options"] [role="radio"][aria-checked="true"]')
          ?.textContent ?? '').trim();
      const normalized = normalizeText(label);
      if (normalized.startsWith('instant')) return 'Instant';
      if (normalized.startsWith('thinking')) return 'Thinking';
      if (normalized.startsWith('pro')) return 'Pro';
      return label;
    };
    const configuredSelectionMatchesTarget = () => {
      const configuredVersionLabel = normalizeText(getConfiguredVersionLabel());
      const configuredVersion = versionFromLabel(configuredVersionLabel);
      if (!configuredVersion || configuredVersion !== desiredVersion) return false;
      const configuredVariant = normalizeText(getConfiguredVariantLabel());
      if (
        desiredModelVariant &&
        !configuredVersionLabel.includes(desiredModelVariant) &&
        !configuredVariant.includes(desiredModelVariant)
      ) return false;
      if (wantsPro) return labelHasProWord(configuredVariant);
      if (wantsInstant) return configuredVariant.includes('instant');
      if (wantsThinking) {
        return configuredVariant.includes('thinking') && !labelHasProWord(configuredVariant);
      }
      return true;
    };

    // ---------- Unified Intelligence picker: Advanced -> Model submenu ----------
    // The slider picker exposes only the current effort in the composer pill. Its
    // model versions live behind Advanced -> Model, next to an identically-shaped
    // Effort submenu. Identify the Model opener positively so a Pro effort label can
    // never be mistaken for a model target.
    const ADVANCED_VIEW_SELECTOR = '[data-testid="composer-model-picker-slider-advanced-view"]';
    const INTELLIGENCE_PICKER_SELECTOR = '[data-testid="composer-intelligence-picker-content"]';
    const SUBMENU_OPENER_SELECTOR = '[role="menuitem"][aria-haspopup="menu"]';
    const ADVANCED_WORDS = ['advanced', 'erweitert', '高级', '고급', 'avanzado', 'avancado', 'avance'];
    const MODEL_WORDS = ['model', 'modell', '模型', '모델', 'modelo', 'modello', 'modele'];
    const EFFORT_WORDS = [
      'effort', 'aufwand', '强度', '努力', '추론 수준',
      'esfuerzo', 'esforco', 'sforzo', 'inspanning', 'wysilek',
    ];
    const pickerNodeLabel = (node) => {
      const value =
        (node?.getAttribute?.('aria-label') ?? '') + ' ' + (node?.textContent ?? '');
      try {
        return value
          .toLowerCase()
          .normalize('NFD')
          .replace(/[\u0300-\u036f]/g, '')
          .normalize('NFC')
          .replace(/\\s+/g, ' ')
          .trim();
      } catch {
        return value.toLowerCase().replace(/\\s+/g, ' ').trim();
      }
    };
    const containsPickerWord = (label, words) =>
      words.some((word) => label.includes(word));
    const isSubmenuOpener = (node) =>
      node?.getAttribute?.('role') === 'menuitem' &&
      node?.getAttribute?.('aria-haspopup') === 'menu';
    const isUnifiedPickerMenu = (menu) =>
      Boolean(
        menu?.getAttribute?.('data-testid') === 'composer-intelligence-picker-content' ||
          menu?.querySelector?.(INTELLIGENCE_PICKER_SELECTOR) ||
          menu?.querySelector?.(ADVANCED_VIEW_SELECTOR),
      );
    const findUnifiedPickerMenu = () =>
      Array.from(document.querySelectorAll(${menuContainerLiteral})).find(isUnifiedPickerMenu) ??
      null;
    const findAdvancedToggle = (menu) => {
      for (const item of (menu || document).querySelectorAll('[role="menuitem"]')) {
        if (!isVisibleElement(item)) continue;
        if (containsPickerWord(pickerNodeLabel(item), ADVANCED_WORDS)) return item;
      }
      return null;
    };
    const findModelSubmenuOpener = (menu) => {
      const scope = menu?.querySelector?.(ADVANCED_VIEW_SELECTOR) || menu || document;
      for (const item of scope.querySelectorAll(SUBMENU_OPENER_SELECTOR)) {
        if (!isVisibleElement(item) || !isSubmenuOpener(item)) continue;
        const label = pickerNodeLabel(item);
        if (
          containsPickerWord(label, MODEL_WORDS) &&
          !containsPickerWord(label, EFFORT_WORDS)
        ) {
          return item;
        }
      }
      return null;
    };
    const advancedModelSignalMatchesTarget = (menu = null) => {
      const parentMenu = menu || findUnifiedPickerMenu();
      const opener = findModelSubmenuOpener(parentMenu);
      if (!opener) return false;
      const label = normalizeText(pickerNodeLabel(opener));
      const version = versionFromLabel(label);
      if (desiredVersion && version !== desiredVersion) return false;
      if (desiredModelVariant && !label.split(' ').includes(desiredModelVariant)) return false;
      if (wantsPro) return labelHasProWord(label) && !labelHasLegacyProVersion(label);
      if (wantsInstant) return label.includes('instant');
      if (wantsThinking) return Boolean(desiredVersion) && !labelHasProWord(label);
      if (desiredVersion) return true;
      // A version-less target ("Latest") must match the radio that is actually checked in the
      // advanced view: the opener's text lists every radio label, so a substring test would
      // report "Latest" as selected while GPT-5.6 Sol is the checked model.
      const checkedAdvancedRadio = findCheckedAdvancedModelRadio(parentMenu);
      if (checkedAdvancedRadio) {
        const checkedLabel = checkedAdvancedRadio.textContent ?? '';
        return targetIsLatest
          ? isLatestModelLabel(checkedLabel)
          : normalizedTokens.some((token) => token && normalizeText(checkedLabel) === token);
      }
      return normalizedTokens.some((token) => token && label.includes(token));
    };
    const findCheckedAdvancedModelRadio = (menu = null) => {
      const scope = menu || findUnifiedPickerMenu() || document;
      const advanced = scope?.querySelector?.(
          '[data-testid="composer-model-picker-slider-advanced-view"] [role="menuitemradio"][aria-checked="true"]',
        ) ?? null;
      if (advanced) return advanced;
      // The new picker uses ordinary menu/listbox model rows without the old
      // advanced-view wrapper. Require positive checked-state and exact model
      // identity; the nearby Medium effort row cannot verify a model.
      const ownedPopup = getOwnedModelPopup();
      if (!ownedPopup) return null;
      const checked = Array.from(ownedPopup.querySelectorAll?.(
        '[role="menuitemradio"][aria-checked="true"], [role="radio"][aria-checked="true"], [role="option"][aria-selected="true"]',
      ) ?? []).filter((node) =>
        isVisibleElement(node) &&
        Boolean(node.closest?.('[role="menu"], [role="listbox"], [role="dialog"]')) &&
        isLatestModelLabel(node.textContent ?? ''));
      return checked.length === 1 ? checked[0] : null;
    };
    const getAdvancedModelLabel = () => {
      const opener = findModelSubmenuOpener(findUnifiedPickerMenu());
      if (!opener) return '';
      const raw = (opener.textContent ?? '').trim();
      const normalized = normalizeText(pickerNodeLabel(opener));
      const version = versionFromLabel(normalized);
      if (!version) {
        const checkedAdvancedRadio = findCheckedAdvancedModelRadio(findUnifiedPickerMenu());
        const checkedLabel = (checkedAdvancedRadio?.textContent ?? '').trim();
        return checkedLabel || raw;
      }
      const [major, minor] = version.split('-');
      const suffix = normalized.split(' ').includes('sol') ? ' Sol' : '';
      return 'GPT-' + major + '.' + minor + suffix;
    };
    const isLegacyFlatIntelligencePickerOpen = () => {
      const menu = findUnifiedPickerMenu();
      return Boolean(
        menu &&
          !menu.querySelector?.(ADVANCED_VIEW_SELECTOR) &&
          !findAdvancedToggle(menu),
      );
    };
    const getResolvedLabel = (observedOptionLabel = '') => {
      if (targetIsLatest) {
        const checkedAdvancedRadio = findCheckedAdvancedModelRadio();
        if (checkedAdvancedRadio) return (checkedAdvancedRadio.textContent ?? '').trim();
        // Picker closed: the pill ("6 Pro") is the evidence; report the radio's name so callers
        // can compare against the requested target instead of the tier-suffixed pill text.
        if (latestButtonSelected()) return 'Latest';
        // Only supplied after a checked option or settled selection was verified,
        // never directly from the clicked target's intent.
        if (isLatestModelLabel(observedOptionLabel)) return observedOptionLabel.trim();
        const currentButtonLabel = getButtonLabel();
        if (currentButtonLabel) return currentButtonLabel;
        // No picker button at all (e.g. the "current" strategy on a page that hides it): fall back
        // to the generic composer/observed label resolution below.
      }
      if (configuredSelectionMatchesTarget()) {
        const variant = getConfiguredVariantLabel();
        const version = formatModelOptionLabel(getConfiguredVersionLabel());
        if (desiredModelVariant === 'sol' && labelHasProWord(normalizeText(variant))) return version;
        return [variant, version].filter(Boolean).join(' ');
      }
      if (advancedModelSignalMatchesTarget()) {
        return getAdvancedModelLabel();
      }
      const composerLabel = getComposerModelLabel();
      const normalizedComposerLabel = normalizeText(composerLabel);
      if (
        desiredVersion &&
        desiredModelVariant &&
        versionFromLabel(normalizedComposerLabel) === desiredVersion &&
        normalizedComposerLabel.split(' ').includes(desiredModelVariant)
      ) {
        if (desiredModelVariant === 'sol' && hasProComposerPill()) return PRIMARY_LABEL;
        return withProPillSignal(composerLabel);
      }
      const buttonLabel = getButtonLabel();
      const normalizedButton = normalizeText(buttonLabel);
      if (
        desiredVersion &&
        desiredModelVariant &&
        versionFromLabel(normalizedButton) === desiredVersion &&
        normalizedButton.split(' ').includes(desiredModelVariant)
      ) {
        if (desiredModelVariant === 'sol' && hasProComposerPill()) return PRIMARY_LABEL;
        return withProPillSignal(buttonLabel);
      }
      const observedLabel = (label) => {
        const formatted = formatModelOptionLabel(label);
        return formatted && !isEffortOnlyLabel(normalizeText(formatted))
          ? withProPillSignal(formatted)
          : '';
      };
      return (
        observedLabel(observedOptionLabel) ||
        observedLabel(composerLabel) ||
        observedLabel(buttonLabel) ||
        (wantsPro && hasProComposerPill() ? 'Pro' : '')
      );
    };
    if (MODEL_STRATEGY === 'current') {
      const currentLabel = getResolvedLabel('') || null;
      return {
        status: 'already-selected',
        label: currentLabel,
      };
    }

    const button = findModelButton();
    if (!button) {
      return { status: modelButtonAmbiguous ? 'button-ambiguous' : 'button-missing' };
    }
    const buttonMatchesTarget = () => {
      if (configuredSelectionMatchesTarget()) return true;
      const normalizedLabel = normalizeText(getButtonLabel());
      if (!normalizedLabel) return false;
      if (wantsThinking && !wantsPro && hasProComposerPill()) return false;
      if (isTargetGpt55VisibleAlias(normalizedLabel)) return true;
      if (
        wantsThinking &&
        desiredVersion === '5-5' &&
        !hasProComposerPill() &&
        isThinkingEffortLabel(normalizedLabel) &&
        (isTargetGpt55VisibleAlias(readComposerModelSignal()) ||
          (isNonProIntelligenceThinkingLabel(normalizedLabel) &&
            isLegacyFlatIntelligencePickerOpen()))
      ) {
        return true;
      }
      if (
        wantsPro &&
        hasProComposerPill() &&
        (normalizedLabel === 'chatgpt' ||
          normalizedLabel === 'extended' ||
          normalizedLabel === 'standard' ||
          normalizedLabel === 'heavy' ||
          normalizedLabel === 'light')
      ) {
        return true;
      }
      if (desiredVersion) {
        if (desiredVersion === '5-6' && !normalizedLabel.includes('5 6')) return false;
        if (desiredVersion === '5-5' && !normalizedLabel.includes('5 5')) return false;
        if (desiredVersion === '5-4' && !normalizedLabel.includes('5 4')) return false;
        if (desiredVersion === '5-3' && !normalizedLabel.includes('5 3')) return false;
        if (desiredVersion === '5-2' && !normalizedLabel.includes('5 2')) return false;
        if (desiredVersion === '5-1' && !normalizedLabel.includes('5 1')) return false;
        if (desiredVersion === '5-0' && !normalizedLabel.includes('5 0')) return false;
      }
      if (desiredModelVariant && !normalizedLabel.includes(desiredModelVariant)) return false;
      if (wantsPro && labelHasLegacyProVersion(normalizedLabel)) return false;
      if (wantsPro && !labelHasProWord(normalizedLabel)) return false;
      if (wantsInstant && !normalizedLabel.includes('instant')) return false;
      if (
        wantsThinking &&
        desiredVersion &&
        versionFromLabel(normalizedLabel) === desiredVersion &&
        !labelHasProWord(normalizedLabel)
      ) {
        return true;
      }
      if (wantsThinking && !normalizedLabel.includes('thinking')) return false;
      // Also reject if button has variants we DON'T want
      if (
        !wantsPro &&
        normalizedLabel.includes(' pro') &&
        !(desiredModelVariant === 'sol' && hasProComposerPill())
      ) return false;
      if (!wantsInstant && normalizedLabel.includes('instant')) return false;
      if (!wantsThinking && normalizedLabel.includes('thinking')) return false;
      return true;
    };
    const buttonHasGenericLabel = () => {
      const normalizedLabel = normalizeText(getButtonLabel());
      return !normalizedLabel || normalizedLabel === 'chatgpt';
    };
    const composerSignalMatchesTarget = () => {
      const signal = readComposerModelSignal();
      if (!signal) {
        return COMPOSER_SIGNAL_ALLOW_BLANK;
      }
      if (wantsPro && labelHasLegacyProVersion(signal)) {
        return false;
      }
      if (COMPOSER_SIGNAL_EXCLUDES.some((token) =>
        token &&
        signal.includes(token) &&
        !(token === 'pro' && desiredModelVariant === 'sol' && hasProComposerPill())
      )) {
        return false;
      }
      if (COMPOSER_SIGNAL_INCLUDES.length === 0) {
        return true;
      }
      return COMPOSER_SIGNAL_INCLUDES.some((token) => token && signal.includes(token));
    };
    const activeSelectionMatchesTarget = () => {
      if (targetIsLatest) {
        const checkedAdvancedRadio = findCheckedAdvancedModelRadio();
        if (checkedAdvancedRadio) {
          return isLatestModelLabel(checkedAdvancedRadio.textContent ?? '');
        }
        return latestButtonSelected();
      }
      if (advancedModelSignalMatchesTarget()) {
        return true;
      }
      if (buttonMatchesTarget()) {
        return true;
      }
      if (!buttonHasGenericLabel()) {
        return false;
      }
      return composerSignalMatchesTarget();
    };
    const selectionStateChanged = (previousButtonLabel, previousComposerSignal) => {
      const currentButtonLabel = normalizeText(getButtonLabel());
      const currentComposerSignal = readComposerModelSignal();
      if (
        currentButtonLabel &&
        currentButtonLabel !== previousButtonLabel &&
        !buttonHasGenericLabel()
      ) {
        return true;
      }
      return currentComposerSignal !== previousComposerSignal;
    };

    if (activeSelectionMatchesTarget()) {
      return { status: 'already-selected', label: getResolvedLabel() };
    }

    let lastPointerClick = 0;
    const pointerClick = () => {
      modelPopupBaseline = new Set(Array.from(document.querySelectorAll(${menuContainerLiteral})).filter(isVisibleElement));
      ownedModelPopup = null;
      if (dispatchClickSequence(button)) {
        clickedModelButton = button;
        lastPointerClick = performance.now();
      }
    };

    const getOptionLabel = (node) => node?.textContent?.trim() ?? '';
    const isDetachedProEffortMenu = (menu) => {
      const text = normalizeText(menu?.textContent ?? '');
      return (
        menu?.getAttribute?.('data-testid') !== 'composer-intelligence-picker-content' &&
        text.includes('pro standard') &&
        text.includes('pro extended')
      );
    };
    const isNestedEffortControl = (node, menu) =>
      node instanceof HTMLElement &&
      (node.getAttribute('data-model-picker-thinking-effort-action') === 'true' ||
        node.getAttribute('data-composer-intelligence-pro-effort-action') === 'true' ||
        Boolean(node.closest('[data-model-picker-thinking-effort-action="true"]')) ||
        Boolean(node.closest('[data-composer-intelligence-pro-effort-action="true"]')) ||
        (isUnifiedPickerMenu(menu) && isSubmenuOpener(node) &&
          containsPickerWord(pickerNodeLabel(node), EFFORT_WORDS)) ||
        isDetachedProEffortMenu(menu));
    const optionIsSelected = (node) => {
      if (!(node instanceof HTMLElement)) {
        return false;
      }
      const ariaChecked = node.getAttribute('aria-checked');
      const ariaSelected = node.getAttribute('aria-selected');
      const ariaCurrent = node.getAttribute('aria-current');
      const dataSelected = node.getAttribute('data-selected');
      const dataState = (node.getAttribute('data-state') ?? '').toLowerCase();
      const selectedStates = ['checked', 'selected', 'on', 'true'];
      if (ariaChecked === 'true' || ariaSelected === 'true' || ariaCurrent === 'true') {
        return true;
      }
      if (dataSelected === 'true' || selectedStates.includes(dataState)) {
        return true;
      }
      return false;
    };

    const scoreOption = (normalizedText, testid, node) => {
      // Assign a score to every node so we can pick the most likely match without brittle equality checks.
      // Latest is localized in the advanced radio list. Match the documented labels
      // exactly instead of falling through to generic scoring, which could select Sol.
      if (targetIsLatest) {
        if (String(node?.textContent ?? '').normalize('NFC').trim() === 'GPT-6') {
          const owner = getOwnedModelPopup();
          if (!owner || !owner.contains?.(node)) return 0;
        }
        return isLatestModelLabel(node?.textContent ?? '') ? 2000 : 0;
      }
      if (!normalizedText && !testid) {
        return 0;
      }
      let score = 0;
      const normalizedTestId = (testid ?? '').toLowerCase();
      const candidateTextVersion = versionFromLabel(normalizedText);
      const candidateIsVersionSubmenuTrigger = Boolean(
        candidateTextVersion && node?.getAttribute?.('aria-haspopup') === 'menu'
      );
      const candidateIsVersionCombobox =
        node?.getAttribute?.('role') === 'combobox' &&
        node?.getAttribute?.('aria-labelledby') === 'model-selection-label';
      if (candidateIsVersionCombobox && candidateTextVersion === desiredVersion) {
        return 0;
      }
      const candidateOpensConfiguration =
        Boolean(desiredVersion) &&
        desiredVersion !== '5-5' &&
        (normalizedTestId === 'model-configure-modal' ||
          (candidateIsVersionCombobox && node?.getAttribute?.('aria-expanded') !== 'true'));
      const candidateIsConfiguredVersionNavigation =
        Boolean(getConfigurationDialog()) &&
        candidateTextVersion === desiredVersion &&
        (node?.getAttribute?.('role') === 'option' ||
          node?.getAttribute?.('role') === 'menuitemradio');
      if (
        desiredModelVariant &&
        !normalizedText.includes(desiredModelVariant) &&
        !candidateIsVersionSubmenuTrigger &&
        !candidateOpensConfiguration &&
        !candidateIsConfiguredVersionNavigation
      ) {
        return 0;
      }
      if (candidateOpensConfiguration) {
        return 2000;
      }
      let exactTestIdMatch = false;
      if (normalizedTestId) {
        if (desiredVersion) {
          // data-testid strings have been observed with both dotted and dashed versions (e.g. gpt-5.2-pro vs gpt-5-2-pro).
          const has56 =
            normalizedTestId.includes('5-6') ||
            normalizedTestId.includes('5.6') ||
            normalizedTestId.includes('gpt-5-6') ||
            normalizedTestId.includes('gpt-5.6') ||
            normalizedTestId.includes('gpt56');
          const has52 =
            normalizedTestId.includes('5-2') ||
            normalizedTestId.includes('5.2') ||
            normalizedTestId.includes('gpt-5-2') ||
            normalizedTestId.includes('gpt-5.2') ||
            normalizedTestId.includes('gpt52');
          const has55 =
            normalizedTestId.includes('5-5') ||
            normalizedTestId.includes('5.5') ||
            normalizedTestId.includes('gpt-5-5') ||
            normalizedTestId.includes('gpt-5.5') ||
            normalizedTestId.includes('gpt55');
          const has54 =
            normalizedTestId.includes('5-4') ||
            normalizedTestId.includes('5.4') ||
            normalizedTestId.includes('gpt-5-4') ||
            normalizedTestId.includes('gpt-5.4') ||
            normalizedTestId.includes('gpt54');
          const has53 =
            normalizedTestId.includes('5-3') ||
            normalizedTestId.includes('5.3') ||
            normalizedTestId.includes('gpt-5-3') ||
            normalizedTestId.includes('gpt-5.3') ||
            normalizedTestId.includes('gpt53');
          const has51 =
            normalizedTestId.includes('5-1') ||
            normalizedTestId.includes('5.1') ||
            normalizedTestId.includes('gpt-5-1') ||
            normalizedTestId.includes('gpt-5.1') ||
            normalizedTestId.includes('gpt51');
          const has50 =
            normalizedTestId.includes('5-0') ||
            normalizedTestId.includes('5.0') ||
            normalizedTestId.includes('gpt-5-0') ||
            normalizedTestId.includes('gpt-5.0') ||
            normalizedTestId.includes('gpt50');
          const candidateVersion = has56
            ? '5-6'
            : has55
              ? '5-5'
            : has54
              ? '5-4'
              : has53
                ? '5-3'
                : has52
                  ? '5-2'
                  : has51
                    ? '5-1'
                    : has50
                      ? '5-0'
                      : null;
          // If a candidate advertises a different version, ignore it entirely.
          if (candidateVersion && candidateVersion !== desiredVersion) {
            return 0;
          }
          // When targeting an explicit version, avoid selecting submenu wrappers that can contain legacy models.
          if (normalizedTestId.includes('submenu') && candidateVersion === null) {
            return 0;
          }
        }
        // Exact testid matches take priority over substring matches
        const exactMatch = TEST_IDS.find((id) => id && normalizedTestId === id);
        if (exactMatch) {
          exactTestIdMatch = true;
          score += 1500;
          if (exactMatch.startsWith('model-switcher-')) score += 200;
        } else {
          const matches = TEST_IDS.filter((id) => id && normalizedTestId.includes(id));
          if (matches.length > 0) {
            // Prefer the most specific match (longest token) instead of treating any hit as equal.
            // This prevents generic tokens (e.g. "pro") from outweighing version-specific targets.
            const best = matches.reduce((acc, token) => (token.length > acc.length ? token : acc), '');
            score += 200 + Math.min(900, best.length * 25);
            if (best.startsWith('model-switcher-')) score += 120;
            if (best.includes('gpt-')) score += 60;
          }
        }
      }
      const candidateGpt55VisibleAlias = isTargetGpt55VisibleAlias(normalizedText);
      const candidateIsNonProThinkingEffort =
        isNonProIntelligenceThinkingLabel(normalizedText) && !normalizedTestId.includes('pro');
      const hasActiveProPill = hasProComposerPill();
      const candidateIsNonProGpt55Thinking =
        wantsThinking && desiredVersion === '5-5' && candidateIsNonProThinkingEffort;
      const candidateClearsProForThinking =
        wantsThinking && !wantsPro && hasActiveProPill && candidateIsNonProThinkingEffort;
      const candidateOpensVersionSubmenu =
        (desiredVersion === '5-6' && candidateIsVersionSubmenuTrigger) ||
        (wantsThinking &&
          desiredVersion !== '5-5' &&
          normalizedText === 'gpt 5 5' &&
          !normalizedTestId.includes('pro'));
      const candidateSelectsDesiredVersion =
        Boolean(desiredVersion) && candidateTextVersion === desiredVersion;
      if (
        desiredVersion &&
        candidateTextVersion &&
        candidateTextVersion !== desiredVersion &&
        !candidateOpensVersionSubmenu
      ) {
        return 0;
      }
      const candidateIsGpt55ThinkingFamily =
        wantsThinking &&
        desiredVersion === '5-5' &&
        (normalizedText === 'gpt 5 5' ||
          normalizedText === '5 5' ||
          normalizedTestId.includes('gpt-5-5') ||
          normalizedTestId.includes('gpt-5.5') ||
          normalizedTestId.includes('gpt55'));
      const candidateHasThinking =
        normalizedText.includes('thinking') ||
        normalizedTestId.includes('thinking') ||
        candidateIsNonProGpt55Thinking ||
        candidateClearsProForThinking ||
        candidateOpensVersionSubmenu ||
        candidateIsGpt55ThinkingFamily ||
        (wantsThinking && desiredVersion && (exactTestIdMatch || candidateSelectsDesiredVersion));
      const candidateHasLegacyProVersion = labelHasLegacyProVersion(normalizedText);
      const candidateHasPro =
        labelHasProWord(normalizedText) ||
        normalizedText.includes('proresearch') ||
        normalizedTestId.includes('pro');
      const candidateHasInstant =
        normalizedText.includes('instant') || normalizedTestId.includes('instant');
      const candidateSelectsConfiguredVersion =
        Boolean(getConfigurationDialog()) &&
        candidateSelectsDesiredVersion &&
        (node?.getAttribute?.('role') === 'option' ||
          node?.getAttribute?.('role') === 'menuitemradio');
      const candidateOpensInstantSubmenu =
        wantsInstant &&
        candidateSelectsDesiredVersion &&
        !candidateHasInstant &&
        (normalizedTestId.includes('submenu') ||
          node?.getAttribute?.('aria-haspopup') === 'menu' ||
          node?.getAttribute?.('data-has-submenu') !== null);
      if (wantsPro && candidateHasThinking) return 0;
      if (desiredModelVariant === 'sol' && candidateHasPro) return 0;
      if (wantsPro && candidateHasLegacyProVersion && !candidateSelectsDesiredVersion) return 0;
      if (wantsPro && !candidateHasPro && !candidateSelectsDesiredVersion) return 0;
      if (
        wantsInstant &&
        !candidateHasInstant &&
        !candidateOpensInstantSubmenu &&
        !candidateSelectsConfiguredVersion
      ) return 0;
      if (wantsThinking && candidateHasPro) return 0;
      if (wantsThinking && !candidateHasThinking && !candidateSelectsDesiredVersion) return 0;
      if (desiredVersion === '5-5' && normalizedText && !candidateGpt55VisibleAlias) {
        const candidateHasVersion =
          normalizedText.includes('5 5') ||
          normalizedText.includes('gpt55') ||
          normalizedText.includes('gpt 5 5');
        const versionLikeLabel = /(?:^|\\s)5\\s+[0-9](?:\\s|$)/.test(normalizedText) || normalizedText.includes('gpt');
        if (versionLikeLabel && !candidateHasVersion) {
          return 0;
        }
      }
      if (candidateGpt55VisibleAlias) {
        score += 900;
      }
      if (wantsPro && candidateHasPro && !candidateSelectsDesiredVersion) {
        score += 900;
      }
      if (wantsInstant && candidateHasInstant && !candidateSelectsDesiredVersion) {
        score += 900;
      }
      if (
        wantsThinking &&
        candidateHasThinking &&
        !candidateSelectsDesiredVersion &&
        !candidateOpensVersionSubmenu
      ) {
        score += 900;
      }
      if (candidateIsNonProGpt55Thinking) {
        score += scoreNonProGpt55ThinkingLabel(normalizedText);
      }
      if (candidateClearsProForThinking) {
        score += scoreNonProGpt55ThinkingLabel(normalizedText) + 600;
      }
      if (
        desiredVersion &&
        candidateTextVersion === desiredVersion &&
        !candidateOpensInstantSubmenu &&
        !(wantsThinking && desiredVersion === '5-5' && normalizedText === 'gpt 5 5')
      ) {
        score += 1200;
      }
      if (candidateOpensInstantSubmenu) {
        score += 300;
      }
      if (candidateOpensVersionSubmenu) {
        score += 500;
      }
      if (candidateIsGpt55ThinkingFamily) {
        score += 260;
      }
      if (normalizedText && normalizedTarget) {
        if (normalizedText === normalizedTarget) {
          score += 500;
        } else if (normalizedText.startsWith(normalizedTarget)) {
          score += 420;
        } else if (normalizedText.includes(normalizedTarget)) {
          score += 380;
        }
      }
      for (const token of normalizedTokens) {
        // Reward partial matches to the expanded label/token set.
        if (token && normalizedText.includes(token)) {
          const tokenWeight = Math.min(120, Math.max(10, token.length * 4));
          score += tokenWeight;
        }
      }
      if (targetWords.length > 1) {
        let missing = 0;
        for (const word of targetWords) {
          if (!normalizedText.includes(word)) {
            missing += 1;
          }
        }
        score -= missing * 12;
      }
      // If the caller didn't explicitly ask for Pro, prefer non-Pro options when both exist.
      if (wantsPro) {
        if (!labelHasProWord(normalizedText)) {
          score -= 80;
        }
      } else if (labelHasProWord(normalizedText)) {
        score -= 40;
      }
      // Similarly for Thinking variant
      if (wantsThinking) {
        if (
          !candidateIsGpt55ThinkingFamily &&
          !candidateIsNonProGpt55Thinking &&
          !candidateClearsProForThinking &&
          !candidateOpensVersionSubmenu &&
          !normalizedText.includes('thinking') &&
          !normalizedTestId.includes('thinking')
        ) {
          score -= 80;
        }
      } else if (normalizedText.includes('thinking') || normalizedTestId.includes('thinking')) {
        score -= 40;
      }
      // Similarly for Instant variant
      if (wantsInstant) {
        if (!normalizedText.includes('instant') && !normalizedTestId.includes('instant')) {
          score -= 80;
        }
      } else if (normalizedText.includes('instant') || normalizedTestId.includes('instant')) {
        score -= 40;
      }
      return Math.max(score, 0);
    };

    const hasModelSwitcherItem = (node) =>
      Boolean(node?.querySelector?.('[data-testid^="model-switcher-"]'));
    const hasModelLikeMenuText = (node) => {
      const text = normalizeText(node?.textContent ?? '');
      return (
        text.includes('instant') ||
        text.includes('thinking') ||
        labelHasProWord(text) ||
        text.includes('5 5') ||
        text.includes('5 4') ||
        text.includes('5 2') ||
        text.includes('gpt 5') ||
        text.includes('gpt5')
      );
    };
    const queryPickerMenus = () => {
      const menus = Array.from(document.querySelectorAll(${menuContainerLiteral}));
      const pickerMenus = menus.filter(hasModelSwitcherItem);
      if (pickerMenus.length === 0) return menus;
      const textFallbackMenus = menus.filter(
        (menu) => !pickerMenus.includes(menu) && hasModelLikeMenuText(menu),
      );
      return pickerMenus.concat(textFallbackMenus);
    };
    const isSubmenuOption = (node, testid) =>
      (testid ?? '').toLowerCase().includes('submenu') ||
      (testid ?? '').toLowerCase() === 'model-configure-modal' ||
      (node?.getAttribute?.('role') === 'combobox' &&
        node?.getAttribute?.('aria-labelledby') === 'model-selection-label') ||
      node?.getAttribute?.('aria-haspopup') === 'menu' ||
      node?.getAttribute?.('data-has-submenu') !== null;
    const canTrustSelectedOption = (node, normalizedText, testid) => {
      if (!optionIsSelected(node)) return false;
      if (getConfigurationDialog() && !configuredSelectionMatchesTarget()) return false;
      const optionVersion = versionFromLabel(normalizedText) ?? versionFromTestId(testid);
      if (desiredVersion && optionVersion !== desiredVersion) return false;
      if (desiredModelVariant && !normalizedText.includes(desiredModelVariant)) return false;
      if (
        desiredModelVariant === 'sol' &&
        (labelHasProWord(normalizedText) || normalizeText(testid ?? '').includes('pro'))
      ) return false;
      if (desiredVersion === '5-6') return true;
      const currentButtonLabel = normalizeText(getButtonLabel());
      return !labelHasProWord(currentButtonLabel) && !hasProComposerPill();
    };
    const openedSubmenuKeys = new Set();
    let clickedTargetOption = false;
    const submenuKey = (normalizedText, testid) =>
      normalizeText(testid ?? '') + '|' + normalizedText;

    const findBestOption = () => {
      // Walk through every menu item and keep whichever earns the highest score.
      let bestMatch = null;
      const menus = queryPickerMenus();
      for (const menu of menus) {
        const buttons = Array.from(menu.querySelectorAll(${menuItemLiteral}));
        for (const option of buttons) {
          if (!isVisibleElement(option)) {
            continue;
          }
          if (isNestedEffortControl(option, menu)) {
            continue;
          }
          const text = option.textContent ?? '';
          const normalizedText = normalizeText(text);
          const testid = option.getAttribute('data-testid') ?? '';
          const optionSubmenuKey = submenuKey(normalizedText, testid);
          if (isSubmenuOption(option, testid) && openedSubmenuKeys.has(optionSubmenuKey)) {
            continue;
          }
          let score = scoreOption(normalizedText, testid, option);
          if (score <= 0) {
            continue;
          }
          if (canTrustSelectedOption(option, normalizedText, testid)) {
            score += 1000;
          }
          const label = getOptionLabel(option);
          if (!bestMatch || score > bestMatch.score) {
            bestMatch = {
              node: option,
              label,
              score,
              testid,
              normalizedText,
              submenuKey: optionSubmenuKey,
            };
          }
        }
      }
      return bestMatch;
    };
    const waitForTargetSelection = (previousButtonLabel, previousComposerSignal) => new Promise((resolve) => {
      const waitStart = performance.now();
      const check = () => {
        if (activeSelectionMatchesTarget()) {
          resolve('target');
          return;
        }
        const currentButtonLabel = normalizeText(getButtonLabel());
        if (
          wantsInstant &&
          desiredVersion === '5-5' &&
          currentButtonLabel === 'instant' &&
          currentButtonLabel !== previousButtonLabel
        ) {
          resolve('target');
          return;
        }
        if (selectionStateChanged(previousButtonLabel, previousComposerSignal)) {
          resolve('changed');
          return;
        }
        if (performance.now() - waitStart > SETTLE_WAIT_MS) {
          resolve('timeout');
          return;
        }
        setTimeout(check, 100);
      };
      check();
    });
    const dispatchHoverSequence = (target) => {
      if (!target || !(target instanceof EventTarget)) return false;
      const rect = target.getBoundingClientRect?.();
      const clientX = rect ? rect.x + rect.width / 2 : 0;
      const clientY = rect ? rect.y + rect.height / 2 : 0;
      const types = ['pointerover', 'pointerenter', 'mouseover', 'mouseenter', 'pointermove', 'mousemove'];
      for (const type of types) {
        try {
          const common = { bubbles: true, cancelable: true, view: window, clientX, clientY };
          const event =
            type.startsWith('pointer') && 'PointerEvent' in window
              ? new PointerEvent(type, { ...common, pointerId: 1, pointerType: 'mouse' })
              : new MouseEvent(type, common);
          target.dispatchEvent(event);
        } catch {}
      }
      try {
        target.focus?.();
      } catch {}
      return true;
    };
    const openSubmenuOption = (node) => {
      dispatchHoverSequence(node);
      dispatchClickSequence(node);
    };
    const descendIntoAdvancedModelPicker = () => {
      const parentMenu = findUnifiedPickerMenu();
      if (!parentMenu) return 'unavailable';

      const opener = findModelSubmenuOpener(parentMenu);
      if (opener) {
        if (advancedModelSignalMatchesTarget(parentMenu)) {
          return 'already-selected';
        }
        const key = submenuKey(
          normalizeText(opener.textContent ?? ''),
          opener.getAttribute?.('data-testid') ?? '',
        );
        openedSubmenuKeys.add(key);
        if (opener.getAttribute?.('aria-expanded') !== 'true') {
          openSubmenuOption(opener);
        }
        return 'opened';
      }

      const advancedToggle = findAdvancedToggle(parentMenu);
      if (!advancedToggle) return 'unavailable';
      if (advancedToggle.getAttribute?.('aria-expanded') !== 'true') {
        dispatchClickSequence(advancedToggle);
      }
      return 'expanded';
    };

    return new Promise((resolve) => {
      const start = performance.now();
      const detectTemporaryChat = () => {
        try {
          const url = new URL(window.location.href);
          const flag = (url.searchParams.get('temporary-chat') ?? '').toLowerCase();
          if (flag === 'true' || flag === '1' || flag === 'yes') return true;
        } catch {}
        const title = (document.title || '').toLowerCase();
        if (title.includes('temporary chat')) return true;
        const body = (document.body?.innerText || '').toLowerCase();
        return body.includes('temporary chat');
      };
      const collectAvailableOptions = () => {
        const menuRoots = queryPickerMenus();
        const nodes = menuRoots.flatMap((root) => Array.from(root.querySelectorAll(${menuItemLiteral})));
        const labels = nodes
          .map((node) => (node?.textContent ?? '').trim())
          .filter(Boolean)
          .filter((label, index, arr) => arr.indexOf(label) === index);
        return labels.slice(0, 12);
      };
      const ensureMenuOpen = () => {
        const menuOpen = queryPickerMenus().length > 0;
        if (!menuOpen && performance.now() - lastPointerClick > REOPEN_INTERVAL_MS) {
          pointerClick();
        }
      };

      // Open once and wait a tick before first scan.
      pointerClick();
      const openDelay = () => new Promise((r) => setTimeout(r, INITIAL_WAIT_MS));
      let initialized = false;
      const attempt = async () => {
        if (performance.now() - start > MAX_WAIT_MS) {
          resolve({
            status: 'option-not-found',
            hint: { temporaryChat: detectTemporaryChat(), availableOptions: collectAvailableOptions() },
          });
          return;
        }
        if (!initialized) {
          initialized = true;
          await openDelay();
        }
        ensureMenuOpen();
        const match = findBestOption();
        if (match) {
          if (
            activeSelectionMatchesTarget() ||
            canTrustSelectedOption(match.node, match.normalizedText, match.testid)
          ) {
            const resolvedLabel = getResolvedLabel(match.label);
            closeMenu();
            resolve({
              status: clickedTargetOption ? 'switched' : 'already-selected',
              label: resolvedLabel,
            });
            return;
          }
          const previousButtonLabel = normalizeText(getButtonLabel());
          const previousComposerSignal = readComposerModelSignal();
          // Submenus (e.g. "Legacy models") need a second pass to pick the actual model option.
          // Keep scanning once the submenu opens instead of treating the submenu click as a final switch.
          const isSubmenu = isSubmenuOption(match.node, match.testid);
          if (isSubmenu) {
            openedSubmenuKeys.add(match.submenuKey);
            openSubmenuOption(match.node);
            setTimeout(attempt, REOPEN_INTERVAL_MS / 2);
            return;
          }
          clickedTargetOption = true;
          dispatchClickSequence(match.node);
          // Wait for the selected model signal to settle before reopening the picker.
          waitForTargetSelection(previousButtonLabel, previousComposerSignal).then((selectionSettled) => {
            if (selectionSettled === 'target') {
              const resolvedLabel = getResolvedLabel(match.label);
              closeMenu();
              resolve({ status: 'switched', label: resolvedLabel });
              return;
            }
            attempt();
          });
          return;
        }
        const advancedState = descendIntoAdvancedModelPicker();
        if (advancedState === 'already-selected') {
          const resolvedLabel = getResolvedLabel();
          closeMenu();
          resolve({ status: 'already-selected', label: resolvedLabel });
          return;
        }
        if (advancedState === 'expanded' || advancedState === 'opened') {
          setTimeout(attempt, REOPEN_INTERVAL_MS / 2);
          return;
        }
        if (performance.now() - start > MAX_WAIT_MS) {
          resolve({
            status: 'option-not-found',
            hint: { temporaryChat: detectTemporaryChat(), availableOptions: collectAvailableOptions() },
          });
          return;
        }
        setTimeout(attempt, REOPEN_INTERVAL_MS / 2);
      };
      attempt();
    });
  })()`;
}
export function buildModelMatchersLiteralForTest(targetModel) {
    return buildModelMatchersLiteral(targetModel);
}
function buildComposerSignalMatchers(targetModel) {
    const normalized = targetModel
        .trim()
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, " ")
        .replace(/\s+/g, " ")
        .trim();
    if (normalized.includes("5 6")) {
        return { includesAny: ["5 6 sol"], excludesAny: ["pro"], allowBlank: false };
    }
    if (normalized.includes("pro")) {
        return { includesAny: ["pro"], excludesAny: ["thinking"], allowBlank: false };
    }
    if (normalized.includes("thinking")) {
        return { includesAny: ["thinking"], excludesAny: ["pro"], allowBlank: false };
    }
    if (normalized.includes("instant")) {
        return { includesAny: ["instant"], excludesAny: ["thinking", "pro"], allowBlank: false };
    }
    return { includesAny: [], excludesAny: ["thinking", "pro"], allowBlank: true };
}
export function buildComposerSignalMatchersForTest(targetModel) {
    return buildComposerSignalMatchers(targetModel);
}
function buildModelMatchersLiteral(targetModel) {
    const base = targetModel.trim().toLowerCase();
    const labelTokens = new Set();
    const testIdTokens = new Set();
    const push = (value, set) => {
        const normalized = value?.trim();
        if (normalized) {
            set.add(normalized);
        }
    };
    push(base, labelTokens);
    push(base.replace(/\s+/g, " "), labelTokens);
    const collapsed = base.replace(/\s+/g, "");
    push(collapsed, labelTokens);
    const dotless = base.replace(/[.]/g, "");
    push(dotless, labelTokens);
    push(`chatgpt ${base}`, labelTokens);
    push(`chatgpt ${dotless}`, labelTokens);
    push(`gpt ${base}`, labelTokens);
    push(`gpt ${dotless}`, labelTokens);
    // Numeric variations (5.6 <-> 56 <-> gpt-5-6)
    if (base.includes("5.6") || base.includes("5-6") || base.includes("56")) {
        push("5.6", labelTokens);
        push("gpt-5.6", labelTokens);
        push("gpt5.6", labelTokens);
        push("gpt-5-6", labelTokens);
        push("gpt5-6", labelTokens);
        push("gpt56", labelTokens);
        push("gpt-5.6 sol", labelTokens);
        push("chatgpt 5.6", labelTokens);
        testIdTokens.add("model-switcher-gpt-5-6");
        testIdTokens.add("gpt-5-6");
        testIdTokens.add("gpt5-6");
        testIdTokens.add("gpt56");
    }
    if (base === "latest") {
        // Exact Japanese and Korean labels for the advanced-model Latest radio.
        push("最新", labelTokens);
        push("최신", labelTokens);
    }
    // Numeric variations (5.5 <-> 55 <-> gpt-5-5)
    if (base.includes("5.5") || base.includes("5-5") || base.includes("55")) {
        push("5.5", labelTokens);
        push("gpt-5.5", labelTokens);
        push("gpt5.5", labelTokens);
        push("gpt-5-5", labelTokens);
        push("gpt5-5", labelTokens);
        push("gpt55", labelTokens);
        push("chatgpt 5.5", labelTokens);
        if (base.includes("thinking")) {
            push("thinking heavy", labelTokens);
            push("heavy thinking", labelTokens);
            testIdTokens.add("model-switcher-gpt-5-5-thinking");
            testIdTokens.add("gpt-5-5-thinking");
            testIdTokens.add("gpt-5.5-thinking");
        }
        if (base.includes("instant")) {
            push("instant", labelTokens);
            testIdTokens.add("model-switcher-gpt-5-5-instant");
            testIdTokens.add("gpt-5-5-instant");
            testIdTokens.add("gpt-5.5-instant");
        }
        if (!base.includes("pro") && !base.includes("thinking") && !base.includes("instant")) {
            testIdTokens.add("model-switcher-gpt-5-5");
        }
        testIdTokens.add("gpt-5-5");
        testIdTokens.add("gpt5-5");
        testIdTokens.add("gpt55");
    }
    // Numeric variations (5.4 ↔ 54 ↔ gpt-5-4)
    if (base.includes("5.4") || base.includes("5-4") || base.includes("54")) {
        push("5.4", labelTokens);
        push("gpt-5.4", labelTokens);
        push("gpt5.4", labelTokens);
        push("gpt-5-4", labelTokens);
        push("gpt5-4", labelTokens);
        push("gpt54", labelTokens);
        push("chatgpt 5.4", labelTokens);
        if (!base.includes("pro")) {
            testIdTokens.add("model-switcher-gpt-5-4");
        }
        testIdTokens.add("gpt-5-4");
        testIdTokens.add("gpt5-4");
        testIdTokens.add("gpt54");
    }
    // Numeric variations (5.3 ↔ 53 ↔ gpt-5-3)
    if (base.includes("5.3") || base.includes("5-3") || base.includes("53")) {
        push("5.3", labelTokens);
        push("gpt-5.3", labelTokens);
        push("gpt5.3", labelTokens);
        push("gpt-5-3", labelTokens);
        push("gpt5-3", labelTokens);
        push("gpt53", labelTokens);
        push("chatgpt 5.3", labelTokens);
        if (base.includes("thinking")) {
            push("thinking", labelTokens);
            testIdTokens.add("model-switcher-gpt-5-3-thinking");
            testIdTokens.add("gpt-5-3-thinking");
            testIdTokens.add("gpt-5.3-thinking");
        }
        if (base.includes("instant")) {
            push("instant", labelTokens);
            testIdTokens.add("model-switcher-gpt-5-3-instant");
            testIdTokens.add("gpt-5-3-instant");
            testIdTokens.add("gpt-5.3-instant");
        }
        if (!base.includes("pro") && !base.includes("thinking") && !base.includes("instant")) {
            testIdTokens.add("model-switcher-gpt-5-3");
        }
        testIdTokens.add("gpt-5-3");
        testIdTokens.add("gpt5-3");
        testIdTokens.add("gpt53");
    }
    // Numeric variations (5.1 ↔ 51 ↔ gpt-5-1)
    if (base.includes("5.1") || base.includes("5-1") || base.includes("51")) {
        push("5.1", labelTokens);
        push("gpt-5.1", labelTokens);
        push("gpt5.1", labelTokens);
        push("gpt-5-1", labelTokens);
        push("gpt5-1", labelTokens);
        push("gpt51", labelTokens);
        push("chatgpt 5.1", labelTokens);
        testIdTokens.add("gpt-5-1");
        testIdTokens.add("gpt5-1");
        testIdTokens.add("gpt51");
    }
    // Numeric variations (5.0 ↔ 50 ↔ gpt-5-0)
    if (base.includes("5.0") || base.includes("5-0") || base.includes("50")) {
        push("5.0", labelTokens);
        push("gpt-5.0", labelTokens);
        push("gpt5.0", labelTokens);
        push("gpt-5-0", labelTokens);
        push("gpt5-0", labelTokens);
        push("gpt50", labelTokens);
        push("chatgpt 5.0", labelTokens);
        testIdTokens.add("gpt-5-0");
        testIdTokens.add("gpt5-0");
        testIdTokens.add("gpt50");
    }
    // Numeric variations (5.2 ↔ 52 ↔ gpt-5-2)
    if (base.includes("5.2") || base.includes("5-2") || base.includes("52")) {
        push("5.2", labelTokens);
        push("gpt-5.2", labelTokens);
        push("gpt5.2", labelTokens);
        push("gpt-5-2", labelTokens);
        push("gpt5-2", labelTokens);
        push("gpt52", labelTokens);
        push("chatgpt 5.2", labelTokens);
        // Thinking variant: explicit testid for "Thinking" picker option
        if (base.includes("thinking")) {
            push("thinking", labelTokens);
            testIdTokens.add("model-switcher-gpt-5-2-thinking");
            testIdTokens.add("gpt-5-2-thinking");
            testIdTokens.add("gpt-5.2-thinking");
        }
        // Instant variant: explicit testid for "Instant" picker option
        if (base.includes("instant")) {
            push("instant", labelTokens);
            testIdTokens.add("model-switcher-gpt-5-2-instant");
            testIdTokens.add("gpt-5-2-instant");
            testIdTokens.add("gpt-5.2-instant");
        }
        // Base 5.2 testids (for "Auto" mode when no suffix specified)
        if (!base.includes("thinking") && !base.includes("instant") && !base.includes("pro")) {
            testIdTokens.add("model-switcher-gpt-5-2");
        }
        testIdTokens.add("gpt-5-2");
        testIdTokens.add("gpt5-2");
        testIdTokens.add("gpt52");
    }
    // Pro / research variants
    if (base.includes("pro")) {
        push("proresearch", labelTokens);
        push("research grade", labelTokens);
        push("advanced reasoning", labelTokens);
        if (base.includes("5.5") || base.includes("5-5") || base.includes("55")) {
            push("pro extended", labelTokens);
            push("extended pro", labelTokens);
            testIdTokens.add("gpt-5.5-pro");
            testIdTokens.add("gpt-5-5-pro");
            testIdTokens.add("gpt55pro");
        }
        if (base.includes("5.4") || base.includes("5-4") || base.includes("54")) {
            testIdTokens.add("gpt-5.4-pro");
            testIdTokens.add("gpt-5-4-pro");
            testIdTokens.add("gpt54pro");
        }
        if (base.includes("5.3") || base.includes("5-3") || base.includes("53")) {
            testIdTokens.add("gpt-5.3-pro");
            testIdTokens.add("gpt-5-3-pro");
            testIdTokens.add("gpt53pro");
        }
        if (base.includes("5.1") || base.includes("5-1") || base.includes("51")) {
            testIdTokens.add("gpt-5.1-pro");
            testIdTokens.add("gpt-5-1-pro");
            testIdTokens.add("gpt51pro");
        }
        if (base.includes("5.0") || base.includes("5-0") || base.includes("50")) {
            testIdTokens.add("gpt-5.0-pro");
            testIdTokens.add("gpt-5-0-pro");
            testIdTokens.add("gpt50pro");
        }
        if (base.includes("5.2") || base.includes("5-2") || base.includes("52")) {
            testIdTokens.add("gpt-5.2-pro");
            testIdTokens.add("gpt-5-2-pro");
            testIdTokens.add("gpt52pro");
        }
        testIdTokens.add("pro");
        testIdTokens.add("proresearch");
    }
    base
        .split(/\s+/)
        .map((token) => token.trim())
        .filter(Boolean)
        .forEach((token) => {
        push(token, labelTokens);
    });
    const hyphenated = base.replace(/\s+/g, "-");
    push(hyphenated, testIdTokens);
    push(collapsed, testIdTokens);
    push(dotless, testIdTokens);
    // data-testid values observed in the ChatGPT picker (e.g., model-switcher-gpt-5.1-pro)
    push(`model-switcher-${hyphenated}`, testIdTokens);
    push(`model-switcher-${collapsed}`, testIdTokens);
    push(`model-switcher-${dotless}`, testIdTokens);
    if (!labelTokens.size) {
        labelTokens.add(base);
    }
    if (!testIdTokens.size) {
        testIdTokens.add(base.replace(/\s+/g, "-"));
    }
    return {
        labelTokens: Array.from(labelTokens).filter(Boolean),
        testIdTokens: Array.from(testIdTokens).filter(Boolean),
    };
}
export function buildModelSelectionExpressionForTest(targetModel, strategy = "select") {
    return buildModelSelectionExpression(targetModel, strategy);
}

function buildModelSelectorDiagnosticExpression() {
    return `(() => {
      // Structural-only, bounded evidence. Never emit raw text, labels, title,
      // HTML, URLs, aria-controls IDs, input values, or conversation content.
      const selector = '[data-testid="model-switcher-dropdown-button"], button.__composer-pill, header button, form button, [data-testid="composer-footer-actions"] button, button[aria-haspopup], [role="button"][aria-haspopup], button[data-testid*="model"], button[data-testid*="intelligence"]';
      const nodes = Array.from(new Set(document.querySelectorAll(selector)));
      const allowedRoles = ['button', 'menuitem', 'menuitemradio', 'combobox'];
      const allowedPopups = ['true', 'menu', 'listbox', 'dialog'];
      const knownWords = ['latest', '最新', '최신', 'chatgpt', 'model', 'models', 'intelligence', 'select model', 'choose model', 'change model', 'модель', 'модели', 'выбрать модель', 'интеллект', 'pro', 'thinking', 'high', 'medium', 'extra high', 'instant', 'light', 'standard', 'extended', 'heavy', 'sol'];
      const safeModelSignal = (value) => {
        const label = String(value ?? '').normalize('NFC').trim().toLowerCase().replace(/\\s+/g, ' ');
        if (knownWords.includes(label)) return label;
        const words = ' ' + label.replace(/[^\\p{L}\\p{N}.-]+/gu, ' ') + ' ';
        const signals = knownWords.filter((word) => words.includes(' ' + word + ' '));
        const versions = label.matchAll(/(?:^|[^a-z0-9])((?:gpt[- ]?)?[56](?:[.-][0-9]{1,2})?(?: ?(?:pro|thinking|high|medium|low|extra high|instant|light|standard|extended|heavy|sol))?)(?=$|[^a-z0-9])/g);
        for (const match of versions) signals.push(match[1]);
        return Array.from(new Set(signals)).slice(0, 6).join(' | ') || null;
      };
      const isVisible = (node) => {
        const rect = node.getBoundingClientRect?.();
        const style = window.getComputedStyle?.(node);
        return Boolean(rect && rect.width > 0 && rect.height > 0 && style?.display !== 'none' && style?.visibility !== 'hidden');
      };
      const describe = (node) => {
        const visible = isVisible(node);
        const testid = node.getAttribute?.('data-testid') ?? '';
        const role = node.getAttribute?.('role') ?? '';
        const popup = node.getAttribute?.('aria-haspopup') ?? '';
        const tag = String(node.tagName ?? '').toLowerCase();
        return {
          tag: ['button', 'div', 'a', 'span'].includes(tag) ? tag : 'other',
          testid: testid === 'model-switcher-dropdown-button' ? testid : testid ? 'other' : null,
          role: allowedRoles.includes(role) ? role : null,
          popup: allowedPopups.includes(popup) ? popup : null,
          visible,
          disabled: Boolean(node.disabled || node.getAttribute?.('aria-disabled') === 'true'),
          composerPill: Boolean(node.matches?.('button.__composer-pill')),
          inHeader: Boolean(node.closest?.('header')),
          inForm: Boolean(node.closest?.('form')),
          inComposerFooter: Boolean(node.closest?.('[data-testid="composer-footer-actions"]')),
          textSignal: safeModelSignal(node.textContent),
          ariaSignal: safeModelSignal(node.getAttribute?.('aria-label')),
          titleSignal: safeModelSignal(node.getAttribute?.('title')),
        };
      };
      const controls = nodes.map(describe);
      const relevance = (node) =>
        (node.testid === 'model-switcher-dropdown-button' ? 100 : 0) +
        (node.composerPill ? 50 : 0) + (node.inComposerFooter ? 40 : 0) +
        (node.textSignal || node.ariaSignal || node.titleSignal ? 30 : 0) +
        (node.inForm ? 20 : 0) + (node.inHeader ? 10 : 0);
      const visible = controls.filter((node) => node.visible).sort((a, b) => relevance(b) - relevance(a));
      const inputNodes = Array.from(new Set(document.querySelectorAll('input, textarea, [contenteditable="true"], [role="textbox"]')));
      const inputs = inputNodes.map((node) => {
        const tag = String(node.tagName ?? '').toLowerCase();
        const type = node.getAttribute?.('type') ?? '';
        return {
          tag: ['input', 'textarea', 'div'].includes(tag) ? tag : 'other',
          type: ['text', 'search', 'password', 'email', 'hidden', 'file', 'submit'].includes(type) ? type : null,
          visible: isVisible(node),
          disabled: Boolean(node.disabled || node.getAttribute?.('aria-disabled') === 'true'),
          textboxRole: node.getAttribute?.('role') === 'textbox',
          contenteditable: node.getAttribute?.('contenteditable') === 'true',
          promptId: node.getAttribute?.('id') === 'prompt-textarea',
          promptTestId: node.getAttribute?.('data-testid') === 'prompt-textarea',
          promptName: node.getAttribute?.('name') === 'prompt-textarea',
          inForm: Boolean(node.closest?.('form')),
          inMain: Boolean(node.closest?.('main, [role="main"]')),
        };
      });
      const visibleInputs = inputs.filter((node) => node.visible);
      const loginNodes = Array.from(document.querySelectorAll('[data-testid="login-button"], a[href="/auth/login"]'));
      const title = String(document.title ?? '').trim().toLowerCase();
      const titleBucket = title === 'just a moment...' || title === 'just a moment' ? 'challenge'
        : title.includes('chatgpt') ? 'chatgpt'
        : /^(?:log in|login|sign in)(?:$| -| to)/.test(title) ? 'login' : 'other';
      const has = (selector) => Boolean(document.querySelector(selector));
      return {
        status: 'captured',
        readyState: ['loading', 'interactive', 'complete'].includes(document.readyState) ? document.readyState : 'unknown',
        titleBucket,
        shell: { prompt: has('#prompt-textarea'), form: has('form'), header: has('header'), main: has('main, [role="main"]') },
        indicators: { login: loginNodes.some(isVisible), loginPresent: loginNodes.length > 0, challenge: titleBucket === 'challenge', challengeScriptPresent: has('script[src*="/challenge-platform/"]') },
        inputs: { counts: { total: inputs.length, visible: visibleInputs.length, textareas: inputs.filter((node) => node.tag === 'textarea').length, contenteditable: inputs.filter((node) => node.contenteditable).length }, nodes: visibleInputs.slice(0, 10), truncated: visibleInputs.length > 10 },
        counts: { total: controls.length, visible: visible.length, hidden: controls.length - visible.length, disabledVisible: visible.filter((node) => node.disabled).length },
        controls: visible.slice(0, 40),
        truncated: visible.length > 40,
      };
    })()`;
}

function serializeModelSelectorDiagnostic(diagnostic) {
    const value = { ...diagnostic };
    if (Array.isArray(value.controls)) {
        value.truncated = Boolean(value.truncated || value.controls.length > 10);
        value.controls = value.controls.slice(0, 10);
    }
    let serialized = JSON.stringify(value);
    // Keep valid JSON and aggregate shell/count evidence when reducing controls.
    // The full error message stays below 8 KiB, including multibyte labels.
    while (Buffer.byteLength(serialized, "utf8") > 7600 && value.controls?.length) {
        value.controls.pop();
        value.truncated = true;
        serialized = JSON.stringify(value);
    }
    return Buffer.byteLength(serialized, "utf8") <= 7600
        ? serialized
        : JSON.stringify({ status: "diagnostic-over-budget" });
}

export function buildModelSelectorDiagnosticExpressionForTest() {
    return buildModelSelectorDiagnosticExpression();
}
