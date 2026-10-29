---
name: storyart-web-image
description: Route an explicitly requested ChatGPT Web image generation through StoryArt's project-local web_image agent, with final-spec handoff and original image download. Use only in StoryArt; text consultations use Web Advisor.
---

# StoryArt Web images

This is an execution choice inside the existing StoryArt scenario. Keep the
current chat's approved style, identity, references, prohibitions and QA.
Do not ask again for resolved choices. Do not use a built-in/API fallback.

Before final call readiness, check the runtime using
`scripts/setup-web-image.ps1 -CheckOnly`. The parent handles missing dependencies
with `scripts/setup-web-image.ps1`, using bundled Codex runtime paths when needed.
Setup does not open a browser. Only account login and creation/binding of a
dedicated ChatGPT project require the human. See `docs/CHATGPT_WEB_IMAGE.md`;
authentication is never inferred from the existence of a profile folder.

After source selection, exact-call validation and EXECUTION_STARTED, the parent
prepares a `chatgpt_web_image` journal in the one active request folder. Save
the complete final user specification as UTF-8 in that same folder. Use
`chatgpt_web_image_handoff create` to bind that specification, unchanged prompt,
reference hashes, scenario attempt and pinned project into a WEB_IMAGE handoff.

Spawn one `web_image` agent with the packet path and workspace, without the
conversation transcript. The role is registered locally at
`.codex/agents/web_image.toml`, using Sol 6.1 Low. The agent executes the same
prepared Oracle operation and returns the downloaded original and receipt.
Do not perform manual browser actions from the parent chat.

Timeouts preserve the original operation. UNKNOWN requires reconciliation,
never an automatic replacement send. CAPTCHA, safety refusal or missing
login/project cannot be bypassed. Internal adapter errors remain recovery work
while a safe authorized step exists.

The parent inspects the full-resolution image against the complete contract,
registers the original artifact, delivers it and completes the guard.
DOWNLOADED is transport evidence, not visual acceptance. No setup/calibration
image is generated merely to configure this integration.
