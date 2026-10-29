/* Project-local Oracle MCP stdio client for the StoryArt Web image backend.
 * Derived from Web Advisor's dist/oracle.js (ProjectLocalOracleMcpClient and launch
 * helpers only). setup-web-image.ps1 copies this file into the private installation
 * next to node_modules, so the pinned @steipete/oracle and @modelcontextprotocol/client
 * resolve from there and the file is covered by the installation manifest. */
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { Client } from "@modelcontextprotocol/client";
import { getDefaultEnvironment, StdioClientTransport } from "@modelcontextprotocol/client/stdio";

const CLIENT_VERSION = "1.0.0";
const ORACLE_ENV_ALLOWLIST = ["ORACLE_HOME_DIR", "ORACLE_BROWSER_PROFILE_DIR", "CHROME_PATH"];
const IMAGE_ORACLE_ENV_ALLOWLIST = ["ORACLE_REQUIRE_NATIVE_IMAGE_TOOL", "ORACLE_IMAGE_PROJECT_ID", "ORACLE_IMAGE_BROWSER_DOWNLOAD_ONLY"];

export class OracleClientError extends Error {
    constructor(message, code) {
        super(message);
        this.name = "OracleClientError";
        this.code = code;
    }
}

/** Owns one pinned Oracle MCP stdio child started from the private installation. */
export class ProjectLocalOracleMcpClient {
    constructor(installationRoot, launchContext) {
        this.installationRoot = installationRoot;
        this.launchContext = launchContext;
    }
    async callTool(name, arguments_) {
        const result = await (await this.connect()).callTool({ name, arguments: arguments_ });
        if (result.isError)
            throw new OracleClientError(readMcpError(result), "ORACLE_MCP");
        if (!isRecord(result.structuredContent))
            throw new OracleClientError(`Oracle MCP ${name} response has no object structuredContent`, "TRANSPORT_SHAPE");
        return result.structuredContent;
    }
    async close() {
        const client = this.client;
        const transport = this.transport;
        this.client = undefined;
        this.transport = undefined;
        this.connecting = undefined;
        await client?.close().catch(() => undefined);
        await transport?.close().catch(() => undefined);
    }
    async connect() {
        if (this.client)
            return this.client;
        if (!this.connecting)
            this.connecting = this.start();
        return this.connecting;
    }
    async start() {
        const transport = new StdioClientTransport(buildProjectLocalOracleLaunchOptions(this.installationRoot, this.launchContext));
        const client = new Client({ name: "storyart-web-image", version: CLIENT_VERSION });
        try {
            await client.connect(transport);
            this.client = client;
            this.transport = transport;
            return client;
        }
        catch (error) {
            await client.close().catch(() => undefined);
            await transport.close().catch(() => undefined);
            this.connecting = undefined;
            throw error;
        }
    }
}

export function resolveOracleEntrypoint(installationRoot, binary) {
    const localRequire = createRequire(join(installationRoot, "package.json"));
    return join(dirname(localRequire.resolve("@steipete/oracle/package.json")), "dist", "bin", binary);
}

function oracleBrowserEnvironment(context) {
    const environment = { ...getDefaultEnvironment(), ORACLE_ENGINE: "browser" };
    for (const key of ORACLE_ENV_ALLOWLIST)
        if (process.env[key])
            environment[key] = process.env[key];
    if (context?.environment) {
        for (const [key, value] of Object.entries(context.environment)) {
            if (![...ORACLE_ENV_ALLOWLIST, ...IMAGE_ORACLE_ENV_ALLOWLIST].includes(key))
                throw new OracleClientError(`Oracle launch context does not allow ${key}`, "ORACLE_ENV");
            if (typeof value !== "string" || value.length === 0)
                throw new OracleClientError(`Oracle launch context requires a non-empty ${key}`, "ORACLE_ENV");
            environment[key] = value;
        }
    }
    return environment;
}

export function buildProjectLocalOracleLaunchOptions(installationRoot, context) {
    const launcher = context?.launcher;
    if (launcher && (!launcher.command || !Array.isArray(launcher.args) || launcher.args.some((arg) => typeof arg !== "string"))) {
        throw new OracleClientError("Oracle launcher command/arguments are invalid", "ORACLE_LAUNCH");
    }
    return {
        command: launcher?.command ?? process.execPath,
        args: launcher ? [...launcher.args] : [resolveOracleEntrypoint(installationRoot, "oracle-mcp.js")],
        cwd: context?.cwd ?? installationRoot,
        env: oracleBrowserEnvironment(context),
        stderr: "pipe"
    };
}

function readMcpError(result) {
    if (!Array.isArray(result.content))
        return "Oracle MCP returned an error without text content";
    return result.content.filter(isRecord).map((item) => typeof item.text === "string" ? item.text : "").filter(Boolean).join("\n")
        || "Oracle MCP returned an error without text content";
}
function isRecord(value) { return Boolean(value && typeof value === "object" && !Array.isArray(value)); }
